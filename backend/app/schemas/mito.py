"""Mitochondrial (mtDNA) analysis."""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from .reviews import (
    SmallVariantReviewOut,
)


class MitoDNACoverageOut(BaseModel):
    mean_depth: Optional[float] = None
    min_depth: Optional[float] = None
    max_depth: Optional[float] = None
    breadth: Optional[float] = None
    source: Optional[str] = None
    regions: int = 0


class MitoDNAQcOut(BaseModel):
    # Shares the QC vocabulary used by sample-integrity QC (`QcStatus`) and the
    # sequencing-QC verdict, rather than the `warning`/`unknown` spelling this module
    # used to have on its own. `skip` means not assessed — no acceptance limit is
    # configured, or there was nothing to measure — and is deliberately not `pass`.
    status: Literal["pass", "warn", "fail", "skip"] = "skip"
    notes: List[str] = Field(default_factory=list)
    contamination: Optional[float] = None
    mean_depth: Optional[float] = None
    min_mean_depth: Optional[float] = None


class MitoDNASampleOut(BaseModel):
    sample_id: str
    role: Optional[str] = None
    affected: Optional[bool] = None
    sex: Optional[str] = None
    haplogroup: Optional[str] = None
    coverage: MitoDNACoverageOut = Field(default_factory=MitoDNACoverageOut)
    qc: MitoDNAQcOut = Field(default_factory=MitoDNAQcOut)


class MitoDNAVariantSampleCallOut(BaseModel):
    sample: str
    role: Optional[str] = None
    affected: Optional[bool] = None
    sex: Optional[str] = None
    genotype: str
    allele_fraction: Optional[float] = None
    depth: Optional[int] = None
    alt_depth: Optional[int] = None
    zygosity: Literal["homoplasmic", "heteroplasmic", "low_level", "reference", "no_call", "unknown"] = "unknown"
    display: str


class MitoDNAVariantAnnotationOut(BaseModel):
    region: str
    gene: Optional[str] = None
    consequence: Optional[str] = None
    # Normalised consequence terms (e.g. ``synonymous_variant``) parsed off the
    # underlying small-variant annotation so the UI can filter on consequence.
    consequence_terms: List[str] = Field(default_factory=list)
    # Numeric gnomAD allele frequency (chrM) when annotated; used to filter out
    # common variants. ``None`` when the variant carries no gnomAD frequency.
    gnomad_af: Optional[float] = None
    category: Optional[str] = None
    clinical_significance: Literal[
        "pathogenic",
        "likely_pathogenic",
        "benign",
        "likely_benign",
        "reported",
        "uncertain",
        "polymorphism",
        "unknown",
    ] = "unknown"
    disorders: List[str] = Field(default_factory=list)
    polymorphism_notes: List[str] = Field(default_factory=list)
    mitomap_query: str
    mitomap_url: str
    source_keys: List[str] = Field(default_factory=list)


class MitoDNAVariantOut(BaseModel):
    variant_id: str
    position: int
    ref: str
    alt: str
    label: str
    type: str
    rsid: Optional[str] = None
    annotation: MitoDNAVariantAnnotationOut
    calls: Dict[str, MitoDNAVariantSampleCallOut] = Field(default_factory=dict)
    maternal_transmission: Literal[
        "maternal_shared",
        "maternal_not_observed",
        "mother_not_assessed",
        "no_proband",
        "maternal_only",
        "father_only",
        "family_private",
        "no_alt_calls",
        "unknown",
    ] = "unknown"
    # MT variants share the small-variant review store, so the same ACMG
    # classification / tags / notes payload is attached here.
    review: Optional[SmallVariantReviewOut] = None


class MitoDNAStructuralVariantCallOut(BaseModel):
    sample: str
    role: Optional[str] = None
    genotype: str
    # The caller's fraction of reads carrying the event (Sniffles INFO/VAF): its
    # heteroplasmy level.
    heteroplasmy: Optional[float] = None
    read_support: Optional[int] = None


class MitoDNAStructuralVariantOut(BaseModel):
    variant_id: str
    sv_type: str
    start: int
    end: int
    length: Optional[int] = None
    genes: List[str] = Field(default_factory=list)
    source: Optional[str] = None
    filters: List[str] = Field(default_factory=list)
    calls: Dict[str, MitoDNAStructuralVariantCallOut] = Field(default_factory=dict)


class FamilyMitoDNAAnalysisOut(BaseModel):
    samples: List[MitoDNASampleOut] = Field(default_factory=list)
    variants: List[MitoDNAVariantOut] = Field(default_factory=list)
    # chrM structural variants (large deletions and duplications), with each sample's
    # heteroplasmy.
    structural_variants: List[MitoDNAStructuralVariantOut] = Field(default_factory=list)
    # Presence summary; with ``count_only`` the heavy ``variants``/``samples``
    # payload is skipped and only these two fields are returned.
    variant_count: int = 0
    has_coverage: bool = False
    qc_notes: List[str] = Field(default_factory=list)
    heteroplasmy_threshold: float = 0.02
    homoplasmy_threshold: float = 0.95
