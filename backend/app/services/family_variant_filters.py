from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import List

from .genotypes import HET, HOM_ALT, HOM_REF, GenotypeClass, classify_genotype


GENOTYPE_TOKEN_PATTERN = re.compile(r"absent|hom_alt|hom_ref|hom|het|ref|wt|\.\/\.|[0-9.][/|][0-9.]")
# A sample filter names genotype *classes* (#511). Words map to their class; a literal
# genotype such as ``1/1`` stands for its class too, so the UI's Hom group (``1/1|1|1``)
# also matches a haploid ``1`` or a ``2/2``, and its Het group a ``1/2``.
GENOTYPE_ALIASES: dict[str, GenotypeClass] = {
    "het": HET,
    "hom": HOM_ALT,
    "hom_alt": HOM_ALT,
    "ref": HOM_REF,
    "wt": HOM_REF,
    "hom_ref": HOM_REF,
}


class SampleFilterError(ValueError):
    """A sample filter carries a value that cannot be read (#686).

    The app answers it with a 422 naming the value. An unreadable AF or AD-alt minimum used to
    be dropped, which widened the search without saying so, and an unreadable GQ or DP one
    surfaced as a 500.
    """


@dataclass(slots=True)
class StructuralSampleFilter:
    sample_name: str
    genotype_classes: frozenset[GenotypeClass] = frozenset()
    minimum_quality: float | None = None
    read_support: str | None = None
    filter_text: str | None = None
    include_absent: bool = False


@dataclass(slots=True)
class SmallVariantSampleFilter:
    sample_name: str
    genotype_classes: frozenset[GenotypeClass] = frozenset()
    minimum_genotype_quality: float | None = None
    minimum_depth: int | None = None
    minimum_allele_frequency: float | None = None
    minimum_alt_depth: int | None = None
    include_absent: bool = False


@dataclass(slots=True)
class StructuralVariantQueryFilters:
    page: int
    page_size: int
    chromosome: str | None = None
    start: int | None = None
    end: int | None = None
    length: int | None = None
    min_length: int | None = None
    variant_type: str | None = None
    source: str | None = None
    sample_filters: List[str] = field(default_factory=list)
    selected_samples: List[str] = field(default_factory=list)
    remote_chr: str | None = None
    remote_start: int | None = None
    gene: str | None = None
    panel_id: str | None = None
    inheritance: str | None = None
    phenotype: str | None = None
    hpo: str | None = None
    moi: str | None = None
    gencc_support: str | None = None
    region_flags: List[str] = field(default_factory=list)
    max_control_af: float | None = None
    max_population_af: float | None = None
    min_pli: float | None = None
    review_classifications: List[str] = field(default_factory=list)
    review_tags: List[str] = field(default_factory=list)
    exclude_review_tags: List[str] = field(default_factory=list)
    has_notes: bool = False
    overlap: bool = False

    def __post_init__(self) -> None:
        # Read every sample filter once, so an unreadable value is refused before any
        # search runs, whichever path the filters take (#686).
        for entry in self.sample_filters:
            parse_structural_sample_filter(entry)


