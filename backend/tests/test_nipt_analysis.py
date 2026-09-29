from __future__ import annotations

import pytest

from backend.app.services.nipt_analysis import (
    FetalFractionEstimate,
    NiptQualityThresholds,
    NiptSiteObservation,
    classify_site,
    estimate_fetal_fraction,
    infer_fetal_sex,
    run_nipt_analysis,
)


def _x_sites(*, present: bool, vaf: float | None, pos0: int = 3_000_000, n: int = 10):
    return [
        _site(
            f"X-{pos0 + i * 1000}-A-G", father_state="hom_alt", chrom="X",
            pos=pos0 + i * 1000, is_autosomal=False, dp=200,
            alt=(int((vaf or 0) * 200) if present else 0), vaf=vaf, present=present,
        )
        for i in range(n)
    ]


def test_infer_fetal_sex_female_when_paternal_x_transmitted() -> None:
    # Paternal alt present at ~FF/2 across non-PAR X -> daughter inherited father's X.
    result = infer_fetal_sex(_x_sites(present=True, vaf=0.05), _ff(0.10), NiptQualityThresholds())
    assert result.inferred == "female" and result.x_transmitted >= 3


def test_infer_fetal_sex_male_when_paternal_x_absent() -> None:
    # Paternal alt absent with depth -> son inherited father's Y (paternal X not transmitted).
    result = infer_fetal_sex(_x_sites(present=False, vaf=None), _ff(0.10), NiptQualityThresholds())
    assert result.inferred == "male" and result.x_not_transmitted >= 8


def test_infer_fetal_sex_excludes_par_and_maternal_level_vaf() -> None:
    qc = NiptQualityThresholds()
    # PAR1 sites carry no X-vs-Y signal -> excluded -> indeterminate.
    par = _x_sites(present=True, vaf=0.05, pos0=1_000)
    assert infer_fetal_sex(par, _ff(0.10), qc).inferred == "indeterminate"
    # A maternal-level VAF (~0.5) means the mother carries it -> uninformative.
    maternal = _x_sites(present=True, vaf=0.5)
    assert infer_fetal_sex(maternal, _ff(0.10), qc).inferred == "indeterminate"


def _site(
    variant_id: str,
    *,
    father_state: str,
    dp: int | None = None,
    alt: int | None = None,
    vaf: float | None = None,
    present: bool = True,
    qual: float | None = 30.0,
    father_dp: int | None = 50,
    father_qual: float | None = 40.0,
    is_autosomal: bool = True,
    chrom: str = "1",
    pos: int = 100,
) -> NiptSiteObservation:
    if vaf is None and alt is not None and dp:
        vaf = alt / dp
    return NiptSiteObservation(
        variant_id=variant_id,
        chrom=chrom,
        pos=pos,
        is_autosomal=is_autosomal,
        cf_present=present,
        cf_dp=dp,
        cf_alt_reads=alt,
        cf_vaf=vaf,
        cf_qual=qual,
        father_state=father_state,
        father_dp=father_dp,
        father_qual=father_qual,
    )


def _ff(ff: float = 0.10, *, low_confidence: bool = False) -> FetalFractionEstimate:
    return FetalFractionEstimate(
        ff=ff,
        ff_computed=ff,
        ff_external=None,
        ff_median=ff,
        ci_low=max(0.0, ff - 0.005),
        ci_high=ff + 0.005,
        n_sites=50,
        method="category7_pooled",
        low_confidence=low_confidence,
        disagreement=False,
    )


# --------------------------------------------------------------------------- #
# 1. Fetal fraction recovery
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("ff_true", [0.02, 0.04, 0.10, 0.20, 0.40])
def test_fetal_fraction_recovery(ff_true: float) -> None:
    qc = NiptQualityThresholds()
    dp = 400
    alt = round(dp * ff_true / 2)
    sites = [_site(f"cat7-{i}", father_state="het", dp=dp, alt=alt) for i in range(40)]

    est = estimate_fetal_fraction(sites, qc)

    assert est.n_sites == 40
    assert est.ff_computed is not None
    assert abs(est.ff_computed - ff_true) < 0.01
    assert not est.low_confidence
    assert est.ci_low is not None and est.ci_high is not None
    assert est.ci_low <= ff_true <= est.ci_high


