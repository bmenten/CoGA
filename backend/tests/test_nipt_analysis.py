from __future__ import annotations

import pytest

from backend.app.services.nipt_analysis import (
    FetalFractionEstimate,
    NiptQualityThresholds,
    NiptSiteObservation,
    classify_site,
    combined_fetal_sex,
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


@pytest.mark.parametrize(
    ("seen", "absent", "expected"),
    [
        (9, 1, "female"),  # one paternal X allele missed
        (1, 9, "male"),  # one stray call
        (4, 6, "indeterminate"),  # neither: not this father's X, or noise
        (6, 1, "indeterminate"),  # fewer than 8 informative sites
    ],
)
def test_the_fetal_sex_needs_most_or_hardly_any_paternal_x_alleles(seen: int, absent: int, expected: str) -> None:
    sites = _x_sites(present=True, vaf=0.05, n=seen) + _x_sites(present=False, vaf=None, pos0=5_000_000, n=absent)
    result = infer_fetal_sex(sites, _ff(0.10), NiptQualityThresholds())
    assert (result.inferred, result.x_transmitted, result.x_not_transmitted) == (expected, seen, absent)


def test_the_fetal_sex_leaves_out_a_plasma_call_read_off_another_record() -> None:
    # The father's allele whose plasma reads are another record's: that record is a site
    # of its own, so this one is no second piece of evidence.
    sites = _x_sites(present=True, vaf=0.05)
    for site in sites:
        site.representation = "plasma_call_other_representation"
    result = infer_fetal_sex(sites, _ff(0.10), NiptQualityThresholds())
    assert (result.inferred, result.informative_sites) == ("indeterminate", 0)


@pytest.mark.parametrize(
    ("paternal_x", "chry_profile", "ff", "expected"),
    [
        ("female", "female_no_chrY_signal", 0.10, "female"),
        ("male", "female_with_male_fetal_signal", 0.10, "male"),
        ("female", "female_with_male_fetal_signal", 0.10, "discordant"),
        ("indeterminate", "female_no_chrY_signal", 0.10, "female"),
        # Below 4% FF a male fetus's chrY can sit under the noise: no chrY signal says nothing.
        ("indeterminate", "female_no_chrY_signal", 0.03, "indeterminate"),
        ("male", None, 0.10, "male"),
        ("indeterminate", "high_chrY_review", 0.10, "indeterminate"),
        ("indeterminate", "no_chrY_targets", 0.10, "indeterminate"),
    ],
)
def test_the_fetal_sex_combines_the_paternal_x_and_the_chry_coverage(
    paternal_x: str, chry_profile: str | None, ff: float, expected: str
) -> None:
    assert combined_fetal_sex(paternal_x, chry_profile, ff) == expected


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
        ff_median=ff,
        ci_low=max(0.0, ff - 0.005),
        ci_high=ff + 0.005,
        n_sites=50,
        method="category7_pooled",
        low_confidence=low_confidence,
    )


# --------------------------------------------------------------------------- #
# 1. Fetal fraction recovery
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("ff_true", [0.02, 0.04, 0.10, 0.20, 0.40])
def test_fetal_fraction_recovery(ff_true: float) -> None:
    # At the assay's depth (about 1000x): a 2% FF still leaves the 5 alt reads a call needs.
    qc = NiptQualityThresholds()
    dp = 1000
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
# 2. Per-category assignment
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
# 3. Confidence honesty
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
# 4. Absence logic
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
# 5. Edge flags
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
# 5b. The father's genotype bounds the fetal state
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
    assert qc.min_father_dp == 20  # the R NIPT-M paternal genotype classes
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
    trusted = classify_site(_site("v", father_state="hom_ref", father_dp=20, dp=200, alt=10), ff, qc)
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
# 6. Orchestration and filter counts
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
        "paternal_only": 0,
    }


