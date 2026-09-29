"""Tests for trio de novo detection in small-variant segregation."""

from types import SimpleNamespace

from backend.app.services.clickhouse_variant_queries import (
    _call_is_confident_hom_ref,
    _child_parent_map,
    _record_matches_de_novo,
)
from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord


def _call(sample: str, gt: str, dp: int | None = 30) -> SmallVariantCall:
    return SmallVariantCall(sample=sample, gt=gt, gq=99, dp=dp, af=[], ad=[], ps=None)


def _record(calls: list[SmallVariantCall], chrom: str = "1") -> SmallVariantRecord:
    return SmallVariantRecord(
        variant_key=1,
        variant_id="v1",
        chr=chrom,
        start=100,
        end=100,
        ref="A",
        alt="G",
        source=None,
        rsid=None,
        filters=[],
        gene_symbols=["GENE1"],
        annotations=[],
        calls=calls,
    )


_TRIO_CONTEXT = SimpleNamespace(
    relationship_rows=[
        {"relationship_type": "parent_child", "sample_id_a": "FA", "role_a": "father",
         "sample_id_b": "CH", "role_b": "child"},
        {"relationship_type": "parent_child", "sample_id_a": "MO", "role_a": "mother",
         "sample_id_b": "CH", "role_b": "child"},
        {"relationship_type": "couple", "sample_id_a": "FA", "role_a": "partner",
         "sample_id_b": "MO", "role_b": "partner"},
    ]
)


def test_child_parent_map_extracts_both_parents() -> None:
    assert _child_parent_map(_TRIO_CONTEXT) == {"CH": {"FA", "MO"}}


def test_de_novo_when_child_het_and_both_parents_hom_ref() -> None:
    record = _record([_call("CH", "0/1"), _call("FA", "0/0"), _call("MO", "0/0")])
    assert _record_matches_de_novo(
        record, affected_samples=["CH"], child_parents={"CH": {"FA", "MO"}}
    )


def test_not_de_novo_when_a_parent_carries_the_alt() -> None:
    record = _record([_call("CH", "0/1"), _call("FA", "0/1"), _call("MO", "0/0")])
    assert not _record_matches_de_novo(
        record, affected_samples=["CH"], child_parents={"CH": {"FA", "MO"}}
    )


def test_not_de_novo_when_a_parent_is_missing_genotype() -> None:
    # Cannot exclude inheritance from an uncalled parent.
    record = _record([_call("CH", "0/1"), _call("FA", "./."), _call("MO", "0/0")])
    assert not _record_matches_de_novo(
        record, affected_samples=["CH"], child_parents={"CH": {"FA", "MO"}}
    )


def test_not_de_novo_without_full_trio() -> None:
    record = _record([_call("CH", "0/1"), _call("MO", "0/0")])
    assert not _record_matches_de_novo(
        record, affected_samples=["CH"], child_parents={"CH": {"MO"}}
    )


def test_hom_alt_child_with_ref_parents_is_not_de_novo() -> None:
    # Biologically implausible -> treated as a (likely-artifact) recessive call, not de novo.
    record = _record([_call("CH", "1/1"), _call("FA", "0/0"), _call("MO", "0/0")])
    assert not _record_matches_de_novo(
        record, affected_samples=["CH"], child_parents={"CH": {"FA", "MO"}}
    )


def test_low_coverage_reference_parent_is_not_confident() -> None:
    assert _call_is_confident_hom_ref(_call("FA", "0/0", dp=30))
    assert not _call_is_confident_hom_ref(_call("FA", "0/0", dp=3))
    assert not _call_is_confident_hom_ref(_call("FA", "./.", dp=30))
    # Depth unknown is accepted (some callers omit DP).
    assert _call_is_confident_hom_ref(_call("FA", "0/0", dp=None))