@dataclass(slots=True)
class SmallVariantQueryFilters:
    page: int
    page_size: int
    chromosome: str | None = None
    start: int | None = None
    end: int | None = None
    intervals: str | None = None
    inheritance: str | None = None
    expanded_carrier_screening: bool = False
    phase_set: int | None = None
    variant_type: str | None = None
    source: str | None = None
    gene: str | None = None
    transcript: str | None = None
    impact: List[str] = field(default_factory=list)
    effect: List[str] = field(default_factory=list)
    clinvar: List[str] = field(default_factory=list)
    exclude_clinvar: List[str] = field(default_factory=list)
    # When set, a ClinVar Pathogenic/Likely_pathogenic variant is kept even if it
    # exceeds the gnomAD frequency / hom / hemi / AC thresholds (P/LP "rescue").
    clinvar_overrides_frequency: bool = False
    exclude_gene: str | None = None
    exclude_intervals: str | None = None
    rsid: str | None = None
    hgvsc: str | None = None
    hgvsp: str | None = None
    canonical_only: bool = False
    mane_only: bool = False
    lof_only: bool = False
    max_gnomad_af: float | None = None
    max_gnomad_exomes_af: float | None = None
    max_gnomad_genomes_af: float | None = None
    max_gnomad_popmax_af: float | None = None
    max_topmed_af: float | None = None
    max_gnomad_ac: int | None = None
    max_gnomad_hom_count: int | None = None
    max_gnomad_hemi_count: int | None = None
    min_cadd: float | None = None
    min_revel: float | None = None
    min_spliceai: float | None = None
    sift: str | None = None
    polyphen: str | None = None
    panel_id: str | None = None
    sample_filters: List[str] = field(default_factory=list)
    overlap: bool = False
    # Restrict to variants whose gene is also hit by a structural variant (the cross-type
    # "second hit"). ``sv_hit_genes`` is resolved server-side from the family's SV→gene index
    # (not a request field) and intersected with the other gene/panel constraints.
    require_sv_second_hit: bool = False
    sv_hit_genes: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Read every sample filter once, so an unreadable value is refused before any
        # search runs, whichever path the filters take (#686).
        for entry in self.sample_filters:
            parse_small_variant_sample_filter(entry)


def _sample_filter_number(sample_name: str, label: str, value: str) -> float | None:
    """One numeric field of a sample filter: blank restricts nothing; anything else must be a
    finite number, or the filter is refused (#686)."""
    if not value.strip():
        return None
    try:
        number = float(value)
    except ValueError:
        number = math.nan
    if not math.isfinite(number):
        raise SampleFilterError(
            f"Sample filter for {sample_name}: {label} {value!r} is not a number."
        )
    return number


def _optional_int(value: float | None) -> int | None:
    return int(value) if value is not None else None


def split_filter_entry(entry: str, expected_parts: int) -> List[str]:
    parts = entry.split(":")
    if len(parts) < expected_parts:
        parts.extend([""] * (expected_parts - len(parts)))
    return parts


def parse_genotype_filter(raw_value: str | None) -> tuple[frozenset[GenotypeClass], bool]:
    """The genotype classes a sample filter asks for, and whether "not called" counts.

    Returns ``(classes, include_absent)``; empty classes mean "any genotype". Asking for
    reference also admits a sample with no call at the site, as before.
    """
    if not raw_value:
        return frozenset(), False

    matches = GENOTYPE_TOKEN_PATTERN.findall(raw_value.lower())
    tokens = matches if matches else raw_value.split("|")
    classes: set[GenotypeClass] = set()
    for token in tokens:
        if token == "absent":
            continue
        classes.add(GENOTYPE_ALIASES.get(token) or classify_genotype(token))
    include_absent = "absent" in tokens or HOM_REF in classes
    return frozenset(classes), include_absent


def parse_structural_sample_filter(entry: str) -> StructuralSampleFilter | None:
    parts = split_filter_entry(entry, expected_parts=5)
    sample_name = parts[0]
    if not sample_name:
        return None

    genotype_classes, include_absent = parse_genotype_filter(parts[1] or None)
    return StructuralSampleFilter(
        sample_name=sample_name,
        genotype_classes=genotype_classes,
        minimum_quality=_sample_filter_number(sample_name, "QUAL", parts[2]),
        read_support=parts[3] or None,
        filter_text=parts[4] or None,
        include_absent=include_absent,
    )


def parse_small_variant_sample_filter(entry: str) -> SmallVariantSampleFilter | None:
    parts = split_filter_entry(entry, expected_parts=6)
    sample_name = parts[0]
    if not sample_name:
        return None

    genotype_classes, include_absent = parse_genotype_filter(parts[1] or None)
    return SmallVariantSampleFilter(
        sample_name=sample_name,
        genotype_classes=genotype_classes,
        minimum_genotype_quality=_sample_filter_number(sample_name, "GQ", parts[2]),
        minimum_depth=_optional_int(_sample_filter_number(sample_name, "DP", parts[3])),
        minimum_allele_frequency=_sample_filter_number(sample_name, "AF", parts[4]),
        minimum_alt_depth=_optional_int(_sample_filter_number(sample_name, "AD alt", parts[5])),
        include_absent=include_absent,
    )
