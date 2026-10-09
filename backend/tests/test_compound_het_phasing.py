"""Compound-het pairing takes read-backed phasing into account.

Two heterozygous variants in one gene are only a recessive explanation if they sit on
opposite haplotypes. A long-read caller says so directly: variants sharing a phase set
(PS) carry haplotype-resolved genotypes, so ``0|1`` against ``1|0`` is in trans and
``0|1`` against ``0|1`` is in cis. A cis pair leaves one intact copy of the gene, so it
is not a candidate at all.

The SNV + SV second-hit badge reads a phased call by the same rule
(``compound_het_phase.phased_alt_haplotype``): ``TestBothPathsReadAPhasedCallAlike`` runs
each phased pair through both.
"""

import pytest

from backend.app.services.clickhouse_variant_queries import (
    _compound_het_pair_verdict,
    _compound_het_pairs,
)
from backend.app.services.clickhouse_variant_records import (
    SmallVariantCall,
    SmallVariantRecord,
)
from backend.app.services.compound_het_phase import phased_alt_haplotype
from backend.app.services.sv_gene_index_service import summarize_second_hit

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


def _compound_het_pair_phase(left: SmallVariantRecord, right: SmallVariantRecord, **kwargs) -> str | None:
    """The pair's phase, or None when it is not a compound-het candidate at all."""
    verdict = _compound_het_pair_verdict(left, right, **kwargs)
    return None if verdict is None else verdict[0]


def _phase(left_gt: str, right_gt: str, *, left_ps=None, right_ps=None) -> str | None:
    return _compound_het_pair_phase(
        _variant("left", calls=[_call("PROBAND", left_gt, left_ps)], start=100),
        _variant("right", calls=[_call("PROBAND", right_gt, right_ps)], start=200),
        affected_samples=AFFECTED,
        unaffected_samples=[],
    )


class TestPhasedAltHaplotype:
    def test_places_the_alt_on_its_haplotype(self) -> None:
        assert phased_alt_haplotype("0|1") == 1
        assert phased_alt_haplotype("1|0") == 0
        # A multi-allelic call with one alt allele places it too.
        assert phased_alt_haplotype("0|2") == 1
        assert phased_alt_haplotype("2|0") == 0

    def test_declines_an_unphased_call(self) -> None:
        # `0/1` is het but says nothing about which haplotype carries the alt.
        assert phased_alt_haplotype("0/1") is None

    def test_declines_a_homozygous_call(self) -> None:
        # A hom call is on both haplotypes, which is why the caller emits no phase set.
        assert phased_alt_haplotype("1|1") is None
        assert phased_alt_haplotype("0|0") is None

    def test_declines_a_multiallelic_call_with_alt_on_both_haplotypes(self) -> None:
        assert phased_alt_haplotype("1|2") is None

    @pytest.mark.parametrize("gt", [".|1", "1|.", "|1", "1|", ".|2", "2|.", ".|0", "0|.", ".|."])
    def test_declines_a_half_call(self, gt: str) -> None:
        # The missing allele may be an alt as well, so the call places nothing.
        assert phased_alt_haplotype(gt) is None

    def test_declines_a_no_call_or_junk(self) -> None:
        assert phased_alt_haplotype(None) is None
        assert phased_alt_haplotype("") is None
        assert phased_alt_haplotype("0|1|0") is None


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


def _pedigree(parents_of: dict[str, set[str]]):
    # Imported here so the tests above still run where the pedigree type is absent.
    from backend.app.services.compound_het_phase import FamilyPedigree

    return FamilyPedigree(
        parents_of={child: frozenset(parents) for child, parents in parents_of.items()},
        assembly_name="GRCh38",
    )


TRIO = {"PROBAND": {"MOTHER", "FATHER"}}


