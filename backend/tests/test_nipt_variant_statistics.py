"""The variant summary's NIPT-M statistics (nipt_variant_statistics): the plasma's sites grouped
by the mother's and the fetus's genotype, transmission per autosome with its marks, and the
caller-FILTER tally of the analysis."""

from __future__ import annotations

import pytest

from backend.app.services.nipt_analysis import (
    FetalFractionEstimate,
    NiptAnalysisResult,
    NiptClassification,
    NiptQualityThresholds,
    NiptSiteObservation,
    caller_filter_values,
    expected_plasma_vaf,
    run_nipt_analysis,
)
from backend.app.services.nipt_variant_statistics import (
    CHROMOSOME_MARK_MIN_SITES,
    GROUPS,
    PATERNAL_NOT_TRANSMITTED,
    nipt_variant_statistics,
    vaf_bin,
)

QC = NiptQualityThresholds()
DEPTH = 1000


def _site(
    index: int,
    *,
    father_state: str,
    vaf: float | None,
    chrom: str = "1",
    dp: int = DEPTH,
    ref: str = "A",
    alt: str = "G",
    filters: list[str] | None = None,
    is_autosomal: bool = True,
) -> NiptSiteObservation:
    """A site whose plasma shows ``vaf`` (None: no plasma call, its depth read off the
    coverage), beside a confident father call of ``father_state``."""
    present = vaf is not None
    return NiptSiteObservation(
        variant_id=f"{chrom}-{1000 + index}-{ref}-{alt}",
        chrom=chrom,
        pos=1000 + index,
        is_autosomal=is_autosomal,
        cf_present=present,
        cf_dp=dp,
        cf_alt_reads=round(dp * vaf) if present else 0,
        cf_vaf=vaf,
        cf_qual=30.0 if present else None,
        father_state=father_state,
        father_dp=60,
        father_qual=40.0,
        ref=ref,
        alt=alt,
        cf_filters=list(filters or []),
        cf_depth_estimated=not present,
    )


def _family_sites(ff: float = 0.10) -> tuple[list[NiptSiteObservation], dict[str, int]]:
    """Sites of every group at the allele fractions the model expects at ``ff``, and how many
    of each were made."""
    half = ff / 2
    plan: list[tuple[str, str, float | None, int]] = [
        # (group, father, plasma VAF, sites)
        ("7", "het", half, 40),
        ("1", "hom_ref", half, 2),
        ("2", "hom_ref", 0.5 - half - 0.011, 30),
        ("3", "hom_ref", 0.5 - 0.011, 25),
        ("4", "het", 0.5 + half - 0.011, 10),
        ("5", "hom_ref", 1.0 - half, 8),
        ("6", "hom_alt", 1.0, 12),
        ("8", "hom_alt", None, 3),
        (PATERNAL_NOT_TRANSMITTED, "het", None, 6),
    ]
    sites: list[NiptSiteObservation] = []
    made: dict[str, int] = {}
    for group, father, vaf, count in plan:
        made[group] = count
        for _ in range(count):
            sites.append(_site(len(sites), father_state=father, vaf=vaf))
    return sites, made


def test_the_analysis_keeps_its_sites_in_the_order_of_their_classifications() -> None:
    sites, _made = _family_sites()
    result = run_nipt_analysis(sites, QC)
    assert len(result.sites) == len(result.classifications)
    assert [site.variant_id for site in result.sites] == [item.variant_id for item in result.classifications]


