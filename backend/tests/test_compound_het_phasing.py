"""Compound-het pairing takes read-backed phasing into account.

Two heterozygous variants in one gene are only a recessive explanation if they sit on
opposite haplotypes. A long-read caller says so directly: variants sharing a phase set
(PS) carry haplotype-resolved genotypes, so ``0|1`` against ``1|0`` is in trans and
``0|1`` against ``0|1`` is in cis. A cis pair leaves one intact copy of the gene, so it
is not a candidate at all.
"""

import pytest

from backend.app.services.clickhouse_variant_queries import (
    _compound_het_pair_phase,
    _compound_het_pairs,
    _phased_alt_haplotype,
)
from backend.app.services.clickhouse_variant_records import (
    SmallVariantCall,
    SmallVariantRecord,
)

AFFECTED = ["PROBAND"]
UNAFFECTED = ["MOTHER"]


def _call(sample: str, gt: str, ps: int | None = None) -> SmallVariantCall:
    return SmallVariantCall(sample=sample, gt=gt, gq=None, dp=None, af=[], ad=[], ps=ps)


def _variant(variant_id: str, *, calls: list[SmallVariantCall], start: int = 100) -> SmallVariantRecord:
    return SmallVariantRecord(
        variant_key=None,
        variant_id=variant_id,
        chr="1",
        start=start,
        end=start,
        ref="A",
        alt="G",
        source="deepvariant",
        rsid=None,
        filters=[],
        gene_symbols=["TRNT1"],
        annotations=[{"gene": "TRNT1"}],
        calls=calls,
    )


def _phase(left_gt: str, right_gt: str, *, left_ps=None, right_ps=None) -> str | None:
    return _compound_het_pair_phase(
        _variant("left", calls=[_call("PROBAND", left_gt, left_ps)], start=100),
        _variant("right", calls=[_call("PROBAND", right_gt, right_ps)], start=200),
        affected_samples=AFFECTED,
        unaffected_samples=[],
    )


class TestPhasedAltHaplotype:
    def test_places_the_alt_on_its_haplotype(self) -> None:
        assert _phased_alt_haplotype("0|1") == 1
        assert _phased_alt_haplotype("1|0") == 0

    def test_declines_an_unphased_call(self) -> None:
        # `0/1` is het but says nothing about which haplotype carries the alt.
        assert _phased_alt_haplotype("0/1") is None

    def test_declines_a_homozygous_call(self) -> None:
        # A hom call is on both haplotypes, which is why the caller emits no phase set.
        assert _phased_alt_haplotype("1|1") is None
        assert _phased_alt_haplotype("0|0") is None

    def test_declines_a_multiallelic_call_with_alt_on_both_haplotypes(self) -> None:
        assert _phased_alt_haplotype("1|2") is None

    def test_declines_a_no_call_or_junk(self) -> None:
        assert _phased_alt_haplotype(".|1") is None
        assert _phased_alt_haplotype(None) is None
        assert _phased_alt_haplotype("") is None


class TestPairPhase:
    def test_opposite_haplotypes_in_one_phase_set_are_trans(self) -> None:
        assert _phase("0|1", "1|0", left_ps=2803880, right_ps=2803880) == "trans"

    def test_same_haplotype_in_one_phase_set_is_not_a_candidate(self) -> None:
        # The whole point: cis is dropped, not merely labelled.
        assert _phase("0|1", "0|1", left_ps=2803880, right_ps=2803880) is None
        assert _phase("1|0", "1|0", left_ps=2803880, right_ps=2803880) is None

    def test_different_phase_sets_leave_the_pair_unresolved(self) -> None:
        # Haplotype indices are only comparable inside one phase block.
        assert _phase("0|1", "0|1", left_ps=2803880, right_ps=9999999) == "unknown"

    def test_a_missing_phase_set_leaves_the_pair_unresolved(self) -> None:
        assert _phase("0|1", "1|0", left_ps=2803880, right_ps=None) == "unknown"
        assert _phase("0/1", "0/1") == "unknown"

    def test_an_unphased_genotype_is_never_resolved_even_within_a_phase_set(self) -> None:
        assert _phase("0/1", "1|0", left_ps=2803880, right_ps=2803880) == "unknown"

    def test_a_homozygous_call_is_not_a_compound_het_candidate(self) -> None:
        # The TRNT1 case: hom calls are unphased because they are on both haplotypes.
        assert _phase("1/1", "1/1") is None
        assert _phase("1/1", "0/1") is None


