"""The phase of a compound-het pair: from the reads, and from the family (``compound_het_phase``).

Read-backed phase. Two heterozygous variants in one gene are only a recessive explanation if
they sit on opposite haplotypes. A long-read caller says so directly: variants sharing a phase
set (PS) carry haplotype-resolved genotypes, so ``0|1`` against ``1|0`` is in trans and ``0|1``
against ``0|1`` is in cis. A cis pair leaves one intact copy of the gene, so it is not a
candidate at all. The SNV + SV second-hit badge reads a phased call by the same rule
(``compound_het_phase.phased_alt_haplotype``): ``TestBothPathsReadAPhasedCallAlike`` runs each
phased pair through both.

Phase from the family. SNV+SNV compound hets and the SNV+SV second hit answer alike from the
same genotypes: an unaffected member who does not carry both hits, or carries neither, makes
neither a pair trans nor a deletion "effectively biallelic". Both go through one rule,
``compound_het_phase.segregation_phase``, which counts a relative as evidence only where they
are: a parent, traced hit by hit, or an unaffected relative carrying both hits. Every scenario
of ``SCENARIOS`` is run through both paths, and both must reach the same verdict; the reads
outrank the parents.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from backend.app.services.clickhouse_variant_queries import (
    MODE_COMPOUND_HET,
    _compound_het_pair_verdict,
    _compound_het_pairs,
    _family_pedigree,
    _segregation_modes_by_variant,
)
from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord
from backend.app.services.compound_het_phase import (
    CARRIER,
    NON_CARRIER,
    UNKNOWN_CARRIAGE,
    FamilyPedigree,
    phased_alt_haplotype,
    segregation_phase,
    small_variant_carriage,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.sv_gene_index_service import summarize_second_hit
from backend.tests._segregation_flags import flag, members_of

# =========================================================================================
# Read-backed phase
# =========================================================================================

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


class TestBothPathsReadAPhasedCallAlike:
    """The SNV + SNV pair and the SNV + SV second-hit badge read one phase set alike.

    The badge declines a half call such as ``1|.`` as the pair does, so the same calls are
    never trans by read phasing on the badge and unresolved as a pair. Each case is one
    affected sample with both calls in one phase set, the second call standing for the
    second small variant or for the SV.
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


# =========================================================================================
# Phase from the family
# =========================================================================================


@dataclass(frozen=True)
class Scenario:
    name: str
    # Genotype per sample at each hit. The second hit is a small variant in the SNV+SNV
    # path and the structural variant in the SNV+SV path.
    first: dict[str, str]
    second: dict[str, str]
    affected: tuple[str, ...]
    unaffected: tuple[str, ...]
    parents_of: dict[str, set[str]] | None
    expected: str
    first_dp: dict[str, int] = field(default_factory=dict)


TRIO = {"P": {"M", "F"}}

