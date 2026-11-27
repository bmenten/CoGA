"""The locked diagnostic criteria of the monogenic NIPT (REQ-NIPT-020): the R NIPT-M v0.7.3
blinded analysis's rarity, protein-effect, ClinVar and repeat-context rules."""

from __future__ import annotations

import pytest

from backend.app.services.nipt_diagnostic_filters import (
    NIPT_DIAGNOSTIC_CRITERIA_REFERENCE,
    DiagnosticAnnotation,
    NiptDiagnosticCriteria,
    clinvar_override,
    clinvar_strictly_pathogenic,
    cohort_count,
    dominant_failures,
    has_protein_effect,
    is_cohort_common,
    is_periodic,
    is_rare_dominant,
    is_rare_recessive,
    is_repeat_context_indel,
    recessive_failures,
)

CRITERIA = NiptDiagnosticCriteria()


def _annotation(
    af: float | None = None,
    impact: str | None = "MODERATE",
    spliceai: float | None = None,
    clinvar: bool = False,
    carriers: int = 0,
) -> DiagnosticAnnotation:
    return DiagnosticAnnotation(
        max_population_af=af, impact=impact, spliceai_max=spliceai, clinvar_pathogenic=clinvar,
        other_cfdna_carriers=carriers,
    )


def test_the_criteria_are_the_validated_ones_and_frozen_with_their_version() -> None:
    assert (CRITERIA.rare_max_af_dominant, CRITERIA.rare_max_af_recessive) == (0.001, 0.01)
    assert (CRITERIA.effect_impacts, CRITERIA.min_spliceai, CRITERIA.clinvar_max_af) == (("HIGH", "MODERATE"), 0.2, 0.05)
    assert (CRITERIA.cohort_common_min_samples, CRITERIA.repeat_min_units) == (3, 3.0)
    frozen = CRITERIA.frozen()
    assert frozen["reference"] == NIPT_DIAGNOSTIC_CRITERIA_REFERENCE and "v0.7.3" in frozen["reference"]
    assert frozen["effect_impacts"] == ["HIGH", "MODERATE"]


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["Pathogenic"], True),
        (["Likely_pathogenic"], True),
        (["Pathogenic/Likely_pathogenic"], True),
        (["uncertain_significance&pathogenic"], True),
        (["Conflicting_classifications_of_pathogenicity"], False),  # R: never strict
        (["Pathogenic", "Benign"], False),  # a benign assertion beside it
        (["Likely_benign"], False),
        (["Uncertain_significance"], False),
        ([None, ""], False),
    ],
)
def test_clinvar_is_read_strictly_as_the_r_pipeline_reads_it(values: list[str | None], expected: bool) -> None:
    assert clinvar_strictly_pathogenic(values) is expected


def test_rarity_differs_for_dominant_and_recessive_and_an_unannotated_common_allele_is_not_rare() -> None:
    assert is_rare_dominant(_annotation(af=0.001), CRITERIA)
    assert not is_rare_dominant(_annotation(af=0.002), CRITERIA)
    assert is_rare_recessive(_annotation(af=0.01), CRITERIA)
    assert not is_rare_recessive(_annotation(af=0.02), CRITERIA)
    # No population frequency: rare, unless it is in 3 cfDNA samples or more, this one included.
    assert is_rare_dominant(_annotation(af=None, carriers=1), CRITERIA)
    assert is_cohort_common(_annotation(af=None, carriers=2), CRITERIA)
    assert not is_rare_dominant(_annotation(af=None, carriers=2), CRITERIA)
    assert not is_rare_recessive(_annotation(af=None, carriers=2), CRITERIA)
    # A known frequency is never "cohort common", however often the allele recurs.
    assert not is_cohort_common(_annotation(af=0.0001, carriers=9), CRITERIA)