def test_paternity_evidence_counts_only_sites_with_a_confident_father_call() -> None:
    # A present FF/2 site whose father call is missing or thin is classified category 7 on
    # the de novo prior alone: it says nothing about who the father is.
    qc = NiptQualityThresholds()
    sites = [_site(f"cat7-{i}", father_state="het", dp=400, alt=20) for i in range(40)]
    sites += [_site(f"nocall-{i}", father_state="missing", father_dp=None, dp=400, alt=20) for i in range(20)]
    sites += [_site(f"thin-{i}", father_state="hom_ref", father_dp=3, dp=400, alt=20) for i in range(10)]
    sites += [
        _site(f"absent-{i}", father_state="hom_alt", present=False, dp=120, alt=0, vaf=0.0)
        for i in range(3)
    ]
    sites += [
        _site(f"thin-absent-{i}", father_state="hom_alt", father_dp=3, present=False, dp=120, alt=0, vaf=0.0)
        for i in range(2)
    ]

    result = run_nipt_analysis(sites, qc)

    assert result.category_counts[7] == 70  # the prior still places the thin/no calls here
    assert result.category_counts[8] == 3
    assert result.paternal_evidence == {7: 40, 8: 3}


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
# Degraded / incomplete input fails safe (REQ-PERF-003, risk H6)
# --------------------------------------------------------------------------- #


def test_estimate_fetal_fraction_fails_safe_on_empty_input() -> None:
    est = estimate_fetal_fraction([], NiptQualityThresholds())

    assert est.n_sites == 0
    assert est.ff_computed is None  # no spurious estimate from no evidence
    assert est.ff == 0.0
    assert est.low_confidence is True


def test_run_nipt_analysis_fails_safe_on_empty_input() -> None:
    result = run_nipt_analysis([], NiptQualityThresholds())

    # No crash, and the fetal fraction is reported as low-confidence / unknown
    # rather than a fabricated value.
    assert result.fetal_fraction.n_sites == 0
    assert result.fetal_fraction.ff_computed is None
    assert result.fetal_fraction.low_confidence is True


# --------------------------------------------------------------------------- #
# 7. The R NIPT-M model: quality filter, priors and fetal-inheritance probabilities
# --------------------------------------------------------------------------- #

from backend.app.services.nipt_analysis import (  # noqa: E402
    MATERNAL_HET_BIAS,
    NIPT_MODEL_REFERENCE,
    OVERDISPERSION,
    PaternalTransmissionEvidence,
    filter_sites_and_estimate_ff,
    paternal_transmission_evidence,
    paternal_transmission_probability,
    quality_failures,
    variant_class,
)


def test_the_model_constants_are_the_validated_ones() -> None:
    assert "v0.5.1" in NIPT_MODEL_REFERENCE
    assert OVERDISPERSION == pytest.approx(0.003721)
    assert MATERNAL_HET_BIAS == pytest.approx(-0.01123)
    qc = NiptQualityThresholds()
    assert (qc.min_qual, qc.min_cf_alt_reads, qc.min_vaf, qc.vaf_ff_fraction, qc.max_fs) == (20.0, 5, 0.01, 0.25, 20.0)
    assert (qc.father_het_min_vaf, qc.father_hom_alt_min_vaf, qc.min_father_dp, qc.min_father_alt_reads) == (
        0.20, 0.80, 20, 5,
    )


def _call_site(variant_id: str, **kwargs) -> NiptSiteObservation:
    site = _site(variant_id, father_state=kwargs.pop("father_state", "absent"), **{
        key: kwargs.pop(key) for key in list(kwargs) if key in {"dp", "alt", "vaf", "qual", "present"}
    })
    for key, value in kwargs.items():
        setattr(site, key, value)
    return site


def test_quality_failures_name_each_reason_and_a_missing_value_passes() -> None:
    qc = NiptQualityThresholds()
    good = _call_site("good", dp=1000, alt=100, cf_metrics={"TLOD": 300.0}, cf_fs=1.0)
    assert quality_failures(good, qc) == []
    # Mutect2's TLOD is the quality; a record QUAL of '.' (None) leaves the test to it.
    weak = _call_site("weak", dp=1000, alt=100, qual=None, cf_metrics={"TLOD": 12.0})
    assert quality_failures(weak, qc) == ["low_quality"]
    assert quality_failures(_call_site("few", dp=1000, alt=4, cf_metrics={"TLOD": 30.0}), qc) == [
        "few_alt_reads", "low_vaf",
    ]
    assert quality_failures(_call_site("fs", dp=1000, alt=100, cf_fs=25.0), qc) == ["strand_bias"]
    # Without any quality value the call passes, as in the R pipeline.
    assert quality_failures(_call_site("noq", dp=1000, alt=100, qual=None), qc) == []
    # Once FF is known: VAF >= 0.25 x FF/2. At FF 0.20 the floor is 2.5%.
    low = _call_site("floor", dp=1000, alt=20, cf_metrics={"TLOD": 50.0})
    assert quality_failures(low, qc) == []
    assert quality_failures(low, qc, ff=0.20) == ["below_fetal_vaf_floor"]
    # A site without a plasma call has nothing to filter.
    absent = _call_site("absent", dp=1000, alt=0, present=False, cf_depth_estimated=True)
    assert quality_failures(absent, qc) == []


