"""A gene-reference refresh and a clinical CNV knowledgebase rebuild run only for an active administrator (E2E, real Postgres; SEC-35).

Only an administrator asks for either (``/api/admin/gene-reference/refresh-gene``,
``/api/admin/clinical-cnv-kb/rebuild``), and the job ran whoever had asked for it by then:
those jobs act with no account's rights, and recorded who asked only by the e-mail address.
The owner decided on 2026-10-28 that a job whose requester was deactivated, or is no longer an
administrator, is refused. Each job now keeps its requester by the account's id
(``requested_by_id``), and its run checks that account first.

Through the app over HTTP (an in-process ``httpx.ASGITransport`` client on one event loop, see
test_e2e_api_contract.py), for each of two colleagues, administrators with a password of their
own: the colleague asks for a refresh of one gene and for a rebuild of the knowledgebase. The
e2e administrator then deactivates the first colleague's account, and sets the second's role
to viewer (``PATCH /api/auth/users/{id}``). The gene-reference worker takes the refresh up and
runs it (``run_gene_reference_refresh_job``), and the rebuild's run starts
(``clinical_cnv_kb_jobs._run_job``): each job ends ``failed``, saying why, its slot freed for
the next one, with no gene record written and no build started; the log names neither
colleague's account nor their address. Then the e2e administrator asks for the same refresh
and the worker runs it to its end. The rebuild's build and the refresh's sources are stand-ins
(no download, nothing loaded into the shared e2e reference data): the rebuild's queueing keeps
its run from starting by itself, and its real run is started by the test.

The values are synthetic: the colleagues' addresses are unique per run and their passwords
made at run time. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job
sets it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

# What the e2e administrator changes of each colleague's account after the colleague asked for
# the jobs, and the error each job ends with (gene_info_jobs_pg, clinical_cnv_kb_jobs).
_CHANGES = {"deactivated": {"is_active": False}, "demoted": {"role": "viewer"}}
_ERRORS = {"deactivated": "REQUESTER_INACTIVE_ERROR", "demoted": "REQUESTER_NOT_ADMIN_ERROR"}
_GENE = "SEC35GENE"


def _cap(response) -> dict[str, Any]:
    out: dict[str, Any] = {"status": response.status_code}
    if response.content:
        out["json"] = response.json()
    return out


class _LogLines(logging.Handler):
    """Each record logged while it is attached, formatted with its traceback."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