SCENARIOS = [
    Scenario(
        "one hit from each parent",
        first={"P": "0/1", "M": "0/1", "F": "0/0"},
        second={"P": "0/1", "M": "0/0", "F": "0/1"},
        affected=("P",),
        unaffected=("M", "F"),
        parents_of=TRIO,
        expected="trans",
    ),
    Scenario(
        "the only sequenced parent carries exactly one hit",
        first={"P": "0/1", "M": "0/1"},
        second={"P": "0/1", "M": "0/0"},
        affected=("P",),
        unaffected=("M",),
        parents_of={"P": {"M"}},
        expected="trans",
    ),
    Scenario(
        "a parent without a call at one hit",
        first={"P": "0/1", "M": "0/1", "F": "0/0"},
        second={"P": "0/1", "M": "0/0", "F": "./."},
        affected=("P",),
        unaffected=("M", "F"),
        parents_of=TRIO,
        expected="trans",
    ),
    Scenario(
        "two affected siblings, one hit from each parent",
        first={"P": "0/1", "P2": "0/1", "M": "0/1", "F": "0/0"},
        second={"P": "0/1", "P2": "0/1", "M": "0/0", "F": "0/1"},
        affected=("P", "P2"),
        unaffected=("M", "F"),
        parents_of={"P": {"M", "F"}, "P2": {"M", "F"}},
        expected="trans",
    ),
    Scenario(
        # Unknown on the SV side too: a sibling carrying neither hit is no evidence of trans.
        "an unaffected sibling carrying neither hit",
        first={"P": "0/1", "S": "0/0"},
        second={"P": "0/1", "S": "0/0"},
        affected=("P",),
        unaffected=("S",),
        parents_of=None,
        expected="unknown",
    ),
    Scenario(
        "relatives carrying one hit each, but not recorded as parents",
        first={"P": "0/1", "M": "0/1", "F": "0/0"},
        second={"P": "0/1", "M": "0/0", "F": "0/1"},
        affected=("P",),
        unaffected=("M", "F"),
        parents_of=None,
        expected="unknown",
    ),
    Scenario(
        "both hits absent from both parents",
        first={"P": "0/1", "M": "0/0", "F": "0/0"},
        second={"P": "0/1", "M": "0/0", "F": "0/0"},
        affected=("P",),
        unaffected=("M", "F"),
        parents_of=TRIO,
        expected="unknown",
    ),
    Scenario(
        "the second hit is absent from both parents (de novo)",
        first={"P": "0/1", "M": "0/1", "F": "0/0"},
        second={"P": "0/1", "M": "0/0", "F": "0/0"},
        affected=("P",),
        unaffected=("M", "F"),
        parents_of=TRIO,
        expected="unknown",
    ),
    Scenario(
        "both parents carry the same hit",
        first={"P": "0/1", "M": "0/1", "F": "0/1"},
        second={"P": "0/1", "M": "0/0", "F": "0/1"},
        affected=("P",),
        unaffected=(),
        parents_of=TRIO,
        expected="unknown",
    ),
    Scenario(
        "an unaffected sibling carrying both hits",
        first={"P": "0/1", "S": "0/1"},
        second={"P": "0/1", "S": "0/1"},
        affected=("P",),
        unaffected=("S",),
        parents_of=None,
        expected="cis",
    ),
    Scenario(
        "both hits from one parent, the other genotyped and carrying neither",
        first={"P": "0/1", "M": "0/1", "F": "0/0"},
        second={"P": "0/1", "M": "0/1", "F": "0/0"},
        affected=("P",),
        unaffected=("F",),  # the mother's affection status is not recorded
        parents_of=TRIO,
        expected="cis",
    ),
    Scenario(
        # Cis removes a candidate, so it needs both parents genotyped for both hits.
        "both hits in the only sequenced parent",
        first={"P": "0/1", "M": "0/1"},
        second={"P": "0/1", "M": "0/1"},
        affected=("P",),
        unaffected=(),
        parents_of={"P": {"M"}},
        expected="unknown",
    ),
    Scenario(
        "a shallow parental reference call is not evidence",
        first={"P": "0/1", "M": "0/0"},
        second={"P": "0/1", "M": "0/1"},
        affected=("P",),
        unaffected=("M",),
        parents_of={"P": {"M"}},
        expected="unknown",
        first_dp={"P": 30, "M": 3},
    ),
    Scenario(
        "a deep parental reference call is",
        first={"P": "0/1", "M": "0/0"},
        second={"P": "0/1", "M": "0/1"},
        affected=("P",),
        unaffected=("M",),
        parents_of={"P": {"M"}},
        expected="trans",
        first_dp={"P": 30, "M": 30},
    ),
]


def _pedigree(parents_of: dict[str, set[str]] | None) -> FamilyPedigree | None:
    if parents_of is None:
        return None
    return FamilyPedigree(
        parents_of={child: frozenset(parents) for child, parents in parents_of.items()},
        assembly_name="GRCh38",
    )


class TestReadsAndParents:
    """Where the reads place the pair, the parents' genotypes do not decide it."""

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
                pedigree=_pedigree({"PROBAND": {"MOTHER", "FATHER"}}),
            )
            is None
        )


def _record(variant_id: str, start: int, gts: dict[str, str], dp: dict[str, int]) -> SmallVariantRecord:
    return SmallVariantRecord(
        variant_key=None,
        variant_id=variant_id,
        chr="1",
        start=start,
        end=start,
        ref="A",
        alt="G",
        source="clair3",
        rsid=None,
        filters=[],
        gene_symbols=["GENE1"],
        annotations=[{"gene": "GENE1"}],
        calls=[
            SmallVariantCall(sample=sample, gt=gt, gq=None, dp=dp.get(sample), af=[], ad=[], ps=None)
            for sample, gt in gts.items()
        ],
    )


def _snv_pair_verdict(scenario: Scenario) -> tuple[str, str | None] | None:
    pairs = _compound_het_pairs(
        [
            _record("first", 100, scenario.first, scenario.first_dp),
            _record("second", 200, scenario.second, {}),
        ],
        affected_samples=list(scenario.affected),
        unaffected_samples=list(scenario.unaffected),
        pedigree=_pedigree(scenario.parents_of),
    )
    assert len(pairs) <= 1
    return (pairs[0].phase, pairs[0].phase_evidence) if pairs else None


def _sv_badge_verdict(scenario: Scenario) -> tuple[str, str | None]:
    summary = summarize_second_hit(
        [
            {
                "sv_id": "sv1",
                "sv_type": "DEL",
                "chr": "1",
                "start": 150,
                "end": 250,
                "gt": dict(scenario.second),
                "ps": {},
            }
        ],
        list(scenario.affected),
        unaffected_samples=list(scenario.unaffected),
        snv_gt_by_sample=dict(scenario.first),
        snv_dp_by_sample=dict(scenario.first_dp),
        pedigree=_pedigree(scenario.parents_of),
        snv_locus=("1", 100),
    )
    return summary["phase"], summary["phase_evidence"]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[scenario.name for scenario in SCENARIOS])