def test_each_group_holds_the_sites_of_its_mother_and_fetus_genotypes() -> None:
    sites, made = _family_sites()
    result = run_nipt_analysis(sites, QC)
    stats = nipt_variant_statistics(result, QC)
    by_key = {group.key: group for group in stats.groups}
    assert [group.key for group in stats.groups] == [group.key for group in GROUPS]
    assert {key: group.sites for key, group in by_key.items()} == made
    # Every category the analysis counts is a group of the same size.
    for category, count in result.category_counts.items():
        assert by_key[str(category)].sites == count
    assert stats.classified_sites == sum(made.values())
    assert stats.not_classified == {}
    # The matrix's axes.
    assert (by_key["2"].maternal_genotype, by_key["2"].fetal_genotype) == ("het", "hom_ref")
    assert (by_key["4"].maternal_genotype, by_key["4"].fetal_genotype) == ("het", "hom_alt")
    assert (by_key["5"].maternal_genotype, by_key["5"].fetal_genotype) == ("hom_alt", "het")
    assert (by_key[PATERNAL_NOT_TRANSMITTED].maternal_genotype, by_key[PATERNAL_NOT_TRANSMITTED].fetal_genotype) == (
        "hom_ref",
        "hom_ref",
    )


def test_a_group_gives_its_expected_and_observed_allele_fraction_at_the_fetal_fraction() -> None:
    sites, _made = _family_sites()
    result = run_nipt_analysis(sites, QC)
    ff = result.fetal_fraction.ff
    by_key = {group.key: group for group in nipt_variant_statistics(result, QC).groups}
    for category in range(1, 8):
        group = by_key[str(category)]
        assert group.expected_vaf == pytest.approx(expected_plasma_vaf(category, ff))
        assert group.observed_median_vaf == pytest.approx(group.expected_vaf, abs=0.01)
    # An allele the fetus did not inherit predicts no plasma allele fraction, and the plasma has
    # no call of it to draw.
    for key in ("8", PATERNAL_NOT_TRANSMITTED):
        assert by_key[key].expected_vaf is None and by_key[key].observed_median_vaf is None
        assert sum(by_key[key].vaf_histogram) == 0
    assert expected_plasma_vaf(8, ff) is None


def test_a_group_counts_its_sites_by_father_variant_class_confidence_and_allele_fraction() -> None:
    sites, made = _family_sites()
    # Two of the hom-alt sites are an indel and an MNV.
    hom_alt = [site for site in sites if site.father_state == "hom_alt" and site.cf_vaf == 1.0]
    hom_alt[0].ref, hom_alt[0].alt = "A", "AT"
    hom_alt[1].ref, hom_alt[1].alt = "AC", "GT"
    result = run_nipt_analysis(sites, QC)
    by_key = {group.key: group for group in nipt_variant_statistics(result, QC).groups}
    six = by_key["6"]
    assert six.by_father == {"hom_alt": made["6"]}
    assert six.by_variant_class == {"SNV": made["6"] - 2, "indel": 1, "MNV": 1}
    assert six.high_confidence == made["6"] and six.ambiguous == 0
    assert len(six.vaf_histogram) == 50 and sum(six.vaf_histogram) == made["6"]
    # A VAF of 1.0 is the last bin's.
    assert six.vaf_histogram[49] == made["6"]
    assert vaf_bin(1.0) == 49 and vaf_bin(0.0) == 0 and vaf_bin(0.05) == 2
    # A fraction on a bin's lower edge is that bin's, whatever the floating point.
    assert [vaf_bin(edge) for edge in (0.06, 0.3, 0.58, 0.98)] == [3, 15, 29, 49]
    assert vaf_bin(0.0599) == 2
    assert sum(by_key["7"].vaf_histogram) == made["7"]


def _classification(
    variant_id: str,
    *,
    category: int | None,
    fetal_inheritance: str = "unknown",
    flags: list[str] | None = None,
    maternal_allele_probability: float | None = None,
    observed_vaf: float | None = None,
    confidence: float = 1.0,
) -> NiptClassification:
    return NiptClassification(
        variant_id=variant_id,
        category=category,
        category_label="",
        maternal_state="het",
        fetal_inheritance=fetal_inheritance,
        expected_vaf=0.0,
        observed_vaf=observed_vaf,
        confidence=confidence,
        flags=list(flags or []),
        maternal_allele_probability=maternal_allele_probability,
    )


