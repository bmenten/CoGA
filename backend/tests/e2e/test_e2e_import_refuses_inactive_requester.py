"""A queued import does not run as an account deactivated, or no longer an administrator, since it was queued (E2E, real Postgres and ClickHouse).

Found while fixing SEC-32 (#1005). The worker runs a queued import job as the account that
requested it, found by the job's ``requested_by_id``, and took that account whatever its
state: an account an administrator deactivated between the request and the run had its import
run as it, writing the family, though deactivating an account ends its access at once
(docs/security-posture.md). And only an administrator queues an import, but the import
checked the requester's access to the job's project only: an administrator demoted to viewer
since, an analyst of that project, still had it run (SEC-35; refused as the owner decided on
2026-10-28).

Through the app over HTTP (an in-process ``httpx.ASGITransport`` client on one event loop, see
test_e2e_api_contract.py), for each of two colleagues, administrators and analysts in the e2e
project with a password of their own: the colleague queues the import of a new one-sample
family with an SNV callset. The e2e administrator then deactivates the first colleague's
account, and sets the second's role to viewer (``PATCH /api/auth/users/{id}``); the first
colleague's next request is refused (401), the second's (403: no longer an administrator).
The worker claims each job and runs it (``run_family_import_job``): the job ends ``failed``,
saying why, and nothing of the family is written, neither the family nor its sample; the log
names neither colleague's account nor their address. Then the e2e administrator queues the
same package and the worker runs it: the family and its variants are imported, so the package
was one the import takes.

The values are synthetic: the family is new and uniquely named, the colleagues' passwords are
made at run time, and the job rows end failed or completed. Skipped unless
``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

_VARIANTS = 3
# What the e2e administrator changes of each colleague's account after the colleague queued
# the import, the answer to the colleague's next request, and the error the job ends with
# (family_package_import).
_CHANGES = {"deactivated": {"is_active": False}, "demoted": {"role": "viewer"}}
_NEXT_REQUEST = {"deactivated": 401, "demoted": 403}
_ERRORS = {"deactivated": "REQUESTER_INACTIVE_ERROR", "demoted": "REQUESTER_NOT_ADMIN_ERROR"}


def _cap(response) -> dict[str, Any]:
    out: dict[str, Any] = {"status": response.status_code}
    if response.content:
        out["json"] = response.json()
    return out


def _write_package(root: Path, family_id: str) -> Path:
    """A one-sample package: a PED and a family VCF of _VARIANTS SNVs."""
    sample = f"{family_id}_S1"  # sample ids are unique across families
    root.mkdir(parents=True)
    (root / "family.ped").write_text(f"{family_id}\t{sample}\t0\t0\t1\t2\n", encoding="utf-8")
    (root / "manifest.yaml").write_text(
        f"schema_version: 1\nfamily_id: {family_id}\nped: family.ped\ndatasets:\n"
        "  snv:\n    family_vcf: snv/family.vcf\n",
        encoding="utf-8",
    )
    (root / "snv").mkdir()
    (root / "snv" / "family.vcf").write_text(
        "##fileformat=VCFv4.2\n"
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}\n"
        + "".join(f"1\t{100 * (i + 1)}\t.\tA\tG\t60\tPASS\t.\tGT\t0/1\n" for i in range(_VARIANTS)),
        encoding="utf-8",
    )
    return root


class _LogLines(logging.Handler):
    """Each record logged while it is attached, formatted with its traceback."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


