"""A source-scoped SV delete leaves none of that source's rows behind (unit, SQLite stand-in).

Since #658 an SV's storage key hashes its source, so each caller's call of an SV has its own
``SV/variants/details`` row (span, length, filters, annotation) and its own ``SV/key_lookup``
row beside its ``SV/entries`` rows. ``delete_family_structural_variants`` with a ``source``,
the delete half of a per-sample upload's rewrite and of a package dataset's re-import,
deleted that source's ``entries`` rows only. The details and lookup rows of every SV it
removed stayed behind, and no entry reached them. Before #658 that was deliberate: two
callers at one position shared those rows.

Both tables are family-scoped: the key hashes the family and every row carries
``family_guid``. The delete now also removes the deleted source's details and lookup rows
whose key no remaining ``entries`` row of the family has. A row that another source's entry
still reaches (a key two callers shared, as before #658) is kept. Other sources' and other
families' rows are never touched, and every value is bound.

The storage functions run against an in-memory SQLite copy of the SV tables, so each
statement's predicate, subquery included, is evaluated rather than string-matched.
``test_e2e_sv_source_delete_leaves_no_orphans`` shows the same against real ClickHouse.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

import pytest

from backend.app.core.config import settings
from backend.app.services import clickhouse_variant_storage as cvs
from backend.app.services.clickhouse_variant_ids import structural_variant_key
from backend.app.services.clickhouse_variant_records import (
    StoredStructuralVariantRow,
    StructuralVariantCall,
    StructuralVariantRecord,
)

_ASSEMBLY = "GRCh38"
_FAMILY = "family-a"
_OTHER_FAMILY = "family-b"
_PROJECT = "project-1"
# A per-sample upload's ids: they do not name the caller.
_DEL = "1-30000-31000-DEL---"
_DUP = "1-50000-50500-DUP---"
_NEEDLR = "SVDEL1"

_SV_TABLES = ("SV/entries", "SV/variants/details", "SV/key_lookup")
# The columns the storage module's statements write, per table.
_COLUMNS: dict[str, tuple[str, ...]] = {
    "SV/entries": (
        "key",
        "variantId",
        "project_guid",
        "family_guid",
        "sample_type",
        "chrom",
        "start",
        "end",
        "svType",
        "source",
        "gene_symbols",
        "calls.sampleId",
        "calls.gt",
        "calls.qual",
        "calls.readSupport",
        "calls.filter",
        "calls.ps",
        "calls.cn",
        "sign",
    ),
    "SV/variants/details": (
        "key",
        "variantId",
        "family_guid",
        "chrom",
        "start",
        "end",
        "svType",
        "source",
        "remoteChrom",
        "remoteStart",
        "remoteEnd",
        "svLen",
        "filters",
        "annotationsJson",
    ),
    "SV/key_lookup": ("family_guid", "variantId", "source", "key"),
    "SV/family_data_version": ("family_guid", "token"),
}

_INSERT = re.compile(r"INSERT INTO (?P<table>\S+) \((?P<columns>[^)]*)\) VALUES")
_DELETE = re.compile(
    r"ALTER TABLE (?P<table>\S+) DELETE WHERE (?P<where>.+) SETTINGS mutations_sync = 1"
)
_PLACEHOLDER = re.compile(r"%\((\w+)\)s")


def _stored(value: Any) -> Any:
    """A value as SQLite holds it: an array as JSON, a UInt64 beyond SQLite's integers as text."""
    if isinstance(value, (list, tuple)):
        return json.dumps(list(value))
    if isinstance(value, int) and not isinstance(value, bool) and value >= 2**63:
        return str(value)
    return value


