"""A project that families are linked to is not deleted: their variants stay found (E2E).

ClickHouse stores a family's small and structural variant rows once per project it is linked
to, and the family's pages read the rows of its projects only. Deleting a linked project would
drop its family links (``ON DELETE CASCADE``) and leave the rows under the deleted project: a
family whose only project it was would have no project left, no assembly to read, and pages
that find no small or structural variants; a family with other projects would keep a full copy
there, which no reader looks at. So ``DELETE /api/projects/{id}`` refuses the deletion while a
family is linked (409, naming how many; owner decision 2026-11-10): moving a family
(``PUT /admin/families/{family_id}/projects``) takes its rows along.

Over the golden trio (clair3 SNVs and needlr SVs, in the e2e project A), with a project D on
the same assembly, through the real API, Postgres and ClickHouse:

* the family moved to D alone: deleting D is refused (409, 1 family), D and the family's link
  stay, its rows stay under D as they were, and its pages find them;
* linked to A and D: deleting D is refused, both keep every row;
* moved back to A: D, with no family, is deleted (204); the family's rows are under A alone,
  as imported, and none is left under D.

Everything runs in ONE event loop via an in-process ``httpx.ASGITransport`` client (see
test_e2e_api_contract.py for why). The family ends in project A with its rows as imported;
the project made here is removed. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the
CI ``e2e`` job sets it. The values are synthetic.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from backend.tests.e2e._harness import capture
from backend.tests.e2e.test_e2e_family_project_move_keeps_variants import (
    _family_projects,
    _link_in_postgres_only,
    _rows,
    _sql,
)

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).parent / "fixtures" / "golden_trio"
FAMILY = "FAM_TRIO"
_PROJECT_D = "e2e-project-delete-d"


async def _make_project(project_a: str) -> str:
    """Project D, on A's organism and assembly."""
    species, assembly = (
        await _sql("SELECT species_id::text, assembly_id::text FROM projects WHERE id = CAST(:p AS uuid)", {"p": project_a})
    )[0]
    return (
        await _sql(
            "INSERT INTO projects (name, description, species_id, assembly_id, metadata) "
            "VALUES (:name, 'E2E project delete', CAST(:s AS uuid), CAST(:a AS uuid), '{}'::jsonb) RETURNING id::text",
            {"name": _PROJECT_D, "s": species, "a": assembly},
        )
    )[0][0]


async def _project_exists(project_id: str) -> bool:
    return bool(await _sql("SELECT 1 FROM projects WHERE id = CAST(:p AS uuid)", {"p": project_id}))


async def _exercise(root: Path) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient

    from backend.app.main import app
    from backend.app.services.clickhouse_variant_storage import (
        copy_family_variants_to_projects,
        prune_family_variant_projects,
    )
    from backend.tests.e2e import _harness

    facts = await _harness.import_golden_trio(root)
    family_uuid, project_a = facts["family_uuid"], facts["project_id"]
    out: dict[str, Any] = {"facts": facts, "before": await _rows(family_uuid), "project_a": project_a}
    project_d = await _make_project(project_a)
    out["project_d"] = project_d
    url = f"/api/admin/families/{FAMILY}/projects"

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://e2e") as ac:
            token = await _harness.login_admin_token(ac)
            ac.headers["Authorization"] = f"Bearer {token}"

            async def presence(project_id: str) -> dict[str, Any]:
                params = {"count_only": "true", "project_id": project_id}
                return {
                    kind: capture(await ac.get(f"/api/families/{FAMILY}/{kind}", params=params))
                    for kind in ("small-variants", "structural-variants")
                }

            # D the family's only project.
            out["move_d"] = capture(await ac.put(url, json={"project_ids": [project_d]}))
            out["after_move_d"] = await _rows(family_uuid)
            out["delete_only"] = capture(await ac.delete(f"/api/projects/{project_d}"))
            out["after_delete_only"] = await _rows(family_uuid)
            out["projects_after_delete_only"] = await _family_projects(family_uuid)
            out["d_after_delete_only"] = await _project_exists(project_d)
            out["presence_after_delete_only"] = await presence(project_d)

            # D one of the family's projects.
            out["link_ad"] = capture(await ac.put(url, json={"project_ids": [project_a, project_d]}))
            out["after_link_ad"] = await _rows(family_uuid)
            out["delete_one_of_two"] = capture(await ac.delete(f"/api/projects/{project_d}"))
            out["after_delete_one_of_two"] = await _rows(family_uuid)
            out["projects_after_delete_one_of_two"] = await _family_projects(family_uuid)

            # The family moved off D first: D goes.
            out["back_a"] = capture(await ac.put(url, json={"project_ids": [project_a]}))
            out["delete_empty"] = capture(await ac.delete(f"/api/projects/{project_d}"))
            out["d_after_delete_empty"] = await _project_exists(project_d)
            out["after_delete_empty"] = await _rows(family_uuid)
            out["projects_after_delete_empty"] = await _family_projects(family_uuid)
    finally:
        # The family back in A alone with its rows as imported, whatever happened above.
        await copy_family_variants_to_projects(
            _harness.ASSEMBLY, family_uuid, project_ids=[project_a], linked_project_ids=[project_a]
        )
        await _link_in_postgres_only(family_uuid, project_a)
        await prune_family_variant_projects(_harness.ASSEMBLY, family_uuid, project_ids=[project_a])
        await _sql("DELETE FROM projects WHERE id = CAST(:d AS uuid)", {"d": project_d})
        out["final"] = await _rows(family_uuid)
        out["final_projects"] = await _family_projects(family_uuid)
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory, request) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    if not (_FIXTURE / "manifest.yaml").exists():
        pytest.fail("golden_trio fixture missing: it is committed, so restore it (scripts/generate_golden_trio.py rebuilds it)")

    base = tmp_path_factory.mktemp("golden_project_delete")
    root = base / "FAM_TRIO"
    shutil.copytree(_FIXTURE, root)

    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(root.parent)])
    request.addfinalizer(mp.undo)

    snapshot = _harness.run_async(lambda: _exercise(root))
    assert snapshot["facts"]["completed"] is True, snapshot["facts"]
    return snapshot


