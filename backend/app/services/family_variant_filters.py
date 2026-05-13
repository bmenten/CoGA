from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import List


GENOTYPE_TOKEN_PATTERN = re.compile(r"absent|hom_alt|hom_ref|hom|het|ref|wt|\.\/\.|[0-9.][/|][0-9.]")
GENOTYPE_ALIASES = {
    "het": ["0/1", "1/0", "0|1", "1|0"],
    "hom": ["1/1", "1|1"],
    "hom_alt": ["1/1", "1|1"],
    "ref": ["0/0", "0|0"],
    "wt": ["0/0", "0|0"],
    "hom_ref": ["0/0", "0|0"],
}


@dataclass(slots=True)
class StructuralSampleFilter:
    sample_name: str
    genotype_values: List[str] = field(default_factory=list)
    minimum_quality: float | None = None
    read_support: str | None = None
    filter_text: str | None = None
    include_absent: bool = False


@dataclass(slots=True)
class SmallVariantSampleFilter:
    sample_name: str
    genotype_values: List[str] = field(default_factory=list)
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


def split_filter_entry(entry: str, expected_parts: int) -> List[str]:
    parts = entry.split(":")
    if len(parts) < expected_parts:
        parts.extend([""] * (expected_parts - len(parts)))
    return parts


def parse_genotype_filter(raw_value: str | None) -> tuple[List[str], bool]:
    if not raw_value:
        return [], False

    matches = GENOTYPE_TOKEN_PATTERN.findall(raw_value.lower())
    genotype_values = matches if matches else raw_value.split("|")
    expanded_values: list[str] = []
    for genotype in genotype_values:
        expanded_values.extend(GENOTYPE_ALIASES.get(genotype, [genotype]))
    include_absent = "absent" in genotype_values or any(
        genotype in {"0/0", "0|0"} for genotype in expanded_values
    )
    return [genotype for genotype in expanded_values if genotype != "absent"], include_absent


def parse_structural_sample_filter(entry: str) -> StructuralSampleFilter | None:
    parts = split_filter_entry(entry, expected_parts=5)
    sample_name = parts[0]
    if not sample_name:
        return None

    genotype_values, include_absent = parse_genotype_filter(parts[1] or None)
    return StructuralSampleFilter(
        sample_name=sample_name,
        genotype_values=genotype_values,
        minimum_quality=float(parts[2]) if parts[2] else None,
        read_support=parts[3] or None,
        filter_text=parts[4] or None,
        include_absent=include_absent,
    )


def parse_small_variant_sample_filter(entry: str) -> SmallVariantSampleFilter | None:
    parts = split_filter_entry(entry, expected_parts=6)
    sample_name = parts[0]
    if not sample_name:
        return None

    genotype_values, include_absent = parse_genotype_filter(parts[1] or None)

    minimum_allele_frequency = None
    if parts[4]:
        try:
            minimum_allele_frequency = float(parts[4])
        except Exception:
            pass

    minimum_alt_depth = None
    if parts[5]:
        try:
            minimum_alt_depth = int(float(parts[5]))
        except Exception:
            pass

    return SmallVariantSampleFilter(
        sample_name=sample_name,
        genotype_values=genotype_values,
        minimum_genotype_quality=float(parts[2]) if parts[2] else None,
        minimum_depth=int(float(parts[3])) if parts[3] else None,
        minimum_allele_frequency=minimum_allele_frequency,
        minimum_alt_depth=minimum_alt_depth,
        include_absent=include_absent,
    )