async def _exercise(
    run: str, stand_ins: dict[str, list[str]], run_rebuild: Callable[[str], Awaitable[None]]
) -> dict[str, Any]:
    """``run_rebuild`` is the rebuild's real run, which its queueing is kept from starting."""
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.dependencies import get_password_hash
    from backend.app.main import app
    from backend.app.services import gene_info_jobs_pg as gene_jobs
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        await _harness.ensure_e2e_project(session)
        # The rules are global (one active refresh, one active rebuild): start with none.
        await session.execute(
            text(
                "UPDATE gene_info_refresh_jobs SET status = 'failed', active_slot = NULL, "
                "worker_id = NULL, completed_at = now(), error = 'e2e: left unfinished' "
                "WHERE status IN ('queued', 'running')"
            )
        )
        await session.execute(
            text(
                "UPDATE clinical_cnv_kb_jobs SET status = 'failed', worker_id = NULL, "
                "completed_at = now(), error = 'e2e: left unfinished' "
                "WHERE status IN ('queued', 'running')"
            )
        )
        await session.commit()

    colleagues: dict[str, dict[str, str]] = {}
    for change in _CHANGES:
        email = f"colleague-{change}-{run}@example.org"
        password = uuid4().hex  # made at run time
        async with sessionmaker() as session:
            colleague_id = (
                await session.execute(
                    text(
                        "INSERT INTO users (username, hashed_password, role, email, is_active) "
                        "VALUES (:email, :hashed, 'admin', :email, TRUE) RETURNING id::text"
                    ),
                    {"email": email, "hashed": get_password_hash(password)},
                )
            ).scalar_one()
            await session.commit()
        colleagues[change] = {"email": email, "password": password, "id": colleague_id}

    async def refresh_row(job_id: str) -> dict[str, Any]:
        async with sessionmaker() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT status, error, active_slot, worker_id, completed_at IS NOT NULL AS ended, "
                        "requested_by, requested_by_id::text AS requested_by_id "
                        "FROM gene_info_refresh_jobs WHERE id = CAST(:j AS uuid)"
                    ),
                    {"j": job_id},
                )
            ).mappings().one()
        return dict(row)

    async def rebuild_row(job_id: str) -> dict[str, Any]:
        async with sessionmaker() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT status, error, worker_id, inserted, completed_at IS NOT NULL AS ended, "
                        "requested_by, requested_by_id::text AS requested_by_id "
                        "FROM clinical_cnv_kb_jobs WHERE id = CAST(:j AS uuid)"
                    ),
                    {"j": job_id},
                )
            ).mappings().one()
        return dict(row)

    async def take_up_and_run_refresh(job_id: str) -> str | None:
        """Take the refresh up as the worker does, and run it; the job it took."""
        worker = f"e2e-refresh-{uuid4().hex[:8]}"
        async with sessionmaker() as session:
            taken = await gene_jobs.claim_next_gene_reference_refresh_job(session, worker_id=worker)
        if taken is None or taken["id"] != job_id:
            return None if taken is None else str(taken["id"])
        await gene_jobs.run_gene_reference_refresh_job(job_id=job_id, worker_id=worker)
        return job_id

    out: dict[str, Any] = {}
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as client:
        administrator = {"Authorization": f"Bearer {await _harness.login_admin_token(client)}"}
        for change, update in _CHANGES.items():
            account = colleagues[change]
            seen: dict[str, Any] = {"email": account["email"], "colleague_id": account["id"]}
            signed_in = await client.post(
                "/api/auth/login", json={"email": account["email"], "password": account["password"]}
            )
            colleague = {"Authorization": f"Bearer {signed_in.json()['access_token']}"}

            refresh = await client.post(
                "/api/admin/gene-reference/refresh-gene", params={"symbol": _GENE}, headers=colleague
            )
            seen["refresh_queued"] = _cap(refresh)
            rebuild = await client.post(
                "/api/admin/clinical-cnv-kb/rebuild",
                json={"assembly": _harness.ASSEMBLY, "skip_clinvar": True},
                headers=colleague,
            )
            seen["rebuild_queued"] = _cap(rebuild)

            # The e2e administrator deactivates the colleague, or sets their role to viewer,
            # before either job runs.
            seen["changed"] = _cap(
                await client.patch(f"/api/auth/users/{account['id']}", json=update, headers=administrator)
            )

            lines = _LogLines()
            root = logging.getLogger()
            root.addHandler(lines)
            try:
                refresh_id = str(refresh.json()["_id"])
                seen["refresh_taken"] = await take_up_and_run_refresh(refresh_id)
                rebuild_id = str(rebuild.json()["_id"])
                await run_rebuild(rebuild_id)
            finally:
                root.removeHandler(lines)
            seen["log"] = lines.lines
            seen["refresh"] = await refresh_row(refresh_id)
            seen["rebuild"] = await rebuild_row(rebuild_id)
            out[change] = seen

        # The same refresh, asked for by the e2e administrator, active.
        control = await client.post(
            "/api/admin/gene-reference/refresh-gene", params={"symbol": _GENE}, headers=administrator
        )
        out["control_queued"] = _cap(control)
        control_id = str(control.json()["_id"])
        out["control_taken"] = await take_up_and_run_refresh(control_id)
        out["control"] = await refresh_row(control_id)
        out["administrator_id"] = (await client.get("/api/auth/me", headers=administrator)).json()["id"]
    out["stand_ins"] = {name: list(calls) for name, calls in stand_ins.items()}
    return out


@pytest.fixture(scope="module")
def ran(request) -> dict[str, Any]:
    from backend.app.schemas import GeneBulkRefreshOut
    from backend.app.services import clinical_cnv_kb_jobs as kb
    from backend.app.services import gene_info_jobs_pg as gene_jobs
    from backend.tests.e2e import _harness

    stand_ins: dict[str, list[str]] = {
        "rebuild started by itself": [],
        "build": [],
        "load": [],
        "refresh": [],
    }
    # The rebuild's real run, kept before its queueing is kept from starting it.
    run_rebuild = kb._run_job

    async def queued_rebuild_not_started(job_id: str) -> None:
        stand_ins["rebuild started by itself"].append(job_id)

    async def build(*cmd: Any, **_kwargs: Any) -> Any:
        stand_ins["build"].append(" ".join(str(part) for part in cmd))
        raise RuntimeError("e2e: the build is not run here")

    async def load(_session: Any, **_kwargs: Any) -> Any:
        stand_ins["load"].append("loaded")
        raise RuntimeError("e2e: nothing is loaded here")

    async def sources(**_kwargs: Any) -> Any:
        stand_ins["refresh"].append("sources loaded")
        return None

    async def genes(_session: Any, **_kwargs: Any) -> Any:
        stand_ins["refresh"].append("genes chosen")
        return gene_jobs.HumanGeneContext(species={}, assemblies=[]), {}

    async def records(_session: Any, **_kwargs: Any) -> Any:
        stand_ins["refresh"].append("records written")
        return GeneBulkRefreshOut(
            human_assemblies=1, gene_symbols=0, updated_records=0, completed_at=datetime.now(timezone.utc)
        )

    patch = pytest.MonkeyPatch()
    request.addfinalizer(patch.undo)
    patch.setattr(kb, "_script_path", lambda: Path("/app/scripts/clinical_cnv_knowledgebase.py"))
    patch.setattr(kb, "_run_job", queued_rebuild_not_started)
    patch.setattr(kb.asyncio, "create_subprocess_exec", build)
    patch.setattr(kb, "apply_reference_dataset_text", load)
    patch.setattr(gene_jobs, "load_human_gene_bulk_context", sources)
    patch.setattr(gene_jobs, "_load_human_gene_groups", genes)
    patch.setattr(gene_jobs, "_refresh_grouped_human_gene_info", records)
    run = uuid4().hex[:12]
    return _harness.run_async(lambda: _exercise(run, stand_ins, run_rebuild))