def _ff(ff: float = 0.10) -> FetalFractionEstimate:
    return FetalFractionEstimate(
        ff=ff, ff_computed=ff, ff_median=ff, ci_low=ff - 0.01, ci_high=ff + 0.01,
        n_sites=40, method="category7_pooled", low_confidence=False,
    )


def _result(pairs: list[tuple[NiptSiteObservation, NiptClassification]]) -> NiptAnalysisResult:
    return NiptAnalysisResult(
        fetal_fraction=_ff(),
        category_counts={},
        filter_counts={},
        classifications=[item for _site, item in pairs],
        sites=[site for site, _item in pairs],
    )


def test_a_site_without_a_group_is_counted_by_its_reason() -> None:
    pairs = [
        (_site(0, father_state="het", vaf=0.05, chrom="X", is_autosomal=False),
         _classification("x", category=None, flags=["sex_chromosome_unsupported"])),
        (_site(1, father_state="het", vaf=None),
         _classification("a", category=None, fetal_inheritance="paternal_not_transmitted", flags=["undetectable_at_ff"])),
        (_site(2, father_state="het", vaf=None),
         _classification("b", category=None, flags=["low_depth_dropout"])),
        (_site(3, father_state="het", vaf=None),
         _classification("c", category=None, flags=["no_plasma_depth"])),
        (_site(4, father_state="hom_ref", vaf=None),
         _classification("d", category=None, flags=["no_alt_signal"])),
        (_site(5, father_state="het", vaf=None), _classification("e", category=None)),
    ]
    stats = nipt_variant_statistics(_result(pairs), QC)
    assert stats.classified_sites == 0
    assert stats.not_classified == {
        "sex_chromosome": 1,
        "undetectable_at_fetal_fraction": 1,
        "low_plasma_depth": 1,
        "no_plasma_depth": 1,
        "no_alt_signal": 1,
        "other": 1,
    }
    # A sex chromosome has no transmission row: the fetal sex reads chrX.
    assert [row.chrom for row in stats.chromosomes] == ["1"]
    assert stats.father_genotypes == {"het": 5, "hom_ref": 1}


def _paternal_sites(chrom: str, *, transmitted: int, not_transmitted: int, father: str = "het") -> list:
    """Sites where the mother does not carry the father's allele and the plasma is deep enough
    to see it (the paternity check's informative sites)."""
    pairs = []
    for index in range(transmitted + not_transmitted):
        seen = index < transmitted
        site = _site(index, father_state=father, vaf=0.05 if seen else None, chrom=chrom)
        pairs.append((site, _classification(site.variant_id, category=7 if seen else None)))
    return pairs


def _maternal_sites(chrom: str, *, inherited: int, not_inherited: int, flags: list[str] | None = None) -> list:
    """The mother's het alleles beside a hom-ref father."""
    pairs = []
    for index in range(inherited + not_inherited):
        seen = index < inherited
        site = _site(100 + index, father_state="hom_ref", vaf=0.489 if seen else 0.439, chrom=chrom)
        item = _classification(
            site.variant_id,
            category=3 if seen else 2,
            flags=flags,
            maternal_allele_probability=0.99 if seen else 0.01,
        )
        pairs.append((site, item))
    return pairs


def test_transmission_per_autosome_counts_each_parents_alleles_the_fetus_inherited() -> None:
    pairs = (
        _paternal_sites("1", transmitted=12, not_transmitted=10)
        + _paternal_sites("1", transmitted=4, not_transmitted=0, father="hom_alt")
        + _maternal_sites("1", inherited=9, not_inherited=11)
        + _maternal_sites("2", inherited=3, not_inherited=2, flags=["ambiguous"])
    )
    stats = nipt_variant_statistics(_result(pairs), QC)
    rows = {row.chrom: row for row in stats.chromosomes}
    one = rows["1"]
    assert (one.paternal_het_transmitted, one.paternal_het_not_transmitted) == (12, 10)
    assert (one.paternal_hom_alt_transmitted, one.paternal_hom_alt_not_transmitted) == (4, 0)
    assert (one.maternal_het_inherited, one.maternal_het_not_inherited) == (9, 11)
    assert one.marks == []
    # An ambiguous classification does not say which allele the fetus inherited.
    assert (rows["2"].maternal_het_inherited, rows["2"].maternal_het_not_inherited) == (0, 0)