def test_the_funnel_counts_cfdna_calls_and_applies_the_ff_floor() -> None:
    qc = NiptQualityThresholds()
    sites = [_site(f"cat7-{i}", father_state="het", dp=1000, alt=100) for i in range(40)]  # FF 0.20
    sites.append(_site("noise", father_state="absent", dp=1000, alt=12))  # VAF 1.2% < 2.5%
    sites.append(
        _call_site("paternal-only", father_state="hom_alt", dp=1000, alt=0, present=False, cf_depth_estimated=True)
    )
    filtered = filter_sites_and_estimate_ff(sites, qc)
    assert filtered.fetal_fraction.ff_computed == pytest.approx(0.20, abs=0.005)
    assert filtered.filter_counts == {
        "total_in": 41,
        "passed": 40,
        "failed_quality": 1,
        "failed_artifact": 0,
        "paternal_only": 1,
    }
    assert filtered.quality_failure_counts == {"below_fetal_vaf_floor": 1}
    assert {site.variant_id for site in filtered.passed} >= {"paternal-only"}


def test_the_ff_estimate_reports_the_fetal_band_of_its_sites() -> None:
    qc = NiptQualityThresholds()
    sites = [_site(f"cat7-{i}", father_state="het", dp=1000, alt=80 + i) for i in range(40)]
    est = estimate_fetal_fraction(sites, qc)
    assert est.vaf_q05 == pytest.approx(0.08195, abs=1e-4)
    assert est.vaf_q95 == pytest.approx(0.11705, abs=1e-4)
    # A paternal site the mother also carries (VAF above 0.35) is not an FF site.
    assert estimate_fetal_fraction([_site("both", father_state="het", dp=1000, alt=400)], qc).n_sites == 0


def test_variant_class() -> None:
    assert variant_class("A", "G") == "SNV"
    assert variant_class("AT", "GC") == "MNV"
    assert variant_class("A", "AT") == "indel"
    assert variant_class("", "") == "SNV"


def test_a_het_father_makes_a_hom_alt_fetus_possible_at_a_maternal_het_site() -> None:
    # FF 0.20 at 2000x: the three fetal states at a maternal het site sit 10 points apart.
    ff = _ff(0.20)
    qc = NiptQualityThresholds()
    for alt, expected, genotype in ((780, 2, "hom_ref"), (980, 3, "het"), (1180, 4, "hom_alt")):
        c = classify_site(_site("v", father_state="het", dp=2000, alt=alt), ff, qc)
        assert c.category == expected
        assert c.fetal_genotype_posterior is not None
        assert max(c.fetal_genotype_posterior, key=c.fetal_genotype_posterior.get) == genotype
    hom = classify_site(_site("v", father_state="het", dp=2000, alt=1180), ff, qc)
    # The overdispersion caps the effective depth near 1/rho (~270 reads): at FF 0.20 a
    # hom-alt fetus is told from a het one with about 98.5% certainty, not more.
    assert hom.fetal_hom_alt_probability is not None and 0.95 < hom.fetal_hom_alt_probability < 0.995
    assert hom.maternal_allele_probability is not None and hom.maternal_allele_probability > 0.95


def test_the_maternal_allele_probability_beside_a_reference_father() -> None:
    ff = _ff(0.20)
    qc = NiptQualityThresholds()
    inherited = classify_site(_site("v", father_state="absent", dp=2000, alt=980), ff, qc)
    assert inherited.category == 3
    assert inherited.maternal_allele_probability is not None and inherited.maternal_allele_probability > 0.99
    # Beside a reference father the fetus cannot be hom-alt.
    assert inherited.fetal_hom_alt_probability is None
    missed = classify_site(_site("v", father_state="absent", dp=2000, alt=780), ff, qc)
    assert missed.category == 2
    assert missed.maternal_allele_probability is not None and missed.maternal_allele_probability < 0.01
    # Half way between the two states the probability says so.
    between = classify_site(_site("v", father_state="absent", dp=2000, alt=878), ff, qc)
    assert between.maternal_allele_probability is not None
    assert 0.05 < between.maternal_allele_probability < 0.95
    assert "ambiguous" in between.flags


