"""The monogenic NIPT de novo triage (the R NIPT-M v0.7.3 rules) and the recessive fetal
risk. Synthetic sites only."""

from __future__ import annotations

import dataclasses

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
    RecessiveMode,
    de_novo_window,
    is_de_novo_candidate,
    mother_carries,
    recessive_gene_risks,
    recessive_listing_order,
    recessive_mode,
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
        cf_coverage_dp=depth,
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


def test_an_in_window_candidate_below_the_medium_score_is_low_priority() -> None:
    poor = _site(0.16, filters=["weak_evidence"], fs=15.0, metrics={"TLOD": 200.0, "MMQ": 40.0})
    triage = triage_de_novo(poor, _WINDOW, DeNovoAnnotation(impact="MODIFIER", max_population_af=0.2, is_novel=False))
    assert triage is not None
    # 6 loose - 3 common + (-1 FILTER -1 MMQ -1 FS +1 NM +1 repeat +1 unique) + 1 SNV = 4.
    assert (triage.window, triage.score, triage.label) == ("loose", 4, "low")
    assert {"common in the population", "caller FILTER not PASS", "low alt mapping quality", "strand bias"} <= set(
        triage.reasons
    )


def test_a_spliceai_score_of_at_least_0_2_adds_two() -> None:
    plain = triage_de_novo(_site(0.11), _WINDOW, DeNovoAnnotation(impact="MODERATE", spliceai_max=0.19))
    splicing = triage_de_novo(_site(0.11), _WINDOW, DeNovoAnnotation(impact="MODERATE", spliceai_max=0.2))
    assert plain is not None and splicing is not None
    assert (plain.score, splicing.score) == (19, 21)
    assert "SpliceAI >= 0.2" in splicing.reasons and "SpliceAI >= 0.2" not in plain.reasons


def test_a_vardict_msi_is_read_as_the_repeat_units() -> None:
    # VarDict writes the microsatellite units as MSI where Mutect2 writes STR and RPA.
    unique = triage_de_novo(_site(0.11, metrics={"TLOD": 200.0, "MSI": 1.0}), _WINDOW, DeNovoAnnotation(impact="HIGH"))
    repeat = triage_de_novo(_site(0.11, metrics={"TLOD": 200.0, "MSI": 4.0}), _WINDOW, DeNovoAnnotation(impact="HIGH"))
    assert unique is not None and repeat is not None
    assert repeat.score == unique.score - 2 and "repeat context" in repeat.reasons
    # An indel in it is a repeat indel, which cannot be high priority.
    indel = triage_de_novo(
        _site(0.11, ref="CA", alt="C", metrics={"TLOD": 200.0, "MSI": 4.0}), _WINDOW, DeNovoAnnotation(impact="HIGH")
    )
    assert indel is not None and "indel in a repeat" in indel.reasons and indel.label != "high"


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


# A plasma call's allele fraction by the mother's genotype: her het allele near 50%, her hom
# allele near 100%, else a fetal allele.
_PLASMA_VAF = {"het": 0.47, "hom": 0.97}