def _under(rows: dict[str, dict[str, list[str]]], *projects: str, like: dict[str, dict[str, list[str]]], at: str) -> bool:
    """Whether ``rows`` holds, in each table, exactly ``projects``, each with the rows ``like``
    holds under ``at``."""
    return all(set(rows[kind]) == set(projects) and all(rows[kind][p] == like[kind][at] for p in projects) for kind in rows)


def _refused_for_one_family(response: dict[str, Any]) -> None:
    assert response["status"] == 409, response["text"]
    detail = response["json"]["detail"]
    assert detail.startswith("1 family is linked to this project. Move it to other projects, or to none, or delete it")
    assert "variants are stored per project" in detail


def test_the_golden_trio_has_small_and_structural_variants_in_project_a(run) -> None:
    before, project_a = run["before"], run["project_a"]
    assert set(before["small"]) == {project_a} and before["small"][project_a]
    assert set(before["structural"]) == {project_a} and before["structural"][project_a]


def test_deleting_a_familys_only_project_is_refused_and_its_variants_stay_found(run) -> None:
    # D is the family's only project: refused, and its pages find its rows under D.
    assert run["move_d"]["status"] == 200, run["move_d"]["text"]
    assert _under(run["after_move_d"], run["project_d"], like=run["before"], at=run["project_a"])
    _refused_for_one_family(run["delete_only"])
    assert run["d_after_delete_only"] is True
    assert run["projects_after_delete_only"] == [run["project_d"]]
    assert run["after_delete_only"] == run["after_move_d"]
    for kind in ("small-variants", "structural-variants"):
        presence = run["presence_after_delete_only"][kind]
        assert presence["status"] == 200, presence["text"]
        assert presence["json"]["total"] == 1, kind


def test_deleting_one_of_a_familys_projects_is_refused_and_both_keep_every_row(run) -> None:
    # Linked to A and D: refused, and both keep every row.
    assert run["link_ad"]["status"] == 200, run["link_ad"]["text"]
    _refused_for_one_family(run["delete_one_of_two"])
    assert sorted(run["projects_after_delete_one_of_two"]) == sorted([run["project_a"], run["project_d"]])
    assert _under(run["after_delete_one_of_two"], run["project_a"], run["project_d"], like=run["before"], at=run["project_a"])


def test_a_project_the_family_was_moved_off_is_deleted_and_leaves_no_rows(run) -> None:
    assert run["back_a"]["status"] == 200, run["back_a"]["text"]
    assert run["delete_empty"]["status"] == 204, run["delete_empty"]["text"]
    assert run["d_after_delete_empty"] is False
    assert run["projects_after_delete_empty"] == [run["project_a"]]
    assert run["after_delete_empty"] == run["before"]


def test_the_family_ends_in_project_a_with_its_rows_as_imported(run) -> None:
    assert run["final_projects"] == [run["project_a"]]
    assert run["final"] == run["before"]