def test_fetal_fraction_low_confidence_with_few_sites() -> None:
    sites = [_site(f"cat7-{i}", father_state="het", dp=400, alt=20) for i in range(10)]
    est = estimate_fetal_fraction(sites, NiptQualityThresholds())
    assert est.n_sites == 10
    assert est.ff_computed is not None
    assert est.low_confidence


def test_fetal_fraction_none_below_hard_floor() -> None:
    sites = [_site(f"cat7-{i}", father_state="het", dp=400, alt=20) for i in range(3)]
    est = estimate_fetal_fraction(sites, NiptQualityThresholds())
    assert est.n_sites == 3
    assert est.ff_computed is None
    assert est.low_confidence


def test_fetal_fraction_excludes_father_hom_ref_and_high_vaf_sites() -> None:
    qc = NiptQualityThresholds()
    sites = [
        _site("dn", father_state="hom_ref", dp=400, alt=20),   # de novo, not cat7
        _site("mathet", father_state="het", dp=400, alt=200),  # vaf 0.5, above ceiling
        _site("cat7", father_state="het", dp=400, alt=20),     # the only FF site
    ]
    est = estimate_fetal_fraction(sites, qc)
    assert est.n_sites == 1


# --------------------------------------------------------------------------- #
# 2. External-FF reconciliation
# --------------------------------------------------------------------------- #

def test_external_ff_agreement_and_disagreement() -> None:
    qc = NiptQualityThresholds()
    sites = [_site(f"cat7-{i}", father_state="het", dp=400, alt=20) for i in range(40)]  # FF 0.10

    agree = estimate_fetal_fraction(sites, qc, external_ff=0.10)
    assert not agree.disagreement
    assert agree.method == "category7_pooled+external"
    assert agree.ff == pytest.approx(0.10, abs=1e-6)

    disagree = estimate_fetal_fraction(sites, qc, external_ff=0.30)
    assert disagree.disagreement
    assert disagree.ff == pytest.approx(0.10, abs=1e-6)  # computed stays the default

    prefer = estimate_fetal_fraction(sites, qc, external_ff=0.30, prefer_external=True)
    assert prefer.ff == pytest.approx(0.30, abs=1e-6)


def test_external_only_when_no_sites() -> None:
    est = estimate_fetal_fraction([], NiptQualityThresholds(), external_ff=0.12)
    assert est.ff_computed is None
    assert est.method == "external"
    assert est.ff == pytest.approx(0.12)


# --------------------------------------------------------------------------- #
# 3. Per-category assignment
# --------------------------------------------------------------------------- #

def test_category_7_paternal_transmitted() -> None:
    site = _site("v", father_state="het", dp=120, alt=6)  # VAF 0.05 = FF/2
    c = classify_site(site, _ff(0.10), NiptQualityThresholds())
    assert c.category == 7
    assert c.fetal_inheritance == "paternal_transmitted"


def test_category_1_de_novo() -> None:
    site = _site("v", father_state="hom_ref", dp=200, alt=10)  # VAF 0.05, father hom-ref
    c = classify_site(site, _ff(0.10), NiptQualityThresholds())
    assert c.category == 1
    assert c.fetal_inheritance == "de_novo"


def test_category_3_maternal_het_inherited() -> None:
    site = _site("v", father_state="hom_ref", dp=600, alt=300)  # VAF 0.50
    c = classify_site(site, _ff(0.10), NiptQualityThresholds())
    assert c.category == 3
    assert c.maternal_state == "het"


def test_category_2_maternal_het_not_inherited() -> None:
    site = _site("v", father_state="hom_ref", dp=600, alt=270)  # VAF 0.45 = 0.5 - FF/2
    c = classify_site(site, _ff(0.10), NiptQualityThresholds())
    assert c.category == 2


def test_category_4_maternal_het_hom_fetus() -> None:
    # A hom-alt fetus needs an alt from the father too, so he carries one.
    site = _site("v", father_state="het", dp=600, alt=330)  # VAF 0.55 = 0.5 + FF/2
    c = classify_site(site, _ff(0.10), NiptQualityThresholds())
    assert c.category == 4


