"""The variant tables lose the columns no code reads the first time they are ensured (ENG-6),
real ClickHouse.

An earlier version created ``SNV_INDEL/variants/details`` with ``liftedOverChrom`` and
``liftedOverPos`` (always null), ``SNV_INDEL/entries`` and ``SV/entries`` with ``sample_type``
(always ``WGS``), ``SNV_INDEL/variants/annotation_index`` with ``transcript_ids``,
``sift_terms`` and ``polyphen_terms`` (written, never read) and ``SV/entries`` with
``calls.gq`` (never written). Here such tables, holding a family's rows as that version wrote
them, are ensured under an assembly of their own: the columns are gone afterwards, one ALTER
per table, and every other value of every row reads back as it was stored, through the readers
the rewrites use too, before and after the parts are merged; the writers store new rows; the
same ALTER issued again by a second process that found the columns first changes nothing; a new
process's ensure, finding none, alters nothing; and an import's backup of the family, taken on
the altered tables, puts its rows back.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it. All values
are synthetic.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_FAMILY = "family-eng6"
_PROJECT = "project-eng6"
# The columns an earlier version created, as it created them: (table, column, type, after).
_OLD_COLUMNS = (
    ("SNV_INDEL/variants/details", "liftedOverChrom", "LowCardinality(Nullable(String))", "annotationsJson"),
    ("SNV_INDEL/variants/details", "liftedOverPos", "Nullable(UInt32)", "liftedOverChrom"),
    ("SNV_INDEL/variants/annotation_index", "transcript_ids", "Array(String)", "gene_ids"),
    ("SNV_INDEL/variants/annotation_index", "sift_terms", "Array(String)", "max_spliceai"),
    ("SNV_INDEL/variants/annotation_index", "polyphen_terms", "Array(String)", "sift_terms"),
    ("SNV_INDEL/entries", "sample_type", "LowCardinality(String)", "family_guid"),
    ("SV/entries", "sample_type", "LowCardinality(String)", "family_guid"),
    ("SV/entries", "calls.gq", "Array(Nullable(UInt16))", "calls.gt"),
)
_TABLES = tuple(dict.fromkeys(table for table, *_rest in _OLD_COLUMNS))
# The tables the family's rows are written to before the first ensure: those, and the SV
# details and key lookup, which keep their columns.
_WRITTEN_BEFORE = (*_TABLES, "SV/variants/details", "SV/key_lookup")
_DROPPED = {table: {column for t, column, *_rest in _OLD_COLUMNS if t == table} for table in _TABLES}

# The columns of the rows the row builders make, as the writers name them.
_DETAIL_COLUMNS = (
    "key", "variantId", "annotation_version", "annotationSetHash", "chrom", "pos", "ref", "alt",
    "rsid", "annotationsJson",
)
_INDEX_COLUMNS = (
    "key", "variantId", "annotation_version", "annotationSetHash", "chrom", "pos", "ref", "alt",
    "rsid", "gene_symbols", "gene_ids", "hgvsc_values", "hgvsp_values", "impacts", "effects",
    "clinvar_terms", "has_canonical", "has_mane_select", "has_mane_plus_clinical", "has_lof",
    "max_gnomad_af", "max_gnomad_exomes_af", "max_gnomad_genomes_af", "max_gnomad_popmax_af",
    "max_topmed_af", "max_gnomad_ac", "max_gnomad_hom_count", "max_gnomad_hemi_count",
    "max_cadd_phred", "max_revel", "max_spliceai",
)
_SV_DETAIL_COLUMNS = (
    "key", "variantId", "family_guid", "chrom", "start", "end", "svType", "source", "remoteChrom",
    "remoteStart", "remoteEnd", "svLen", "filters", "annotationsJson",
)
_SV_ENTRY_COLUMNS = (
    "key", "variantId", "project_guid", "family_guid", "chrom", "start", "end", "svType", "source",
    "gene_symbols", "calls.sampleId", "calls.gt", "calls.qual", "calls.readSupport", "calls.filter",
    "calls.ps", "calls.cn", "sign",
)


def _small(pos: int) -> Any:
    from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord

    return SmallVariantRecord(
        variant_key=None,
        variant_id=f"1-{pos}-G-A",
        chr="1",
        start=pos,
        end=pos,
        ref="G",
        alt="A",
        source="clair3",
        rsid=None,
        filters=["PASS"],
        gene_symbols=["GENE1"],
        annotations=[{"gene": "GENE1", "transcript_id": "ENST0001", "sift": "deleterious", "polyphen": "benign"}],
        calls=[SmallVariantCall(sample="S1", gt="0/1", gq=40, dp=30, af=[], ad=[15, 15], ps=None)],
    )


def _sv(start: int) -> Any:
    from backend.app.services.clickhouse_variant_records import StructuralVariantCall, StructuralVariantRecord

    return StructuralVariantRecord(
        variant_key=None,
        variant_id=f"1-{start}-{start + 1000}-DEL---",
        chr="1",
        start=start,
        end=start + 1000,
        sv_type="DEL",
        source="sniffles",
        remote_chr=None,
        remote_start=None,
        remote_end=None,
        sv_len=-1000,
        filters=["PASS"],
        gene_symbols=["GENE1"],
        annotations=[],
        calls=[StructuralVariantCall(sample="S1", gt="0/1", qual=50.0, read_support=12, filter="PASS", copy_number=1)],
    )


async def _exercise(dataset: str) -> dict[str, Any]:
    from backend.app.core.clickhouse import close_clickhouse_client, execute_clickhouse, init_clickhouse_schema
    from backend.app.core.config import settings
    from backend.app.services import clickhouse_family_snapshot as snapshots
    from backend.app.services import clickhouse_variant_storage as cvs
    from backend.app.services.clickhouse_variant_rows import _small_variant_entry_rows, _structural_variant_entry_rows

    database = settings.clickhouse_database
    out: dict[str, Any] = {}

    def table(suffix: str) -> str:
        return f"{database}.`{dataset}/{suffix}`"

    async def columns(suffix: str) -> list[str]:
        rows = await execute_clickhouse(
            "SELECT name FROM system.columns WHERE database = %(database)s AND table = %(table)s ORDER BY position",
            {"database": database, "table": f"{dataset}/{suffix}"},
        )
        return [str(name) for (name,) in rows]

    async def stored(kept: dict[str, list[str]]) -> dict[str, list[tuple]]:
        rows = {}
        for suffix, names in kept.items():
            selected = ", ".join(f"`{name}`" for name in names)
            rows[suffix] = [
                tuple(row) for row in await execute_clickhouse(f"SELECT {selected} FROM {table(suffix)} ORDER BY key")
            ]
        return rows

    async def insert(suffix: str, names: tuple[str, ...], rows: list[tuple]) -> None:
        await execute_clickhouse(f"INSERT INTO {table(suffix)} ({', '.join(f'`{n}`' for n in names)}) VALUES", rows)

    issued: list[str] = []
    execute = cvs._execute

    async def recording(query: str, params: Any = None, data: Any = None) -> Any:
        issued.append(" ".join(query.split()))
        return await execute(query, params, data)

    mp = pytest.MonkeyPatch()
    try:
        await init_clickhouse_schema()
        await cvs.ensure_clickhouse_variant_tables("GRCh38")
        # The tables as an earlier version created them: this version's, with the old columns where
        # that version had them.
        for suffix in _WRITTEN_BEFORE:
            await execute_clickhouse(f"CREATE TABLE {table(suffix)} AS {database}.`GRCh38/{suffix}`")
        for suffix, column, column_type, after in _OLD_COLUMNS:
            await execute_clickhouse(f"ALTER TABLE {table(suffix)} ADD COLUMN `{column}` {column_type} AFTER `{after}`")
        out["old_columns"] = {suffix: await columns(suffix) for suffix in _TABLES}

        # A family's rows as that version wrote them.
        details, entries, _annotations, index, _genes = _small_variant_entry_rows(dataset, _FAMILY, [_PROJECT], [_small(3000)])
        await insert("SNV_INDEL/variants/details", (*_DETAIL_COLUMNS, "liftedOverChrom", "liftedOverPos"), [(*details[0], None, None)])
        await insert(
            "SNV_INDEL/variants/annotation_index",
            (*_INDEX_COLUMNS, "transcript_ids", "sift_terms", "polyphen_terms"),
            [(*index[0], ["ENST0001"], ["deleterious"], ["benign"])],
        )
        await insert("SNV_INDEL/entries", (*cvs.SMALL_VARIANT_ENTRY_COLUMNS, "sample_type"), [(*entries[0], "WGS")])
        sv_details, sv_lookups, sv_entries = _structural_variant_entry_rows(dataset, _FAMILY, [_PROJECT], [_sv(30000)])
        await insert("SV/variants/details", _SV_DETAIL_COLUMNS, sv_details)
        await insert("SV/key_lookup", ("family_guid", "variantId", "source", "key"), sv_lookups)
        await insert("SV/entries", (*_SV_ENTRY_COLUMNS, "sample_type", "calls.gq"), [(*sv_entries[0], "WGS", [None])])
        kept = {
            suffix: [name for name in out["old_columns"][suffix] if name not in _DROPPED[suffix]] for suffix in _TABLES
        }
        out["kept"] = kept
        out["before"] = await stored(kept)

        mp.setattr(cvs, "_execute", recording)
        await cvs.ensure_clickhouse_variant_tables(dataset)
        out["alters"] = [query for query in issued if query.startswith("ALTER TABLE")]
        out["new_columns"] = {suffix: await columns(suffix) for suffix in _TABLES}
        out["after"] = await stored(kept)
        out["small_read_back"] = await cvs.fetch_family_small_variant_entries(dataset, _FAMILY)
        out["sv_read_back"] = await cvs.fetch_family_structural_variant_rows(dataset, _FAMILY)
        for suffix in _TABLES:
            await execute_clickhouse(f"OPTIMIZE TABLE {table(suffix)} FINAL")
        out["after_merge"] = await stored(kept)

        # The writers store new rows in the altered tables.
        await cvs.insert_small_variant_records(dataset, _FAMILY, [_PROJECT], [_small(4000)])
        await cvs.insert_structural_variant_records(dataset, _FAMILY, [_PROJECT], [_sv(40000)])
        out["positions_after_writes"] = (
            sorted(entry["pos"] for entry in await cvs.fetch_family_small_variant_entries(dataset, _FAMILY)),
            sorted(row.record.start for row in await cvs.fetch_family_structural_variant_rows(dataset, _FAMILY)),
        )

        # A second process that found the columns before the first dropped them issues the same
        # ALTERs: they change nothing.
        before_second = await stored(kept)
        for alter in out["alters"]:
            await execute_clickhouse(alter)
        out["columns_after_second_alter"] = {suffix: await columns(suffix) for suffix in _TABLES}
        out["stored_after_second_alter"] = (before_second, await stored(kept))

        # A new process: nothing ensured yet, and the columns already gone.
        issued.clear()
        cvs._ensured_variant_table_assemblies.discard(dataset)
        await cvs.ensure_clickhouse_variant_tables(dataset)
        out["alters_again"] = [query for query in issued if query.startswith("ALTER TABLE")]
        out["unfinished_mutations"] = (
            await execute_clickhouse(
                "SELECT count() FROM system.mutations WHERE database = %(database)s "
                "AND startsWith(table, %(prefix)s) AND NOT is_done",
                {"database": database, "prefix": f"{dataset}/"},
            )
        )[0][0]

        # An import's backup of the family, taken on the altered tables, puts its rows back.
        snapshot = await snapshots.snapshot_family_clickhouse_state(dataset, _FAMILY, owner=f"run-{uuid4().hex}")
        try:
            await cvs.delete_family_small_variants(dataset, _FAMILY)
            await cvs.delete_family_structural_variants(dataset, _FAMILY)
            out["emptied"] = (
                len(await cvs.fetch_family_small_variant_entries(dataset, _FAMILY)),
                len(await cvs.fetch_family_structural_variant_rows(dataset, _FAMILY)),
            )
            await snapshots.restore_family_clickhouse_state(snapshot)
        finally:
            await snapshots.discard_family_clickhouse_snapshot(snapshot)
        out["restored"] = (
            sorted(entry["pos"] for entry in await cvs.fetch_family_small_variant_entries(dataset, _FAMILY)),
            sorted(row.record.start for row in await cvs.fetch_family_structural_variant_rows(dataset, _FAMILY)),
        )
    finally:
        mp.undo()
        cvs._ensured_variant_table_assemblies.discard(dataset)
        tables = await execute_clickhouse(
            "SELECT name FROM system.tables WHERE database = %(database)s AND startsWith(name, %(prefix)s)",
            {"database": database, "prefix": f"{dataset}/"},
        )
        for (name,) in tables:
            await execute_clickhouse(f"DROP TABLE IF EXISTS {database}.`{name}` SYNC")
        await close_clickhouse_client()
    return out


@pytest.fixture(scope="module")
def run() -> dict[str, Any]:
    return asyncio.run(_exercise(f"ENG6_{uuid4().hex[:8]}"))


def test_the_tables_had_the_old_columns(run) -> None:
    for suffix in _TABLES:
        assert _DROPPED[suffix] <= set(run["old_columns"][suffix]), suffix


def test_the_first_ensure_drops_them_with_one_alter_per_table(run) -> None:
    assert len(run["alters"]) == len(_TABLES), run["alters"]
    for suffix in _TABLES:
        [alter] = [query for query in run["alters"] if f"/{suffix}`" in query]
        assert alter.count("DROP COLUMN IF EXISTS") == len(_DROPPED[suffix])
        assert run["new_columns"][suffix] == run["kept"][suffix], suffix


def test_every_other_value_reads_back_as_stored(run) -> None:
    assert run["after"] == run["before"]
    assert run["after_merge"] == run["before"]
    assert all(len(rows) == 1 for rows in run["before"].values())
    [entry] = run["small_read_back"]
    assert "sample_type" not in entry
    assert (entry["calls.sampleId"], entry["calls.gt"], entry["calls.gq"], entry["calls.dp"]) == (["S1"], ["0/1"], [40], [30])
    [sv] = run["sv_read_back"]
    assert (sv.record.start, sv.record.calls[0].gt, sv.record.calls[0].read_support) == (30000, "0/1", 12)


def test_the_writers_store_new_rows(run) -> None:
    assert run["positions_after_writes"] == ([3000, 4000], [30000, 40000])


def test_the_same_alter_from_a_second_process_changes_nothing(run) -> None:
    assert run["columns_after_second_alter"] == run["new_columns"]
    before, after = run["stored_after_second_alter"]
    assert after == before


def test_a_table_without_them_is_not_altered_again(run) -> None:
    assert run["alters_again"] == []
    assert run["unfinished_mutations"] == 0


def test_a_backup_taken_on_the_altered_tables_puts_the_family_back(run) -> None:
    assert run["emptied"] == (0, 0)
    assert run["restored"] == run["positions_after_writes"]
