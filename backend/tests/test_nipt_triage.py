"""The monogenic NIPT de novo triage (the R NIPT-M v0.5.1 rules) and the recessive fetal
risk. Synthetic sites only."""

from __future__ import annotations

import pytest

from backend.app.services.nipt_analysis import (
    FetalFractionEstimate,
    NiptClassification,
    NiptSiteObservation,
)
from backend.app.services.nipt_triage import (
    DeNovoAnnotation,
    DeNovoWindow,
    RecessiveCandidate,
    de_novo_window,
    is_de_novo_candidate,
    recessive_gene_risks,
    triage_de_novo,
)


def _ff(q05: float | None = 0.08, q95: float | None = 0.14) -> FetalFractionEstimate:
    return FetalFractionEstimate(
        ff=0.22, ff_computed=0.22, ff_median=0.22, ci_low=0.21, ci_high=0.23, n_sites=500,
        method="category7_pooled", low_confidence=False, vaf_q05=q05, vaf_q95=q95,
    )


_WINDOW = DeNovoWindow(strict_min=0.08, strict_max=0.14, loose_min=0.06, loose_max=0.175)


def _site(
    vaf: float,
    *,
    ref: str = "A",
    alt: str = "G",
    father_state: str = "absent",
    metrics: dict[str, float] | None = None,
    filters: list[str] | None = None,
    fs: float | None = 0.0,
    depth: int = 1500,
) -> NiptSiteObservation:
    return NiptSiteObservation(
        variant_id="7-1000-A-G",
        chrom="7",
        pos=1000,
        is_autosomal=True,
        cf_present=True,
        cf_dp=depth,
        cf_alt_reads=round(vaf * depth),
        cf_vaf=vaf,
        cf_qual=None,
        father_state=father_state,
        ref=ref,
        alt=alt,
        cf_fs=fs,
        cf_filters=filters if filters is not None else ["PASS"],
        cf_metrics=metrics if metrics is not None else {"TLOD": 200.0, "MMQ": 60.0},
    )


def test_the_window_is_the_fetal_band_of_the_ff_sites() -> None:
    window = de_novo_window(_ff(0.08, 0.14))
    assert window == DeNovoWindow(strict_min=0.08, strict_max=0.14, loose_min=pytest.approx(0.06), loose_max=pytest.approx(0.175))
    # Capped at 35%, floored at 0.5%.
    wide = de_novo_window(_ff(0.004, 0.32))
    assert wide is not None and wide.loose_max == 0.35 and wide.loose_min == 0.005
    assert de_novo_window(_ff(None, None)) is None


def test_a_clean_rare_missense_snv_in_the_strict_window_is_high_priority() -> None:
    # 8 base + 2 MODERATE + 1 novel + 1 very rare + 6 technical + 1 SNV = 19.
    triage = triage_de_novo(_site(0.11), _WINDOW, DeNovoAnnotation(impact="MODERATE", max_population_af=None))
    assert triage is not None
    assert (triage.window, triage.score, triage.label) == ("strict", 19, "high")


def test_a_common_known_variant_scores_low() -> None:
    # 8 + 0 (MODIFIER) - 3 common + 6 + 1 = 12, but in the strict window and eligible.
    common = triage_de_novo(
        _site(0.11), _WINDOW, DeNovoAnnotation(impact="MODIFIER", max_population_af=0.2, is_novel=False)
    )
    assert common is not None and common.score == 12 and "common in the population" in common.reasons


def test_the_loose_window_reaches_medium_at_most() -> None:
    triage = triage_de_novo(_site(0.16), _WINDOW, DeNovoAnnotation(impact="HIGH"))
    assert triage is not None
    assert triage.window == "loose" and triage.label == "medium"


def test_outside_the_window_is_not_prioritised() -> None:
    below = triage_de_novo(_site(0.02), _WINDOW, DeNovoAnnotation(impact="HIGH"))
    above = triage_de_novo(_site(0.25), _WINDOW, DeNovoAnnotation(impact="HIGH"))
    assert below is not None and (below.window, below.label) == ("below", "outside_window")
    assert above is not None and (above.window, above.label) == ("above", "outside_window")


def test_technical_terms_and_class_scores() -> None:
    poor = _site(
        0.11,
        filters=["weak_evidence"],
        fs=15.0,
        metrics={"TLOD": 25.0, "MMQ": 40.0, "STR": 1.0, "RPA_REF": 5.0},
        ref="CA",
        alt="C",
    )
    triage = triage_de_novo(poor, _WINDOW, DeNovoAnnotation(impact="HIGH"))
    assert triage is not None
    # 8 + (4 + 1 + 1) + (-1 FILTER -1 MMQ -1 FS +1 NM -1 repeat +1 unique) + (-1 indel -2 repeat) = 9.
    assert triage.score == 9
    # A repeat indel cannot be high priority.
    assert triage.label == "medium"
    assert {"caller FILTER not PASS", "low alt mapping quality", "strand bias", "repeat context", "indel in a repeat"} <= set(
        triage.reasons
    )
    mnv = triage_de_novo(_site(0.11, ref="AT", alt="GC"), _WINDOW, DeNovoAnnotation(impact="HIGH"))
    assert mnv is not None and mnv.label == "medium"  # an MNV is capped at medium