def test_category_6_shared_hom() -> None:
    site = _site("v", father_state="hom_alt", dp=400, alt=400)  # VAF 1.0
    c = classify_site(site, _ff(0.10), NiptQualityThresholds())
    assert c.category == 6


def test_de_novo_below_alt_threshold_is_not_called() -> None:
    site = _site("v", father_state="hom_ref", dp=140, alt=2)  # k=2 < min_cf_alt_reads
    c = classify_site(site, _ff(0.10), NiptQualityThresholds())
    assert c.category is None


# --------------------------------------------------------------------------- #
# 4. Confidence honesty
# --------------------------------------------------------------------------- #

def test_ff_too_low_suppresses_fetal_inheritance() -> None:
    site = _site("v", father_state="hom_ref", dp=600, alt=300)  # VAF 0.50
    c = classify_site(site, _ff(0.005), NiptQualityThresholds())
    assert c.maternal_state == "het"
    assert c.fetal_inheritance == "unknown"
    assert "ff_too_low" in c.flags


def test_low_depth_is_flagged() -> None:
    site = _site("v", father_state="hom_ref", dp=12, alt=6)  # VAF 0.5, dp < min_cf_dp
    c = classify_site(site, _ff(0.10), NiptQualityThresholds())
    assert "low_depth" in c.flags


def test_low_ff_low_depth_around_half_is_ambiguous() -> None:
    # FF 4%, shallow depth: cat 2/3/4 cannot be resolved -> ambiguous, low confidence.
    site = _site("v", father_state="hom_ref", dp=40, alt=20)  # VAF 0.5
    c = classify_site(site, _ff(0.04), NiptQualityThresholds())
    assert c.confidence < 0.9
    assert "ambiguous" in c.flags


# --------------------------------------------------------------------------- #
# 5. Absence logic
# --------------------------------------------------------------------------- #

def test_category_8_false_negative_when_detectable() -> None:
    site = _site("v", father_state="hom_alt", present=False, dp=120, alt=0, vaf=0.0)
    c = classify_site(site, _ff(0.10), NiptQualityThresholds())
    assert c.category == 8
    assert "false_negative" in c.flags


def test_absent_undetectable_at_low_expected_depth() -> None:
    site = _site("v", father_state="hom_alt", present=False, dp=30, alt=0, vaf=0.0)
    c = classify_site(site, _ff(0.10), NiptQualityThresholds())  # E = 30 * 0.05 = 1.5 < 3
    assert c.category is None
    assert "undetectable_at_ff" in c.flags


def test_absent_low_depth_dropout() -> None:
    site = _site("v", father_state="hom_alt", present=False, dp=10, alt=0, vaf=0.0)
    c = classify_site(site, _ff(0.10), NiptQualityThresholds())
    assert c.category is None
    assert "low_depth_dropout" in c.flags


def test_absent_father_het_is_not_transmitted() -> None:
    site = _site("v", father_state="het", present=False, dp=120, alt=0, vaf=0.0)
    c = classify_site(site, _ff(0.10), NiptQualityThresholds())
    assert c.category is None
    assert c.fetal_inheritance == "paternal_not_transmitted"


# --------------------------------------------------------------------------- #
# 6. Edge flags
# --------------------------------------------------------------------------- #

def test_sex_chromosome_unsupported() -> None:
    site = _site("v", father_state="het", dp=120, alt=6, is_autosomal=False, chrom="X")
    c = classify_site(site, _ff(0.10), NiptQualityThresholds())
    assert c.category is None
    assert "sex_chromosome_unsupported" in c.flags


def test_father_no_coverage_flag() -> None:
    site = _site("v", father_state="missing", dp=120, alt=6)  # VAF 0.05
    c = classify_site(site, _ff(0.10), NiptQualityThresholds())
    assert "father_no_coverage" in c.flags
    assert c.category in (1, 7)  # de novo vs paternal cannot be separated


