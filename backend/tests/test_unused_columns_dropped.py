"""The columns no code reads are dropped, and an import no longer writes ``unresolved_roi`` (ENG-6).

The owner's decision of 2026-10-28 (ENG-6): the schema keeps no column that nothing reads.

- Postgres: ``auth_login_attempts.last_success_at`` and
  ``small_variant_reviews.compound_het_partner_variant_keys`` were never read or written, and
  ``structural_variant_reviews.variant_key`` never written (#853): the sign-out froze it into
  each reported structural variant, always null. The baselines create none of them; revision
  0011 drops them from a database made before (``test_postgres_migrations.py`` and its
  integration test).
- ClickHouse: ``liftedOverChrom``, ``liftedOverPos`` (always null) and ``sample_type`` (always
  ``WGS``) were never read, the annotation index's ``transcript_ids``, ``sift_terms`` and
  ``polyphen_terms`` written and never read, and the SV calls' ``calls.gq`` never written
  (#868). The tables are created without them, no writer names one, and a table created before
  loses them the first time this version ensures it; one without them is not altered
  (``integration/test_unused_columns_dropped_clickhouse.py`` on real ClickHouse).
- ``families.metadata.unresolved_roi`` was written when a manifest's ROI could not be resolved,
  never read, and never cleared by a later import that resolved it: the import writes nothing
  to the family then, and revision 0011 removes the key from the families that hold it.

All values are synthetic.
"""

from __future__ import annotations

import asyncio
import re
import types
from pathlib import Path
from typing import Any

import pytest

from backend.app.core.clickhouse import _parse_insert_query
from backend.app.core.config import settings
from backend.app.core.postgres import _split_sql_script
from backend.app.services import clickhouse_variant_storage as cvs
from backend.app.services import family_package_registration as registration
from backend.app.services import report_signout_service as rss
from backend.app.services import structural_variant_review_pg as svr
from backend.app.services.clickhouse_variant_records import (
    SmallVariantCall,
    SmallVariantRecord,
    StructuralVariantCall,
    StructuralVariantRecord,
)

_SCHEMA_DIR = Path(__file__).resolve().parents[1] / "db" / "schema" / "postgres"

# The ClickHouse columns ENG-6 drops, per table (the suffix under the assembly).
_DROPPED = {
    "SNV_INDEL/variants/details": ("liftedOverChrom", "liftedOverPos"),
    "SNV_INDEL/variants/annotation_index": ("transcript_ids", "sift_terms", "polyphen_terms"),
    "SNV_INDEL/entries": ("sample_type",),
    "SV/entries": ("sample_type", "calls.gq"),
}


# --- Postgres: the baselines ------------------------------------------------------------------


def _baseline_statements() -> list[str]:
    return [
        " ".join(statement.split())
        for path in sorted(_SCHEMA_DIR.glob("*.sql"))
        for statement in _split_sql_script(path.read_text())
    ]


def _create_table(table: str) -> str:
    return next(s for s in _baseline_statements() if s.startswith(f"CREATE TABLE IF NOT EXISTS {table} "))


def _columns(create: str) -> set[str]:
    """The column names a ``CREATE TABLE`` statement defines (not its constraints)."""
    body = create[create.index("(") + 1 : create.rindex(")")]
    return {
        part.strip().split()[0]
        for part in re.split(r",(?![^(]*\))", body)
        if part.strip() and not part.strip().startswith(("CONSTRAINT", "PRIMARY KEY"))
    }


@pytest.mark.parametrize(
    ("table", "column"),
    [
        ("auth_login_attempts", "last_success_at"),
        ("small_variant_reviews", "compound_het_partner_variant_keys"),
        ("structural_variant_reviews", "variant_key"),
    ],
)
def test_the_baselines_create_no_column_nothing_reads(table: str, column: str) -> None:
    assert column not in _columns(_create_table(table))
    # Nor does any other statement name it: an index, a grant or an upgrade statement would
    # fail on the next start once revision 0011 dropped the column.
    assert not [s for s in _baseline_statements() if f"{table} " in s and re.search(rf"\b{column}\b", s)]


