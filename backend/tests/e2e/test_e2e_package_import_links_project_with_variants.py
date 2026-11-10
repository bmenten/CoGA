"""A package import that links an existing family to another project takes its variants along (E2E).

ClickHouse stores a family's small and structural variant rows once per project it is linked
to, and a reader of the family reads the rows of the projects it may see. A package import into
an existing family links the family to the import's project, and writes only the datasets it
imports, under each of the family's projects (an update leaves out those stored). It copies the
family's other callsets to the new project too: left under its first project alone, they would
be missing for a reader whose only project is the new one.

Over the golden trio (clair3 SNVs and needlr SVs, in the e2e project A), with a project B on the
same assembly and a project C on another one, through the real import, Postgres and ClickHouse:

* an update import into B of the needlr SVs alone (the family's SVs removed first, so it writes
  that one dataset) leaves A and B each holding every row, column for column as imported; a
  reader of B alone finds both small and structural variants;
* a family linked to B that holds none of its rows there (what an import into B leaves when
  it stops before its copy) gets them under B at its next import, one that writes nothing;
* an overwrite into B that fails after its dataset is put back from its snapshot, which holds
  B's copy: the family stays linked to B with every row under A and B;
* an import into C, on another assembly, is refused before the family is marked or linked.

Everything runs in ONE event loop. The family ends in project A with its rows as imported; the
projects made here are removed. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI
``e2e`` job sets it. The values are synthetic.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from backend.tests._kept_manifest import kept_manifest
from backend.tests.e2e._harness import capture
from backend.tests.e2e.test_e2e_family_project_move_keeps_variants import (
    _family_projects,
    _link_in_postgres_only,
    _only,
    _rows,
    _sql,
)

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).parent / "fixtures" / "golden_trio"
FAMILY = "FAM_TRIO"
_PROJECT_B = "e2e-import-link-b"
_PROJECT_C = "e2e-import-link-c"
_OTHER_ASSEMBLY = "E2E-import-link-assembly"
# The package an import into B reads: the golden trio's family, its needlr SVs alone.
_SV_ONLY_MANIFEST = (
    "schema_version: 1\n"
    f"family_id: {FAMILY}\n"
    "ped: family.ped\n"
    "datasets:\n"
    "  sv_needlr:\n"
    "    family_vcf: sv_needlr/family.needlr.vcf\n"
)
_COPIED = "Copied the family's stored variants to the projects it is linked to that held none of them."


async def _make_projects(project_a: str) -> tuple[str, str, str]:
    """Project B on A's assembly, and C on an assembly of its own: (B, C, C's assembly)."""
    species, assembly = (
        await _sql("SELECT species_id::text, assembly_id::text FROM projects WHERE id = CAST(:p AS uuid)", {"p": project_a})
    )[0]
    other_assembly = (
        await _sql(
            "INSERT INTO assemblies (species_id, assembly_name, version, release_date) "
            "VALUES (CAST(:s AS uuid), :name, 'e2e', DATE '2009-02-27') RETURNING id::text",
            {"s": species, "name": _OTHER_ASSEMBLY},
        )
    )[0][0]
    made = []
    for name, assembly_id in ((_PROJECT_B, assembly), (_PROJECT_C, other_assembly)):
        made.append(
            (
                await _sql(
                    "INSERT INTO projects (name, description, species_id, assembly_id, metadata) "
                    "VALUES (:name, 'E2E import link', CAST(:s AS uuid), CAST(:a AS uuid), '{}'::jsonb) "
                    "RETURNING id::text",
                    {"name": name, "s": species, "a": assembly_id},
                )
            )[0][0]
        )
    return made[0], made[1], other_assembly


async def _reader_of(project_id: str, run: str) -> tuple[str, str]:
    """A viewer whose only project is ``project_id``: (email, password)."""
    from backend.app.dependencies import get_password_hash

    email = f"import-link-{run}@example.org"
    # Made at run time: the repository holds no password-shaped literal.
    password = uuid4().hex
    user_id = (
        await _sql(
            "INSERT INTO users (username, hashed_password, role, email, metadata, created_at) "
            "VALUES (:username, :hashed, 'viewer', :email, '{}'::jsonb, now()) RETURNING id::text",
            {"username": f"import-link-{run}", "hashed": get_password_hash(password), "email": email},
        )
    )[0][0]
    await _sql(
        "INSERT INTO project_users (project_id, user_id) VALUES (CAST(:p AS uuid), CAST(:u AS uuid))",
        {"p": project_id, "u": user_id},
    )
    return email, password


async def _in_project_a_alone(family_uuid: str, project_a: str) -> None:
    """The family linked to A alone, its rows under A alone."""
    from backend.app.services.clickhouse_variant_storage import prune_family_variant_projects
    from backend.tests.e2e import _harness

    await _link_in_postgres_only(family_uuid, project_a)
    await prune_family_variant_projects(_harness.ASSEMBLY, family_uuid, project_ids=[project_a])


async def _import_state(family_uuid: str) -> dict[str, Any]:
    rows = await _sql(
        "SELECT metadata -> 'import_incomplete', metadata -> 'import_unfinished' FROM families WHERE id = CAST(:f AS uuid)",
        {"f": family_uuid},
    )
    incomplete, unfinished = rows[0]
    return {"incomplete": incomplete, "unfinished": unfinished}


async def _exercise(root: Path, sv_only: Path) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_variant_storage import (
        copy_family_variants_to_projects,
        delete_family_structural_variants,
        prune_family_variant_projects,
    )
    from backend.tests.e2e import _harness

    facts = await _harness.import_golden_trio(root)
    family_uuid, project_a = facts["family_uuid"], facts["project_id"]
    out: dict[str, Any] = {"facts": facts, "before": await _rows(family_uuid)}
    project_b, project_c, other_assembly = await _make_projects(project_a)
    out.update(project_a=project_a, project_b=project_b, project_c=project_c)
    email, password = await _reader_of(project_b, uuid4().hex[:8])

    async def run_import(project_id: str, conflict_mode: str) -> dict[str, Any]:
        async with get_postgres_sessionmaker()() as session:
            admin, _project, _assembly = await _harness.ensure_e2e_project(session)
            try:
                result = await package_import.execute_family_package_import(
                    session,
                    folder_path=str(sv_only),
                    manifest_text=kept_manifest(sv_only),
                    project_id=project_id,
                    dry_run=False,
                    user=admin,
                    conflict_mode=conflict_mode,
                )
            except Exception as exc:  # noqa: BLE001 - the refusal is what is asserted
                return {"refused": type(exc).__name__, "message": str(exc)}
        return {
            "completed": result.completed,
            "error": result.error,
            "status": {d.dataset_type: d.status for d in result.datasets},
            "logs": result.logs,
        }

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://e2e") as reader:
            login = await reader.post("/api/auth/login", json={"email": email, "password": password})
            assert login.status_code == 200, login.text
            reader.headers["Authorization"] = f"Bearer {login.json()['access_token']}"

            async def presence() -> dict[str, Any]:
                return {
                    kind: capture(await reader.get(f"/api/families/{FAMILY}/{kind}", params={"count_only": "true"}))
                    for kind in ("small-variants", "structural-variants")
                }

            # A family into which the SV dataset was never imported: its clair3 SNVs alone.
            await delete_family_structural_variants(_harness.ASSEMBLY, family_uuid)
            out["without_svs"] = await _rows(family_uuid)

            out["into_b"] = await run_import(project_b, "update")
            out["after_into_b"] = await _rows(family_uuid)
            out["projects_after_into_b"] = await _family_projects(family_uuid)
            out["presence_b"] = await presence()

            # A family linked to B that holds none of its rows there, as an import into B
            # leaves it when it stops after the link, before its copy: B linked in Postgres,
            # its rows under A alone.
            await _in_project_a_alone(family_uuid, project_a)
            await _sql(
                "INSERT INTO family_projects (family_id, project_id) VALUES (CAST(:f AS uuid), CAST(:p AS uuid))",
                {"f": family_uuid, "p": project_b},
            )
            out["linked_without_copy"] = await _rows(family_uuid)
            out["presence_linked_without_copy"] = await presence()
            out["next_import"] = await run_import(project_a, "update")
            out["after_next_import"] = await _rows(family_uuid)
            out["presence_after_next_import"] = await presence()

            # An overwrite into B that fails after its dataset: put back from its snapshot.
            await _in_project_a_alone(family_uuid, project_a)
            real_import_dataset = package_import._import_dataset

            async def import_then_fail(*args: Any, **kwargs: Any) -> Any:
                await real_import_dataset(*args, **kwargs)
                raise RuntimeError("injected failure after the dataset")

            package_import._import_dataset = import_then_fail
            try:
                out["failed_into_b"] = await run_import(project_b, "overwrite")
            finally:
                package_import._import_dataset = real_import_dataset
            out["after_failed_into_b"] = await _rows(family_uuid)
            out["projects_after_failed_into_b"] = await _family_projects(family_uuid)
            out["state_after_failed_into_b"] = await _import_state(family_uuid)
            out["presence_after_failed_into_b"] = await presence()

            # A project on another assembly.
            out["into_c"] = await run_import(project_c, "update")
            out["after_into_c"] = await _rows(family_uuid)
            out["projects_after_into_c"] = await _family_projects(family_uuid)
            out["state_after_into_c"] = await _import_state(family_uuid)
    finally:
        # The family back in A alone with its rows, whatever happened above.
        await copy_family_variants_to_projects(
            _harness.ASSEMBLY, family_uuid, project_ids=[project_a], linked_project_ids=[project_a]
        )
        await _link_in_postgres_only(family_uuid, project_a)
        await prune_family_variant_projects(_harness.ASSEMBLY, family_uuid, project_ids=[project_a])
        await _sql("DELETE FROM projects WHERE id IN (CAST(:b AS uuid), CAST(:c AS uuid))", {"b": project_b, "c": project_c})
        await _sql("DELETE FROM assemblies WHERE id = CAST(:a AS uuid)", {"a": other_assembly})
        out["final"] = await _rows(family_uuid)
        out["final_projects"] = await _family_projects(family_uuid)
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory, request) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    if not (_FIXTURE / "manifest.yaml").exists():
        pytest.fail("golden_trio fixture missing: it is committed, so restore it (scripts/generate_golden_trio.py rebuilds it)")

    base = tmp_path_factory.mktemp("golden_import_link")
    root = base / "trio" / FAMILY
    shutil.copytree(_FIXTURE, root)
    sv_only = base / "sv_only" / FAMILY
    shutil.copytree(_FIXTURE, sv_only)
    (sv_only / "manifest.yaml").write_text(_SV_ONLY_MANIFEST, encoding="utf-8")

    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(root.parent), str(sv_only.parent)])
    request.addfinalizer(mp.undo)

    snapshot = _harness.run_async(lambda: _exercise(root, sv_only))
    assert snapshot["facts"]["completed"] is True, snapshot["facts"]
    return snapshot


def test_the_golden_trio_has_small_and_structural_variants_in_project_a(run) -> None:
    before, project_a = run["before"], run["project_a"]
    assert set(before["small"]) == {project_a} and before["small"][project_a]
    assert set(before["structural"]) == {project_a} and before["structural"][project_a]
    assert run["without_svs"] == {"small": before["small"], "structural": {}}


def test_an_update_into_b_of_one_dataset_leaves_every_callset_under_a_and_b(run) -> None:
    into_b = run["into_b"]
    assert into_b["completed"] is True and into_b["error"] is None, into_b
    # The one dataset it writes; every other one is not in its package.
    assert {name: status for name, status in into_b["status"].items() if status != "skipped"} == {
        "sv_needlr": "imported"
    }, into_b["status"]
    assert _COPIED in into_b["logs"]
    assert run["projects_after_into_b"] == sorted([run["project_a"], run["project_b"]])
    assert _only(run["after_into_b"], run["project_a"], run["project_b"], like=run["before"], under=run["project_a"])


def test_a_reader_of_b_alone_finds_the_familys_small_and_structural_variants(run) -> None:
    # The small variants too, not only the SVs the import wrote.
    for kind in ("small-variants", "structural-variants"):
        assert run["presence_b"][kind]["status"] == 200, run["presence_b"][kind]["text"]
        assert run["presence_b"][kind]["json"]["total"] == 1, kind


def test_a_family_linked_to_b_without_a_copy_gets_its_variants_at_its_next_import(run) -> None:
    for kind in ("small-variants", "structural-variants"):
        assert run["presence_linked_without_copy"][kind]["json"]["total"] == 0, kind
    linked = run["linked_without_copy"]
    assert set(linked["small"]) == {run["project_a"]} and set(linked["structural"]) == {run["project_a"]}
    next_import = run["next_import"]
    # It writes nothing: the SVs are stored, so the update leaves them.
    assert next_import["completed"] is True, next_import
    assert set(next_import["status"].values()) == {"skipped"}, next_import["status"]
    assert _COPIED in next_import["logs"]
    assert _only(run["after_next_import"], run["project_a"], run["project_b"], like=run["before"], under=run["project_a"])
    for kind in ("small-variants", "structural-variants"):
        assert run["presence_after_next_import"][kind]["json"]["total"] == 1, kind


def test_a_failed_import_into_b_is_put_back_with_bs_copy(run) -> None:
    failed = run["failed_into_b"]
    assert failed["completed"] is False and failed["error"], failed
    assert "Import failed; atomically restored the family's data to its pre-import state." in failed["logs"]
    # The link stays (the restore puts back the rows, not the links), and so does the copy
    # made before the snapshot: B holds every row.
    assert run["projects_after_failed_into_b"] == sorted([run["project_a"], run["project_b"]])
    assert _only(run["after_failed_into_b"], run["project_a"], run["project_b"], like=run["before"], under=run["project_a"])
    assert run["state_after_failed_into_b"] == {"incomplete": None, "unfinished": None}
    for kind in ("small-variants", "structural-variants"):
        assert run["presence_after_failed_into_b"][kind]["json"]["total"] == 1, kind


def test_an_import_into_a_project_on_another_assembly_is_refused_before_anything_is_written(run) -> None:
    refused = run["into_c"]
    assert refused.get("refused") == "ImportRefused", refused
    assert "stored on GRCh38" in refused["message"]
    assert run["after_into_c"] == run["after_failed_into_b"]
    assert run["projects_after_into_c"] == run["projects_after_failed_into_b"]
    assert run["state_after_into_c"] == {"incomplete": None, "unfinished": None}


def test_the_family_ends_in_project_a_with_its_rows_as_imported(run) -> None:
    assert run["final_projects"] == [run["project_a"]]
    assert run["final"] == run["before"]