def test_beside_a_hom_alt_father_the_maternal_allele_is_read_off_the_hom_state() -> None:
    ff = _ff(0.20)
    qc = NiptQualityThresholds()
    het = classify_site(_site("v", father_state="hom_alt", dp=2000, alt=980), ff, qc)
    assert het.category == 3
    assert het.maternal_allele_probability is not None and het.maternal_allele_probability < 0.01
    assert het.fetal_hom_alt_probability is not None and het.fetal_hom_alt_probability < 0.01


def test_a_hom_alt_mother_always_passes_her_allele() -> None:
    c = classify_site(_site("v", father_state="het", dp=2000, alt=2000), _ff(0.20), NiptQualityThresholds())
    assert c.category == 6
    assert c.maternal_allele_probability == 1.0
    assert c.fetal_hom_alt_probability is not None and c.fetal_hom_alt_probability > 0.99


def test_an_indel_and_an_allele_balance_outlier_are_flagged_at_a_maternal_site() -> None:
    ff = _ff(0.20)
    qc = NiptQualityThresholds()
    indel = _call_site("v", father_state="absent", dp=2000, alt=980)
    indel.ref, indel.alt = "A", "AT"
    c = classify_site(indel, ff, qc)
    assert "maternal_inference_indel" in c.flags
    assert c.maternal_allele_probability is not None  # still reported, with the warning
    # VAF 0.25 at 2000x is over four standard deviations below the nearest fetal state
    # (0.39): the site's allele balance fits no fetal genotype. The relative posterior is
    # still confident, which is why the flag is needed.
    outlier = classify_site(_site("v", father_state="absent", dp=2000, alt=500), ff, qc)
    assert outlier.category == 2 and outlier.confidence > 0.99
    assert "allele_balance_outlier" in outlier.flags
    # 0.30 is within the overdispersed spread of 0.39 (2.8 SD): no flag.
    near = classify_site(_site("v", father_state="absent", dp=2000, alt=600), ff, qc)
    assert "allele_balance_outlier" not in near.flags


def test_paternal_transmission_probability() -> None:
    # Present at FF/2: transmitted.
    assert paternal_transmission_probability(100, 1000, 0.20, father_state="het") == pytest.approx(1.0)
    # No read at 1000x and FF 0.20 (100 expected): not transmitted, het or hom-alt father.
    assert paternal_transmission_probability(0, 1000, 0.20, father_state="het") < 1e-6
    assert paternal_transmission_probability(0, 1000, 0.20, father_state="hom_alt") < 1e-6
    # At 40x and FF 0.04 (0.8 reads expected) an absence says little.
    weak = paternal_transmission_probability(0, 40, 0.04, father_state="het")
    assert weak is not None and 0.2 < weak < 0.5
    assert paternal_transmission_probability(0, 0, 0.2, father_state="het") is None
    assert paternal_transmission_probability(5, 100, 0.2, father_state="hom_ref") is None


def test_a_paternal_allele_absent_from_the_plasma_with_its_depth_from_coverage() -> None:
    qc = NiptQualityThresholds()
    absent = _call_site(
        "v", father_state="het", dp=1500, alt=0, present=False, cf_depth_estimated=True
    )
    absent.father_dp = 300
    c = classify_site(absent, _ff(0.20), qc)
    assert c.category is None
    assert c.fetal_inheritance == "paternal_not_transmitted"
    assert c.paternal_transmission_probability is not None and c.paternal_transmission_probability < 1e-6
    hom = _call_site("v", father_state="hom_alt", dp=1500, alt=0, present=False, cf_depth_estimated=True)
    hom.father_dp = 300
    assert classify_site(hom, _ff(0.20), qc).category == 8
    no_depth = _call_site("v", father_state="het", dp=None, alt=0, present=False, cf_depth_estimated=True)
    no_depth.father_dp = 300
    assert "no_plasma_depth" in classify_site(no_depth, _ff(0.20), qc).flags


