"""Package paths through the import: ``~`` and a symlink loop in the manifest, and package folders (E2E).

Over the real API (an in-process ``httpx.ASGITransport`` client that answers an app failure
with 500, see test_e2e_api_contract.py) and Postgres, with synthetic packages:

* a package whose manifest names a file under ``~coga-no-such-user``: the job ends failed with
  that refusal (400), never expanding a home folder;
* a package whose manifest names a file through a symlink loop: the job ends failed with that
  refusal (400), on every Python;
* a package whose PED names a father it lacks, with an escape in his ID, added to Deliveries
  by hand: CoGA proposes no members for it, and the delivery needs attention, naming the ID
  (``sample_id_invalid``);
* the package list, with a package folder that is a symlink to a package outside the import
  root, one that loops and one that leads to a folder in the root: it leaves out the first
  two and lists the third by the folder it leads to, and adding the first by hand, typed, is
  refused.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from backend.tests.e2e._harness import capture, ensure_postgres_schema
from backend.tests.e2e._import_jobs import row_counts, run_import_job

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


async def _collect(base: Path) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.tests.e2e import _harness

    await ensure_postgres_schema()
    async with get_postgres_sessionmaker()() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    imports = base / "imports"
    outside = base / "outside"
    outside.mkdir()
    _package(imports, "FAMPATH1", "1", manifest=_manifest("FAMPATH1"))
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
        "before": await row_counts(),
        "foreign": str(foreign.resolve()),
        "archived": str(archived.resolve()),
    }
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        token = await _harness.login_admin_token(ac)
        ac.headers["Authorization"] = f"Bearer {token}"

        async def add(folder: Path) -> dict[str, Any]:
            return capture(
                await ac.post(
                    "/api/family-imports/deliveries",
                    json={"folder_path": str(folder), "project_id": project_id, "analysis_type": "wgs_trio"},
                )
            )

        out["added_parent"] = await add(parent)
        for name, folder in (("tilde", tilde), ("looping", looping)):
            out[f"job_{name}"] = await run_import_job(folder, project_id, _WORKER)
        out["packages"] = capture(await ac.get("/api/family-imports/packages"))
        out["added_linked_folder"] = await add(imports / "FAMPATH5")
    out["after"] = await row_counts()
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


def test_a_manifest_path_starting_with_a_tilde_is_refused(snap: dict[str, Any]) -> None:
    detail = (
        f"Package manifest path '{UNKNOWN_HOME}/FAMPATH2.vcf.gz' starts with '~': a manifest path is "
        "relative to the package directory, and CoGA does not read a home folder"
    )
    job = snap["job_tilde"]
    assert (job["status"], job["ended"], job["error"], job["family_id"]) == ("failed", True, f"400: {detail}", None)


def test_a_manifest_path_through_a_symlink_loop_is_refused(snap: dict[str, Any]) -> None:
    detail = f"Package manifest path 'snv/FAMPATH3.vcf.gz' {LOOP}"
    job = snap["job_looping"]
    assert (job["status"], job["ended"], job["error"], job["family_id"]) == ("failed", True, f"400: {detail}", None)


def test_a_parent_id_the_ped_lacks_that_cannot_be_stored_gets_no_members_proposed(snap: dict[str, Any]) -> None:
    from backend.app.services.family_identifiers import IDENTIFIER_RULE

    response = snap["added_parent"]
    assert response["status"] == 200, response["text"]
    assert response["json"]["state"] == "needs_attention"
    messages = [issue["message"] for issue in response["json"]["issues"] if issue["code"] == "sample_id_invalid"]
    assert messages == [
        "Sample ID 'FATHER\\x1bP4' (a parent of CHILDP4 in the PED) contains a control character (\\x1b). "
        f"{IDENTIFIER_RULE}"
    ]


def test_the_package_list_leaves_out_a_package_folder_linked_out_of_the_import_root(snap: dict[str, Any]) -> None:
    listed = snap["packages"]
    assert listed["status"] == 200, listed["text"]
    by_name = {item["name"]: item for item in listed["json"]}
    # FAMPATH5 was listed by its path outside the root, under the family and analysis type of
    # the manifest found there. FAMPATH6 loops.
    assert sorted(by_name) == ["FAMPATH1", "FAMPATH2", "FAMPATH3", "FAMPATH4", "FAMPATH7"]
    assert [by_name["FAMPATH7"][key] for key in ("folder_path", "family_id")] == [snap["archived"], "FAMPATH7"]
    for value in (snap["foreign"], "FAMPATHOUT", "decoy_analysis"):
        assert value not in listed["text"]
    # Typed, the folder is refused.
    added = snap["added_linked_folder"]
    assert added["status"] == 403, added["text"]
    assert added["json"]["detail"].startswith("Family import path is outside configured FAMILY_IMPORT_ROOTS")


def test_nothing_of_a_refused_package_was_written(snap: dict[str, Any]) -> None:
    before, after = snap["before"], snap["after"]
    assert (after["families"], after["samples"]) == (before["families"], before["samples"])
    # The two import jobs run above, and no other.
    assert after["jobs"] == before["jobs"] + 2
