"""SNV+SNV compound hets and the SNV+SV second hit read phase from the family the same way.

The two used to answer differently from the same genotypes. The SV second-hit badge
called a pair trans whenever the family had an unaffected member who did not carry both
hits, even one who carried neither, and marked a deletion "effectively biallelic" on
that; an SNV pair with the same genotypes stayed unknown. Both now go through one rule,
``compound_het_phase.segregation_phase``, which counts a relative as evidence only where
they are: a parent, traced hit by hit, or an unaffected relative carrying both hits.

Every scenario below is run through both paths, and both must reach the same verdict.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from backend.app.services.clickhouse_variant_queries import (
    MODE_COMPOUND_HET,
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
    segregation_phase,
    small_variant_carriage,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.sv_gene_index_service import summarize_second_hit


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
        # The reported defect: this used to be trans on the SV side.
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
    modes = _segregation_modes_by_variant(records, context=_trio_context(mother_status="unknown"))
    assert MODE_COMPOUND_HET not in modes["first"]
    assert MODE_COMPOUND_HET not in modes["second"]

    traced_apart = [
        _record("first", 100, {"P": "0/1", "M": "0/1", "F": "0/0"}, {}),
        _record("second", 200, {"P": "0/1", "M": "0/0", "F": "0/1"}, {}),
    ]
    modes = _segregation_modes_by_variant(traced_apart, context=_trio_context())
    assert MODE_COMPOUND_HET in modes["first"] and MODE_COMPOUND_HET in modes["second"]
