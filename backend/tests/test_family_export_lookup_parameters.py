"""DATA-3: the family CSV exports failed above about 32,766 rows.

A family export reads up to 50,001 rows, its 50,000-row cap and one more to know whether more
matched (#512), and looks each row up in Postgres: its review, its gene's constraint metrics
and the structural variants on its genes (the SV second-hit badge). Those lookups bound one
parameter per id (an expanding IN), and asyncpg refuses a statement with more than 32,767
parameters, so an export of a larger result answered 500 before it reached its cap and its
truncation signal never fired. Each lookup now binds its ids as one text[] parameter, as the
Variant Explorer's review lookup does since #808.

Each lookup is run with 50,001 ids through a session that records the statement and its
parameters; the statement is compiled as the asyncpg dialect sends it, and the ids must
reach Postgres as one parameter. Both exports are then run over more matches than their cap
through a session that refuses, as asyncpg does, a statement with more parameters than that:
each file holds the first 50,000 rows and says it was cut.
"""

from __future__ import annotations

import asyncio
import types
from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql.asyncpg import dialect as asyncpg_dialect

from backend.app.services import clickhouse_family_variants as cfv
from backend.app.services.clickhouse_variant_records import (
    SmallVariantCall,
    SmallVariantRecord,
    StructuralVariantCall,
    StructuralVariantRecord,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.small_variant_review_pg import get_small_variant_review_map
from backend.app.services.small_variant_review_repository import _fetch_review_rows_for_variants
from backend.app.services.structural_variant_review_pg import get_structural_variant_review_map
from backend.app.services.sv_gene_index_service import get_sv_second_hits

_FAMILY = "00000000-0000-0000-0000-00000000da73"
# The family's assembly, whose gene profiles the constraint lookup reads (TRACE-15).
_ASSEMBLY = "00000000-0000-0000-0000-0000000a55e7"
# What an export reads: its 50,000-row cap and one row more (#512).
_EXPORT_READ = 50_001
# The most parameters asyncpg sends in one statement.
_ASYNCPG_MAX_PARAMETERS = 32_767


def _small_ids(count: int = _EXPORT_READ) -> list[str]:
    return [f"1-{1_000 + index}-A-G" for index in range(count)]


def _sent(statement, params: dict) -> tuple[list[str], dict]:
    """The parameters ``statement`` reaches Postgres with, as the asyncpg dialect binds it:
    their names in order ($1, $2, ...) and their values."""
    expanded = statement.compile(dialect=asyncpg_dialect()).construct_expanded_state(params)
    return list(expanded.positiontup), dict(expanded.parameters)


class _Result:
    def __init__(self, rows) -> None:
        self._rows = list(rows)

    def all(self):
        return list(self._rows)

    def mappings(self):
        return self


class _RecordingSession:
    """Records each statement with its parameters and answers with ``rows``."""

    def __init__(self, rows=()) -> None:
        self.rows = list(rows)
        self.calls: list[tuple[object, dict]] = []

    async def execute(self, statement, params=None):
        self.calls.append((statement, dict(params or {})))
        return _Result(self.rows)


class _TooManyParameters(Exception):
    """What asyncpg raises for a statement with more parameters than it sends."""


class _AsyncpgSession(_RecordingSession):
    """Answers every query with no rows, but refuses, as asyncpg does, a statement with more
    parameters than it sends."""

    async def execute(self, statement, params=None):
        names, _values = _sent(statement, dict(params or {}))
        if len(names) > _ASYNCPG_MAX_PARAMETERS:
            raise _TooManyParameters(
                f"the number of query arguments cannot exceed {_ASYNCPG_MAX_PARAMETERS}"
            )
        return await super().execute(statement, params)

    def lookup(self, table: str) -> dict:
        """The parameters of the one statement that read ``table``."""
        [params] = [params for statement, params in self.calls if f"FROM {table}" in str(statement)]
        return params


# --- each lookup binds its ids as one parameter -----------------------------------------


def test_the_small_variant_review_lookup_binds_any_number_of_ids_as_one_parameter() -> None:
    session = _RecordingSession()
    variant_ids = _small_ids()
    asyncio.run(get_small_variant_review_map(session, family_uuid=_FAMILY, variant_ids=variant_ids))

    statement, params = session.calls[0]
    names, values = _sent(statement, params)
    # The family and one array holding every id; before, 50,002 parameters.
    assert names == ["family_id", "variant_ids"]
    assert values["variant_ids"] == variant_ids
    assert "variant_id = ANY(CAST(:variant_ids AS text[]))" in str(statement)


def test_the_structural_variant_review_lookup_binds_any_number_of_ids_as_one_parameter() -> None:
    session = _RecordingSession()
    variant_ids = [f"sv-{index}" for index in range(_EXPORT_READ)]
    asyncio.run(get_structural_variant_review_map(session, family_uuid=_FAMILY, variant_ids=variant_ids))

    statement, params = session.calls[0]
    names, values = _sent(statement, params)
    assert names == ["family_id", "variant_ids"]
    assert values["variant_ids"] == variant_ids
    assert "variant_id = ANY(CAST(:variant_ids AS text[]))" in str(statement)


def test_the_gene_constraint_lookup_binds_any_number_of_genes_as_one_parameter_per_list() -> None:
    # 50,001 rows can hit as many genes, each with a symbol and an Ensembl id.
    variants = [
        types.SimpleNamespace(id=f"v{index}", gene=f"G{index}", gene_id=f"ENSG{index:011d}")
        for index in range(_EXPORT_READ)
    ]
    session = _RecordingSession()
    asyncio.run(cfv._fetch_gene_constraint_metric_map(session, variants, assembly_id=_ASSEMBLY))

    statement, params = session.calls[0]
    names, values = _sent(statement, params)
    # The family's assembly, one array of symbols and one of gene ids; before, 100,002
    # parameters.
    assert names == ["assembly_id", "gene_symbols", "gene_ids"]
    assert values["assembly_id"] == _ASSEMBLY
    assert values["gene_symbols"] == sorted(f"G{index}" for index in range(_EXPORT_READ))
    assert values["gene_ids"] == sorted(f"ENSG{index:011d}" for index in range(_EXPORT_READ))


def test_the_gene_constraint_lookup_maps_its_rows_back_to_the_variants_as_before() -> None:
    # The symbols go upper-cased, the gene ids as they are. A variant takes its gene id's
    # metrics first, else its symbol's; the first row of a gene wins (the query reads the
    # newest first).
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    rows = [
        {"hgnc_symbol": "GENEA", "gene_id": "ENSG01", "extra": {"pLI": 0.9}, "source_status": {}, "updated_at": now},
        {"hgnc_symbol": "GENEB", "gene_id": "ENSG02", "extra": {"mis_z": 3.1}, "source_status": {}, "updated_at": now},
        {"hgnc_symbol": "GENEB", "gene_id": "ENSG02", "extra": {"mis_z": 1.0}, "source_status": {}, "updated_at": now},
    ]
    variants = [
        types.SimpleNamespace(id="by-id", gene="other", gene_id="ENSG01"),
        types.SimpleNamespace(id="by-symbol", gene="GENEB", gene_id=None),
        types.SimpleNamespace(id="none", gene="GENEC", gene_id="ENSG03"),
    ]
    session = _RecordingSession(rows)

    metrics = asyncio.run(cfv._fetch_gene_constraint_metric_map(session, variants, assembly_id=_ASSEMBLY))

    assert metrics == {
        "by-id": {"gene_pli": 0.9, "gene_missense_z": None},
        "by-symbol": {"gene_pli": None, "gene_missense_z": 3.1},
    }
    _statement, params = session.calls[0]
    assert params == {
        "assembly_id": _ASSEMBLY,
        "gene_symbols": ["GENEB", "GENEC", "OTHER"],
        "gene_ids": ["ENSG01", "ENSG03"],
    }


def test_the_sv_second_hit_lookup_binds_any_number_of_genes_as_one_parameter() -> None:
    session = _RecordingSession()
    genes = {f"g{index}" for index in range(_EXPORT_READ)}
    asyncio.run(get_sv_second_hits(session, family_uuid=_FAMILY, gene_symbols=genes))

    statement, params = session.calls[0]
    names, values = _sent(statement, params)
    assert names == ["fid", "genes"]
    assert values["genes"] == sorted(gene.upper() for gene in genes)
    assert "gene_symbol = ANY(CAST(:genes AS text[]))" in str(statement)


def test_an_empty_id_list_reaches_postgres_as_an_empty_text_array() -> None:
    # An expanding IN over no ids renders "IN (SELECT CAST(NULL AS INTEGER) WHERE 1!=1)",
    # which Postgres refuses on a text column (text = integer). The array parameter is
    # typed in the SQL, so no ids is an empty text[]: no rows, no error.
    session = _RecordingSession()
    assert asyncio.run(_fetch_review_rows_for_variants(session, family_uuid=_FAMILY, variant_ids=[])) == []

    statement, params = session.calls[0]
    expanded = statement.compile(dialect=asyncpg_dialect()).construct_expanded_state(params)
    assert list(expanded.positiontup) == ["family_id", "variant_ids"]
    assert expanded.parameters["variant_ids"] == []
    assert "variant_id = ANY(CAST($2 AS text[]))" in expanded.statement
    assert "CAST(NULL AS INTEGER)" not in expanded.statement


# --- the exports past their cap ---------------------------------------------------------


def _context() -> FamilyMetadataContext:
    return FamilyMetadataContext(
        family_uuid=_FAMILY,
        family_id="FAM-DATA3",
        project_ids=["11111111-1111-1111-1111-111111111111"],
        sample_rows=[{"sample_id": "S1", "role": "proband", "sex": "female", "affected": True}],
        sample_uuid_to_name={"22222222-2222-2222-2222-222222222222": "S1"},
        sample_name_to_uuid={"S1": "22222222-2222-2222-2222-222222222222"},
        affected_sample_names=["S1"],
        assembly_id=_ASSEMBLY,
        assembly_name="GRCh38",
    )


def _small_record(index: int) -> SmallVariantRecord:
    pos = 1_000 + index
    return SmallVariantRecord(
        variant_key=None,
        variant_id=f"1-{pos}-A-G",
        chr="1",
        start=pos,
        end=pos,
        ref="A",
        alt="G",
        source="test",
        rsid=None,
        filters=["PASS"],
        gene_symbols=[f"G{index}"],
        annotations=[],
        calls=[SmallVariantCall(sample="S1", gt="0/1", gq=99.0, dp=30, af=[0.5], ad=[15, 15], ps=None)],
    )


def _structural_record(index: int) -> StructuralVariantRecord:
    start = 1_000 + 1_000 * index
    return StructuralVariantRecord(
        variant_key=None,
        variant_id=f"sv-{index}",
        chr="1",
        start=start,
        end=start + 500,
        sv_type="DEL",
        source="test",
        remote_chr=None,
        remote_start=None,
        remote_end=None,
        sv_len=-500,
        filters=["PASS"],
        gene_symbols=[],
        annotations=[],
        calls=[StructuralVariantCall(sample="S1", gt="0/1", qual=60.0, read_support=12, filter="PASS")],
    )


# More variants match than an export holds; the ClickHouse reads return at most the rows
# they are asked for, as the queries do.
_MATCHING = _EXPORT_READ + 10


async def _nothing(*_args, **_kwargs):
    return None


async def _no_cohort(*_args, **_kwargs):
    return {}


def test_a_small_variant_export_past_its_cap_is_cut_at_the_cap_and_says_so(monkeypatch) -> None:
    records = [_small_record(index) for index in range(_MATCHING)]

    async def _rows(context, filters, *, limit=None, offset=0, **_row_filters):
        return records[offset : offset + limit]

    async def _count(*_args, **_kwargs):
        return 10_001, True

    monkeypatch.setattr(cfv, "_fetch_small_variant_summary", _nothing)
    monkeypatch.setattr(cfv, "_fetch_small_variant_rows", _rows)
    monkeypatch.setattr(cfv, "_count_small_variant_rows_bounded", _count)
    # Left out: the SV index build (a ClickHouse scan) and the internal cohort (ClickHouse).
    # Every Postgres lookup of the rows runs against the session.
    monkeypatch.setattr(cfv, "_ensure_family_sv_gene_index", _nothing)
    monkeypatch.setattr(cfv, "_fetch_internal_cohort_map", _no_cohort)
    session = _AsyncpgSession()

    export = asyncio.run(cfv.export_family_small_variants(session, context=_context()))

    # Before, the review lookup's 50,002 parameters failed the export (a 500).
    assert export.truncated_reason == "row-limit"
    assert export.limit == 50_000
    assert [row.id for row in export.rows] == [record.variant_id for record in records[:50_000]]
    # Every row read was looked up in one statement each: its review, its gene's
    # constraint, the SVs on its gene.
    every_id = [record.variant_id for record in records[:_EXPORT_READ]]
    assert session.lookup("small_variant_reviews")["variant_ids"] == every_id
    assert len(session.lookup("gene_info")["gene_symbols"]) == _EXPORT_READ
    assert len(session.lookup("family_sv_gene_index")["genes"]) == _EXPORT_READ


def test_a_structural_variant_export_past_its_cap_is_cut_at_the_cap_and_says_so(monkeypatch) -> None:
    records = [_structural_record(index) for index in range(_MATCHING)]

    async def _rows(context, filters, *, limit=None, offset=0, **_kwargs):
        return records[offset : offset + limit]

    async def _summary(*_args, **_kwargs):
        return len(records), {"DEL": {"test": len(records)}}

    monkeypatch.setattr(cfv, "_fetch_structural_variant_summary", _summary)
    monkeypatch.setattr(cfv, "_fetch_structural_variant_rows", _rows)
    session = _AsyncpgSession()

    export = asyncio.run(cfv.export_family_structural_variants(session, context=_context()))

    assert export.truncated_reason == "row-limit"
    assert export.limit == 50_000
    assert [row.id for row in export.rows] == [record.variant_id for record in records[:50_000]]
    assert session.lookup("structural_variant_reviews")["variant_ids"] == [
        record.variant_id for record in records[:_EXPORT_READ]
    ]
