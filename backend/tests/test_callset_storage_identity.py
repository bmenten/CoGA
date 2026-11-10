"""A stored variant row is one callset's (unit).

A small variant's storage key is the variant (``hash(assembly, variantId)``), shared by
every family and callset so they all find the one annotation. Sorted by
``(project, family, position, key)`` only, a family's clair3 row and GLIMPSE2 row for one
variant would share a sort key, and the CollapsingMergeTree would keep one of them when
ClickHouse merged the parts. The ``entries`` row identity ends with the callset (``source``).

A per-sample upload's SV id (``chrom-start-end-type---``) names no caller, so a key hashing
only the family and the variant id would give a Sniffles and a Spectre call at the same
coordinates one key, one ``entries`` sort key, one ``variants/details`` row and one
``key_lookup`` row. The key hashes the source too; the variant id does not, since reviews,
drift snapshots, the ranking cache and the SV gene index attach by it.
``e2e/test_e2e_callset_storage_identity.py`` shows the same on real ClickHouse, with a forced
part merge.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.app.services import clickhouse_small_variants
from backend.app.services import clickhouse_variant_storage as cvs
from backend.app.services.clickhouse_variant_ids import (
    small_variant_key,
    structural_variant_key,
)
from backend.app.services.clickhouse_variant_queries import IMPUTED_SMALL_VARIANT_SOURCES
from backend.app.services.clickhouse_variant_records import (
    SmallVariantCall,
    SmallVariantRecord,
    StructuralVariantCall,
    StructuralVariantRecord,
)
from backend.app.services.clickhouse_variant_rows import (
    _small_variant_entry_rows,
    _structural_variant_entry_rows,
)

_SMALL_KEY = "project_guid, family_guid, xpos, key, source"
_SV_KEY = "project_guid, family_guid, svType, chrom, start, key, source"
_LOOKUP_KEY = "family_guid, variantId, source"


def _small(source: str, gt: str) -> SmallVariantRecord:
    return SmallVariantRecord(
        variant_key=None,
        variant_id="1-3000-G-A",
        chr="1",
        start=3000,
        end=3000,
        ref="G",
        alt="A",
        source=source,
        rsid=None,
        filters=[],
        gene_symbols=["GENE_DENOVO"],
        annotations=[{"gene": "GENE_DENOVO", "impact": "MODERATE"}],
        calls=[SmallVariantCall(sample="PROBAND", gt=gt, gq=None, dp=None, af=[], ad=[], ps=None)],
    )


def _sv(source: str, *, variant_key: int | None = None) -> StructuralVariantRecord:
    return StructuralVariantRecord(
        variant_key=variant_key,
        variant_id="1-30000-31000-DEL---",
        chr="1",
        start=30000,
        end=31000,
        sv_type="DEL",
        source=source,
        remote_chr=None,
        remote_start=None,
        remote_end=None,
        sv_len=-1000,
        filters=["PASS"],
        gene_symbols=[],
        annotations=[],
        calls=[StructuralVariantCall(sample="PROBAND", gt="0/1", qual=40.0, read_support=9, filter="PASS")],
    )


def _capture_ddl(monkeypatch) -> list[str]:
    """Run the real table ensure over a fake ClickHouse; return every statement it issued."""
    statements: list[str] = []

    async def fake_execute(query, params=None, data=None):
        statements.append(" ".join(query.split()))
        return []

    monkeypatch.setattr(cvs, "_execute", fake_execute)
    monkeypatch.setattr(cvs, "_ensured_variant_table_assemblies", set())
    return statements


def _create_statement(statements: list[str], table: str) -> str:
    matches = [s for s in statements if s.startswith("CREATE TABLE IF NOT EXISTS") and f"`{table}`" in s]
    assert len(matches) == 1, matches
    return matches[0]


# --- The row identity in the table definitions -------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("table", "sort_key"),
    [
        ("GRCh38/SNV_INDEL/entries", _SMALL_KEY),
        ("GRCh38/SV/entries", _SV_KEY),
    ],
    ids=["small-variants", "structural-variants"],
)
async def test_the_entries_are_sorted_by_variant_and_callset(monkeypatch, table: str, sort_key: str) -> None:
    statements = _capture_ddl(monkeypatch)
    await cvs.ensure_clickhouse_variant_tables("GRCh38")
    ddl = _create_statement(statements, table)
    assert f"ORDER BY ({sort_key})" in ddl
    assert "ENGINE = CollapsingMergeTree(sign)" in ddl


@pytest.mark.asyncio
async def test_sv_key_lookup_keeps_one_row_per_variant_and_source(monkeypatch) -> None:
    statements = _capture_ddl(monkeypatch)
    await cvs.ensure_clickhouse_variant_tables("GRCh38")
    ddl = _create_statement(statements, "GRCh38/SV/key_lookup")
    assert "`source` LowCardinality(String)" in ddl
    assert f"ORDER BY ({_LOOKUP_KEY})" in ddl


# --- The keys -------------------------------------------------------------------------------


def test_a_small_variant_has_one_key_whatever_the_callset() -> None:
    direct = _small("clair3", "0/1")
    imputed = _small("glimpse2", "1|1")
    _details, entries, _annotations, _index, _genes = _small_variant_entry_rows(
        "GRCh38", "fam-1", ["p1"], [direct, imputed]
    )
    # key, variantId and source are the first, second and twelfth entry columns.
    assert [(row[0], row[1], row[11]) for row in entries] == [
        (small_variant_key("GRCh38", "1-3000-G-A"), "1-3000-G-A", "clair3"),
        (small_variant_key("GRCh38", "1-3000-G-A"), "1-3000-G-A", "glimpse2"),
    ]


def test_an_sv_key_names_the_source_and_the_variant_id_does_not() -> None:
    sniffles = structural_variant_key("GRCh38", "fam-1", "1-30000-31000-DEL---", source="sniffles")
    spectre = structural_variant_key("GRCh38", "fam-1", "1-30000-31000-DEL---", source="spectre")
    assert sniffles != spectre
    # Stable: the same call always gets the same key.
    assert sniffles == structural_variant_key("GRCh38", "fam-1", "1-30000-31000-DEL---", source="sniffles")
    assert structural_variant_key("GRCh38", "fam-2", "1-30000-31000-DEL---", source="sniffles") != sniffles

    details, lookups, entries = _structural_variant_entry_rows(
        "GRCh38", "fam-1", ["p1"], [_sv("sniffles"), _sv("spectre")]
    )
    # Each caller's row has its own key, in the details, the lookup and the entries.
    assert [row[0] for row in details] == [sniffles, spectre]
    assert [row[0] for row in entries] == [sniffles, spectre]
    assert [row[1] for row in entries] == ["1-30000-31000-DEL---", "1-30000-31000-DEL---"]
    assert [row[8] for row in entries] == ["sniffles", "spectre"]
    assert lookups == [
        ("fam-1", "1-30000-31000-DEL---", "sniffles", sniffles),
        ("fam-1", "1-30000-31000-DEL---", "spectre", spectre),
    ]


def test_a_stored_sv_row_written_back_keeps_its_key() -> None:
    # A rewrite (per-sample upload, admin delete) writes stored rows back under their key.
    details, lookups, entries = _structural_variant_entry_rows(
        "GRCh38", "fam-1", ["p1"], [_sv("sniffles", variant_key=12345)]
    )
    assert details[0][0] == entries[0][0] == lookups[0][3] == 12345


@pytest.mark.asyncio
async def test_the_key_lookup_insert_names_the_source(monkeypatch) -> None:
    issued: list[tuple[str, Any]] = []

    async def fake_execute(query, params=None, data=None):
        issued.append((" ".join(query.split()), data))
        return []

    async def fake_ensure(_assembly):
        return None

    monkeypatch.setattr(cvs, "_execute", fake_execute)
    monkeypatch.setattr(cvs, "ensure_clickhouse_variant_tables", fake_ensure)
    await cvs.insert_structural_variant_records("GRCh38", "fam-1", ["p1"], [_sv("spectre")])
    lookup = [(query, data) for query, data in issued if "SV/key_lookup" in query]
    assert len(lookup) == 1
    query, data = lookup[0]
    assert "(family_guid, variantId, source, key)" in query
    assert data == [
        (
            "fam-1",
            "1-30000-31000-DEL---",
            "spectre",
            structural_variant_key("GRCh38", "fam-1", "1-30000-31000-DEL---", source="spectre"),
        )
    ]


# --- One variant, several callsets: the per-variant lookup reads the direct call ---------------


@pytest.mark.asyncio
async def test_the_family_record_of_a_variant_is_its_direct_call(monkeypatch) -> None:
    # Reviews (the compound-het genotype checks) and the drift check read one record per
    # variant id. With a clair3 and a GLIMPSE2 row both stored, an unordered LIMIT 1
    # returned either; the direct call must win, then the callset name.
    captured: dict[str, Any] = {}

    async def fake_execute(query, params=None):
        captured["query"] = " ".join(query.split())
        captured["params"] = params
        return []

    monkeypatch.setattr(clickhouse_small_variants, "execute_clickhouse", fake_execute)
    await clickhouse_small_variants.get_small_variant_family_record(
        assembly_name="GRCh38", family_guid="fam-1", variant_id="1-3000-G-A"
    )
    query = captured["query"]
    assert "ORDER BY lowerUTF8(e.source) IN %(imputed_sources)s, e.source LIMIT 1" in query
    assert captured["params"] == {
        "family_guid": "fam-1",
        "variant_id": "1-3000-G-A",
        "imputed_sources": tuple(IMPUTED_SMALL_VARIANT_SOURCES),
    }
