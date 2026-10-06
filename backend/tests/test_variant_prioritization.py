"""Tests for the Exomiser-style variant prioritization scoring math."""

import pytest

from backend.app.services.variant_prioritization import (
    MODE_COMPOUND_HET,
    MODE_DE_NOVO,
    MODE_DOMINANT,
    clinvar_may_assert_pathogenic,
    combine,
    frequency_score,
    pathogenicity_score,
    score_structural_variant,
    score_variant,
    segregation_weight,
    structural_pathogenicity_score,
)
from backend.app.services.monarch_phenotype_score import (
    _MAX_GENE_TERMS,
    _cap_terms_by_ic,
    phenomizer_score,
)


def test_pathogenicity_clinvar_pathogenic_overrides() -> None:
    assert pathogenicity_score(
        impact="low", clinvar="Pathogenic", cadd_phred=0, revel=0,
        spliceai_max=0, lof=None,
    ) == 1.0


@pytest.mark.parametrize(
    ("clinvar", "protected"),
    [
        ("Pathogenic", True),
        ("Likely_pathogenic", True),
        ("Pathogenic/Likely_pathogenic", True),
        ("Pathogenic,_low_penetrance", True),
        ("Likely_pathogenic,_low_penetrance", True),
        ("Pathogenic|risk_factor", True),
        ("uncertain_significance&likely_pathogenic", True),  # a VEP CLIN_SIG list
        # A conflict may hold a P/LP submission, and ClinVar's per-submission breakdown is
        # not imported, so every conflicting record is protected.
        ("Conflicting_classifications_of_pathogenicity", True),
        ("Conflicting_interpretations_of_pathogenicity", True),
        ("conflicting_interpretations_of_pathogenicity&pathogenic", True),
        # The normalised terms stored in clinvar_terms read the same way.
        (["likely pathogenic"], True),
        (["conflicting classifications of pathogenicity"], True),
        (["uncertain significance", "pathogenic"], True),
        (["benign", "likely benign"], False),
        ("Uncertain_significance", False),
        ("Benign/Likely_benign", False),
        ("risk_factor", False),
        ("drug_response", False),
        ("not_provided", False),
        ("", False),
        (None, False),
        ([], False),
    ],
)
def test_clinvar_may_assert_pathogenic(clinvar, protected: bool) -> None:
    assert clinvar_may_assert_pathogenic(clinvar) is protected


def test_pathogenicity_clinvar_conflicting_is_not_scored_pathogenic() -> None:
    # "Conflicting_interpretations_of_pathogenicity" contains the substring "pathogenic"
    # but must NOT short-circuit to 1.0 — it is not a clean pathogenic call. Whole-word
    # matching + the conflict guard fall through to the predictor-based score.
    conflicting = pathogenicity_score(
        impact="MODERATE", clinvar="Conflicting_interpretations_of_pathogenicity",
        cadd_phred=20, revel=0.8, spliceai_max=0.1, lof=None,
    )
    assert conflicting < 1.0
    # A real likely-pathogenic assertion still overrides to 1.0...
    assert pathogenicity_score(
        impact="low", clinvar="Likely_pathogenic", cadd_phred=0, revel=0,
        spliceai_max=0, lof=None,
    ) == 1.0
    # ...and a clean benign assertion still scores low.
    assert pathogenicity_score(
        impact="low", clinvar="Benign", cadd_phred=0, revel=0, spliceai_max=0, lof=None,
    ) == 0.05


def test_pathogenicity_high_impact_lof() -> None:
    # LoF/HIGH predicted-deleterious, but capped below the ClinVar-reserved 1.0.
    assert pathogenicity_score(
        impact="HIGH", clinvar=None, cadd_phred=None, revel=None,
        spliceai_max=None, lof="HC",
    ) == 0.85


def test_pathogenicity_missense_takes_max_predictor() -> None:
    # MODERATE base 0.5 vs REVEL 0.8 -> 0.8
    score = pathogenicity_score(
        impact="MODERATE", clinvar=None, cadd_phred=20, revel=0.8,
        spliceai_max=0.1, lof=None,
    )
    assert abs(score - 0.8) < 1e-9


def test_alpha_missense_pathogenic_class_raises_missense_score() -> None:
    common = dict(
        impact="MODERATE", clinvar=None, cadd_phred=None, revel=None,
        spliceai_max=None, lof=None,
    )
    plain = pathogenicity_score(**common)
    flagged = pathogenicity_score(**common, alpha_missense_class="likely_pathogenic")
    assert flagged > plain
    assert flagged == 0.85


