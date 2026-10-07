"""An ID no family or sample can be stored under is refused, and nothing is written (E2E).

A family or sample ID is printable text without spaces (``family_identifiers.py``). One that
holds a control character could start a line in a log or report and hide in a display, and
Postgres cannot store a NUL at all: not in a text column, and not in the JSONB an import job
keeps its validation findings in. So a refusal has to keep the raw ID out of everything the job
records, or the job could not record why it failed.

Over the real API (an in-process ``httpx.ASGITransport`` client, see test_e2e_api_contract.py)
and Postgres, with synthetic packages:

* Discover and Validate answer a package whose manifest ``family_id`` holds a NUL, and a
  Discover request whose ``family_id`` holds one, with ``family_id_invalid``; before, the
  request's family was looked up in Postgres with the NUL, and the request failed (500);
* an import request naming such a family is refused (400) before its job is written;
* a real import job (``POST /api/family-imports``, then the worker's ``run_family_import_job``)
  of a package whose manifest ``family_id`` holds a NUL, and of one whose PED has a sample ID
  with an escape character, ends ``failed`` with ``family_id_invalid`` or ``sample_id_invalid``
  in its stored validation errors, names no family, and writes no family or sample;
* the Family Builder (``POST /api/ped/manual``) refuses a sample ID with a line break and a
  family ID with a NUL, and the PED upload (``POST /api/ped/upload``) a sample ID with an
  escape, each with 400 and nothing written; before, the line break was stored.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
import yaml

pytestmark = pytest.mark.integration

NUL, LF, ESC = "\x00", "\n", "\x1b"
_WORKER = "e2e-unstorable-ids-worker"


def _trio_ped(family_id: str, *, mother: str) -> str:
    """A trio whose sample IDs no other test uses (FATHER02 and CHILD02 in FAMID02)."""
    father, child = f"FATHER{family_id[-2:]}", f"CHILD{family_id[-2:]}"
    return f"{family_id} {father} 0 0 1 1\n{family_id} {mother} 0 0 2 1\n{family_id} {child} {father} {mother} 1 2\n"


def _package(root: Path, *, family_id: str, ped: str) -> Path:
    root.mkdir(parents=True)
    (root / "family.ped").write_text(ped, encoding="utf-8")
    # safe_dump writes the NUL as the YAML escape "\0".
    manifest = {"schema_version": 1, "family_id": family_id, "ped": "family.ped", "datasets": {}}
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return root


def _cap(resp: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"status": resp.status_code, "text": resp.text}
    try:
        out["json"] = resp.json()
    except ValueError:  # pragma: no cover
        out["json"] = None
    return out


async def _counts() -> dict[str, int]:
    from backend.app.core.postgres import get_postgres_sessionmaker

    async with get_postgres_sessionmaker()() as session:
        row = (
            await session.execute(
                text(
                    "SELECT (SELECT count(*) FROM families) AS families, "
                    "(SELECT count(*) FROM samples) AS samples, "
                    "(SELECT count(*) FROM family_import_jobs) AS jobs"
                )
            )
        ).mappings().one()
    return dict(row)


async def _job(job_id: str) -> dict[str, Any]:
    from backend.app.core.postgres import get_postgres_sessionmaker

    async with get_postgres_sessionmaker()() as session:
        row = (
            await session.execute(
                text(
                    "SELECT status, family_id, error, validation_errors, completed_at IS NOT NULL AS ended "
                    "FROM family_import_jobs WHERE id = CAST(:j AS uuid)"
                ),
                {"j": job_id},
            )
        ).mappings().one()
    return dict(row)


async def _run_job(ac: Any, folder: Path, project_id: str) -> dict[str, Any]:
    """Queue an import of ``folder`` as the page does, claim it as the worker does, run it."""
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services import family_package_import

    resp = await ac.post(
        "/api/family-imports",
        json={"folder_path": str(folder), "project_id": project_id, "dry_run": False, "conflict_mode": "cancel"},
    )
    assert resp.status_code == 200, resp.text
    job_id = str(resp.json()["_id"])
    async with get_postgres_sessionmaker()() as session:
        await session.execute(
            text(
                "UPDATE family_import_jobs SET status = 'validating', worker_id = :w, "
                "started_at = now(), heartbeat_at = now() WHERE id = CAST(:j AS uuid) AND status = 'queued'"
            ),
            {"j": job_id, "w": _WORKER},
        )
        await session.commit()
    try:
        await family_package_import.run_family_import_job(job_id=job_id, worker_id=_WORKER)
    except Exception as exc:  # noqa: BLE001 - the worker logs it and goes on
        return {**await _job(job_id), "raised": type(exc).__name__}
    return await _job(job_id)


async def _collect(base: Path) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    async with get_postgres_sessionmaker()() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    nul_family = _package(base / "nul-family" / "FAMID01", family_id=f"FAMID{NUL}01", ped=_trio_ped("FAMID01", mother="MOTHER01"))
    esc_sample = _package(base / "esc-sample" / "FAMID02", family_id="FAMID02", ped=_trio_ped("FAMID02", mother=f"MOTHER{ESC}02"))
    clean = _package(base / "clean" / "FAMID03", family_id="FAMID03", ped=_trio_ped("FAMID03", mother="MOTHER03"))

    out: dict[str, Any] = {"before": await _counts()}
    # A request the app fails on is answered 500, as a server answers it, so that every step
    # below is taken and asserted on its own.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        token = await _harness.login_admin_token(ac)
        ac.headers["Authorization"] = f"Bearer {token}"

        out["discover_manifest"] = _cap(
            await ac.post("/api/family-imports/manifest/discover", json={"folder_path": str(nul_family)})
        )
        out["discover_request"] = _cap(
            await ac.post(
                "/api/family-imports/manifest/discover",
                json={"folder_path": str(clean), "family_id": f"FAMID{NUL}03"},
            )
        )
        out["validate"] = _cap(
            await ac.post(
                "/api/family-imports/validate",
                json={"folder_path": str(nul_family), "family_id": f"FAMID{NUL}01"},
            )
        )
        jobs_before = (await _counts())["jobs"]
        out["import_request"] = _cap(
            await ac.post(
                "/api/family-imports",
                json={"folder_path": str(clean), "family_id": f"FAMID{NUL}03", "dry_run": True},
            )
        )
        out["jobs_written_by_refused_request"] = (await _counts())["jobs"] - jobs_before

        out["job_nul_family"] = await _run_job(ac, nul_family, project_id)
        out["job_esc_sample"] = await _run_job(ac, esc_sample, project_id)

        out["manual_sample"] = _cap(
            await ac.post(
                "/api/ped/manual",
                json={"family_id": "FAMID04", "project_id": project_id, "members": [{"sample_id": f"MOTHER{LF}04"}]},
            )
        )
        out["manual_family"] = _cap(
            await ac.post(
                "/api/ped/manual",
                json={"family_id": f"FAMID{NUL}05", "project_id": project_id, "members": [{"sample_id": "MOTHER05"}]},
            )
        )
        out["ped_upload"] = _cap(
            await ac.post(
                "/api/ped/upload",
                params={"project_id": project_id},
                files={"file": ("family.ped", _trio_ped("FAMID06", mother=f"MOTHER{ESC}06").encode(), "text/plain")},
            )
        )
    out["after"] = await _counts()
    return out


@pytest.fixture(scope="module")
def snap(tmp_path_factory: pytest.TempPathFactory, request: pytest.FixtureRequest) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("unstorable_ids")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    request.addfinalizer(mp.undo)
    return _harness.run_async(lambda: _collect(base))


def _codes(response: dict[str, Any]) -> list[str]:
    return [issue["code"] for issue in response["json"]["errors"]]


def test_discover_and_validate_report_a_family_id_holding_a_nul(snap: dict[str, Any]) -> None:
    for name in ("discover_manifest", "discover_request", "validate"):
        response = snap[name]
        assert response["status"] == 200, (name, response["text"])
        assert _codes(response) == ["family_id_invalid"], (name, response["json"])
        assert response["json"]["family_id"] is None
    messages = {name: snap[name]["json"]["errors"][0]["message"] for name in ("discover_manifest", "discover_request")}
    assert "'FAMID\\x0001' (the manifest's family_id) contains a control character (\\x00)" in messages["discover_manifest"]
    assert "'FAMID\\x0003' (the family_id of the request) contains a control character (\\x00)" in messages["discover_request"]


def test_an_import_request_naming_such_a_family_is_refused_before_its_job_is_written(snap: dict[str, Any]) -> None:
    response = snap["import_request"]
    assert response["status"] == 400, response["text"]
    assert "contains a control character (\\x00)" in response["json"]["detail"]
    assert snap["jobs_written_by_refused_request"] == 0


@pytest.mark.parametrize(
    ("run", "code", "shown", "family_id"),
    [
        ("job_nul_family", "family_id_invalid", "FAMID\\x0001", None),
        ("job_esc_sample", "sample_id_invalid", "MOTHER\\x1b02", "FAMID02"),
    ],
)
def test_an_import_job_records_the_refusal(
    snap: dict[str, Any], run: str, code: str, shown: str, family_id: str | None
) -> None:
    job = snap[run]
    assert "raised" not in job, job
    assert (job["status"], job["ended"], job["error"]) == ("failed", True, "Package validation failed"), job
    assert [issue["code"] for issue in job["validation_errors"]] == [code], job["validation_errors"]
    assert shown in job["validation_errors"][0]["message"]
    # A family ID that cannot be stored is not recorded on the job either; a valid one is,
    # as for any package that fails validation.
    assert job["family_id"] == family_id


def test_the_family_builder_and_the_ped_upload_refuse_such_ids(snap: dict[str, Any]) -> None:
    for name, shown in (("manual_sample", "MOTHER\\n04"), ("manual_family", "FAMID\\x0005"), ("ped_upload", "MOTHER\\x1b06")):
        response = snap[name]
        assert response["status"] == 400, (name, response["text"])
        assert shown in response["json"]["detail"], (name, response["json"])


def test_nothing_of_a_refused_family_or_sample_was_written(snap: dict[str, Any]) -> None:
    before, after = snap["before"], snap["after"]
    assert (after["families"], after["samples"]) == (before["families"], before["samples"])
    # The two import jobs run above, and no other.
    assert after["jobs"] == before["jobs"] + 2
