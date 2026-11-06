"""The variant summary's statistics (PROD-1), without a database.

The SQL is checked against real ClickHouse in
``integration/test_family_variant_statistics_clickhouse.py``; this covers what Python
decides: the Mendelian rule over every trio of dosages, the histogram bins, the consequence
ranks, which members form a trio, and each member's structural variants by type. It also
checks that the statistics read no family's data without the family's assembly.

Synthetic data only.
"""

from __future__ import annotations

import asyncio
from itertools import product
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.services import family_service
from backend.app.services import family_variant_statistics as statistics
from backend.app.services.clickhouse_variant_records import StructuralVariantCall, StructuralVariantRecord
from backend.app.services.family_metadata_context import FamilyMetadataContext


def _context(samples: list[str], relationships: list[dict[str, Any]], *, assembly: str | None = "GRCh38") -> FamilyMetadataContext:
    return FamilyMetadataContext(
        family_uuid="00000000-0000-4000-8000-0000000000f1",
        family_id="FAM1",
        project_ids=[],
        sample_rows=[{"sample_id": sample, "role": "", "sex": ""} for sample in samples],
        sample_uuid_to_name={},
        sample_name_to_uuid={sample: sample for sample in samples},
        affected_sample_names=[],
        assembly_id=None,
        assembly_name=assembly,
        relationship_rows=relationships,
    )


def _parents(child: str, father: str | None, mother: str | None) -> list[dict[str, Any]]:
    rows = []
    for parent, role in ((father, "father"), (mother, "mother")):
        if parent:
            rows.append({"relationship_type": "parent_child", "sample_id_a": parent, "sample_id_b": child, "role_a": role})
    return rows


@pytest.mark.parametrize(("child", "father", "mother"), list(product((0, 1, 2), repeat=3)))
def test_a_trio_is_inconsistent_exactly_when_no_transmission_gives_the_child(child: int, father: int, mother: int) -> None:
    transmits = {0: {0}, 1: {0, 1}, 2: {1}}
    consistent = any(a + b == child for a in transmits[father] for b in transmits[mother])
    verdict = statistics.mendelian_class(child, father, mother)
    assert (verdict is None) == consistent
    if verdict == "not_from_father":
        # The child holds no allele the father has, and one the mother has.
        assert not any(a in transmits[father] for a in ({0} if child == 0 else {1}))
        assert any(b in transmits[mother] for b in ({0} if child == 0 else {1}))


def test_the_rule_names_each_parent_whose_allele_is_missing() -> None:
    assert statistics.mendelian_class(2, 0, 1) == "not_from_father"
    assert statistics.mendelian_class(0, 1, 2) == "not_from_mother"
    assert statistics.mendelian_class(2, 0, 0) == "from_neither"
    assert statistics.mendelian_class(1, 0, 0) == "from_neither"
    assert statistics.mendelian_class(1, 2, 2) == "from_neither"
    assert statistics.mendelian_class(1, 2, 0) is None


def test_histogram_bins_by_lower_edge_with_an_open_last_bin_and_missing_values() -> None:
    bins, missing = statistics.histogram_bins({-1.0: 4, 0.0: 1, 9.0: 2, 10.0: 3, 95.0: 5, 99.0: 6}, (0, 10, 90))
    assert [(row.low, row.count) for row in bins] == [(0.0, 3), (10.0, 3), (90.0, 11)]
    assert missing == 4
    scaled, _ = statistics.histogram_bins({42.0: 1, 100.0: 2}, statistics.ALLELE_BALANCE_EDGES, scale=100.0)
    assert {row.low: row.count for row in scaled if row.count} == {0.4: 1, 0.95: 2}
    assert len(scaled) == 20 and scaled[1].low == 0.05


def test_the_consequence_ranks_follow_veps_order_then_other_then_none() -> None:
    order = statistics.CONSEQUENCE_ORDER
    assert order.index("stop_gained") < order.index("missense_variant") < order.index("synonymous_variant")
    assert order.index("synonymous_variant") < order.index("intron_variant") < order.index("intergenic_variant")
    assert all(term == term.lower() for term in order) and len(set(order)) == len(order)
    assert [statistics.consequence_for_rank(rank) for rank in (1, len(order))] == ["transcript_ablation", "sequence_variant"]
    assert statistics.consequence_for_rank(len(order) + 1) == "other"
    assert statistics.consequence_for_rank(len(order) + 2) == "none"
    assert statistics.consequence_for_rank(0) == "none"