def test_a_paternal_low_level_signal_costs_two_and_caps_at_medium() -> None:
    clean = triage_de_novo(_site(0.11), _WINDOW, DeNovoAnnotation(impact="HIGH"))
    signal = triage_de_novo(_site(0.11, father_state="low_vaf"), _WINDOW, DeNovoAnnotation(impact="HIGH"))
    assert clean is not None and signal is not None
    assert signal.score == clean.score - 2
    assert (clean.label, signal.label) == ("high", "medium")


def test_an_allele_another_cfdna_sample_carries_is_excluded() -> None:
    triage = triage_de_novo(_site(0.11), _WINDOW, DeNovoAnnotation(impact="HIGH"), other_cfdna_carriers=2)
    assert triage is not None and triage.label == "excluded_recurrent"
    assert "in 2 other cfDNA sample(s)" in triage.reasons


def test_no_window_no_triage() -> None:
    assert triage_de_novo(_site(0.11), None, DeNovoAnnotation()) is None


def _classification(category: int | None, **kwargs) -> NiptClassification:
    defaults = dict(
        variant_id="v", category=category, category_label="", maternal_state="hom_ref",
        fetal_inheritance="", expected_vaf=0.1, observed_vaf=0.1, confidence=0.99,
    )
    defaults.update(kwargs)
    return NiptClassification(**defaults)  # type: ignore[arg-type]


def test_a_candidate_has_a_passing_call_and_no_supported_paternal_call() -> None:
    passing = _classification(1)
    assert is_de_novo_candidate(_site(0.11), passing)
    assert is_de_novo_candidate(_site(0.11, father_state="low_vaf"), passing)
    assert not is_de_novo_candidate(_site(0.11, father_state="het"), passing)
    assert not is_de_novo_candidate(_site(0.11), _classification(1, quality_failures=["low_quality"]))
    coverage_only = _site(0.0)
    coverage_only.cf_depth_estimated = True
    assert not is_de_novo_candidate(coverage_only, passing)


def test_a_father_calling_part_of_the_allele_is_a_paternal_signal() -> None:
    window = DeNovoWindow(strict_min=0.08, strict_max=0.12, loose_min=0.06, loose_max=0.15)
    clean = _site(0.10)
    overlapping = _site(0.10)
    overlapping.representation = "father_call_overlaps"
    plain = triage_de_novo(clean, window, DeNovoAnnotation())
    flagged = triage_de_novo(overlapping, window, DeNovoAnnotation())
    assert plain is not None and flagged is not None
    assert flagged.score == plain.score - 2
    assert "the father has a call over part of the allele" in flagged.reasons
    assert flagged.label != "high"


def test_a_clinvar_pathogenic_candidate_is_never_excluded_as_recurrent() -> None:
    window = DeNovoWindow(strict_min=0.08, strict_max=0.12, loose_min=0.06, loose_max=0.15)
    recurrent = triage_de_novo(_site(0.10), window, DeNovoAnnotation(impact="HIGH"), other_cfdna_carriers=1)
    assert recurrent is not None and recurrent.label == "excluded_recurrent"
    hotspot = triage_de_novo(
        _site(0.10), window, DeNovoAnnotation(impact="HIGH", clinvar_pathogenic=True), other_cfdna_carriers=1
    )
    assert hotspot is not None and hotspot.label == "high"
    # The R score is kept: the other carrier still costs its technical point.
    assert hotspot.score == recurrent.score
    assert "ClinVar pathogenic: not excluded as recurrent" in hotspot.reasons


def test_a_plasma_call_read_off_another_record_is_no_de_novo_candidate() -> None:
    borrowed = _site(0.10)
    borrowed.representation = "plasma_call_other_representation"
    assert not is_de_novo_candidate(borrowed, _classification(1))


def _candidate(variant_id: str, father_state: str, classification: NiptClassification, genes=("GENEA",)) -> RecessiveCandidate:
    return RecessiveCandidate(variant_id=variant_id, genes=genes, father_state=father_state, classification=classification)


def test_the_recessive_risk_of_a_compound_pair() -> None:
    maternal = _candidate(
        "mat", "absent", _classification(3, maternal_state="het", maternal_allele_probability=0.97)
    )
    paternal = _candidate(
        "pat", "het", _classification(7, maternal_state="hom_ref", paternal_transmission_probability=1.0)
    )
    unrelated = _candidate(
        "other", "het", _classification(7, paternal_transmission_probability=1.0), genes=("GENEB",)
    )
    [risk] = recessive_gene_risks([maternal, paternal, unrelated])
    assert risk.gene == "GENEA"
    assert risk.risk == pytest.approx(0.97)
    assert (risk.maternal_variant_id, risk.paternal_variant_id) == ("mat", "pat")
    assert not risk.risk_uses_prior
    assert [allele.variant_id for allele in risk.maternal] == ["mat"]
    assert [allele.variant_id for allele in risk.paternal] == ["pat"]