@pytest.mark.parametrize(
    ("other_plasmas", "plasma_calls", "other_fathers", "father_carries", "plasma_count", "father_count"),
    [
        # Called by this plasma and 2 others: 3 plasmas, common on both sides.
        (2, True, 0, False, 3, 3),
        # A father's allele this plasma does not show, called by 2 other plasmas: 2 either way
        # (2 plasmas; this father alone), rare. This plasma never counts when it shows nothing.
        (2, False, 0, True, 2, 2),
        # Carried by this father and 2 others, no plasma calls it: common as the father's allele.
        (0, False, 2, True, 0, 3),
        # The larger of the two counts, never their sum.
        (1, True, 1, True, 2, 2),
    ],
)
def test_the_cohort_count_reads_the_plasmas_and_for_the_fathers_allele_the_fathers_too(
    other_plasmas: int,
    plasma_calls: bool,
    other_fathers: int,
    father_carries: bool,
    plasma_count: int,
    father_count: int,
) -> None:
    # The R validation's count of an unannotated allele (v0.7.3): the plasmas that show it; for
    # the father's allele of the recessive pairing, those or the fathers that carry it, whichever
    # are more, this family's each counted only when it shows or carries it.
    annotation = DiagnosticAnnotation(
        max_population_af=None, impact="MODERATE", spliceai_max=None, clinvar_pathogenic=False,
        other_cfdna_carriers=other_plasmas, plasma_calls=plasma_calls,
        other_fathers=other_fathers, father_carries=father_carries,
    )
    assert (cohort_count(annotation), cohort_count(annotation, "father")) == (plasma_count, father_count)
    assert is_cohort_common(annotation, CRITERIA) is (plasma_count >= 3)
    assert is_cohort_common(annotation, CRITERIA, "father") is (father_count >= 3)
    assert (recessive_failures(annotation, CRITERIA, "father") == ["not_rare"]) is (father_count >= 3)
    # A de novo or dominant candidate is read off the plasma: the fathers never count for it.
    assert is_rare_dominant(annotation, CRITERIA) is (plasma_count < 3)


def test_clinvar_counts_as_rare_for_the_recessive_pairing_alone_up_to_five_percent() -> None:
    carrier = _annotation(af=0.03, impact="LOW", clinvar=True)  # a 3% carrier frequency (GJB2)
    assert clinvar_override(carrier, CRITERIA)
    assert is_rare_recessive(carrier, CRITERIA) and has_protein_effect(carrier, CRITERIA)
    assert recessive_failures(carrier, CRITERIA) == []
    # De novo and dominant candidates must be rare themselves.
    assert dominant_failures(carrier, CRITERIA) == ["not_rare"]
    # Above 5% ClinVar no longer overrides.
    common = _annotation(af=0.06, impact="LOW", clinvar=True)
    assert not clinvar_override(common, CRITERIA)
    assert recessive_failures(common, CRITERIA) == ["not_rare", "no_protein_effect"]


def test_a_protein_effect_is_a_high_or_moderate_impact_or_a_spliceai_score_of_0_2() -> None:
    assert has_protein_effect(_annotation(impact="HIGH"), CRITERIA)
    assert has_protein_effect(_annotation(impact="moderate"), CRITERIA)
    assert not has_protein_effect(_annotation(impact="LOW"), CRITERIA)
    assert has_protein_effect(_annotation(impact="LOW", spliceai=0.2), CRITERIA)
    assert not has_protein_effect(_annotation(impact=None, spliceai=0.19), CRITERIA)
    assert dominant_failures(_annotation(af=0.0001, impact="LOW"), CRITERIA) == ["no_protein_effect"]
    assert dominant_failures(_annotation(af=0.0001, impact="HIGH"), CRITERIA) == []


@pytest.mark.parametrize(
    ("ref", "alt", "units", "expected"),
    [
        ("A", "G", 9.0, False),  # an SNV is never an indel in a repeat
        ("ACAG", "A", 1.0, False),  # a 3-base deletion, outside a repeat
        ("A", "ACAGCAG", 1.0, True),  # an inserted CAG CAG: periodic
        ("ATTTT", "A", None, True),  # a deleted homopolymer run
        ("AT", "A", 3.0, True),  # Mutect2 RPA 3
        ("AT", "A", 2.0, False),
    ],
)
def test_an_indel_in_a_repeat(ref: str, alt: str, units: float | None, expected: bool) -> None:
    assert is_repeat_context_indel(ref, alt, units, CRITERIA) is expected


def test_a_periodic_sequence_has_a_unit_of_at_most_six_bases_and_four_bases_or_more() -> None:
    assert is_periodic("CAGCAG") and is_periodic("AAAA") and is_periodic("ACGTACACGTAC")
    assert not is_periodic("CAG") and not is_periodic("ACGTTG")
    assert not is_periodic("ACGTACGACGTACG")  # unit of 7