def test_snv_pairs_and_the_sv_second_hit_reach_the_same_verdict(scenario: Scenario) -> None:
    snv = _snv_pair_verdict(scenario)
    sv = _sv_badge_verdict(scenario)
    if scenario.expected == "trans":
        assert snv == ("trans", "segregation")
        assert sv == ("trans", "segregation")
    elif scenario.expected == "cis":
        # A cis SNV pair is not a candidate at all; the SV badge still shows the SV and
        # says why it is not the second copy.
        assert snv is None
        assert sv == ("cis", "segregation")
    else:
        assert snv == ("unknown", None)
        assert sv == ("unknown", None)


class TestCarriage:
    @pytest.mark.parametrize("gt", ["0/1", "1|0", "1/1", "1", "1/2", "./1"])
    def test_an_alt_allele_is_carriage(self, gt: str) -> None:
        assert small_variant_carriage(gt, dp=2) == CARRIER

    @pytest.mark.parametrize(("gt", "dp"), [("0/0", None), ("0/0", 8), ("0|0", 40), ("0", 20)])
    def test_a_reference_call_with_depth_or_without_a_depth_field_is_absence(
        self, gt: str, dp: int | None
    ) -> None:
        assert small_variant_carriage(gt, dp=dp) == NON_CARRIER

    @pytest.mark.parametrize(("gt", "dp"), [("0/0", 7), ("./.", 30), (".", None), ("", None), (None, None)])
    def test_a_shallow_reference_call_or_a_no_call_says_nothing(self, gt: str | None, dp: int | None) -> None:
        assert small_variant_carriage(gt, dp=dp) == UNKNOWN_CARRIAGE


class TestTheRule:
    @staticmethod
    def _carriage(table: dict[str, str]):
        return lambda sample: table.get(sample, UNKNOWN_CARRIAGE)

    def test_no_pedigree_leaves_only_the_unaffected_carrier_rule(self) -> None:
        first = self._carriage({"P": CARRIER, "M": CARRIER, "F": NON_CARRIER})
        second = self._carriage({"P": CARRIER, "M": NON_CARRIER, "F": CARRIER})
        assert (
            segregation_phase(affected=["P"], unaffected=["M", "F"], first=first, second=second)
            == "unknown"
        )
        both = self._carriage({"P": CARRIER, "S": CARRIER})
        assert (
            segregation_phase(affected=["P"], unaffected=["S"], first=both, second=both) == "cis"
        )

    def test_more_than_two_parents_is_a_pedigree_error_not_evidence(self) -> None:
        pedigree = FamilyPedigree(parents_of={"P": frozenset({"A", "B", "C"})})
        first = self._carriage({"P": CARRIER, "A": CARRIER, "B": NON_CARRIER, "C": NON_CARRIER})
        second = self._carriage({"P": CARRIER, "A": NON_CARRIER, "B": CARRIER, "C": NON_CARRIER})
        assert (
            segregation_phase(affected=["P"], unaffected=[], first=first, second=second, pedigree=pedigree)
            == "unknown"
        )

    def test_a_cis_child_outweighs_a_trans_child(self) -> None:
        # Two affected cousins. C1 has one hit from each parent; C2 has both from her
        # mother, whose partner carries neither. C2 keeps an intact copy of the gene, so
        # the pair cannot explain both of them.
        pedigree = FamilyPedigree(
            parents_of={"C1": frozenset({"M1", "F1"}), "C2": frozenset({"M2", "F2"})}
        )
        first = self._carriage(
            {"C1": CARRIER, "M1": CARRIER, "F1": NON_CARRIER, "C2": CARRIER, "M2": CARRIER, "F2": NON_CARRIER}
        )
        second = self._carriage(
            {"C1": CARRIER, "M1": NON_CARRIER, "F1": CARRIER, "C2": CARRIER, "M2": CARRIER, "F2": NON_CARRIER}
        )
        assert (
            segregation_phase(affected=["C1"], unaffected=[], first=first, second=second, pedigree=pedigree)
            == "trans"
        )
        assert (
            segregation_phase(
                affected=["C1", "C2"], unaffected=[], first=first, second=second, pedigree=pedigree
            )
            == "cis"
        )

    def test_an_unresolved_child_does_not_undo_a_trans_child(self) -> None:
        # Half-siblings sharing their mother. C1: first hit from M, second from F1. C2:
        # the first hit is carried by both of her parents and the second by neither, so
        # her trace settles nothing. Trans stands.
        pedigree = FamilyPedigree(
            parents_of={"C1": frozenset({"M", "F1"}), "C2": frozenset({"M", "F2"})}
        )
        first = self._carriage(
            {"C1": CARRIER, "C2": CARRIER, "M": CARRIER, "F1": NON_CARRIER, "F2": CARRIER}
        )
        second = self._carriage(
            {"C1": CARRIER, "C2": CARRIER, "M": NON_CARRIER, "F1": CARRIER, "F2": NON_CARRIER}
        )
        assert (
            segregation_phase(
                affected=["C1", "C2"], unaffected=[], first=first, second=second, pedigree=pedigree
            )
            == "trans"
        )

    def test_a_male_is_only_skipped_where_he_carries_one_copy(self) -> None:
        pedigree = FamilyPedigree(
            parents_of={"SON": frozenset({"M", "F"})}, males=frozenset({"SON", "F"}), assembly_name="GRCh38"
        )
        first = self._carriage({"SON": CARRIER, "M": CARRIER, "F": NON_CARRIER})
        second = self._carriage({"SON": CARRIER, "M": NON_CARRIER, "F": CARRIER})

        def phase_at(chrom: str, position: int) -> str:
            return segregation_phase(
                affected=["SON"],
                unaffected=["M", "F"],
                first=first,
                second=second,
                pedigree=pedigree,
                loci=[(chrom, position)],
            )

        assert phase_at("X", 50_000_000) == "unknown"  # hemizygous: one X, from his mother
        assert phase_at("X", 1_000_000) == "trans"  # PAR1: diploid, traced like an autosome
        assert phase_at("7", 50_000_000) == "trans"


