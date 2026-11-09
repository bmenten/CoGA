"""The variant summary's NIPT-M statistics of a monogenic NIPT family.

Descriptive counts of what the NIPT analysis made of the plasma, for the NIPT-M tab of the
*Variant summary* page. They are read from the analysis the NIPT page runs (the same fetal
fraction, quality filter, artifact list and categories), so the two pages give the same
numbers. No clinical rule reads them and the signed record does not hold them.

The plasma's genotypes cannot be read from the calls: Mutect2 in tumour-only mode writes GT
0/1 whatever the allele fraction. The analysis classes each site by its allele fraction
instead, and this module groups its classes:

* by the mother's and the fetus's genotype: categories 2 to 6 for a mother who carries the
  allele, 7 (the father's allele) and 1 (de novo) for one who does not, and, where the
  fetus is hom-ref beside a hom-ref mother, category 8 (the father is hom-alt and his allele
  is missing: a false negative or a paternity signal) and the father's het alleles the
  fetus did not inherit;
* per group: the expected plasma allele fraction and the observed one of the plasma's own
  calls (their median and histogram), the confidence, the father's genotype and the variant
  class;
* per autosome: the father's alleles the fetus inherited (the paternity check's counts), the
  mother's, and the fetus's hom-alt sites. An autosome where none of a parent's alleles
  reached the fetus over at least ``CHROMOSOME_MARK_MIN_SITES`` informative sites is marked:
  it can point to uniparental disomy or a deletion; CoGA does not call either.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import statistics
from typing import Iterable, Literal, Sequence

from ..schemas import NiptChromosomeTransmissionOut, NiptStatisticsGroupOut
from .data_scope import normalize_chromosome
from .nipt_analysis import (
    FetalFractionEstimate,
    NiptAnalysisResult,
    NiptClassification,
    NiptQualityThresholds,
    NiptSiteObservation,
    expected_plasma_vaf,
    has_own_plasma_call,
    paternal_transmission_evidence,
    trusted_father_state,
)

# The histogram's bins of plasma allele fraction (100 from 0 to 1): narrow enough to show the
# category-7 peak at FF/2 and the maternal het band's three peaks, FF/2 apart, as peaks.
VAF_BIN_WIDTH = 0.01
_VAF_BINS = 100
# A classification at or above this confidence counts as high confidence.
HIGH_CONFIDENCE = 0.99
# An autosome is marked when none of a parent's alleles reached the fetus over at least this
# many informative sites (the owner's choice of 2026-11-09; the minimum is for QA to set).
CHROMOSOME_MARK_MIN_SITES = 20

PATERNAL_NOT_TRANSMITTED = "paternal_not_transmitted"

Genotype = Literal["hom_ref", "het", "hom_alt"]


@dataclass(frozen=True, slots=True)
class _Group:
    key: str
    category: int | None
    maternal: Genotype
    fetal: Genotype


# The groups by the mother's genotype (hom-ref, het, hom-alt) and then the fetus's.
GROUPS: tuple[_Group, ...] = (
    _Group("8", 8, "hom_ref", "hom_ref"),
    _Group(PATERNAL_NOT_TRANSMITTED, None, "hom_ref", "hom_ref"),
    _Group("7", 7, "hom_ref", "het"),
    _Group("1", 1, "hom_ref", "het"),
    _Group("2", 2, "het", "hom_ref"),
    _Group("3", 3, "het", "het"),
    _Group("4", 4, "het", "hom_alt"),
    _Group("5", 5, "hom_alt", "het"),
    _Group("6", 6, "hom_alt", "hom_alt"),
)
_GROUP_BY_CATEGORY = {group.category: group for group in GROUPS if group.category is not None}
_PATERNAL_NOT_TRANSMITTED_GROUP = next(group for group in GROUPS if group.category is None)

# Why a site the analysis read has no group, in the order a site's flags are read.
NOT_CLASSIFIED_REASONS: tuple[tuple[str, str], ...] = (
    ("sex_chromosome_unsupported", "sex_chromosome"),
    ("no_plasma_depth", "no_plasma_depth"),
    ("low_depth_dropout", "low_plasma_depth"),
    ("undetectable_at_ff", "undetectable_at_fetal_fraction"),
    ("no_alt_signal", "no_alt_signal"),
)
_OTHER_REASON = "other"

# A maternal het site tells whether the fetus inherited her allele beside a father whose
# genotype is known and leaves one reading: hom-ref (category 3 is her allele) or hom-alt
# (category 4 is). Beside a het father either parent's allele can make the fetus het.
_MATERNAL_INFORMATIVE_FATHERS = frozenset({"hom_ref", "hom_alt"})
_MATERNAL_HET_CATEGORIES = frozenset({2, 3, 4})
_FETAL_HOM_ALT_CATEGORIES = frozenset({4, 6})
_UNSURE_FLAGS = frozenset({"ambiguous", "ff_too_low"})


@dataclass(slots=True)
class NiptVariantStatistics:
    classified_sites: int
    groups: list[NiptStatisticsGroupOut]
    not_classified: dict[str, int]
    chromosomes: list[NiptChromosomeTransmissionOut]
    father_genotypes: dict[str, int]


def group_of(classification: NiptClassification) -> _Group | None:
    """The group of a site's classification, or None when it has none: a category,
    or the father's allele the fetus did not inherit (no category, and not merely too thin
    to see at the fetal fraction)."""
    if classification.category is not None:
        return _GROUP_BY_CATEGORY.get(classification.category)
    if (
        classification.fetal_inheritance == PATERNAL_NOT_TRANSMITTED
        and "undetectable_at_ff" not in classification.flags
    ):
        return _PATERNAL_NOT_TRANSMITTED_GROUP
    return None


def not_classified_reason(classification: NiptClassification) -> str:
    for flag, reason in NOT_CLASSIFIED_REASONS:
        if flag in classification.flags:
            return reason
    return _OTHER_REASON


def vaf_bin(vaf: float) -> int:
    """The histogram bin of an allele fraction; one on a bin's lower edge is that bin's (0.29
    divided by 0.01 is 28.9999… in floating point, so the division is rounded first)."""
    return min(max(int(round(vaf / VAF_BIN_WIDTH, 9)), 0), _VAF_BINS - 1)


def _median(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def _chromosome_rank(chrom: str) -> tuple[int, str]:
    return (int(chrom), "") if chrom.isdigit() else (99, chrom)


def _group_out(
    group: _Group,
    members: Sequence[tuple[NiptSiteObservation, NiptClassification]],
    ff: float,
    qc: NiptQualityThresholds,
) -> NiptStatisticsGroupOut:
    # The allele fractions of the plasma's own calls: an allele it has no call of (the father's
    # the fetus did not inherit, or one read off the coverage) has no fraction to draw.
    observed = [
        item.observed_vaf
        for site, item in members
        if item.observed_vaf is not None and has_own_plasma_call(site)
    ]
    histogram = [0] * _VAF_BINS
    for vaf in observed:
        histogram[vaf_bin(vaf)] += 1
    return NiptStatisticsGroupOut(
        key=group.key,
        category=group.category,
        maternal_genotype=group.maternal,
        fetal_genotype=group.fetal,
        sites=len(members),
        expected_vaf=expected_plasma_vaf(group.category, ff) if group.category is not None else None,
        observed_median_vaf=_median(observed),
        high_confidence=sum(1 for _site, item in members if item.confidence >= HIGH_CONFIDENCE),
        ambiguous=sum(1 for _site, item in members if "ambiguous" in item.flags),
        by_father=dict(Counter(trusted_father_state(site, qc) for site, _item in members)),
        by_variant_class=dict(Counter(site.variant_class for site, _item in members)),
        vaf_histogram=histogram,
    )


def _maternal_reading(
    site: NiptSiteObservation, item: NiptClassification, qc: NiptQualityThresholds
) -> bool | None:
    """Whether the fetus inherited the mother's het allele at the site (None: the site does
    not say, or not clearly)."""
    if item.category not in _MATERNAL_HET_CATEGORIES or _UNSURE_FLAGS.intersection(item.flags):
        return None
    if trusted_father_state(site, qc) not in _MATERNAL_INFORMATIVE_FATHERS:
        return None
    probability = item.maternal_allele_probability
    if probability is None:
        return None
    return probability >= 0.5


def _chromosome_out(
    chrom: str,
    pairs: Sequence[tuple[NiptSiteObservation, NiptClassification]],
    ff_estimate: FetalFractionEstimate,
    qc: NiptQualityThresholds,
    min_sites: int,
) -> NiptChromosomeTransmissionOut:
    evidence = paternal_transmission_evidence((site for site, _item in pairs), ff_estimate, qc)
    inherited = not_inherited = fetal_hom_alt = 0
    for site, item in pairs:
        if item.category in _FETAL_HOM_ALT_CATEGORIES:
            fetal_hom_alt += 1
        reading = _maternal_reading(site, item, qc)
        if reading is True:
            inherited += 1
        elif reading is False:
            not_inherited += 1
    marks: list[Literal["no_paternal_alleles", "no_maternal_alleles"]] = []
    paternal_transmitted = evidence.het_transmitted + evidence.hom_alt_transmitted
    if evidence.het_informative + evidence.hom_alt_informative >= min_sites and paternal_transmitted == 0:
        marks.append("no_paternal_alleles")
    if inherited + not_inherited >= min_sites and inherited == 0:
        marks.append("no_maternal_alleles")
    return NiptChromosomeTransmissionOut(
        chrom=chrom,
        paternal_het_transmitted=evidence.het_transmitted,
        paternal_het_not_transmitted=evidence.het_not_transmitted,
        paternal_hom_alt_transmitted=evidence.hom_alt_transmitted,
        paternal_hom_alt_not_transmitted=evidence.hom_alt_not_transmitted,
        maternal_het_inherited=inherited,
        maternal_het_not_inherited=not_inherited,
        fetal_hom_alt=fetal_hom_alt,
        marks=marks,
    )


def nipt_variant_statistics(
    analysis: NiptAnalysisResult,
    qc: NiptQualityThresholds,
    *,
    min_sites: int = CHROMOSOME_MARK_MIN_SITES,
) -> NiptVariantStatistics:
    """The NIPT-M statistics of one analysis run (its sites and their classifications)."""
    pairs: Iterable[tuple[NiptSiteObservation, NiptClassification]] = zip(
        analysis.sites, analysis.classifications, strict=True
    )
    ff_estimate = analysis.fetal_fraction
    grouped: dict[str, list[tuple[NiptSiteObservation, NiptClassification]]] = defaultdict(list)
    not_classified: Counter[str] = Counter()
    autosomes: dict[str, list[tuple[NiptSiteObservation, NiptClassification]]] = defaultdict(list)
    father_genotypes: Counter[str] = Counter()
    for site, item in pairs:
        father_genotypes[site.father_state] += 1
        group = group_of(item)
        if group is None:
            not_classified[not_classified_reason(item)] += 1
        else:
            grouped[group.key].append((site, item))
        if site.is_autosomal:
            autosomes[normalize_chromosome(site.chrom)].append((site, item))
    groups = [_group_out(group, grouped.get(group.key, []), ff_estimate.ff, qc) for group in GROUPS]
    chromosomes = [
        _chromosome_out(chrom, autosomes[chrom], ff_estimate, qc, min_sites)
        for chrom in sorted(autosomes, key=_chromosome_rank)
    ]
    return NiptVariantStatistics(
        classified_sites=sum(group.sites for group in groups),
        groups=groups,
        not_classified=dict(not_classified),
        chromosomes=chromosomes,
        father_genotypes=dict(father_genotypes),
    )