class _SvTables:
    """The per-assembly SV tables in SQLite, written through the storage module's ``_execute``.

    It runs the two statement shapes the SV writes use: an ``INSERT … VALUES`` with its rows,
    and an ``ALTER TABLE … DELETE WHERE … SETTINGS mutations_sync = 1``, run as a ``DELETE``
    with each ``%(name)s`` bound as ``:name``. Any other statement fails the test.
    """

    def __init__(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.execute(f"ATTACH DATABASE ':memory:' AS \"{settings.clickhouse_database}\"")
        for suffix, columns in _COLUMNS.items():
            self.db.execute(
                f"CREATE TABLE {self.table(suffix)} ({', '.join(f'`{c}`' for c in columns)})"
            )
        self.statements: list[tuple[str, dict[str, Any] | None]] = []

    @staticmethod
    def table(suffix: str) -> str:
        return cvs._structural_table_name(_ASSEMBLY, suffix.removeprefix("SV/"))

    async def execute(
        self, query: str, params: dict[str, Any] | None = None, data: Any = None
    ) -> None:
        sql = " ".join(query.split())
        self.statements.append((sql, params))
        insert = _INSERT.fullmatch(sql)
        if insert is not None and data is not None:
            placeholders = ", ".join("?" for _ in insert["columns"].split(","))
            self.db.executemany(
                f"{sql} ({placeholders})", [tuple(_stored(value) for value in row) for row in data]
            )
            return None
        delete = _DELETE.fullmatch(sql)
        assert delete is not None, f"a statement the SQLite stand-in does not run: {sql}"
        where = _PLACEHOLDER.sub(r":\1", delete["where"])
        self.db.execute(f"DELETE FROM {delete['table']} WHERE {where}", params or {})
        return None

    def rows(self, suffix: str) -> list[tuple[Any, ...]]:
        """Every stored row of the table, all columns, the key as an integer, in a fixed order."""
        columns = _COLUMNS[suffix]
        key_index = columns.index("key")
        rows = [
            tuple(int(value) if index == key_index else value for index, value in enumerate(row))
            for row in self.db.execute(f"SELECT * FROM {self.table(suffix)}").fetchall()
        ]
        return sorted(rows, key=repr)

    def identities(self, suffix: str) -> list[tuple[str, str, str, int]]:
        """``(family, source, variant id, key)`` per stored row of the table."""
        columns = _COLUMNS[suffix]
        at = {name: columns.index(name) for name in ("family_guid", "source", "variantId", "key")}
        return sorted(
            (row[at["family_guid"]], row[at["source"]], row[at["variantId"]], row[at["key"]])
            for row in self.rows(suffix)
        )

    def unreached(self) -> dict[str, list[tuple[str, str, str, int]]]:
        """The details and lookup rows whose key no entry of their family has."""
        reached = {(family, key) for family, _source, _id, key in self.identities("SV/entries")}
        return {
            suffix: [row for row in self.identities(suffix) if (row[0], row[3]) not in reached]
            for suffix in ("SV/variants/details", "SV/key_lookup")
        }


@pytest.fixture
def tables(monkeypatch: pytest.MonkeyPatch) -> _SvTables:
    fake = _SvTables()

    async def tables_ready(_assembly_name: str) -> None:
        return None

    monkeypatch.setattr(cvs, "ensure_clickhouse_variant_tables", tables_ready)
    monkeypatch.setattr(cvs, "_execute", fake.execute)
    return fake


def _record(
    variant_id: str,
    source: str,
    *,
    start: int,
    end: int,
    sv_type: str = "DEL",
    samples: tuple[str, ...] = ("PROBAND",),
    variant_key: int | None = None,
) -> StructuralVariantRecord:
    return StructuralVariantRecord(
        variant_key=variant_key,
        variant_id=variant_id,
        chr="1",
        start=start,
        end=end,
        sv_type=sv_type,
        source=source,
        remote_chr=None,
        remote_start=None,
        remote_end=None,
        sv_len=start - end if sv_type == "DEL" else end - start,
        filters=["PASS"],
        gene_symbols=["GENE1"],
        annotations=[{"info": {"SVTYPE": sv_type, "CALLER": source}}],
        calls=[
            StructuralVariantCall(sample=sample, gt="0/1", qual=40.0, read_support=9, filter="PASS")
            for sample in samples
        ],
    )


def _sniffles_del(**kwargs: Any) -> StructuralVariantRecord:
    return _record(_DEL, "sniffles", start=30000, end=31000, **kwargs)


def _sniffles_dup() -> StructuralVariantRecord:
    return _record(_DUP, "sniffles", start=50000, end=50500, sv_type="DUP")


def _spectre_del(**kwargs: Any) -> StructuralVariantRecord:
    return _record(_DEL, "spectre", start=30000, end=31000, **kwargs)


async def _store_two_families() -> None:
    """Family A: a Sniffles deletion and duplication, a Spectre call of the same deletion
    (same id, its own key) and a NeedlR deletion. Family B: the same two Sniffles SVs."""
    await cvs.insert_structural_variant_records(
        _ASSEMBLY, _FAMILY, [_PROJECT], [_sniffles_del(), _sniffles_dup()]
    )
    await cvs.insert_structural_variant_records(_ASSEMBLY, _FAMILY, [_PROJECT], [_spectre_del()])
    await cvs.insert_structural_variant_records(
        _ASSEMBLY,
        _FAMILY,
        [_PROJECT],
        [_record(_NEEDLR, "needlr", start=20000, end=25000, samples=("FATHER", "PROBAND"))],
    )
    await cvs.insert_structural_variant_records(
        _ASSEMBLY, _OTHER_FAMILY, [_PROJECT], [_sniffles_del(), _sniffles_dup()]
    )


def _rows_not_of(
    suffix: str, rows: list[tuple[Any, ...]], family: str, source: str
) -> list[tuple[Any, ...]]:
    """The table's rows that are not ``source``'s rows of ``family``."""
    columns = _COLUMNS[suffix]
    at_family, at_source = columns.index("family_guid"), columns.index("source")
    return [row for row in rows if (row[at_family], row[at_source]) != (family, source)]


@pytest.mark.asyncio
async def test_the_stand_in_starts_with_one_row_per_caller_in_each_table(tables) -> None:
    await _store_two_families()
    for suffix in _SV_TABLES:
        assert [row[:3] for row in tables.identities(suffix)] == [
            (_FAMILY, "needlr", _NEEDLR),
            (_FAMILY, "sniffles", _DEL),
            (_FAMILY, "sniffles", _DUP),
            (_FAMILY, "spectre", _DEL),
            (_OTHER_FAMILY, "sniffles", _DEL),
            (_OTHER_FAMILY, "sniffles", _DUP),
        ], suffix
    assert tables.unreached() == {"SV/variants/details": [], "SV/key_lookup": []}


@pytest.mark.asyncio
async def test_a_source_delete_leaves_no_row_of_that_source(tables) -> None:
    await _store_two_families()
    await cvs.delete_family_structural_variants(_ASSEMBLY, _FAMILY, source="sniffles")
    # Before: the entries went, and both SVs' details and lookup rows stayed.
    for suffix in _SV_TABLES:
        left = [row for row in tables.identities(suffix) if row[:2] == (_FAMILY, "sniffles")]
        assert left == [], suffix


@pytest.mark.asyncio
async def test_no_details_or_lookup_row_is_left_that_no_entry_reaches(tables) -> None:
    await _store_two_families()
    await cvs.delete_family_structural_variants(_ASSEMBLY, _FAMILY, source="sniffles")
    assert tables.unreached() == {"SV/variants/details": [], "SV/key_lookup": []}


@pytest.mark.asyncio
async def test_other_sources_and_other_families_keep_every_row_as_stored(tables) -> None:
    await _store_two_families()
    before = {suffix: tables.rows(suffix) for suffix in _SV_TABLES}
    await cvs.delete_family_structural_variants(_ASSEMBLY, _FAMILY, source="sniffles")
    for suffix in _SV_TABLES:
        others = _rows_not_of(suffix, before[suffix], _FAMILY, "sniffles")
        # Every column of every other row: the Spectre call of the same deletion keeps its
        # span, length and annotation, and family B keeps its Sniffles rows.
        assert _rows_not_of(suffix, tables.rows(suffix), _FAMILY, "sniffles") == others, suffix
        assert others, suffix


@pytest.mark.asyncio
async def test_a_row_another_sources_entry_still_reaches_is_kept(tables) -> None:
    # Before #658 two callers' calls at one position had one key; a row written back keeps
    # its stored key. ClickHouse keeps one details row per key, which may be the one labelled
    # with the deleted source: removing it would strip the Spectre SV of its span, length and
    # annotation.
    shared = 4242
    await cvs.insert_structural_variant_records(
        _ASSEMBLY, _FAMILY, [_PROJECT], [_sniffles_del(variant_key=shared)]
    )
    await cvs.insert_structural_variant_records(
        _ASSEMBLY, _FAMILY, [_PROJECT], [_spectre_del(variant_key=shared)]
    )
    await cvs.delete_family_structural_variants(_ASSEMBLY, _FAMILY, source="sniffles")

    assert tables.identities("SV/entries") == [(_FAMILY, "spectre", _DEL, shared)]
    for suffix in ("SV/variants/details", "SV/key_lookup"):
        assert tables.identities(suffix) == [
            (_FAMILY, "sniffles", _DEL, shared),
            (_FAMILY, "spectre", _DEL, shared),
        ], suffix


@pytest.mark.asyncio
async def test_an_overwrite_that_drops_an_sv_leaves_no_row_for_it(tables) -> None:
    # The per-sample upload's overwrite: the source's stored rows are rewritten, each with
    # its stored key, without the duplication the new file no longer has.
    await _store_two_families()
    stored_key = structural_variant_key(_ASSEMBLY, _FAMILY, _DEL, source="sniffles")
    await cvs.rewrite_family_structural_variants(
        _ASSEMBLY,
        _FAMILY,
        [StoredStructuralVariantRow(project_id=_PROJECT, record=_sniffles_del(variant_key=stored_key))],
        source="sniffles",
    )
    for suffix in _SV_TABLES:
        sniffles = [row for row in tables.identities(suffix) if row[:2] == (_FAMILY, "sniffles")]
        # Before: the duplication kept its details and lookup row, and the deletion had two
        # of each (the replaced ones beside the new ones) until ClickHouse merged the parts.
        assert sniffles == [(_FAMILY, "sniffles", _DEL, stored_key)], suffix
    assert tables.unreached() == {"SV/variants/details": [], "SV/key_lookup": []}


@pytest.mark.asyncio
async def test_a_family_delete_clears_that_family_from_every_table(tables) -> None:
    await _store_two_families()
    before = {suffix: tables.identities(suffix) for suffix in _SV_TABLES}
    await cvs.delete_family_structural_variants(_ASSEMBLY, _FAMILY)
    for suffix in _SV_TABLES:
        assert tables.identities(suffix) == [
            row for row in before[suffix] if row[0] == _OTHER_FAMILY
        ], suffix


@pytest.mark.asyncio
async def test_the_delete_binds_every_value_and_stamps_the_version_last(tables) -> None:
    family, source = "family-'a", "sniff'les"
    await cvs.insert_structural_variant_records(
        _ASSEMBLY, family, [_PROJECT], [_record(_DEL, source, start=30000, end=31000)]
    )
    tables.statements.clear()
    await cvs.delete_family_structural_variants(_ASSEMBLY, family, source=source)

    deletes = [(sql, params) for sql, params in tables.statements if sql.startswith("ALTER TABLE")]
    # The entries first, so the details and lookup deletes see only the remaining entries.
    assert [re.search(r"/SV/([^`]+)`", sql)[1] for sql, _params in deletes] == [
        "entries",
        "variants/details",
        "key_lookup",
    ]
    for sql, params in deletes:
        assert "'" not in sql, sql
        assert params == {"family_guid": family, "source": source}
    assert "SV/family_data_version" in tables.statements[-1][0]
    for suffix in _SV_TABLES:
        assert tables.identities(suffix) == [], suffix