@pytest.mark.parametrize(
    ("transmitted", "not_transmitted", "marked"),
    [
        (0, CHROMOSOME_MARK_MIN_SITES, True),
        (0, CHROMOSOME_MARK_MIN_SITES - 1, False),  # too few informative sites
        (1, CHROMOSOME_MARK_MIN_SITES, False),  # one paternal allele reached the fetus
    ],
)
def test_an_autosome_without_the_fathers_alleles_is_marked(transmitted: int, not_transmitted: int, marked: bool) -> None:
    pairs = _paternal_sites("7", transmitted=transmitted, not_transmitted=not_transmitted)
    [row] = nipt_variant_statistics(_result(pairs), QC).chromosomes
    assert row.marks == (["no_paternal_alleles"] if marked else [])


@pytest.mark.parametrize(
    ("inherited", "not_inherited", "marked"),
    [
        (0, CHROMOSOME_MARK_MIN_SITES, True),
        (0, CHROMOSOME_MARK_MIN_SITES - 1, False),
        (1, CHROMOSOME_MARK_MIN_SITES, False),
    ],
)
def test_an_autosome_without_the_mothers_alleles_is_marked(inherited: int, not_inherited: int, marked: bool) -> None:
    pairs = _maternal_sites("15", inherited=inherited, not_inherited=not_inherited)
    [row] = nipt_variant_statistics(_result(pairs), QC).chromosomes
    assert row.marks == (["no_maternal_alleles"] if marked else [])


def test_the_autosomes_come_in_karyotype_order_with_the_fetus_hom_alt_sites() -> None:
    pairs = []
    for chrom in ("10", "2", "1"):
        site = _site(0, father_state="hom_alt", vaf=1.0, chrom=f"chr{chrom}")
        pairs.append((site, _classification(site.variant_id, category=6)))
    site = _site(1, father_state="het", vaf=0.539, chrom="chr2")
    pairs.append((site, _classification(site.variant_id, category=4)))
    stats = nipt_variant_statistics(_result(pairs), QC)
    assert [(row.chrom, row.fetal_hom_alt) for row in stats.chromosomes] == [("1", 1), ("2", 2), ("10", 1)]


def test_the_analysis_tallies_the_callers_own_filter_of_the_plasma_calls_that_went_in() -> None:
    sites, made = _family_sites()
    plasma_calls = [site for site in sites if site.cf_vaf is not None]
    plasma_calls[0].cf_filters = ["germline", "multiallelic"]
    plasma_calls[1].cf_filters = ["weak_evidence"]
    plasma_calls[2].cf_filters = ["PASS"]
    plasma_calls[3].cf_filters = ["."]
    result = run_nipt_analysis(sites, QC)
    assert result.filter_counts["total_in"] == len(plasma_calls)
    assert result.caller_filters == {
        "PASS": len(plasma_calls) - 2,
        "germline": 1,
        "multiallelic": 1,
        "weak_evidence": 1,
    }
    # The paternal-only sites have no plasma call to tally.
    assert made["8"] + made[PATERNAL_NOT_TRANSMITTED] == result.filter_counts["paternal_only"]


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ([], ["PASS"]),
        (["."], ["PASS"]),
        (["PASS"], ["PASS"]),
        (["weak_evidence", "germline", "weak_evidence"], ["germline", "weak_evidence"]),
        (["PASS", "slippage"], ["slippage"]),
    ],
)
def test_a_calls_filter_values_are_counted_once_each(filters: list[str], expected: list[str]) -> None:
    assert caller_filter_values(filters) == expected