def test_a_trio_is_each_member_with_both_parents_in_the_family() -> None:
    relationships = [
        *_parents("PROBAND", "FATHER", "MOTHER"),
        *_parents("SIB", "FATHER", "MOTHER"),
        *_parents("HALFSIB", "FATHER1", "MOTHER"),
        *_parents("FATHER", "GRANDFATHER", None),
    ]
    context = _context(["FATHER", "HALFSIB", "MOTHER", "PROBAND", "SIB", "GRANDFATHER"], relationships)
    assert statistics.family_trios(context) == [
        ("PROBAND", "FATHER", "MOTHER"),
        ("SIB", "FATHER", "MOTHER"),
    ]


def test_without_an_assembly_nothing_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _no_query(*_args: Any, **_kwargs: Any) -> list[Any]:
        raise AssertionError("no query without an assembly")

    monkeypatch.setattr(statistics, "_execute_clickhouse", _no_query)
    out = asyncio.run(statistics.get_family_small_variant_statistics(_context(["PROBAND"], [], assembly=None)))
    assert out.samples == [] and out.trios == [] and out.sources == []


def test_a_family_with_imputed_genotypes_alone_reads_no_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    queries: list[str] = []

    async def _sources(query: str, _params: dict[str, Any]) -> list[Any]:
        queries.append(query)
        return [("glimpse2",)]

    monkeypatch.setattr(statistics, "_execute_clickhouse", _sources)
    out = asyncio.run(statistics.get_family_small_variant_statistics(_context(["PROBAND"], [])))
    assert out.imputed_sources == ["glimpse2"] and out.sources == [] and out.samples == []
    assert len(queries) == 1 and "DISTINCT source" in queries[0]


def _sv(variant_id: str, sv_type: str, calls: dict[str, str]) -> StructuralVariantRecord:
    return StructuralVariantRecord(
        variant_key=None, variant_id=variant_id, chr="1", start=1_000, end=2_000, sv_type=sv_type, source="sniffles",
        remote_chr=None, remote_start=None, remote_end=None, sv_len=1_000, filters=[], gene_symbols=[], annotations=[],
        calls=[StructuralVariantCall(sample=s, gt=gt, qual=None, read_support=None, filter=None) for s, gt in calls.items()],
    )


def test_each_member_counts_the_structural_variants_whose_call_carries_the_alt_allele(monkeypatch: pytest.MonkeyPatch) -> None:
    context = SimpleNamespace(sample_rows=[{"sample_id": "PROBAND"}, {"sample_id": "MOTHER"}, {"sample_id": "FATHER"}])

    async def _context_for(_session: Any, **_kwargs: Any) -> Any:
        return context

    async def _rows(_context: Any, _filters: Any, **_kwargs: Any) -> list[StructuralVariantRecord]:
        return [
            _sv("sv1", "DEL", {"PROBAND": "0/1", "MOTHER": "0/1", "FATHER": "0/0"}),
            _sv("sv2", "DEL", {"PROBAND": "1/1"}),
            _sv("sv3", "DUP", {"PROBAND": "./.", "MOTHER": "1", "OTHER": "0/1"}),
            _sv("sv4", "", {"FATHER": "0/1"}),
        ]

    monkeypatch.setattr(family_service, "build_family_metadata_context", _context_for)
    monkeypatch.setattr(family_service, "_fetch_structural_variant_rows", _rows)
    out = asyncio.run(
        family_service.get_family_structural_variant_sample_counts_for_user(
            None,  # type: ignore[arg-type]
            family_id="FAM1",
            user=None,  # type: ignore[arg-type]
        )
    )
    assert [(row.sample_id, row.total, row.by_type) for row in out] == [
        ("PROBAND", 2, {"DEL": 2}),
        ("MOTHER", 2, {"DEL": 1, "DUP": 1}),
        ("FATHER", 1, {"unknown": 1}),
    ]