def test_alpha_missense_benign_class_caps_missense_score() -> None:
    # A MODERATE missense with a high CADD but AlphaMissense-benign is de-prioritized.
    score = pathogenicity_score(
        impact="MODERATE", clinvar=None, cadd_phred=30, revel=None,
        spliceai_max=None, lof=None, alpha_missense_class="likely_benign",
    )
    assert score <= 0.25


def test_alpha_missense_does_not_override_clinvar_pathogenic() -> None:
    assert pathogenicity_score(
        impact="MODERATE", clinvar="Pathogenic", cadd_phred=None, revel=None,
        spliceai_max=None, lof=None, alpha_missense_class="likely_benign",
    ) == 1.0


def test_numeric_alpha_missense_feeds_predictor_max() -> None:
    score = pathogenicity_score(
        impact="MODERATE", clinvar=None, cadd_phred=None, revel=0.2,
        spliceai_max=None, lof=None, alpha_missense_pathogenicity=0.88,
    )
    assert abs(score - 0.88) < 1e-9  # numeric AlphaMissense drives the predictor max


def test_gene_pli_raises_lof_confidence() -> None:
    base = pathogenicity_score(
        impact="HIGH", clinvar=None, cadd_phred=None, revel=None,
        spliceai_max=None, lof="HC",
    )
    constrained = pathogenicity_score(
        impact="HIGH", clinvar=None, cadd_phred=None, revel=None,
        spliceai_max=None, lof="HC", gene_pli=0.99,
    )
    assert constrained >= base
    assert constrained == 0.9


def test_out_of_range_gene_pli_is_ignored() -> None:
    # Malformed pLI (> 1) from bad source data must not trigger the constraint floor.
    base = pathogenicity_score(
        impact="HIGH", clinvar=None, cadd_phred=None, revel=None,
        spliceai_max=None, lof="HC",
    )
    with_garbage = pathogenicity_score(
        impact="HIGH", clinvar=None, cadd_phred=None, revel=None,
        spliceai_max=None, lof="HC", gene_pli=9.21,
    )
    assert with_garbage == base == 0.85


def test_missense_z_adds_prior_for_missense_in_constrained_gene() -> None:
    plain = pathogenicity_score(
        impact="MODERATE", clinvar=None, cadd_phred=None, revel=None,
        spliceai_max=None, lof=None,
    )
    constrained = pathogenicity_score(
        impact="MODERATE", clinvar=None, cadd_phred=None, revel=None,
        spliceai_max=None, lof=None, gene_missense_z=4.0,
    )
    assert constrained > plain


def test_frequency_score_is_monotonic_decreasing() -> None:
    assert frequency_score(gnomad_popmax_af=None, gnomad_af=None) == 1.0
    f_rare = frequency_score(gnomad_popmax_af=1e-5, gnomad_af=None)
    f_mid = frequency_score(gnomad_popmax_af=5e-4, gnomad_af=None)
    f_common = frequency_score(gnomad_popmax_af=0.1, gnomad_af=None)
    assert f_rare > f_mid > f_common
    assert f_common == 0.0


def test_segregation_weight_prefers_strong_modes() -> None:
    assert segregation_weight([MODE_DE_NOVO]) == 1.0
    assert segregation_weight([MODE_COMPOUND_HET]) == 1.0
    assert segregation_weight([MODE_DOMINANT]) == 0.85
    assert segregation_weight([]) == 0.6


def test_segregation_neutral_when_not_evaluated() -> None:
    # No affected individuals -> segregation can't be evaluated -> neutral, not a penalty.
    assert segregation_weight([], evaluated=False) == 1.0
    assert segregation_weight([], evaluated=True) == 0.6
    # A variant with no compatible mode is not penalised when segregation is unevaluable.
    unevaluated = score_variant(
        impact="MODERATE", clinvar=None, cadd_phred=None, revel=0.8, spliceai_max=None,
        lof=None, gnomad_popmax_af=0.0, gnomad_af=0.0, segregation_modes=[],
        phenotype_score=None, segregation_evaluated=False,
    )
    evaluated = score_variant(
        impact="MODERATE", clinvar=None, cadd_phred=None, revel=0.8, spliceai_max=None,
        lof=None, gnomad_popmax_af=0.0, gnomad_af=0.0, segregation_modes=[],
        phenotype_score=None, segregation_evaluated=True,
    )
    assert unevaluated.variant_score > evaluated.variant_score