# --------------------------------------------------------------------------- #
# 6b. The father's genotype bounds the fetal state
# --------------------------------------------------------------------------- #
# The fetus carries one maternal and one paternal allele. A hom-ref father passes
# no alt, so the fetus is not hom-alt (categories 4 and 6) and nothing is paternal
# (7); a hom-alt father always passes one, so the fetus is not hom-ref (2), not het
# beside a hom-alt mother (5), and a de novo (1) cannot be told from his allele.

_POSSIBLE_BY_FATHER = {
    "hom_ref": {1, 2, 3, 5},
    "het": {2, 3, 4, 5, 6, 7},
    "hom_alt": {3, 4, 6, 7},
}


@pytest.mark.parametrize("father_state", sorted(_POSSIBLE_BY_FATHER))
@pytest.mark.parametrize("alt", [15, 30, 120, 240, 270, 300, 330, 360, 480, 570, 600])
def test_classification_never_reports_a_state_the_father_rules_out(
    father_state: str, alt: int
) -> None:
    site = _site("v", father_state=father_state, dp=600, alt=alt)  # VAF 0.025 .. 1.0
    c = classify_site(site, _ff(0.10), NiptQualityThresholds())
    possible = _POSSIBLE_BY_FATHER[father_state]
    assert c.category in possible
    assert c.runner_up_category is None or c.runner_up_category in possible


@pytest.mark.parametrize(
    ("father_state", "alt", "expected"),
    [
        ("hom_ref", 330, 3),  # VAF 0.55: no paternal alt, so the fetus is het, not hom-alt
        ("hom_ref", 600, 5),  # VAF 1.0: a hom-alt mother, and the father's ref in the fetus
        ("hom_alt", 270, 3),  # VAF 0.45: the father's alt is in the fetus, so it is not hom-ref
        ("hom_alt", 570, 6),  # VAF 0.95: the father's alt makes the fetus hom-alt
    ],
)
def test_a_site_takes_the_closest_state_the_father_allows(
    father_state: str, alt: int, expected: int
) -> None:
    site = _site("v", father_state=father_state, dp=600, alt=alt)
    c = classify_site(site, _ff(0.10), NiptQualityThresholds())
    assert c.category == expected


def test_a_father_call_below_min_father_dp_is_treated_as_no_call() -> None:
    # Pruning by the father's genotype is only as good as his call. A het father read
    # as hom-ref at 3x would turn his transmitted allele into a de novo and rule out
    # a hom-alt fetus; the thin call is treated as missing (and flagged) instead.
    qc = NiptQualityThresholds()
    assert qc.min_father_dp == 10
    ff = _ff(0.10)

    paternal = classify_site(_site("v", father_state="hom_ref", father_dp=3, dp=200, alt=10), ff, qc)
    assert "father_no_coverage" in paternal.flags
    assert paternal.category != 1

    hom_fetus = classify_site(_site("v", father_state="hom_ref", father_dp=3, dp=600, alt=330), ff, qc)
    assert "father_no_coverage" in hom_fetus.flags
    assert hom_fetus.category == 4

    # Nor is an absent allele of a thin hom-alt call a category-8 false negative.
    absent = classify_site(
        _site("v", father_state="hom_alt", father_dp=3, present=False, dp=120, alt=0, vaf=0.0),
        ff,
        qc,
    )
    assert absent.category is None
    assert "false_negative" not in absent.flags

    # At min_father_dp the call is trusted and prunes as before.
    trusted = classify_site(_site("v", father_state="hom_ref", father_dp=10, dp=200, alt=10), ff, qc)
    assert trusted.category == 1
    assert "father_no_coverage" not in trusted.flags


def test_fetal_sex_needs_a_trusted_paternal_x_call() -> None:
    thin = [
        _site(
            f"X-{3_000_000 + i * 1000}-A-G", father_state="hom_alt", father_dp=3, chrom="X",
            pos=3_000_000 + i * 1000, is_autosomal=False, dp=200, alt=10,
        )
        for i in range(10)
    ]
    result = infer_fetal_sex(thin, _ff(0.10), NiptQualityThresholds())
    assert result.inferred == "indeterminate" and result.informative_sites == 0


# --------------------------------------------------------------------------- #
# 7. Orchestration and filter counts
# --------------------------------------------------------------------------- #

