"""Package paths over the API: a manifest through a symlink, ``~``, a symlink loop (E2E).

Over the real API (an in-process ``httpx.ASGITransport`` client that answers an app failure
with 500, see test_e2e_api_contract.py) and Postgres, with synthetic packages:

* a package whose ``manifest.yaml`` is a symlink to a manifest outside the import root:
  Discover and Validate refuse it (``manifest_outside_package``), Write manifest answers 400
  and leaves the outside file as it was, and an import job of it ends ``failed`` with that
  error, having written no family. Before, the outside manifest was read as the package's:
  Validate passed, the job imported the family by it, and Write manifest replaced it;
* a package whose manifest names a file under ``~coga-no-such-user``: Validate answers 400,
  and the job ends failed with that refusal; before, a 500, and a job failed on "Could not
  determine home directory.". Discover drafts, the file absent (it was a 500);
* a package whose manifest names a file through a symlink loop: Validate answers 400, and the
  job ends failed with that refusal; before, on Python 3.12, which CI runs, a 500 and a job
  failed on "Symlink loop from ...", and on 3.13 a missing file;
* a package whose PED names a father it lacks, with an escape in his ID: Discover drafts
  nothing (``sample_id_invalid``); before, it drafted and looked his NIPT VCF up;
* the package list, with a package folder that is a symlink to a package outside the import
  root, one that loops and one that leads to a folder in the root: it leaves out the first
  two and lists the third by the folder it leads to, and Validate refuses the first typed
  (``package_folder_not_allowed``). Before, the list showed the first by its path outside the
  root, under the family and analysis type of the manifest it read there.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
import yaml

pytestmark = pytest.mark.integration

ESC = "\x1b"
UNKNOWN_HOME = "~coga-no-such-user"
LOOP = "runs through a symlink loop"
_WORKER = "e2e-package-paths-worker"


def _trio_ped(family_id: str, n: str) -> str:
    """A trio whose sample IDs no other test uses (FATHERP1, MOTHERP1, CHILDP1 in FAMPATH1)."""
    return (
        f"{family_id} FATHERP{n} 0 0 1 1\n{family_id} MOTHERP{n} 0 0 2 1\n"
        f"{family_id} CHILDP{n} FATHERP{n} MOTHERP{n} 1 2\n"
    )


def _manifest(family_id: str, **extra: Any) -> str:
    return yaml.safe_dump({"schema_version": 1, "family_id": family_id, "ped": "family.ped", "datasets": {}, **extra})


def _package(imports: Path, family_id: str, n: str, *, manifest: str | None) -> Path:
    package = imports / family_id
    package.mkdir(parents=True)
    (package / "family.ped").write_text(_trio_ped(family_id, n), encoding="utf-8")
    if manifest is not None:
        (package / "manifest.yaml").write_text(manifest, encoding="utf-8")
    return package


def _loop(at: Path) -> None:
    other = at.with_name(f"{at.name}.loop")
    at.symlink_to(other.name)
    other.symlink_to(at.name)


def _cap(resp: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"status": resp.status_code, "text": resp.text}
    try:
        out["json"] = resp.json()
    except ValueError:  # a 500 answers in text
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

    imports = base / "imports"
    outside = base / "outside"
    outside.mkdir()
    # A manifest for the package, kept outside the import root.
    decoy = outside / "manifest.yaml"
    decoy.write_text(_manifest("FAMPATH1"), encoding="utf-8")
    linked = _package(imports, "FAMPATH1", "1", manifest=None)
    (linked / "manifest.yaml").symlink_to(decoy)
    tilde = _package(
        imports, "FAMPATH2", "2", manifest=_manifest("FAMPATH2", datasets={"snv": {"family_vcf": f"{UNKNOWN_HOME}/FAMPATH2.vcf.gz"}})
    )
    looping = _package(
        imports, "FAMPATH3", "3", manifest=_manifest("FAMPATH3", datasets={"snv": {"family_vcf": "snv/FAMPATH3.vcf.gz"}})
    )
    _loop(looping / "snv")
    parent = imports / "FAMPATH4"
    parent.mkdir(parents=True)
    (parent / "family.ped").write_text(f"FAMPATH4 MOTHERP4 0 0 2 1\nFAMPATH4 CHILDP4 FATHER{ESC}P4 MOTHERP4 1 2\n")
    # Package folders for the package list: a symlink to a package outside the root, for
    # another family; one that loops; one that leads to a package in the root.
    foreign = _package(outside, "FAMPATHOUT", "5", manifest=_manifest("FAMPATHOUT", analysis_type="decoy_analysis"))
    (imports / "FAMPATH5").symlink_to(foreign)
    _loop(imports / "FAMPATH6")
    archived = _package(imports / "archive", "FAMPATH7", "7", manifest=_manifest("FAMPATH7", analysis_type="trio_check"))
    (imports / "FAMPATH7").symlink_to(archived)

    out: dict[str, Any] = {
        "before": await _counts(),
        "decoy_before": decoy.read_text(),
        "foreign": str(foreign.resolve()),
        "archived": str(archived.resolve()),
    }
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        token = await _harness.login_admin_token(ac)
        ac.headers["Authorization"] = f"Bearer {token}"

        for name, folder in (("linked", linked), ("tilde", tilde), ("parent", parent)):
            out[f"discover_{name}"] = _cap(
                await ac.post("/api/family-imports/manifest/discover", json={"folder_path": str(folder)})
            )
        for name, folder in (("linked", linked), ("tilde", tilde), ("looping", looping)):
            out[f"validate_{name}"] = _cap(await ac.post("/api/family-imports/validate", json={"folder_path": str(folder)}))
        out["write_linked"] = _cap(
            await ac.post(
                "/api/family-imports/manifest/write",
                json={"folder_path": str(linked), "manifest_yaml": _manifest("FAMPATH1"), "overwrite": True},
            )
        )
        for name, folder in (("linked", linked), ("tilde", tilde), ("looping", looping)):
            out[f"job_{name}"] = await _run_job(ac, folder, project_id)
        out["packages"] = _cap(await ac.get("/api/family-imports/packages"))
        out["validate_linked_folder"] = _cap(
            await ac.post("/api/family-imports/validate", json={"folder_path": str(imports / "FAMPATH5")})
        )
    out["decoy_after"] = decoy.read_text()
    out["after"] = await _counts()
    return out


@pytest.fixture(scope="module")
def snap(tmp_path_factory: pytest.TempPathFactory, request: pytest.FixtureRequest) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("package_paths")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base / "imports")])
    request.addfinalizer(mp.undo)
    return _harness.run_async(lambda: _collect(base))


def _codes(response: dict[str, Any]) -> list[str]:
    return [issue["code"] for issue in response["json"]["errors"]]


def test_a_manifest_through_a_symlink_out_of_the_package_is_refused(snap: dict[str, Any]) -> None:
    for name in ("discover_linked", "validate_linked"):
        response = snap[name]
        assert response["status"] == 200, (name, response["text"])
        assert _codes(response) == ["manifest_outside_package"], (name, response["json"])
    assert snap["discover_linked"]["json"]["manifest_yaml"] == ""
    write = snap["write_linked"]
    assert write["status"] == 400, write["text"]
    assert write["json"]["detail"].startswith("manifest.yaml is a symlink that leads out of the package folder")
    assert snap["decoy_after"] == snap["decoy_before"]
    job = snap["job_linked"]
    assert "raised" not in job, job
    assert (job["status"], job["ended"], job["error"], job["family_id"]) == ("failed", True, "Package validation failed", None)
    assert [issue["code"] for issue in job["validation_errors"]] == ["manifest_outside_package"]


def test_a_manifest_path_starting_with_a_tilde_is_refused(snap: dict[str, Any]) -> None:
    detail = (
        f"Package manifest path '{UNKNOWN_HOME}/FAMPATH2.vcf.gz' starts with '~': a manifest path is "
        "relative to the package directory, and CoGA does not read a home folder"
    )
    assert (snap["validate_tilde"]["status"], snap["validate_tilde"]["json"]) == (400, {"detail": detail})
    discover = snap["discover_tilde"]
    assert discover["status"] == 200, discover["text"]
    snv = next(item for item in discover["json"]["datasets"] if item["dataset_type"] == "snv")
    assert [(item["path"], item["exists"]) for item in snv["files"]] == [(f"{UNKNOWN_HOME}/FAMPATH2.vcf.gz", False)]
    job = snap["job_tilde"]
    assert (job["status"], job["ended"], job["error"], job["family_id"]) == ("failed", True, f"400: {detail}", None)


def test_a_manifest_path_through_a_symlink_loop_is_refused(snap: dict[str, Any]) -> None:
    detail = f"Package manifest path 'snv/FAMPATH3.vcf.gz' {LOOP}"
    assert (snap["validate_looping"]["status"], snap["validate_looping"]["json"]) == (400, {"detail": detail})
    job = snap["job_looping"]
    assert (job["status"], job["ended"], job["error"], job["family_id"]) == ("failed", True, f"400: {detail}", None)


def test_discover_refuses_a_parent_id_the_ped_lacks_that_cannot_be_stored(snap: dict[str, Any]) -> None:
    response = snap["discover_parent"]
    assert response["status"] == 200, response["text"]
    assert response["json"]["manifest_yaml"] == ""
    messages = [issue["message"] for issue in response["json"]["errors"] if issue["code"] == "sample_id_invalid"]
    assert messages == [
        "Sample ID 'FATHER\\x1bP4' (a parent of CHILDP4 in the PED) contains a control character (\\x1b). "
        "Family and sample IDs are printable text without spaces."
    ]


def test_the_package_list_leaves_out_a_package_folder_linked_out_of_the_import_root(snap: dict[str, Any]) -> None:
    listed = snap["packages"]
    assert listed["status"] == 200, listed["text"]
    by_name = {item["name"]: item for item in listed["json"]}
    # FAMPATH5 was listed by its path outside the root, under the family and analysis type of
    # the manifest found there. FAMPATH6 loops.
    assert sorted(by_name) == ["FAMPATH1", "FAMPATH2", "FAMPATH3", "FAMPATH4", "FAMPATH7"]
    assert [by_name["FAMPATH7"][key] for key in ("folder_path", "family_id", "analysis_type")] == [
        snap["archived"],
        "FAMPATH7",
        "trio_check",
    ]
    for value in (snap["foreign"], "FAMPATHOUT", "decoy_analysis"):
        assert value not in listed["text"]
    # Typed, the folder is refused, as it was.
    validate = snap["validate_linked_folder"]
    assert validate["status"] == 200, validate["text"]
    assert _codes(validate) == ["package_folder_not_allowed"]


def test_nothing_of_a_refused_package_was_written(snap: dict[str, Any]) -> None:
    before, after = snap["before"], snap["after"]
    assert (after["families"], after["samples"]) == (before["families"], before["samples"])
    # The three import jobs run above, and no other.
    assert after["jobs"] == before["jobs"] + 3