class TestPhaseFromTheParents:
    """Without read-backed phasing, the parents' genotypes can still place the two hits."""

    def test_one_hit_from_each_parent_is_trans_by_segregation(self) -> None:
        maternal = _variant(
            "maternal",
            calls=[_call("PROBAND", "0/1"), _call("MOTHER", "0/1"), _call("FATHER", "0/0")],
            start=100,
        )
        paternal = _variant(
            "paternal",
            calls=[_call("PROBAND", "0/1"), _call("MOTHER", "0/0"), _call("FATHER", "0/1")],
            start=200,
        )
        pairs = _compound_het_pairs(
            [maternal, paternal],
            affected_samples=AFFECTED,
            unaffected_samples=["MOTHER", "FATHER"],
            pedigree=_pedigree(TRIO),
        )
        assert [(pair.phase, pair.phase_evidence) for pair in pairs] == [("trans", "segregation")]

    def test_read_backed_trans_is_labelled_as_such(self) -> None:
        records = [
            _variant("A", calls=[_call("PROBAND", "0|1", 42)], start=100),
            _variant("B", calls=[_call("PROBAND", "1|0", 42)], start=200),
        ]
        pairs = _compound_het_pairs(records, affected_samples=AFFECTED, unaffected_samples=[])
        assert [(pair.phase, pair.phase_evidence) for pair in pairs] == [("trans", "read")]

    def test_the_reads_outrank_the_parents(self) -> None:
        # The parents trace one hit to each of them, but the reads put both on one
        # haplotype: the reads are direct evidence, so the pair is cis and dropped.
        left = _variant(
            "left",
            calls=[_call("PROBAND", "0|1", 7), _call("MOTHER", "0/1"), _call("FATHER", "0/0")],
            start=100,
        )
        right = _variant(
            "right",
            calls=[_call("PROBAND", "0|1", 7), _call("MOTHER", "0/0"), _call("FATHER", "0/1")],
            start=200,
        )
        assert (
            _compound_het_pair_phase(
                left,
                right,
                affected_samples=AFFECTED,
                unaffected_samples=["MOTHER", "FATHER"],
                pedigree=_pedigree(TRIO),
            )
            is None
        )

    def test_an_unaffected_sibling_carrying_neither_hit_leaves_the_pair_unknown(self) -> None:
        records = [
            _variant("A", calls=[_call("PROBAND", "0/1"), _call("SIB", "0/0")], start=100),
            _variant("B", calls=[_call("PROBAND", "0/1"), _call("SIB", "0/0")], start=200),
        ]
        pairs = _compound_het_pairs(records, affected_samples=AFFECTED, unaffected_samples=["SIB"])
        assert [(pair.phase, pair.phase_evidence) for pair in pairs] == [("unknown", None)]

    def test_both_hits_from_one_parent_are_cis_and_not_a_candidate(self) -> None:
        # The mother (affection status not recorded) carries both, the father neither:
        # both hits are on the proband's maternal copy.
        left = _variant(
            "left",
            calls=[_call("PROBAND", "0/1"), _call("MOTHER", "0/1"), _call("FATHER", "0/0")],
            start=100,
        )
        right = _variant(
            "right",
            calls=[_call("PROBAND", "0/1"), _call("MOTHER", "0/1"), _call("FATHER", "0/0")],
            start=200,
        )
        assert (
            _compound_het_pairs(
                [left, right],
                affected_samples=AFFECTED,
                unaffected_samples=["FATHER"],
                pedigree=_pedigree(TRIO),
            )
            == []
        )

    def test_without_a_pedigree_the_parents_are_not_traced(self) -> None:
        maternal = _variant(
            "maternal",
            calls=[_call("PROBAND", "0/1"), _call("MOTHER", "0/1"), _call("FATHER", "0/0")],
            start=100,
        )
        paternal = _variant(
            "paternal",
            calls=[_call("PROBAND", "0/1"), _call("MOTHER", "0/0"), _call("FATHER", "0/1")],
            start=200,
        )
        pairs = _compound_het_pairs(
            [maternal, paternal], affected_samples=AFFECTED, unaffected_samples=["MOTHER", "FATHER"]
        )
        assert [(pair.phase, pair.phase_evidence) for pair in pairs] == [("unknown", None)]


class TestBothPathsReadAPhasedCallAlike:
    """The SNV + SNV pair and the SNV + SV second-hit badge read one phase set alike.

    The badge used to place a half call such as ``1|.`` on a haplotype, where the pair
    declines it, so the same calls could be trans by read phasing on the badge and
    unresolved as a pair. Each case is one affected sample with both calls in one phase
    set, the second call standing for the second small variant or for the SV.
    """

    # The read verdict as each path reports it: a cis pair is dropped, the badge shows cis.
    PAIR = {"trans": ("trans", "read"), "cis": None, None: ("unknown", None)}
    BADGE = {"trans": ("trans", "read"), "cis": ("cis", "read"), None: ("unknown", None)}

    @pytest.mark.parametrize(
        ("first_gt", "second_gt", "expected"),
        [
            ("0|1", "1|0", "trans"),
            ("1|0", "0|1", "trans"),
            ("0|1", "0|1", "cis"),
            ("1|0", "2|0", "cis"),
            ("0|1", "2|0", "trans"),
            ("0|1", "1|.", None),
            ("0|1", ".|1", None),
            ("1|0", ".|1", None),
            ("1|.", "0|1", None),
            (".|1", "1|0", None),
            ("0|1", "1|2", None),
            ("0|1", "0/1", None),
        ],
    )
    def test_the_read_verdict_is_the_same_on_both_paths(
        self, first_gt: str, second_gt: str, expected: str | None
    ) -> None:
        pair = _compound_het_pair_verdict(
            _variant("left", calls=[_call("PROBAND", first_gt, 7)], start=100),
            _variant("right", calls=[_call("PROBAND", second_gt, 7)], start=200),
            affected_samples=AFFECTED,
            unaffected_samples=[],
        )
        badge = summarize_second_hit(
            [
                {
                    "sv_id": "sv1",
                    "sv_type": "DEL",
                    "chr": "1",
                    "start": 150,
                    "end": 250,
                    "gt": {"PROBAND": second_gt},
                    "ps": {"PROBAND": 7},
                }
            ],
            AFFECTED,
            unaffected_samples=[],
            snv_gt_by_sample={"PROBAND": first_gt},
            snv_ps_by_sample={"PROBAND": 7},
        )
        assert pair == self.PAIR[expected]
        assert (badge["phase"], badge["phase_evidence"]) == self.BADGE[expected]