def _context(relationships: list[dict], sample_rows: list[dict]) -> FamilyMetadataContext:
    return FamilyMetadataContext(
        family_uuid="fam",
        family_id="F1",
        project_ids=["p1"],
        sample_rows=sample_rows,
        sample_uuid_to_name={f"u-{row['sample_id']}": row["sample_id"] for row in sample_rows},
        sample_name_to_uuid={row["sample_id"]: f"u-{row['sample_id']}" for row in sample_rows},
        affected_sample_names=[
            row["sample_id"] for row in sample_rows if row["clinical_status"] == "affected"
        ],
        assembly_id="a1",
        assembly_name="GRCh38",
        relationship_rows=relationships,
    )


def _trio_context(mother_status: str = "unaffected") -> FamilyMetadataContext:
    return _context(
        relationships=[
            {"relationship_type": "parent_child", "sample_id_a": "M", "sample_id_b": "P", "role_a": "mother", "role_b": "child"},
            {"relationship_type": "parent_child", "sample_id_a": "F", "sample_id_b": "P", "role_a": "father", "role_b": "child"},
            {"relationship_type": "couple", "sample_id_a": "F", "sample_id_b": "M", "role_a": "partner", "role_b": "partner"},
        ],
        sample_rows=[
            {"sample_id": "P", "clinical_status": "affected", "sex": "male"},
            {"sample_id": "M", "clinical_status": mother_status, "sex": "female"},
            {"sample_id": "F", "clinical_status": "unaffected", "sex": "male"},
        ],
    )


def test_the_family_pedigree_comes_from_the_parent_child_links() -> None:
    pedigree = _family_pedigree(_trio_context())
    assert pedigree.parents_of == {"P": frozenset({"M", "F"})}
    assert pedigree.males == frozenset({"P", "F"})
    assert pedigree.assembly_name == "GRCh38"


def test_a_pair_traced_to_one_parent_no_longer_counts_towards_the_compound_het_mode() -> None:
    # The segregation weight in the prioritised ranking reads the same pairs: a pair both
    # of whose hits came from the mother is not a compound-het candidate there either.
    records = [
        _record("first", 100, {"P": "0/1", "M": "0/1", "F": "0/0"}, {}),
        _record("second", 200, {"P": "0/1", "M": "0/1", "F": "0/0"}, {}),
    ]
    context = _trio_context(mother_status="unknown")
    # As the ranking fetches them: with the modes the SQL finds each to fit.
    modes = _segregation_modes_by_variant(flag(records, **members_of(context)), context=context)
    assert MODE_COMPOUND_HET not in modes["first"]
    assert MODE_COMPOUND_HET not in modes["second"]

    traced_apart = [
        _record("first", 100, {"P": "0/1", "M": "0/1", "F": "0/0"}, {}),
        _record("second", 200, {"P": "0/1", "M": "0/0", "F": "0/1"}, {}),
    ]
    context = _trio_context()
    modes = _segregation_modes_by_variant(flag(traced_apart, **members_of(context)), context=context)
    assert MODE_COMPOUND_HET in modes["first"] and MODE_COMPOUND_HET in modes["second"]