class TestPairingRules:
    def test_an_unaffected_carrier_of_both_still_rules_the_pair_out(self) -> None:
        left = _variant("left", calls=[_call("PROBAND", "0|1", 1), _call("MOTHER", "0/1")], start=100)
        right = _variant("right", calls=[_call("PROBAND", "1|0", 1), _call("MOTHER", "0/1")], start=200)
        assert (
            _compound_het_pair_phase(
                left, right, affected_samples=AFFECTED, unaffected_samples=UNAFFECTED
            )
            is None
        )

    def test_cis_in_any_affected_sample_rules_the_pair_out(self) -> None:
        # Two affected siblings: trans in one, cis in the other. The cis sibling holds an
        # intact copy, so the pair cannot explain a recessive phenotype in both.
        left = _variant("left", calls=[_call("SIB1", "0|1", 5), _call("SIB2", "0|1", 7)], start=100)
        right = _variant("right", calls=[_call("SIB1", "1|0", 5), _call("SIB2", "0|1", 7)], start=200)
        assert (
            _compound_het_pair_phase(
                left, right, affected_samples=["SIB1", "SIB2"], unaffected_samples=[]
            )
            is None
        )

    def test_trans_in_one_sibling_and_unresolved_in_the_other_is_kept_as_trans(self) -> None:
        left = _variant("left", calls=[_call("SIB1", "0|1", 5), _call("SIB2", "0/1")], start=100)
        right = _variant("right", calls=[_call("SIB1", "1|0", 5), _call("SIB2", "0/1")], start=200)
        assert (
            _compound_het_pair_phase(
                left, right, affected_samples=["SIB1", "SIB2"], unaffected_samples=[]
            )
            == "trans"
        )

    def test_no_affected_sample_means_no_pairing(self) -> None:
        assert (
            _compound_het_pair_phase(
                _variant("left", calls=[_call("PROBAND", "0|1", 1)]),
                _variant("right", calls=[_call("PROBAND", "1|0", 1)], start=200),
                affected_samples=[],
                unaffected_samples=[],
            )
            is None
        )


class TestPairsInAGene:
    def test_cis_pairs_are_dropped_and_trans_pairs_are_labelled(self) -> None:
        # Three het variants in one phase set: A and B on one haplotype, C on the other.
        # A-B is cis and must not be reported; A-C and B-C are trans.
        records = [
            _variant("A", calls=[_call("PROBAND", "0|1", 42)], start=100),
            _variant("B", calls=[_call("PROBAND", "0|1", 42)], start=200),
            _variant("C", calls=[_call("PROBAND", "1|0", 42)], start=300),
        ]
        pairs = _compound_het_pairs(records, affected_samples=AFFECTED, unaffected_samples=[])

        assert {(pair.left.variant_id, pair.right.variant_id) for pair in pairs} == {
            ("A", "C"),
            ("B", "C"),
        }
        assert {pair.phase for pair in pairs} == {"trans"}

    def test_unphased_pairs_are_kept_as_unknown(self) -> None:
        records = [
            _variant("A", calls=[_call("PROBAND", "0/1")], start=100),
            _variant("B", calls=[_call("PROBAND", "0/1")], start=200),
        ]
        pairs = _compound_het_pairs(records, affected_samples=AFFECTED, unaffected_samples=[])

        assert len(pairs) == 1
        assert pairs[0].phase == "unknown"


@pytest.mark.parametrize(
    ("left_gt", "right_gt", "expected"),
    [
        ("0|1", "1|0", "trans"),
        ("1|0", "0|1", "trans"),
        ("0|1", "0|1", None),
        ("1|0", "1|0", None),
    ],
)
def test_phase_is_symmetric(left_gt: str, right_gt: str, expected: str | None) -> None:
    assert _phase(left_gt, right_gt, left_ps=1, right_ps=1) == expected