def test_the_columns_the_reviews_still_use_are_kept() -> None:
    small = _columns(_create_table("small_variant_reviews"))
    # A small-variant review's key is written and read; the pair's partners are kept by id.
    assert {"variant_key", "variant_id", "compound_het_partner_variant_ids"} <= small
    assert {"variant_id", "cnv_evidence_snapshot"} <= _columns(_create_table("structural_variant_reviews"))
    assert {"failure_count", "last_failure_at", "locked_until"} <= _columns(_create_table("auth_login_attempts"))
    assert not [s for s in _baseline_statements() if "idx_structural_variant_reviews_family_variant_key" in s]
    assert [s for s in _baseline_statements() if "idx_small_variant_reviews_family_variant_key" in s]


# --- Postgres: the readers of the SV review's key ----------------------------------------------


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> "_Rows":
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class _Session:
    """Answers every statement with ``rows`` and records its SQL."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.sql: list[str] = []
        self.params: list[Any] = []
        self.commits = 0

    async def execute(self, statement: Any, params: Any = None) -> _Rows:
        self.sql.append(" ".join(str(statement).split()))
        self.params.append(params)
        return _Rows(self.rows)

    async def commit(self) -> None:
        self.commits += 1


# An SV review as the table holds it after revision 0011: no ``variant_key``.
_SV_REVIEW = {
    "variant_id": "1-30000-31000-DEL---",
    "classification": "Pathogenic - class 5",
    "cnv_class": "cnv_class_5",
    "cnv_point_total": 1.0,
    "cnv_acmg": {"kind": "loss", "criteria": [], "point_total": 1.0},
    "tags": ["report"],
    "note": None,
    "cnv_evidence_snapshot": None,
}


def test_the_sign_out_freezes_each_reported_sv_without_reading_its_key() -> None:
    session = _Session([dict(_SV_REVIEW)])
    frozen = asyncio.run(rss._reported_structural_reviews(session, "00000000-0000-0000-0000-00000000f001"))

    [sql] = session.sql
    assert "FROM structural_variant_reviews" in sql and "variant_key" not in sql
    # The record keeps its form: the key every record so far holds as null (nothing ever wrote
    # it), so a signed version still compares with the report as it is now, section by section.
    assert frozen == [
        {
            "variant_id": "1-30000-31000-DEL---",
            "variant_key": None,
            "classification": "Pathogenic - class 5",
            "cnv_class": "cnv_class_5",
            "cnv_point_total": 1.0,
            "cnv_acmg": {"kind": "loss", "criteria": [], "point_total": 1.0},
            "tags": ["report"],
            "note": None,
            "evidence_snapshot": None,
        }
    ]


def test_an_sv_review_is_read_without_the_key() -> None:
    session = _Session([{"id": "r-1", **_SV_REVIEW}])
    row = asyncio.run(
        svr._fetch_review_row(session, family_uuid="00000000-0000-0000-0000-00000000f001", variant_id="1-30000-31000-DEL---")
    )

    assert row is not None and row["variant_id"] == "1-30000-31000-DEL---"
    assert "FROM structural_variant_reviews" in session.sql[0] and "variant_key" not in session.sql[0]


# --- The manifest's ROI ------------------------------------------------------------------------


def _bundle(roi: str) -> Any:
    return types.SimpleNamespace(manifest=types.SimpleNamespace(roi=roi, metadata={}))


_FAMILY = types.SimpleNamespace(family_uuid="00000000-0000-0000-0000-00000000f001", assembly_id="00000000-0000-0000-0000-0000000000a1")


def test_an_roi_the_import_cannot_resolve_writes_nothing_to_the_family() -> None:
    # No gene of the assembly has the name: the family's region of interest stays as it was, and
    # nothing is written to its metadata, which no screen or check read.
    session = _Session([])
    asyncio.run(registration._apply_manifest_roi(session, bundle=_bundle("NOT_A_GENE"), context=_FAMILY))

    assert [sql for sql in session.sql if "families" in sql and not sql.startswith("SELECT")] == []
    assert "unresolved_roi" not in " ".join(session.sql)


def test_an_roi_the_import_resolves_is_the_familys() -> None:
    session = _Session([])
    asyncio.run(registration._apply_manifest_roi(session, bundle=_bundle("chr1:1000-2000"), context=_FAMILY))

    [update] = [sql for sql in session.sql if sql.startswith("UPDATE families")]
    assert "SET roi_query = :query" in update and "metadata" not in update
    assert session.params[-1]["chr"] == "1" and (session.params[-1]["start"], session.params[-1]["end"]) == (1000, 2000)


# --- ClickHouse: the tables and their writers ----------------------------------------------------


def _recording(answer=None):
    statements: list[tuple[str, Any]] = []

    async def fake_execute(query: str, params: Any = None, data: Any = None) -> Any:
        flat = " ".join(query.split())
        statements.append((flat, data if data is not None else params))
        return answer(flat, params) if answer else []

    return statements, fake_execute


def _create_statements(statements: list[tuple[str, Any]]) -> dict[str, str]:
    return {
        sql.split("`")[1].split("/", 1)[1]: sql
        for sql, _params in statements
        if sql.startswith("CREATE TABLE IF NOT EXISTS")
    }


def test_the_variant_tables_are_created_without_the_unused_columns(monkeypatch) -> None:
    statements, fake_execute = _recording()
    monkeypatch.setattr(cvs, "_execute", fake_execute)
    monkeypatch.setattr(cvs, "_ensured_variant_table_assemblies", set())

    asyncio.run(cvs.ensure_clickhouse_variant_tables("GRCh38"))

    creates = _create_statements(statements)
    for table, columns in _DROPPED.items():
        assert not [column for column in columns if f"`{column}`" in creates[table]], table
    # The small variants' genotype quality is a call's own and stays.
    assert "`calls.gq` Array(Nullable(UInt16))" in creates["SNV_INDEL/entries"]
    assert "sample_type" not in cvs.SMALL_VARIANT_ENTRY_COLUMNS


def _small_record() -> SmallVariantRecord:
    return SmallVariantRecord(
        variant_key=None,
        variant_id="1-3000-G-A",
        chr="1",
        start=3000,
        end=3000,
        ref="G",
        alt="A",
        source="clair3",
        rsid=None,
        filters=["PASS"],
        gene_symbols=["GENE1"],
        annotations=[
            {"gene": "GENE1", "transcript_id": "ENST0001", "sift": "deleterious", "polyphen": "benign"}
        ],
        calls=[SmallVariantCall(sample="S1", gt="0/1", gq=40, dp=30, af=[], ad=[15, 15], ps=None)],
    )


def _sv_record() -> StructuralVariantRecord:
    return StructuralVariantRecord(
        variant_key=None,
        variant_id="1-30000-31000-DEL---",
        chr="1",
        start=30000,
        end=31000,
        sv_type="DEL",
        source="sniffles",
        remote_chr=None,
        remote_start=None,
        remote_end=None,
        sv_len=-1000,
        filters=["PASS"],
        gene_symbols=["GENE1"],
        annotations=[],
        calls=[StructuralVariantCall(sample="S1", gt="0/1", qual=50.0, read_support=12, filter="PASS")],
    )


def test_no_writer_names_a_dropped_column_and_each_row_fills_the_columns_it_names(monkeypatch) -> None:
    statements, fake_execute = _recording()
    monkeypatch.setattr(cvs, "_execute", fake_execute)

    async def ensured(_assembly: str) -> None:
        return None

    monkeypatch.setattr(cvs, "ensure_clickhouse_variant_tables", ensured)

    async def write() -> None:
        await cvs.insert_small_variant_records("GRCh38", "fam-1", ["project-1"], [_small_record()])
        await cvs.insert_structural_variant_records("GRCh38", "fam-1", ["project-1"], [_sv_record()])
        await cvs.rewrite_family_small_variant_entries(
            "GRCh38", "fam-1", cvs.small_variant_record_entries("GRCh38", "fam-1", ["project-1"], [_small_record()])
        )

    asyncio.run(write())

    inserts = [(sql, data) for sql, data in statements if sql.startswith("INSERT INTO") and isinstance(data, list)]
    written: dict[str, list[str]] = {}
    for sql, rows in inserts:
        table, _database, columns = _parse_insert_query(sql)
        suffix = table.split("/", 1)[1]
        written.setdefault(suffix, columns)
        assert all(len(row) == len(columns) for row in rows), suffix
        assert not [column for column in _DROPPED.get(suffix, ()) if column in columns], suffix
    assert {"SNV_INDEL/variants/details", "SNV_INDEL/variants/annotation_index", "SNV_INDEL/entries", "SV/entries"} <= set(written)


def test_the_row_builders_fill_no_dropped_column() -> None:
    from backend.app.services.clickhouse_variant_rows import _small_variant_entry_rows, _structural_variant_entry_rows

    details, entries, _annotations, index, _genes = _small_variant_entry_rows("GRCh38", "fam-1", ["p1"], [_small_record()])
    # The insert columns, in order: no lifted-over position, no sample type, no index arrays
    # of transcripts, SIFT or PolyPhen terms.
    assert len(details[0]) == 10
    assert entries[0][6] != "WGS" and "WGS" not in entries[0]
    assert len(entries[0]) == len(cvs.SMALL_VARIANT_ENTRY_COLUMNS)
    assert ["ENST0001"] not in index[0] and ["deleterious"] not in index[0] and ["benign"] not in index[0]
    _details, _lookups, sv_entries = _structural_variant_entry_rows("GRCh38", "fam-1", ["p1"], [_sv_record()])
    assert "WGS" not in sv_entries[0]


# --- ClickHouse: a table created before ----------------------------------------------------------


def _server_with(columns_by_table: dict[str, tuple[str, ...]]):
    """A ClickHouse whose tables of the assembly hold ``columns_by_table``'s unused columns."""

    def answer(sql: str, params: Any) -> Any:
        if sql.startswith("SELECT table, name FROM system.columns"):
            return [
                (table, column)
                for table, columns in columns_by_table.items()
                if table in params["tables"]
                for column in columns
                if column in params["names"]
            ]
        return []

    return answer