async def _exercise(base: Path, run: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.dependencies import get_password_hash
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_variant_storage import (
        count_family_small_variants,
        ensure_clickhouse_variant_tables,
    )
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    family_id = f"E2E_INACTIVE_{run}"
    sample_id = f"{family_id}_S1"
    package = _write_package(base / family_id, family_id)
    # Each colleague is an administrator, and an analyst of the e2e project (the membership's
    # default level): demoted to viewer, they keep their access to the project.
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
            await session.execute(
                text(
                    "INSERT INTO project_users (project_id, user_id) "
                    "VALUES (CAST(:p AS uuid), CAST(:u AS uuid))"
                ),
                {"p": project_id, "u": colleague_id},
            )
            await session.commit()
        colleagues[change] = {"email": email, "password": password, "id": colleague_id}

    async def job_row(job_id: str) -> dict[str, Any]:
        async with sessionmaker() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT status, family_id, worker_id, error, completed_at IS NOT NULL AS ended, "
                        "requested_by, requested_by_id::text AS requested_by_id "
                        "FROM family_import_jobs WHERE id = CAST(:j AS uuid)"
                    ),
                    {"j": job_id},
                )
            ).mappings().one()
        return dict(row)

    async def written() -> dict[str, Any]:
        """What an import wrote of the family: its row, the accounts its pedigree versions
        name as their maker, its sample and its variants."""
        async with sessionmaker() as session:
            family_uuid = (
                await session.execute(
                    text("SELECT id::text FROM families WHERE family_id = :f"), {"f": family_id}
                )
            ).scalar_one_or_none()
            made_by = (
                await session.execute(
                    text(
                        "SELECT DISTINCT created_by::text FROM family_structure_versions "
                        "WHERE family_identifier = :f"
                    ),
                    {"f": family_id},
                )
            ).scalars().all()
            samples = (
                await session.execute(
                    text("SELECT count(*) FROM samples WHERE sample_id = :s"), {"s": sample_id}
                )
            ).scalar_one()
        variants = (
            await count_family_small_variants(_harness.ASSEMBLY, family_uuid, project_ids=[project_id])
            if family_uuid is not None
            else 0
        )
        return {
            "family": family_uuid is not None,
            "made_by": sorted(made_by),
            "samples": samples,
            "variants": variants,
        }

    async def claim_and_run(job_id: str) -> str | None:
        """Claim the queued job as the worker does, by its id, and run it; the error the run
        raises to the worker, if any."""
        worker = f"e2e-inactive-{uuid4().hex[:8]}"
        async with sessionmaker() as session:
            await session.execute(
                text(
                    "UPDATE family_import_jobs SET status = 'validating', worker_id = :w, "
                    "started_at = now(), heartbeat_at = now() "
                    "WHERE id = CAST(:j AS uuid) AND status = 'queued'"
                ),
                {"w": worker, "j": job_id},
            )
            await session.commit()
        try:
            await package_import.run_family_import_job(job_id=job_id, worker_id=worker)
        except RuntimeError as exc:
            return str(exc)
        return None

    out: dict[str, Any] = {}
    jobs: list[str] = []
    body = {
        "folder_path": str(package),
        "project_id": project_id,
        "dry_run": False,
        "conflict_mode": "cancel",
    }
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://e2e") as client:
            administrator = {"Authorization": f"Bearer {await _harness.login_admin_token(client)}"}
            for change, update in _CHANGES.items():
                account = colleagues[change]
                seen: dict[str, Any] = {"email": account["email"], "colleague_id": account["id"]}
                signed_in = await client.post(
                    "/api/auth/login", json={"email": account["email"], "password": account["password"]}
                )
                seen["colleague_sign_in"] = signed_in.status_code
                colleague = {"Authorization": f"Bearer {signed_in.json()['access_token']}"}

                queued = await client.post("/api/family-imports", json=body, headers=colleague)
                seen["queued"] = _cap(queued)
                job_id = str(queued.json()["_id"])
                jobs.append(job_id)

                # The e2e administrator deactivates the colleague, or sets their role to viewer,
                # before the worker runs the job.
                seen["changed"] = _cap(
                    await client.patch(f"/api/auth/users/{account['id']}", json=update, headers=administrator)
                )
                seen["colleague_next_request"] = _cap(
                    await client.get(f"/api/family-imports/{job_id}", headers=colleague)
                )

                lines = _LogLines()
                root = logging.getLogger()
                root.addHandler(lines)
                try:
                    seen["raised"] = await claim_and_run(job_id)
                finally:
                    root.removeHandler(lines)
                seen["log"] = lines.lines
                seen["job"] = await job_row(job_id)
                seen["written"] = await written()
                out[change] = seen

            # The same package, queued by the e2e administrator, active.
            control = await client.post("/api/family-imports", json=body, headers=administrator)
            out["control_queued"] = _cap(control)
            control_id = str(control.json()["_id"])
            jobs.append(control_id)
            out["control_raised"] = await claim_and_run(control_id)
            out["control_job"] = await job_row(control_id)
            out["control_written"] = await written()
            out["administrator_id"] = (await client.get("/api/auth/me", headers=administrator)).json()["id"]
    finally:
        async with sessionmaker() as session:
            await session.execute(
                text(
                    "UPDATE family_import_jobs SET status = 'failed', worker_id = NULL, "
                    "completed_at = now(), error = 'e2e: left unfinished' "
                    "WHERE id::text = ANY(:jobs) AND status IN ('queued', 'validating', 'running')"
                ),
                {"jobs": jobs},
            )
            await session.commit()
    return out