def test_a_paternal_allele_not_transmitted_makes_the_risk_low() -> None:
    maternal = _candidate("mat", "absent", _classification(3, maternal_state="het", maternal_allele_probability=0.99))
    paternal = _candidate(
        "pat", "het", _classification(None, maternal_state="hom_ref", paternal_transmission_probability=1e-9)
    )
    [risk] = recessive_gene_risks([maternal, paternal])
    assert risk.risk is not None and risk.risk < 1e-6


def test_a_site_both_parents_carry_is_read_off_the_fetal_hom_alt_probability() -> None:
    shared = _candidate(
        "shared",
        "het",
        _classification(
            4,
            maternal_state="het",
            maternal_allele_probability=0.99,
            fetal_hom_alt_probability=0.985,
            fetal_genotype_posterior={"hom_ref": 0.0, "het": 0.015, "hom_alt": 0.985},
        ),
    )
    [risk] = recessive_gene_risks([shared])
    assert risk.risk == pytest.approx(0.985)
    assert risk.maternal_variant_id == risk.paternal_variant_id == "shared"
    # The paternal allele's inheritance: the hom-alt state, and half the het one.
    assert risk.paternal[0].inherited_probability == pytest.approx(0.985 + 0.5 * 0.015)


def test_an_untold_inheritance_uses_the_half_prior_and_says_so() -> None:
    maternal = _candidate("mat", "absent", _classification(3, maternal_state="het", maternal_allele_probability=None))
    paternal = _candidate("pat", "het", _classification(7, maternal_state="hom_ref", paternal_transmission_probability=0.9))
    [risk] = recessive_gene_risks([maternal, paternal])
    # The maternal inheritance is the 1/2 prior.
    assert risk.risk == pytest.approx(0.45)
    assert risk.risk_uses_prior


def test_a_homozygous_parent_is_not_a_carrier() -> None:
    maternal = _candidate("mat", "absent", _classification(3, maternal_state="het", maternal_allele_probability=0.99))
    both_hom = _candidate(
        "both_hom", "hom_alt", _classification(6, maternal_state="hom", fetal_hom_alt_probability=1.0)
    )
    father_hom = _candidate(
        "father_hom", "hom_alt", _classification(7, maternal_state="hom_ref", paternal_transmission_probability=1.0)
    )
    assert recessive_gene_risks([both_hom]) == []
    assert recessive_gene_risks([maternal, father_hom]) == []


def test_the_fathers_allele_at_a_site_the_mother_is_homozygous_for() -> None:
    # The mother always transmits the allele, so a heterozygous fetus has the father's
    # reference allele: his allele was inherited only by a homozygous fetus.
    maternal = _candidate("mat", "absent", _classification(3, maternal_state="het", maternal_allele_probability=0.99))
    paternal = _candidate(
        "pat",
        "het",
        _classification(
            5,
            maternal_state="hom",
            fetal_hom_alt_probability=0.02,
            fetal_genotype_posterior={"hom_ref": 0.0, "het": 0.98, "hom_alt": 0.02},
        ),
    )
    [risk] = recessive_gene_risks([maternal, paternal])
    assert risk.paternal[0].inherited_probability == pytest.approx(0.02)
    assert risk.risk == pytest.approx(0.99 * 0.02)


def test_an_allele_without_a_population_frequency_says_so() -> None:
    maternal = _candidate("mat", "absent", _classification(3, maternal_state="het", maternal_allele_probability=0.9))
    maternal.has_population_af = False
    paternal = _candidate("pat", "het", _classification(7, paternal_transmission_probability=1.0))
    paternal.has_population_af = False
    paternal.variant_class = "MNV"
    [risk] = recessive_gene_risks([maternal, paternal])
    assert risk.maternal[0].note == "no population frequency"
    assert risk.paternal[0].note == "MNV without a population frequency (gnomAD lists its SNVs)"


def test_genes_are_ordered_by_risk_and_need_both_parents() -> None:
    low = [
        _candidate("m1", "absent", _classification(2, maternal_state="het", maternal_allele_probability=0.01), genes=("LOW",)),
        _candidate("p1", "het", _classification(7, paternal_transmission_probability=1.0), genes=("LOW",)),
    ]
    high = [
        _candidate("m2", "absent", _classification(3, maternal_state="het", maternal_allele_probability=0.99), genes=("HIGH",)),
        _candidate("p2", "het", _classification(7, paternal_transmission_probability=1.0), genes=("HIGH",)),
    ]
    maternal_only = [
        _candidate("m3", "absent", _classification(3, maternal_state="het", maternal_allele_probability=0.99), genes=("SOLO",)),
    ]
    risks = recessive_gene_risks([*low, *high, *maternal_only])
    assert [risk.gene for risk in risks] == ["HIGH", "LOW"]