def test_combine_phenotype_reorders_candidates() -> None:
    # Bounded in [0, 1] and monotonic in both axes.
    assert 0.0 <= combine(0.0, None) <= 1.0
    assert combine(1.0, 1.0) == 1.0
    # A phenotype match lifts the combined score.
    assert combine(0.5, 1.0) > combine(0.5, None)
    # Phenotype reorders: a strong phenotype match on a slightly weaker variant can beat
    # a top-scoring variant in a phenotype-irrelevant gene.
    assert combine(0.8, 0.9) > combine(1.0, None)


def test_score_variant_end_to_end_ranks_phenotype_match_higher() -> None:
    common = dict(
        impact="HIGH", clinvar=None, cadd_phred=30, revel=None, spliceai_max=None,
        lof="HC", gnomad_popmax_af=1e-6, gnomad_af=1e-6, segregation_modes=[MODE_DE_NOVO],
    )
    with_pheno = score_variant(**common, phenotype_score=0.9)
    without_pheno = score_variant(**common, phenotype_score=None)
    assert with_pheno.combined_score > without_pheno.combined_score
    # Variant score (pre-phenotype) is identical.
    assert with_pheno.variant_score == without_pheno.variant_score


def test_structural_pathogenicity_no_gene_is_capped_low() -> None:
    # An SV that overlaps no gene is most likely non-coding for ranking purposes.
    assert structural_pathogenicity_score(sv_type="DEL", gene_count=0) == 0.15


def test_structural_pathogenicity_loss_of_constrained_gene_is_high() -> None:
    base = structural_pathogenicity_score(sv_type="DEL", gene_count=1)
    constrained = structural_pathogenicity_score(sv_type="DEL", gene_count=1, gene_pli=0.99)
    assert constrained >= base
    assert constrained == 0.9


def test_structural_pathogenicity_gain_does_not_use_pli_floor() -> None:
    # The haploinsufficiency floor only applies to copy-number losses.
    gain = structural_pathogenicity_score(sv_type="DUP", gene_count=1, gene_pli=0.99)
    assert gain == 0.5


def test_structural_pathogenicity_multi_gene_bump_is_capped() -> None:
    single = structural_pathogenicity_score(sv_type="DEL", gene_count=1)
    multi = structural_pathogenicity_score(sv_type="DEL", gene_count=5)
    assert multi > single
    assert multi <= 0.9


def test_score_structural_variant_ranks_phenotype_match_higher() -> None:
    common = dict(
        sv_type="DEL",
        gene_count=1,
        control_af=1e-6,
        population_af=1e-6,
        segregation_modes=[MODE_DE_NOVO],
        gene_pli=0.99,
    )
    with_pheno = score_structural_variant(**common, phenotype_score=0.9)
    without_pheno = score_structural_variant(**common, phenotype_score=None)
    assert with_pheno.combined_score > without_pheno.combined_score
    # The phenotype-independent variant score is unchanged.
    assert with_pheno.variant_score == without_pheno.variant_score


def test_score_structural_variant_common_event_scores_low() -> None:
    rare = score_structural_variant(
        sv_type="DEL", gene_count=1, control_af=0.0, population_af=0.0,
        segregation_modes=[], phenotype_score=None,
    )
    common = score_structural_variant(
        sv_type="DEL", gene_count=1, control_af=0.2, population_af=0.2,
        segregation_modes=[], phenotype_score=None,
    )
    assert rare.combined_score > common.combined_score
    assert common.frequency == 0.0


def test_phenomizer_score_perfect_self_match_is_high() -> None:
    ic = {"HP:root": 0.1, "HP:spec": 5.0}
    ancestors = {"HP:spec": {"HP:spec", "HP:root"}, "HP:root": {"HP:root"}}
    score = phenomizer_score(
        ["HP:spec"], ["HP:spec"], ancestors=ancestors, ic=ic, max_ic=5.0
    )
    assert abs(score.score - 1.0) < 1e-9
    assert score.matched and score.matched[0]["hpo_id"] == "HP:spec"