def _classification(category: int | None, **kwargs) -> NiptClassification:
    maternal_state = kwargs.get("maternal_state", "hom_ref")
    defaults = dict(
        variant_id="v", category=category, category_label="", maternal_state=maternal_state,
        fetal_inheritance="", expected_vaf=0.1, observed_vaf=_PLASMA_VAF.get(maternal_state, 0.1),
        confidence=0.99,
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


def _candidate(
    variant_id: str, father_state: str, classification: NiptClassification, mode: RecessiveMode = "autosomal"
) -> RecessiveCandidate:
    return RecessiveCandidate(variant_id=variant_id, father_state=father_state, classification=classification, mode=mode)


def test_the_recessive_risk_of_a_compound_pair() -> None:
    maternal = _candidate(
        "mat", "absent", _classification(3, maternal_state="het", maternal_allele_probability=0.97)
    )
    paternal = _candidate(
        "pat", "het", _classification(7, maternal_state="hom_ref", paternal_transmission_probability=1.0)
    )
    [risk] = recessive_gene_risks("GENEA", [maternal, paternal])
    assert risk.gene == "GENEA"
    assert risk.risk == pytest.approx(0.97)
    assert (risk.maternal_variant_id, risk.paternal_variant_id) == ("mat", "pat")
    assert not risk.risk_uses_prior
    assert [allele.variant_id for allele in risk.maternal] == ["mat"]
    assert [allele.variant_id for allele in risk.paternal] == ["pat"]
    # The father's allele alone pairs nothing.
    assert recessive_gene_risks("GENEA", [paternal]) == []
    # One the locked criteria do not count as the father's (the cohort count of a father's
    # allele reads it as common) pairs nothing either.
    uncounted = dataclasses.replace(paternal, paternal_counted=False)
    assert recessive_gene_risks("GENEA", [maternal, uncounted]) == []


def test_an_allele_both_parents_carry_counts_as_the_mothers_alone_when_not_as_the_fathers() -> None:
    # As the R validation pairs it: the mother's allele read off the plasma, the father's by the
    # cohort count of a father's allele too. A site both carry, uncounted as the father's,
    # pairs as the mother's with the father's other allele, never with itself.
    shared = dataclasses.replace(
        _candidate("both", "het", _classification(3, maternal_state="het", maternal_allele_probability=0.9)),
        paternal_counted=False,
    )
    paternal = _candidate(
        "pat", "het", _classification(7, maternal_state="hom_ref", paternal_transmission_probability=1.0)
    )
    [risk] = recessive_gene_risks("GENEA", [shared, paternal])
    assert [allele.variant_id for allele in risk.maternal] == ["both"]
    assert [allele.variant_id for allele in risk.paternal] == ["pat"]
    assert recessive_gene_risks("GENEA", [shared]) == []


@pytest.mark.parametrize(("plasma_vaf", "at_risk"), [(0.12, False), (0.249, False), (0.25, True), (0.47, True)])
def test_the_mother_carries_an_allele_only_on_a_plasma_call_of_25_percent_or_more(
    plasma_vaf: float, at_risk: bool
) -> None:
    # A low-level plasma call the model places in her het band (a cfDNA artefact, or the
    # fetus's allele) is no allele of hers: it makes no gene one both parents carry.
    maternal = _candidate(
        "mat", "absent", _classification(2, maternal_state="het", maternal_allele_probability=0.1, observed_vaf=plasma_vaf)
    )
    paternal = _candidate("pat", "het", _classification(7, paternal_transmission_probability=1.0))
    assert mother_carries(maternal.classification) is at_risk
    risks = recessive_gene_risks("GENEA", [maternal, paternal])
    assert [risk.gene for risk in risks] == (["GENEA"] if at_risk else [])
    # Nor a son's X-linked gene, which her allele alone would put at risk.
    son = _candidate(
        "x1", "absent",
        _classification(2, maternal_state="het", maternal_allele_probability=0.1, observed_vaf=plasma_vaf),
        mode="x_linked_son",
    )
    assert [risk.gene for risk in recessive_gene_risks("GENEX", [son])] == (["GENEX"] if at_risk else [])


def test_the_fathers_allele_under_a_low_plasma_call_is_not_one_the_mother_carries_too() -> None:
    # The father's het allele, its plasma call low but placed in the mother's band: she does not
    # carry it, so his allele is not the "mother carries it too" case, and the site pairs as his
    # allele alone.
    shared_low = _candidate(
        "pat", "het", _classification(2, maternal_state="het", observed_vaf=0.18, paternal_transmission_probability=None)
    )
    maternal = _candidate("mat", "absent", _classification(3, maternal_state="het", maternal_allele_probability=0.97))
    [risk] = recessive_gene_risks("GENEA", [maternal, shared_low])
    assert [allele.variant_id for allele in risk.maternal] == ["mat"]
    assert [allele.variant_id for allele in risk.paternal] == ["pat"]
    assert risk.paternal[0].note is None


def test_a_paternal_allele_not_transmitted_makes_the_risk_low() -> None:
    maternal = _candidate("mat", "absent", _classification(3, maternal_state="het", maternal_allele_probability=0.99))
    paternal = _candidate(
        "pat", "het", _classification(None, maternal_state="hom_ref", paternal_transmission_probability=1e-9)
    )
    [risk] = recessive_gene_risks("GENEA", [maternal, paternal])
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
    [risk] = recessive_gene_risks("GENEA", [shared])
    assert risk.risk == pytest.approx(0.985)
    assert risk.maternal_variant_id == risk.paternal_variant_id == "shared"
    # The father's allele, which the mother carries too, has no inheritance of its own (the R
    # pairing's maternal_also_carrier): the site is paired on its fetal genotype.
    assert risk.paternal[0].inherited_probability is None
    assert risk.paternal[0].note == "the mother carries it too: its inheritance is not told"
    # Without the fetal genotype posterior, the hom-alt probability alone.
    no_posterior = _candidate(
        "shared",
        "het",
        _classification(4, maternal_state="het", maternal_allele_probability=0.99, fetal_hom_alt_probability=0.9),
    )
    [risk] = recessive_gene_risks("GENEA", [no_posterior])
    assert risk.risk == pytest.approx(0.9)


def test_an_untold_inheritance_uses_the_half_prior_and_says_so() -> None:
    maternal = _candidate("mat", "absent", _classification(3, maternal_state="het", maternal_allele_probability=None))
    paternal = _candidate("pat", "het", _classification(7, maternal_state="hom_ref", paternal_transmission_probability=0.9))
    [risk] = recessive_gene_risks("GENEA", [maternal, paternal])
    # The maternal inheritance is the 1/2 prior.
    assert risk.risk == pytest.approx(0.45)
    assert risk.risk_uses_prior


def test_a_homozygous_parent_always_passes_the_allele() -> None:
    # The R pairing: a homozygous (on his X, hemizygous) parent's allele always reaches the fetus.
    maternal = _candidate("mat", "absent", _classification(3, maternal_state="het", maternal_allele_probability=0.99))
    both_hom = _candidate(
        "both_hom", "hom_alt", _classification(6, maternal_state="hom", fetal_hom_alt_probability=1.0)
    )
    father_hom = _candidate(
        "father_hom", "hom_alt", _classification(7, maternal_state="hom_ref", paternal_transmission_probability=1.0)
    )
    [shared] = recessive_gene_risks("GENEA", [both_hom])
    assert (shared.risk, shared.status, shared.mode) == (1.0, "affected", "autosomal")
    assert shared.maternal[0].note == "mother homozygous" and shared.paternal[0].note == "father homozygous"
    [pair] = recessive_gene_risks("GENEA", [maternal, father_hom])
    assert pair.risk == pytest.approx(0.99) and pair.status == "affected"
    # Even where the plasma does not show his allele (category 8, P ~ 0): the R pairing reads a
    # homozygous father as passing it; the missing allele is the category-8 signal.
    unseen = _candidate(
        "father_hom", "hom_alt", _classification(8, maternal_state="hom_ref", paternal_transmission_probability=0.01)
    )
    [pair] = recessive_gene_risks("GENEA", [maternal, unseen])
    assert (pair.paternal[0].inherited_probability, pair.status) == (1.0, "affected")


def test_the_fathers_allele_at_a_site_the_mother_is_homozygous_for() -> None:
    # As the R pairing reads it: the plasma's allele fraction cannot tell a heterozygous
    # father's allele beside two of the mother's, so that site is undetermined, and his allele
    # has no inheritance of its own for a pair across two sites either.
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
    [risk] = recessive_gene_risks("GENEA", [maternal, paternal])
    assert risk.paternal[0].inherited_probability is None
    # The likeliest affected pair is the two sites, his inheritance at its ½ prior: the gene
    # stays in the list, undetermined, rather than called a carrier.
    assert (risk.maternal_variant_id, risk.paternal_variant_id) == ("mat", "pat")
    assert risk.risk == pytest.approx(0.99 * 0.5)
    assert (risk.status, risk.risk_uses_prior) == ("undetermined", True)
    # The site alone: undetermined whatever its genotype posterior says.
    [alone] = recessive_gene_risks("GENEA", [paternal])
    assert alone.status == "undetermined"


@pytest.mark.parametrize(
    ("maternal_p", "paternal_p", "status"),
    [
        (0.97, 0.99, "affected"),
        (0.97, 0.03, "carrier"),
        (0.05, 0.03, "unaffected"),
        (0.5, 0.99, "undetermined"),  # the R band: 0.2 < p < 0.8 is no call
        (0.97, 0.21, "undetermined"),
        (None, 0.99, "undetermined"),  # an inheritance the cfDNA did not tell
        # One allele surely not inherited: never both, whatever the other.
        (0.5, 0.03, "carrier_or_unaffected"),
        (None, 0.03, "carrier_or_unaffected"),
        (0.05, 0.5, "carrier_or_unaffected"),
    ],
)
def test_the_fetal_status_of_a_pair_and_its_probabilities(
    maternal_p: float | None, paternal_p: float, status: str
) -> None:
    maternal = _candidate("mat", "absent", _classification(3, maternal_state="het", maternal_allele_probability=maternal_p))
    paternal = _candidate("pat", "het", _classification(7, paternal_transmission_probability=paternal_p))
    [risk] = recessive_gene_risks("GENEA", [maternal, paternal])
    m = 0.5 if maternal_p is None else maternal_p
    assert risk.status == status
    assert risk.risk == pytest.approx(m * paternal_p)
    assert risk.carrier_probability == pytest.approx(m * (1 - paternal_p) + (1 - m) * paternal_p)
    assert risk.unaffected_probability == pytest.approx((1 - m) * (1 - paternal_p))


def test_a_sons_maternal_x_allele_alone_makes_him_affected() -> None:
    # X-linked: a son has his mother's X only, so her allele alone makes him affected; he is
    # never a carrier, and his father's X allele is never his.
    inherited = _candidate(
        "x1", "absent", _classification(4, maternal_state="het", maternal_allele_probability=0.95), mode="x_linked_son"
    )
    father_x = _candidate(
        "x2", "hom_alt", _classification(1, maternal_state="hom_ref", paternal_transmission_probability=0.0),
        mode="x_linked_son",
    )
    [risk] = recessive_gene_risks("GENEX", [inherited, father_x])
    assert (risk.gene, risk.mode, risk.status, risk.paternal) == ("GENEX", "x_linked_son", "affected", [])
    assert (risk.risk, risk.carrier_probability, risk.unaffected_probability) == (
        pytest.approx(0.95), 0.0, pytest.approx(0.05),
    )
    not_told = _candidate(
        "x3", "absent", _classification(2, maternal_state="het", maternal_allele_probability=0.4), mode="x_linked_son"
    )
    [risk] = recessive_gene_risks("GENEY", [not_told])
    assert risk.status == "undetermined"
    # A daughter is paired as an autosome: her father's X allele always reaches her.
    maternal = _candidate(
        "d1", "absent", _classification(3, maternal_state="het", maternal_allele_probability=0.9),
        mode="x_linked_daughter",
    )
    paternal = _candidate(
        "d2", "hom_alt", _classification(7, maternal_state="hom_ref", paternal_transmission_probability=0.999),
        mode="x_linked_daughter",
    )
    [risk] = recessive_gene_risks("GENEX", [maternal, paternal])
    assert (risk.mode, risk.status) == ("x_linked_daughter", "affected")
    assert risk.paternal[0].note == "father hemizygous (his X)"


@pytest.mark.parametrize(
    ("maternal_p", "status"),
    [(0.9, "carrier"), (0.1, "unaffected"), (0.5, "carrier_or_unaffected"), (None, "carrier_or_unaffected")],
)
def test_a_daughters_x_allele_from_one_parent_makes_her_at_most_a_carrier(
    maternal_p: float | None, status: str
) -> None:
    # A daughter's X outside the pseudo-autosomal regions: one parent's allele alone makes her
    # a carrier at most. Her father's always reaches her; her mother's with its inheritance.
    mothers = _candidate(
        "d1", "absent", _classification(3, maternal_state="het", maternal_allele_probability=maternal_p),
        mode="x_linked_daughter",
    )
    [risk] = recessive_gene_risks("GENEX", [mothers])
    m = 0.5 if maternal_p is None else maternal_p
    assert (risk.gene, risk.mode, risk.status, risk.paternal) == ("GENEX", "x_linked_daughter", status, [])
    assert (risk.risk, risk.carrier_probability, risk.unaffected_probability) == (
        0.0, pytest.approx(m), pytest.approx(1 - m),
    )
    assert (risk.maternal_variant_id, risk.paternal_variant_id, risk.risk_uses_prior) == ("d1", None, maternal_p is None)
    fathers = _candidate(
        "d2", "hom_alt", _classification(7, maternal_state="hom_ref", paternal_transmission_probability=0.999),
        mode="x_linked_daughter",
    )
    [risk] = recessive_gene_risks("GENEY", [fathers])
    assert (risk.gene, risk.mode, risk.status, risk.maternal) == ("GENEY", "x_linked_daughter", "carrier", [])
    assert (risk.risk, risk.carrier_probability, risk.paternal_variant_id) == (0.0, 1.0, "d2")


def test_chrx_of_a_fetus_whose_sex_is_not_known_lists_each_parents_alleles_undetermined() -> None:
    # Of a fetus whose sex is not known (or of twins) no maternal model classifies chrX outside
    # the pseudo-autosomal regions: the mother's allele (a plasma call of 25% or more) and the
    # father's hemizygous one are listed, nothing told, the status undetermined.
    def x_site(variant_id: str, father_state: str, vaf: float) -> RecessiveCandidate:
        classification = _classification(None, maternal_state="unknown", observed_vaf=vaf)
        return _candidate(variant_id, father_state, classification, mode="x_linked_sex_unknown")

    mothers, fathers = x_site("x1", "absent", 0.48), x_site("x2", "hom_alt", 0.05)
    [risk] = recessive_gene_risks("GENEX", [mothers, fathers])
    assert (risk.gene, risk.mode, risk.status, risk.risk) == ("GENEX", "x_linked_sex_unknown", "undetermined", None)
    # Nothing is paired: no allele is the one the status rests on.
    assert (risk.maternal_variant_id, risk.paternal_variant_id) == (None, None)
    assert (risk.carrier_probability, risk.unaffected_probability) == (None, None)
    assert [(allele.variant_id, allele.inherited_probability) for allele in risk.maternal] == [("x1", None)]
    assert [(allele.variant_id, allele.inherited_probability) for allele in risk.paternal] == [("x2", None)]
    assert risk.maternal[0].note is not None and "fetal sex not known" in risk.maternal[0].note
    # One parent's allele alone is listed too; a low-level plasma call is the mother's no
    # allele, and a het call of the father's X is an artefact.
    assert [risk.mode for risk in recessive_gene_risks("GENEX", [mothers])] == ["x_linked_sex_unknown"]
    assert recessive_gene_risks("GENEX", [x_site("x3", "absent", 0.12), x_site("x4", "het", 0.05)]) == []
    # A gene read on both sides of a pseudo-autosomal boundary is read twice.
    par_mother = _candidate("par1", "absent", _classification(3, maternal_state="het", maternal_allele_probability=0.9))
    par_father = _candidate("par2", "het", _classification(7, paternal_transmission_probability=0.9))
    assert [risk.mode for risk in recessive_gene_risks("GENEX", [mothers, par_mother, par_father])] == [
        "autosomal", "x_linked_sex_unknown",
    ]


@pytest.mark.parametrize(
    ("flags", "chrom", "mode"),
    [
        ([], "chr1", "autosomal"),
        ([], "chrX", "autosomal"),  # a pseudo-autosomal region
        (["x_nonpar_female_fetus"], "chrX", "x_linked_daughter"),
        (["x_nonpar_male_fetus"], "chrX", "x_linked_son"),
        (["sex_chromosome_unsupported"], "X", "x_linked_sex_unknown"),
        (["sex_chromosome_unsupported"], "chrY", None),
        (["sex_chromosome_unsupported"], "chrM", None),
    ],
)
def test_an_allele_is_read_by_the_model_that_classified_its_site(flags: list[str], chrom: str, mode: str | None) -> None:
    assert recessive_mode(_classification(3, flags=flags), chrom) == mode


def test_an_allele_without_a_population_frequency_says_so() -> None:
    maternal = _candidate("mat", "absent", _classification(3, maternal_state="het", maternal_allele_probability=0.9))
    maternal.has_population_af = False
    paternal = _candidate("pat", "het", _classification(7, paternal_transmission_probability=1.0))
    paternal.has_population_af = False
    paternal.variant_class = "MNV"
    [risk] = recessive_gene_risks("GENEA", [maternal, paternal])
    assert risk.maternal[0].note == "no population frequency"
    assert risk.paternal[0].note == "MNV without a population frequency (gnomAD lists its SNVs)"


@pytest.mark.parametrize(
    ("flags", "note"),
    [
        (["maternal_inference_indel"], "indel: the maternal model is less accurate"),
        (["allele_balance_outlier"], "allele balance fits no fetal genotype"),
        # Both: the allele balance, which says the model fits no state at all.
        (["maternal_inference_indel", "allele_balance_outlier"], "allele balance fits no fetal genotype"),
    ],
)
def test_a_maternal_allele_the_model_reads_less_surely_says_so(flags: list[str], note: str) -> None:
    maternal = _candidate(
        "mat", "absent", _classification(3, maternal_state="het", maternal_allele_probability=0.9, flags=flags)
    )
    paternal = _candidate("pat", "het", _classification(7, paternal_transmission_probability=1.0))
    [risk] = recessive_gene_risks("GENEA", [maternal, paternal])
    assert risk.maternal[0].note == note
    # Beside a frequency note, both are given.
    maternal.has_population_af = False
    [risk] = recessive_gene_risks("GENEA", [maternal, paternal])
    assert risk.maternal[0].note == f"{note}; no population frequency"


def test_genes_are_listed_by_status_then_by_risk() -> None:
    def pair(maternal_p: float, paternal_p: float) -> list[RecessiveCandidate]:
        return [
            _candidate("m", "absent", _classification(3, maternal_state="het", maternal_allele_probability=maternal_p)),
            _candidate("p", "het", _classification(7, paternal_transmission_probability=paternal_p)),
        ]

    genes = {
        "AFFECTED": pair(0.99, 1.0),
        "CARRIER": pair(0.01, 1.0),  # risk 1%
        "UNDETERMINED": pair(0.5, 0.5),  # risk 25%
        "CARRIER_OR_UNAFFECTED": pair(0.5, 0.05),
        "UNAFFECTED": pair(0.05, 0.05),
        "UNDETERMINED_HIGHER": pair(0.7, 0.7),
    }
    sex_unknown = _candidate(
        "x", "absent", _classification(None, maternal_state="unknown", observed_vaf=0.48), mode="x_linked_sex_unknown"
    )
    risks = [risk for gene, candidates in genes.items() for risk in recessive_gene_risks(gene, candidates)]
    risks += recessive_gene_risks("SEX_UNKNOWN", [sex_unknown])
    # The cfDNA not telling whether the fetus is affected comes before its being a carrier, a
    # risk not told (the fetal sex not known) last among its status.
    assert [risk.gene for risk in sorted(risks, key=recessive_listing_order)] == [
        "AFFECTED", "UNDETERMINED_HIGHER", "UNDETERMINED", "SEX_UNKNOWN", "CARRIER", "CARRIER_OR_UNAFFECTED", "UNAFFECTED",
    ]