def test_a_table_created_before_loses_the_unused_columns_when_first_ensured(monkeypatch) -> None:
    statements, fake_execute = _recording(
        _server_with({f"GRCh38/{table}": columns for table, columns in _DROPPED.items()})
    )
    monkeypatch.setattr(cvs, "_execute", fake_execute)
    monkeypatch.setattr(cvs, "_ensured_variant_table_assemblies", set())

    asyncio.run(cvs.ensure_clickhouse_variant_tables("GRCh38"))

    alters = sorted(sql for sql, _params in statements if sql.startswith("ALTER TABLE"))
    assert alters == sorted(
        f"ALTER TABLE {settings.clickhouse_database}.`GRCh38/{table}` "
        + ", ".join(f"DROP COLUMN IF EXISTS `{column}`" for column in columns)
        for table, columns in _DROPPED.items()
    )
    # After the tables are created (a missing one has nothing to drop) and before the assembly
    # counts as ensured.
    order = [sql for sql, _params in statements]
    assert max(order.index(sql) for sql in order if sql.startswith("CREATE TABLE")) < min(order.index(a) for a in alters)
    assert "GRCh38" in cvs._ensured_variant_table_assemblies


def test_only_the_unused_columns_a_table_still_has_are_dropped(monkeypatch) -> None:
    statements, fake_execute = _recording(_server_with({"GRCh38/SV/entries": ("calls.gq",)}))
    monkeypatch.setattr(cvs, "_execute", fake_execute)
    monkeypatch.setattr(cvs, "_ensured_variant_table_assemblies", set())

    asyncio.run(cvs.ensure_clickhouse_variant_tables("GRCh38"))

    assert [sql for sql, _params in statements if sql.startswith("ALTER TABLE")] == [
        f"ALTER TABLE {settings.clickhouse_database}.`GRCh38/SV/entries` DROP COLUMN IF EXISTS `calls.gq`"
    ]


def test_a_table_without_them_is_not_altered(monkeypatch) -> None:
    statements, fake_execute = _recording(_server_with({}))
    monkeypatch.setattr(cvs, "_execute", fake_execute)
    monkeypatch.setattr(cvs, "_ensured_variant_table_assemblies", set())

    asyncio.run(cvs.ensure_clickhouse_variant_tables("GRCh38"))

    [lookup] = [(sql, params) for sql, params in statements if sql.startswith("SELECT table, name FROM system.columns")]
    # One bound query over the assembly's four tables, for the six column names.
    assert lookup[1]["tables"] == tuple(sorted(f"GRCh38/{table}" for table in _DROPPED))
    assert set(lookup[1]["names"]) == {column for columns in _DROPPED.values() for column in columns}
    assert [sql for sql, _params in statements if sql.startswith("ALTER TABLE")] == []