@pytest.mark.parametrize("change", list(_CHANGES))
def test_the_colleague_asked_for_both_jobs_and_was_then_deactivated_or_demoted(ran, change: str) -> None:
    seen = ran[change]
    for queued in (seen["refresh_queued"], seen["rebuild_queued"]):
        assert queued["status"] == 200, queued
        assert queued["json"]["status"] == "queued"
        assert queued["json"]["requested_by"] == seen["email"]
    assert seen["changed"]["status"] == 200, seen["changed"]
    if change == "deactivated":
        assert seen["changed"]["json"]["is_active"] is False
    else:
        assert (seen["changed"]["json"]["role"], seen["changed"]["json"]["is_active"]) == ("viewer", True)


@pytest.mark.parametrize("change", list(_CHANGES))
def test_the_refresh_ends_failed_saying_why_and_frees_its_slot(ran, change: str) -> None:
    from backend.app.services import gene_info_jobs_pg as gene_jobs

    seen = ran[change]
    assert seen["refresh_taken"] == str(seen["refresh_queued"]["json"]["_id"])
    job = seen["refresh"]
    # Before, the worker ran the refresh, whoever had asked for it.
    assert job["status"] == "failed", job
    assert job["error"] == getattr(gene_jobs, _ERRORS[change])
    assert job["ended"] is True and job["worker_id"] is None and job["active_slot"] is None
    # The job keeps its requester, the account and the address it was asked under.
    assert (job["requested_by_id"], job["requested_by"]) == (seen["colleague_id"], seen["email"])


@pytest.mark.parametrize("change", list(_CHANGES))
def test_the_rebuild_ends_failed_saying_why_with_nothing_built(ran, change: str) -> None:
    from backend.app.services import clinical_cnv_kb_jobs as kb

    seen = ran[change]
    job = seen["rebuild"]
    assert job["status"] == "failed", job
    assert job["error"] == getattr(kb, _ERRORS[change])
    assert job["ended"] is True and job["worker_id"] is None and job["inserted"] == 0
    assert (job["requested_by_id"], job["requested_by"]) == (seen["colleague_id"], seen["email"])


def test_no_gene_record_was_written_no_build_started_and_nothing_loaded(ran) -> None:
    # Only the administrator's own refresh ran; the queued rebuilds did not start by
    # themselves (the test started their run).
    assert ran["stand_ins"]["refresh"] == ["sources loaded", "genes chosen", "records written"]
    assert ran["stand_ins"]["build"] == []
    assert ran["stand_ins"]["load"] == []
    assert len(ran["stand_ins"]["rebuild started by itself"]) == len(_CHANGES)


@pytest.mark.parametrize("change", list(_CHANGES))
def test_the_log_names_neither_the_account_nor_its_address(ran, change: str) -> None:
    seen = ran[change]
    assert seen["log"], "the refusals were logged"
    for line in seen["log"]:
        assert seen["email"] not in line
        assert seen["colleague_id"] not in line


def test_the_same_refresh_asked_for_by_an_active_administrator_runs(ran) -> None:
    assert ran["control_queued"]["status"] == 200, ran["control_queued"]
    assert ran["control_taken"] == str(ran["control_queued"]["json"]["_id"])
    job = ran["control"]
    assert job["status"] == "completed", job
    assert job["requested_by_id"] == ran["administrator_id"]
