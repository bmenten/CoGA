"""Deleting a sample rewrites the family's small variants from the stored rows (unit, fakes).

``DELETE /admin/samples/{sample_id}`` removes the sample's small-variant calls by deleting the
family's ``SNV_INDEL/entries`` rows and writing them back without that sample. It reads them
with ``fetch_family_small_variant_entries`` (the family-wide delete's scope, every column as
stored) and writes them back with ``rewrite_family_small_variant_entries`` (column for column),
so the calls and fields it does not mean to change come back unchanged. It used to read them
through the family view, which leaves out the imputed callsets, inactive members' calls and
rows under projects the family has left, and normalises the genotype, the allele fractions
and the annotation version. ``test_e2e_small_variant_delete_keeps_every_call`` shows the same
against real ClickHouse and Postgres.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.app.services import admin_service, clickhouse_variant_storage
from backend.app.services.clickhouse_variant_storage import (
    SMALL_VARIANT_ENTRY_COLUMNS,
    small_variant_entry_without_samples,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext

_CALL_COLUMNS = (
    "calls.sampleId",
    "calls.gt",
    "calls.gq",
    "calls.dp",
    "calls.ab",
    "calls.af",
    "calls.ad",
    "calls.ps",
)


def _entry(calls: list[tuple[Any, ...]], **overrides: Any) -> dict[str, Any]:
    """An entry row as stored; ``calls`` holds (sampleId, gt, gq, dp, ab, af, ad, ps)."""
    entry: dict[str, Any] = {
        "key": 101,
        "variantId": "1-1000-A-G",
        "annotation_version": "vep-110",
        "annotationSetHash": 987654321,
        "project_guid": "project-uuid",
        "family_guid": "family-uuid",
        "sample_type": "WGS",
        "xpos": 1000001000,
        "chrom": "1",
        "pos": 1000,
        "ref": "A",
        "alt": "G",
        "source": "clair3",
        "rsid": "rs1",
        "is_gnomad_gt_5_percent": False,
        "is_annotated_in_any_gene": True,
        "gene_symbols": ["GENE_X"],
        "filters": ["PASS"],
        "qual": 50.0,
        "sign": 1,
    }
    for index, column in enumerate(_CALL_COLUMNS):
        entry[column] = [call[index] for call in calls]
    entry.update(overrides)
    return entry


def _calls(entry: dict[str, Any]) -> list[tuple[Any, ...]]:
    return list(zip(*(entry[column] for column in _CALL_COLUMNS)))


_PROBAND = ("PROBAND", "HET", 99, 20, 0.4, [], [12, 8], None)
_FATHER = ("FATHER", "0|1", 99, 30, 0.5, [0.5], [15, 15], 5001)
_SIB = ("SIB", "0/1", 99, 25, 0.5, [0.5], [12, 13], None)
_SIB_BY_UUID = ("uuid-sib", "1/1", 99, 25, 1.0, [1.0], [0, 25], None)


# --- the stored read ----------------------------------------------------------------------


@pytest.fixture
def storage_sql(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, Any] = {"queries": [], "rows": []}

    async def fake_execute(query: str, params: dict[str, Any] | None = None, data=None):
        captured["queries"].append((query, dict(params or {})))
        return captured["rows"]

    async def tables_ready(*_args, **_kwargs):
        return None

    monkeypatch.setattr(clickhouse_variant_storage, "_execute", fake_execute)
    monkeypatch.setattr(clickhouse_variant_storage, "ensure_clickhouse_variant_tables", tables_ready)
    return captured


@pytest.mark.asyncio
async def test_stored_read_has_the_family_wide_deletes_scope_and_every_column(storage_sql) -> None:
    await clickhouse_variant_storage.fetch_family_small_variant_entries("GRCh38", "family-uuid")

    query, params = storage_sql["queries"][0]
    assert "WHERE family_guid = %(family_guid)s AND sign = 1\n" in query
    assert params == {"family_guid": "family-uuid"}
    # None of the family view's filters: projects, visible samples, imputed callsets.
    for view_filter in ("project_guid IN", "hasAny", "NOT IN", "source ="):
        assert view_filter not in query
    for column in SMALL_VARIANT_ENTRY_COLUMNS:
        assert f"`{column}`" in query


@pytest.mark.asyncio
async def test_stored_read_returns_each_row_as_stored_and_joins_a_row_stored_twice(storage_sql) -> None:
    fullest = _entry([_PROBAND, _FATHER])
    second_copy = _entry([_PROBAND, _SIB])
    other_callset = _entry([_FATHER], source="glimpse2")
    storage_sql["rows"] = [
        tuple(row[column] for column in SMALL_VARIANT_ENTRY_COLUMNS)
        for row in (fullest, second_copy, other_callset)
    ]

    entries = await clickhouse_variant_storage.fetch_family_small_variant_entries(
        "GRCh38", "family-uuid"
    )

    # The second live copy of the clair3 row adds SIB's call to the fullest one; the same
    # variant in another callset stays its own row.
    assert len(entries) == 2
    assert _calls(entries[0]) == [_PROBAND, _FATHER, _SIB]
    assert {column: entries[0][column] for column in SMALL_VARIANT_ENTRY_COLUMNS if column not in _CALL_COLUMNS} == {
        column: fullest[column] for column in SMALL_VARIANT_ENTRY_COLUMNS if column not in _CALL_COLUMNS
    }
    assert entries[1] == other_callset


def test_removing_a_sample_keeps_every_other_value_as_stored() -> None:
    entry = _entry([_PROBAND, _SIB, _FATHER, _SIB_BY_UUID])

    kept = small_variant_entry_without_samples(entry, {"SIB", "uuid-sib"})

    assert kept is not None
    assert _calls(kept) == [_PROBAND, _FATHER]
    assert {column: kept[column] for column in kept if column not in _CALL_COLUMNS} == {
        column: entry[column] for column in entry if column not in _CALL_COLUMNS
    }
    assert small_variant_entry_without_samples(_entry([_SIB]), {"SIB", "uuid-sib"}) is None


@pytest.mark.asyncio
async def test_rewrite_deletes_family_wide_and_writes_every_column_back(monkeypatch) -> None:
    calls: list[tuple[str, Any]] = []

    async def fake_delete(_assembly, _family, *, source=None):
        calls.append(("delete", source))

    async def fake_insert(query: str, rows, *, chunk_size: int):
        calls.append(("insert", (query, list(rows))))

    async def fake_refresh(_assembly, _family):
        calls.append(("refresh", None))

    monkeypatch.setattr(clickhouse_variant_storage, "delete_family_small_variants", fake_delete)
    monkeypatch.setattr(clickhouse_variant_storage, "_execute_insert_chunks", fake_insert)
    monkeypatch.setattr(clickhouse_variant_storage, "refresh_family_small_variant_summaries", fake_refresh)
    entry = _entry([_PROBAND, _FATHER])

    await clickhouse_variant_storage.rewrite_family_small_variant_entries("GRCh38", "family-uuid", [entry])

    assert [name for name, _ in calls] == ["delete", "insert", "refresh"]
    assert calls[0][1] is None  # every callset: the scope the read covered
    query, rows = calls[1][1]
    assert query.endswith(
        "(" + ", ".join(f"`{column}`" for column in SMALL_VARIANT_ENTRY_COLUMNS) + ") VALUES"
    )
    assert rows == [tuple(entry[column] for column in SMALL_VARIANT_ENTRY_COLUMNS)]

    calls.clear()
    await clickhouse_variant_storage.rewrite_family_small_variant_entries("GRCh38", "family-uuid", [])
    assert [name for name, _ in calls] == ["delete"]


# --- the whole-sample delete --------------------------------------------------------------


class _Result:
    def fetchall(self) -> list[Any]:
        return []


class _Session:
    async def execute(self, *_args, **_kwargs) -> _Result:
        return _Result()

    async def commit(self) -> None:
        return None


def _context() -> FamilyMetadataContext:
    # FATHER is not in the context's sample map: an inactive member the family view hides.
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM",
        project_ids=["project-uuid"],
        sample_rows=[],
        sample_uuid_to_name={"uuid-proband": "PROBAND", "uuid-sib": "SIB"},
        sample_name_to_uuid={"PROBAND": "uuid-proband", "SIB": "uuid-sib"},
        affected_sample_names=[],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


@pytest.mark.asyncio
async def test_whole_sample_delete_rewrites_from_the_stored_rows(monkeypatch) -> None:
    stored = [
        _entry([_PROBAND, _FATHER, _SIB]),
        _entry([_FATHER, _SIB_BY_UUID], key=102, variantId="1-1500-C-T", source="glimpse2"),
        _entry([_PROBAND, _SIB], key=103, variantId="1-9900-C-G", project_guid="project-left"),
        _entry([_SIB], key=104, variantId="1-9700-A-C"),
    ]
    reads: list[tuple[str, str]] = []
    rewrites: list[list[dict[str, Any]]] = []

    async def sample_row(_session, _sample_id):
        return {"sample_uuid": "uuid-sib", "sample_id": "SIB", "family_uuid": "family-uuid", "family_id": "FAM"}

    async def sample_rows(_session, family_uuids):
        return {family_uuid: [] for family_uuid in family_uuids}

    async def contexts(_session, **_kwargs):
        return [_context()]

    async def fetch_entries(assembly, family):
        reads.append((assembly, family))
        return [dict(entry) for entry in stored]

    async def rewrite_entries(_assembly, _family, entries):
        rewrites.append(list(entries))

    async def no_structural_rows(contexts, _sample_name):
        return [(context, []) for context in contexts]

    async def nothing(*_args, **_kwargs):
        return 0

    for name, fake in {
        "_sample_row_or_404": sample_row,
        "_sample_rows_by_family": sample_rows,
        "_family_assembly_contexts": contexts,
        "fetch_family_small_variant_entries": fetch_entries,
        "rewrite_family_small_variant_entries": rewrite_entries,
        "_structural_variant_records_without_sample": no_structural_rows,
        "replace_family_structural_variants": nothing,
        "delete_interval_tracks": nothing,
        "delete_interval_track_sources": nothing,
        "count_family_small_variants": nothing,
        "count_family_structural_variants": nothing,
        "purge_sample_managed_files": nothing,
    }.items():
        monkeypatch.setattr(admin_service, name, fake)

    await admin_service.delete_sample_with_data(_Session(), "SIB", True)  # type: ignore[arg-type]

    assert reads == [("GRCh38", "family-uuid")]
    [written] = rewrites
    # SIB goes, under its id or its uuid; the inactive FATHER, the imputed row, the row
    # under the project the family left and every stored value stay.
    assert [(_calls(entry), entry["source"], entry["project_guid"]) for entry in written] == [
        ([_PROBAND, _FATHER], "clair3", "project-uuid"),
        ([_FATHER], "glimpse2", "project-uuid"),
        ([_PROBAND], "clair3", "project-left"),
    ]
    assert written[0]["annotation_version"] == "vep-110"
    assert written[0]["annotationSetHash"] == 987654321