def test_phenomizer_score_unrelated_terms_is_low() -> None:
    ic = {"HP:root": 0.1, "HP:a": 5.0, "HP:b": 5.0}
    ancestors = {
        "HP:a": {"HP:a", "HP:root"},
        "HP:b": {"HP:b", "HP:root"},
        "HP:root": {"HP:root"},
    }
    score = phenomizer_score(
        ["HP:a"], ["HP:b"], ancestors=ancestors, ic=ic, max_ic=5.0
    )
    # Only the low-IC root is shared.
    assert score.score < 0.1


def test_cap_terms_by_ic_breaks_ic_ties_deterministically() -> None:
    # With every term sharing the same IC, the tiebreaker alone decides which
    # survive the cap. The deterministic id tiebreaker yields the lexicographically
    # smallest ids in sorted order, independent of set hashing / PYTHONHASHSEED; the
    # old IC-only sort returned set-iteration order (process-dependent), which for a
    # large tied set is virtually never the sorted order.
    ids = [f"HP:{i:07d}" for i in range(_MAX_GENE_TERMS + 80)]
    ic = {hid: 2.5 for hid in ids}
    capped = _cap_terms_by_ic(set(ids), ic, _MAX_GENE_TERMS)
    assert capped == sorted(ids)[:_MAX_GENE_TERMS]


def test_cap_terms_by_ic_keeps_ic_descending_primary_order() -> None:
    ic = {"HP:zzz": 9.0, "HP:aaa": 1.0, "HP:mmm": 5.0}
    assert _cap_terms_by_ic({"HP:aaa", "HP:mmm", "HP:zzz"}, ic, 10) == [
        "HP:zzz",
        "HP:mmm",
        "HP:aaa",
    ]


def test_cap_terms_by_ic_unknown_ic_sinks_to_bottom() -> None:
    ic = {"HP:has": 4.0}
    assert _cap_terms_by_ic({"HP:has", "HP:missing"}, ic, 10) == ["HP:has", "HP:missing"]


def _sv_family_context(*, affected: set[str]):
    from backend.app.services.family_metadata_context import FamilyMetadataContext

    samples = ["CHILD", "MOTHER", "FATHER", "SIB"]
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="SV_SEG",
        project_ids=["project-uuid"],
        sample_rows=[
            {"sample_id": s, "clinical_status": "affected" if s in affected else "unaffected"} for s in samples
        ],
        sample_uuid_to_name={f"uuid-{s}": s for s in samples},
        sample_name_to_uuid={s: f"uuid-{s}" for s in samples},
        affected_sample_names=[s for s in samples if s in affected],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
        relationship_rows=[
            {"relationship_type": "parent_child", "sample_id_a": parent, "sample_id_b": child, "role_a": role, "role_b": "child"}
            for child in ("CHILD", "SIB")
            for parent, role in (("MOTHER", "mother"), ("FATHER", "father"))
        ],
    )


@pytest.mark.parametrize(
    ("inheritance", "affected", "expected"),
    [
        # An SV the affected child got from an affected parent supports dominance.
        ("maternal", {"CHILD", "MOTHER"}, [MODE_DOMINANT]),
        ("paternal", {"CHILD", "FATHER"}, [MODE_DOMINANT]),
        ("inherited", {"CHILD", "FATHER"}, [MODE_DOMINANT]),
        # From an unaffected parent it argues against a dominant model; it used to count
        # as support all the same.
        ("maternal", {"CHILD"}, []),
        ("inherited", {"CHILD"}, []),
        # The affected parent is not the one it came from.
        ("maternal", {"CHILD", "FATHER"}, []),
        # The query child is unaffected.
        ("maternal", {"MOTHER", "SIB"}, []),
        ("de novo", {"CHILD"}, [MODE_DE_NOVO]),
    ],
)
def test_structural_inherited_sv_supports_dominance_only_from_an_affected_parent(
    inheritance: str, affected: set[str], expected: list[str]
) -> None:
    from backend.app.services.clickhouse_variant_queries import _structural_segregation_modes

    context = _sv_family_context(affected=affected)
    # NeedlR names the query sample after its input file.
    annotation = {"inheritance": inheritance, "query_id": "CHILD_sv_phased"}

    assert _structural_segregation_modes(annotation, context) == expected


def test_structural_inherited_sv_without_pedigree_context_is_neutral() -> None:
    from backend.app.services.clickhouse_variant_queries import _structural_segregation_modes

    assert _structural_segregation_modes({"inheritance": "maternal"}) == []
    assert _structural_segregation_modes({"inheritance": "de novo"}) == [MODE_DE_NOVO]