def test_run_nipt_analysis_filter_counts() -> None:
    qc = NiptQualityThresholds()
    sites = [
        _site("cat7", father_state="het", dp=400, alt=20),         # passes
        _site("lowdp", father_state="het", dp=10, alt=2),          # fails quality (dp)
        _site("artifact", father_state="hom_ref", dp=400, alt=200),  # artifact
        _site("cat3", father_state="hom_ref", dp=400, alt=200),    # passes
    ]

    result = run_nipt_analysis(sites, qc, artifact_lookup=lambda v: v == "artifact")

    assert result.filter_counts == {
        "total_in": 4,
        "passed": 2,
        "failed_quality": 1,
        "failed_artifact": 1,
    }


def test_run_nipt_analysis_end_to_end_recovers_ff_and_categories() -> None:
    qc = NiptQualityThresholds()
    sites = [_site(f"cat7-{i}", father_state="het", dp=400, alt=20) for i in range(40)]
    sites.append(_site("cat3", father_state="hom_ref", dp=600, alt=300))
    sites.append(_site("dn", father_state="hom_ref", dp=300, alt=15))

    result = run_nipt_analysis(sites, qc)

    assert result.fetal_fraction.ff_computed == pytest.approx(0.10, abs=0.01)
    assert result.fetal_fraction.n_sites == 40
    assert result.category_counts[7] == 40
    assert result.category_counts[3] == 1
    assert result.category_counts[1] == 1


# --------------------------------------------------------------------------- #
# External fetal-fraction reconciliation (REQ-NIPT-005, risk H6)
# --------------------------------------------------------------------------- #
# An externally-supplied FF is recorded and a disagreement with the computed
# estimate is flagged, but the computed estimate stays the default unless the
# caller explicitly prefers the external value.


def _cat7_sites(ff_true: float = 0.10, *, dp: int = 400, n: int = 40):
    alt = round(dp * ff_true / 2)
    return [_site(f"cat7-{i}", father_state="het", dp=dp, alt=alt) for i in range(n)]


def test_external_ff_agreement_is_recorded_without_disagreement() -> None:
    est = estimate_fetal_fraction(_cat7_sites(0.10), NiptQualityThresholds(), external_ff=0.10)

    assert est.ff_external == 0.10
    assert est.disagreement is False
    assert est.ff == est.ff_computed  # computed estimate is the headline value
    assert est.method == "category7_pooled+external"


def test_external_ff_disagreement_is_flagged_but_does_not_override() -> None:
    est = estimate_fetal_fraction(_cat7_sites(0.10), NiptQualityThresholds(), external_ff=0.30)

    assert est.ff_external == 0.30
    assert est.disagreement is True
    # The disagreement is surfaced, but the computed estimate remains the default.
    assert est.ff == est.ff_computed
    assert est.ff_computed is not None and abs(est.ff_computed - 0.10) < 0.02


def test_prefer_external_overrides_the_computed_estimate_and_still_flags() -> None:
    est = estimate_fetal_fraction(
        _cat7_sites(0.10), NiptQualityThresholds(), external_ff=0.30, prefer_external=True
    )

    assert est.ff == 0.30  # external value is used as the headline
    assert est.disagreement is True  # disagreement still flagged


# --------------------------------------------------------------------------- #
# Degraded / incomplete input fails safe (REQ-PERF-003, risk H6)
# --------------------------------------------------------------------------- #


def test_estimate_fetal_fraction_fails_safe_on_empty_input() -> None:
    est = estimate_fetal_fraction([], NiptQualityThresholds())

    assert est.n_sites == 0
    assert est.ff_computed is None  # no spurious estimate from no evidence
    assert est.ff == 0.0
    assert est.low_confidence is True
    assert est.disagreement is False


def test_run_nipt_analysis_fails_safe_on_empty_input() -> None:
    result = run_nipt_analysis([], NiptQualityThresholds())

    # No crash, and the fetal fraction is reported as low-confidence / unknown
    # rather than a fabricated value.
    assert result.fetal_fraction.n_sites == 0
    assert result.fetal_fraction.ff_computed is None
    assert result.fetal_fraction.low_confidence is True