@pytest.fixture(scope="module")
def ran(tmp_path_factory, request) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("import_inactive_requester")
    patch = pytest.MonkeyPatch()
    patch.setattr(settings, "family_import_roots", [str(base)])
    request.addfinalizer(patch.undo)
    run = uuid4().hex[:12]
    return _harness.run_async(lambda: _exercise(base, run))


@pytest.mark.parametrize("change", list(_CHANGES))
def test_the_colleague_queued_the_import_and_was_then_deactivated_or_demoted(ran, change: str) -> None:
    seen = ran[change]
    assert seen["colleague_sign_in"] == 200
    assert seen["queued"]["status"] == 200, seen["queued"]
    assert seen["queued"]["json"]["status"] == "queued"
    assert seen["queued"]["json"]["requested_by"] == seen["email"]
    assert seen["changed"]["status"] == 200, seen["changed"]
    if change == "deactivated":
        assert seen["changed"]["json"]["is_active"] is False
    else:
        assert (seen["changed"]["json"]["role"], seen["changed"]["json"]["is_active"]) == ("viewer", True)
    # Deactivation ends the colleague's own requests at once (401); the demotion their
    # administration (403: the import routes are an administrator's).
    assert seen["colleague_next_request"]["status"] == _NEXT_REQUEST[change]


@pytest.mark.parametrize("change", list(_CHANGES))
def test_the_job_of_the_changed_account_ends_failed_saying_why(ran, change: str) -> None:
    from backend.app.services import family_package_import as package_import

    seen = ran[change]
    # Before, the worker ran the import as the deactivated or demoted account, and the job
    # completed: a demoted colleague is still an analyst of the job's project.
    job = seen["job"]
    expected = getattr(package_import, _ERRORS[change])
    assert job["status"] == "failed", job
    assert job["error"] == expected
    assert job["ended"] is True and job["worker_id"] is None
    assert seen["raised"] == expected
    # The job keeps its requester, the account and the address it was requested under.
    assert (job["requested_by_id"], job["requested_by"]) == (seen["colleague_id"], seen["email"])


@pytest.mark.parametrize("change", list(_CHANGES))
def test_nothing_of_the_family_is_written(ran, change: str) -> None:
    # Before, the family, its sample and its variants were written, made by the deactivated
    # or demoted account.
    seen = ran[change]
    assert seen["job"]["family_id"] is None
    assert seen["written"] == {"family": False, "made_by": [], "samples": 0, "variants": 0}


@pytest.mark.parametrize("change", list(_CHANGES))
def test_the_log_names_neither_the_account_nor_its_address(ran, change: str) -> None:
    seen = ran[change]
    assert seen["log"], "the refusal was logged"
    for line in seen["log"]:
        assert seen["email"] not in line
        assert seen["colleague_id"] not in line


def test_the_same_package_queued_by_an_active_administrator_is_imported(ran) -> None:
    assert ran["control_queued"]["status"] == 200, ran["control_queued"]
    assert ran["control_raised"] is None
    assert ran["control_job"]["status"] == "completed", ran["control_job"]
    assert ran["control_written"] == {
        "family": True,
        "made_by": [ran["administrator_id"]],
        "samples": 1,
        "variants": _VARIANTS,
    }
