"""Moving a family to another project takes its variants along (E2E).

ClickHouse stores a family's small and structural variant rows once per project it is linked
to, and the family's pages read the rows of its projects only. ``PUT
/admin/families/{family_id}/projects`` used to change Postgres alone: a family moved to another
project kept its rows under the old one, and its pages found no small or structural variants
(the presence check said none, so the links were hidden), while its import said they were
stored.

Over the golden trio (clair3 SNVs and needlr SVs, in the e2e project A), with a second project
B on the same assembly and a project C on another one, through the real API, Postgres and
ClickHouse:

* moved to B, the family's rows are all under B, column for column as they were under A, and
  none is left under A; the summaries count them under B; the family's pages find them;
* linked to A and B, both hold every row; back to A alone, B's go;
* C, on another assembly, is refused with 409 and nothing changes;
* a family moved before the fix (its links changed in Postgres, its rows left under A) gets its
  rows under B when its projects are saved again, and A's go;
* rows left under B from earlier, with other calls, are replaced by A's when the family is
  linked to B: no write kept them current;
* every column of ``SV/entries`` is copied (the copy names them).

Everything runs in ONE event loop via an in-process ``httpx.ASGITransport`` client (see
test_e2e_api_contract.py for why). The family ends in project A with its rows as imported;
the projects made here are removed. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py);
the CI ``e2e`` job sets it. The values are synthetic.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from backend.tests.e2e._harness import capture

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).parent / "fixtures" / "golden_trio"
FAMILY = "FAM_TRIO"
_PROJECT_B = "e2e-project-move-b"
_PROJECT_C = "e2e-project-move-c"
_OTHER_ASSEMBLY = "E2E-other-assembly"


async def _rows(family_uuid: str) -> dict[str, dict[str, list[str]]]:
    """The family's live entry rows as stored, per table (``small``, ``structural``) and
    project: each row without its project, as text, sorted."""
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.services.clickhouse_variant_ids import _small_table_name, _structural_table_name
    from backend.app.services.clickhouse_variant_storage import (
        SMALL_VARIANT_ENTRY_COLUMNS,
        STRUCTURAL_VARIANT_ENTRY_COLUMNS,
    )
    from backend.tests.e2e import _harness

    out: dict[str, dict[str, list[str]]] = {}
    for kind, table, columns in (
        ("small", _small_table_name(_harness.ASSEMBLY, "entries"), SMALL_VARIANT_ENTRY_COLUMNS),
        ("structural", _structural_table_name(_harness.ASSEMBLY, "entries"), STRUCTURAL_VARIANT_ENTRY_COLUMNS),
    ):
        rows = await execute_clickhouse(
            f"SELECT {', '.join(f'`{column}`' for column in columns)} FROM {table} "
            "WHERE family_guid = %(family_guid)s AND sign = 1",
            {"family_guid": family_uuid},
        )
        project_at = columns.index("project_guid")
        by_project: dict[str, list[str]] = {}
        for row in rows:
            values = list(row)
            project = str(values.pop(project_at))
            by_project.setdefault(project, []).append(repr(values))
        out[kind] = {project: sorted(values) for project, values in by_project.items()}
    return out


async def _summary_projects(family_uuid: str) -> list[str]:
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.services.clickhouse_variant_ids import _small_table_name
    from backend.tests.e2e import _harness

    rows = await execute_clickhouse(
        f"SELECT DISTINCT project_guid FROM {_small_table_name(_harness.ASSEMBLY, 'family_variant_summary')} "
        "WHERE family_guid = %(family_guid)s ORDER BY project_guid",
        {"family_guid": family_uuid},
    )
    return [str(project) for (project,) in rows]


async def _sv_entry_columns() -> list[str]:
    from backend.app.core.clickhouse import clickhouse_dataset_key, execute_clickhouse
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    rows = await execute_clickhouse(
        "SELECT name FROM system.columns WHERE database = %(database)s AND table = %(table)s",
        {"database": settings.clickhouse_database, "table": f"{clickhouse_dataset_key(_harness.ASSEMBLY)}/SV/entries"},
    )
    return sorted(str(name) for (name,) in rows)


async def _sql(statement: str, params: dict[str, Any]) -> list[Any]:
    from backend.app.core.postgres import get_postgres_sessionmaker

    async with get_postgres_sessionmaker()() as session:
        result = await session.execute(text(statement), params)
        rows = list(result.all()) if result.returns_rows else []
        await session.commit()
    return rows


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
                    "VALUES (:name, 'E2E project move', CAST(:s AS uuid), CAST(:a AS uuid), '{}'::jsonb) "
                    "RETURNING id::text",
                    {"name": name, "s": species, "a": assembly_id},
                )
            )[0][0]
        )
    return made[0], made[1], other_assembly


async def _link_in_postgres_only(family_uuid: str, project_id: str) -> None:
    """Link the family and its samples to ``project_id`` alone, as the change did before the
    fix: Postgres only, the variants left where they are."""
    await _sql("DELETE FROM family_projects WHERE family_id = CAST(:f AS uuid)", {"f": family_uuid})
    await _sql(
        "INSERT INTO family_projects (family_id, project_id) VALUES (CAST(:f AS uuid), CAST(:p AS uuid))",
        {"f": family_uuid, "p": project_id},
    )
    await _sql(
        "DELETE FROM sample_projects WHERE sample_id IN (SELECT id FROM samples WHERE family_id = CAST(:f AS uuid))",
        {"f": family_uuid},
    )
    await _sql(
        "INSERT INTO sample_projects (sample_id, project_id) "
        "SELECT id, CAST(:p AS uuid) FROM samples WHERE family_id = CAST(:f AS uuid)",
        {"f": family_uuid, "p": project_id},
    )


async def _family_projects(family_uuid: str) -> list[str]:
    rows = await _sql(
        "SELECT project_id::text FROM family_projects WHERE family_id = CAST(:f AS uuid) ORDER BY 1",
        {"f": family_uuid},
    )
    return [str(project) for (project,) in rows]


async def _write_stale_copy(family_uuid: str, project_a: str, project_b: str) -> None:
    """One of A's SNV rows under B, with every genotype ``0/0``: what a copy left under a project
    the family then left would hold after a later rewrite of its rows. Written to the entries
    alone, so the shared variant tables stay as imported."""
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.services.clickhouse_variant_ids import _small_table_name
    from backend.app.services.clickhouse_variant_storage import SMALL_VARIANT_ENTRY_COLUMNS
    from backend.tests.e2e import _harness

    table = _small_table_name(_harness.ASSEMBLY, "entries")
    changed = {"project_guid": "%(b)s", "calls.gt": "arrayMap(g -> '0/0', `calls.gt`)"}
    await execute_clickhouse(
        f"INSERT INTO {table} ({', '.join(f'`{column}`' for column in SMALL_VARIANT_ENTRY_COLUMNS)}) "
        f"SELECT {', '.join(changed.get(column, f'`{column}`') for column in SMALL_VARIANT_ENTRY_COLUMNS)} "
        f"FROM {table} WHERE family_guid = %(f)s AND project_guid = %(a)s AND sign = 1 "
        "AND NOT arrayAll(g -> g = '0/0', `calls.gt`) ORDER BY key LIMIT 1",
        {"f": family_uuid, "a": project_a, "b": project_b},
    )


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
    out: dict[str, Any] = {"facts": facts, "before": await _rows(family_uuid), "sv_columns": await _sv_entry_columns()}
    project_b, project_c, other_assembly = await _make_projects(project_a)
    out.update(project_a=project_a, project_b=project_b, project_c=project_c)
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

            out["presence_a"] = await presence(project_a)

            out["move"] = capture(await ac.put(url, json={"project_ids": [project_b]}))
            out["after_move"] = await _rows(family_uuid)
            out["summary_after_move"] = await _summary_projects(family_uuid)
            out["presence_b"] = await presence(project_b)

            out["both"] = capture(await ac.put(url, json={"project_ids": [project_a, project_b]}))
            out["after_both"] = await _rows(family_uuid)

            out["back"] = capture(await ac.put(url, json={"project_ids": [project_a]}))
            out["after_back"] = await _rows(family_uuid)

            out["other_assembly"] = capture(await ac.put(url, json={"project_ids": [project_c]}))
            out["after_other_assembly"] = await _rows(family_uuid)
            out["projects_after_other_assembly"] = await _family_projects(family_uuid)

            # A family moved before the fix: linked to B in Postgres, its rows under A.
            await _link_in_postgres_only(family_uuid, project_b)
            out["presence_moved_before_fix"] = await presence(project_b)
            out["repair"] = capture(await ac.put(url, json={"project_ids": [project_b]}))
            out["after_repair"] = await _rows(family_uuid)
            out["presence_repaired"] = await presence(project_b)

            out["restore"] = capture(await ac.put(url, json={"project_ids": [project_a]}))

            # Rows left under B with other calls, while the family is in A alone.
            await _write_stale_copy(family_uuid, project_a, project_b)
            out["with_stale_copy"] = await _rows(family_uuid)
            out["link_stale"] = capture(await ac.put(url, json={"project_ids": [project_a, project_b]}))
            out["after_link_stale"] = await _rows(family_uuid)

            out["end"] = capture(await ac.put(url, json={"project_ids": [project_a]}))
    finally:
        # The family back in A alone with its rows as imported, whatever happened above.
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

    base = tmp_path_factory.mktemp("golden_project_move")
    root = base / "FAM_TRIO"
    shutil.copytree(_FIXTURE, root)

    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(root.parent)])
    request.addfinalizer(mp.undo)

    snapshot = _harness.run_async(lambda: _exercise(root))
    assert snapshot["facts"]["completed"] is True, snapshot["facts"]
    return snapshot


def _only(rows: dict[str, dict[str, list[str]]], *projects: str, like: dict[str, dict[str, list[str]]], under: str) -> bool:
    """Whether ``rows`` holds, in each table, exactly ``projects``, each with the rows ``like``
    holds under ``under``."""
    return all(set(rows[kind]) == set(projects) and all(rows[kind][p] == like[kind][under] for p in projects) for kind in rows)


def test_the_golden_trio_has_small_and_structural_variants_in_project_a(run) -> None:
    before, project_a = run["before"], run["project_a"]
    assert set(before["small"]) == {project_a} and before["small"][project_a]
    assert set(before["structural"]) == {project_a} and before["structural"][project_a]
    for kind in ("small-variants", "structural-variants"):
        assert run["presence_a"][kind]["status"] == 200, run["presence_a"][kind]["text"]
        assert run["presence_a"][kind]["json"]["total"] == 1


def test_moved_to_b_the_family_takes_every_row_along(run) -> None:
    assert run["move"]["status"] == 200, run["move"]["text"]
    assert _only(run["after_move"], run["project_b"], like=run["before"], under=run["project_a"])
    assert run["summary_after_move"] == [run["project_b"]]


def test_moved_to_b_the_family_page_finds_its_variants(run) -> None:
    # The owner's report: after a move the family page offered neither small nor structural
    # variants, as the presence check found none under the new project.
    for kind in ("small-variants", "structural-variants"):
        assert run["presence_b"][kind]["status"] == 200, run["presence_b"][kind]["text"]
        assert run["presence_b"][kind]["json"]["total"] == 1, kind


def test_linked_to_a_and_b_both_hold_every_row_and_back_to_a_b_holds_none(run) -> None:
    assert run["both"]["status"] == 200, run["both"]["text"]
    assert _only(run["after_both"], run["project_a"], run["project_b"], like=run["before"], under=run["project_a"])
    assert run["back"]["status"] == 200, run["back"]["text"]
    assert run["after_back"] == run["before"]


def test_a_project_on_another_assembly_is_refused_and_nothing_changes(run) -> None:
    refused = run["other_assembly"]
    assert refused["status"] == 409, refused["text"]
    assert "stored on GRCh38" in refused["json"]["detail"]
    assert run["after_other_assembly"] == run["before"]
    assert run["projects_after_other_assembly"] == [run["project_a"]]


def test_a_family_moved_before_the_fix_gets_its_variants_when_saved_again(run) -> None:
    for kind in ("small-variants", "structural-variants"):
        assert run["presence_moved_before_fix"][kind]["json"]["total"] == 0, kind
    assert run["repair"]["status"] == 200, run["repair"]["text"]
    assert _only(run["after_repair"], run["project_b"], like=run["before"], under=run["project_a"])
    for kind in ("small-variants", "structural-variants"):
        assert run["presence_repaired"][kind]["json"]["total"] == 1, kind


def test_rows_left_under_a_project_are_replaced_when_the_family_is_linked_to_it(run) -> None:
    stale = run["with_stale_copy"]["small"][run["project_b"]]
    assert len(stale) == 1 and stale[0] not in run["before"]["small"][run["project_a"]]
    assert run["link_stale"]["status"] == 200, run["link_stale"]["text"]
    assert _only(run["after_link_stale"], run["project_a"], run["project_b"], like=run["before"], under=run["project_a"])
    assert run["end"]["status"] == 200, run["end"]["text"]


def test_the_copy_names_every_sv_entries_column(run) -> None:
    from backend.app.services.clickhouse_variant_storage import STRUCTURAL_VARIANT_ENTRY_COLUMNS

    assert run["sv_columns"] == sorted(STRUCTURAL_VARIANT_ENTRY_COLUMNS)


def test_the_family_ends_in_project_a_with_its_rows_as_imported(run) -> None:
    assert run["final_projects"] == [run["project_a"]]
    assert run["final"] == run["before"]