def test_a_few_reads_of_a_paternal_allele_at_low_expected_depth_are_a_transmission() -> None:
    site = _site("v", father_state="het", dp=120, alt=3)  # 6 expected at FF 0.10
    c = classify_site(site, _ff(0.10), NiptQualityThresholds())
    assert c.category == 7
    assert "few_alt_reads" in c.flags
    assert c.paternal_transmission_probability is not None and c.paternal_transmission_probability > 0.9


def test_the_father_classes_and_their_flags() -> None:
    qc = NiptQualityThresholds()
    ff = _ff(0.20)
    # No call in his file reads as reference: a de novo candidate, flagged.
    absent = classify_site(_site("v", father_state="absent", father_dp=None, dp=1000, alt=100), ff, qc)
    assert absent.category == 1 and "father_no_call" in absent.flags
    # With his depth there known and too low, it is no call.
    thin = classify_site(_site("v", father_state="absent", father_dp=8, dp=1000, alt=100), ff, qc)
    assert "father_no_coverage" in thin.flags and thin.category != 1
    # A low-level paternal call: de novo and paternal cannot be told apart.
    low = classify_site(_site("v", father_state="low_vaf", dp=1000, alt=100), ff, qc)
    assert {"father_low_level_signal", "father_no_coverage"} <= set(low.flags)
    weak = classify_site(_site("v", father_state="low_support", dp=1000, alt=100), ff, qc)
    assert "father_no_coverage" in weak.flags


def test_paternity_evidence_reads_the_obligate_and_the_half_transmissions() -> None:
    qc = NiptQualityThresholds()
    ff = _ff(0.20)

    def site(variant_id: str, father_state: str, alt: int, *, present: bool = True) -> NiptSiteObservation:
        observed = _site(variant_id, father_state=father_state, dp=1000, alt=alt, father_dp=300, present=present)
        observed.cf_depth_estimated = not present
        return observed

    sites = [site(f"hom-{i}", "hom_alt", 100) for i in range(19)]
    sites.append(site("hom-missed", "hom_alt", 0, present=False))
    sites += [site(f"het-in-{i}", "het", 100) for i in range(10)]
    sites += [site(f"het-out-{i}", "het", 0, present=False) for i in range(10)]
    # The mother carries it too: not paternity evidence.
    sites.append(site("shared", "het", 500))
    # Too shallow to tell (expected 5 reads).
    shallow = _site("shallow", father_state="het", dp=50, alt=0, present=False, father_dp=300)
    shallow.cf_depth_estimated = True
    sites.append(shallow)

    evidence = paternal_transmission_evidence(sites, ff, qc)
    assert evidence == PaternalTransmissionEvidence(
        hom_alt_transmitted=19, hom_alt_not_transmitted=1, het_transmitted=10, het_not_transmitted=10
    )
    assert evidence.hom_alt_rate == pytest.approx(0.95)
    assert evidence.het_rate == pytest.approx(0.5)
    assert paternal_transmission_evidence(sites, _ff(0.0), qc) == PaternalTransmissionEvidence()


def test_a_site_read_off_another_representation_is_no_second_piece_of_evidence() -> None:
    qc = NiptQualityThresholds()
    # The father's MNV whose bases the plasma calls apart: its reads are those calls',
    # which are sites of their own.
    borrowed = _site("mnv", father_state="het", dp=1000, alt=100, father_dp=300)
    borrowed.representation = "plasma_call_other_representation"
    # A father's allele the plasma calls in part: whether it was inherited is uncertain.
    partial = _site("partial", father_state="het", dp=1000, alt=0, present=False, father_dp=300)
    partial.cf_depth_estimated = True
    partial.representation = "plasma_call_overlaps"
    sites = [_site(f"cat7-{i}", father_state="het", dp=1000, alt=100, father_dp=300) for i in range(40)]
    filtered = filter_sites_and_estimate_ff([*sites, borrowed, partial], qc)
    # Neither is a plasma call to filter, nor a fetal-fraction site.
    assert filtered.filter_counts["total_in"] == 40
    assert filtered.filter_counts["paternal_only"] == 2
    assert filtered.fetal_fraction.n_sites == 40
    evidence = paternal_transmission_evidence([*sites, borrowed, partial], _ff(0.20), qc)
    assert (evidence.het_transmitted, evidence.het_not_transmitted) == (40, 0)
    # The classification names how the evidence was read.
    assert "plasma_call_other_representation" in classify_site(borrowed, _ff(0.20), qc).flags
