"""Monogenic NIPT: fetal fraction, classifications, coverage, artifacts."""

from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from .variants import (
    VariantOut,
)


class NiptFetalFractionOut(BaseModel):
    """Fetal-fraction estimate for a monogenic NIPT family."""

    ff: float
    ff_computed: Optional[float] = None
    ff_median: Optional[float] = None
    ci_low: Optional[float] = None
    ci_high: Optional[float] = None
    n_sites: int
    method: str
    low_confidence: bool
    # The 5th and 95th percentile of the FF sites' allele fractions (the de novo window).
    vaf_q05: Optional[float] = None
    vaf_q95: Optional[float] = None


class NiptDeNovoWindowOut(BaseModel):
    """The family's fetal window the de novo triage reads (allele fractions)."""

    strict_min: float
    strict_max: float
    loose_min: float
    loose_max: float


class NiptPaternityOut(BaseModel):
    """Whether the fetus inherited the father's alleles: at the autosomal sites where he
    carries one and the mother does not, with enough plasma depth to see it. The fetus
    inherits every allele he is homozygous for and half of his het ones."""

    hom_alt_transmitted: int
    hom_alt_not_transmitted: int
    het_transmitted: int
    het_not_transmitted: int
    hom_alt_rate: Optional[float] = None
    het_rate: Optional[float] = None
    status: Literal["pass", "warn", "fail"]
    message: str


class NiptFetalSexOut(BaseModel):
    """The fetal sex from the father's X alleles in the plasma and from its chrY coverage."""

    call: Literal["female", "male", "indeterminate", "discordant"]
    paternal_x: str
    x_transmitted: int
    x_not_transmitted: int
    informative_sites: int
    chry_profile: Optional[str] = None
    y_ratio: Optional[float] = None
    x_ratio: Optional[float] = None
    # Indicative only: the panel has a single chrY target.
    chry_fetal_fraction: Optional[float] = None


class NiptTargetCoverageGeneOut(BaseModel):
    gene: str
    targets: int
    weak_targets: int
    min_mean: Optional[float] = None
    mean_of_means: Optional[float] = None
    weak: List["NiptTargetOut"] = Field(default_factory=list)


class NiptTargetOut(BaseModel):
    """A capture target (an exon of a panel transcript) and its depth."""

    chr: str
    start: int
    end: int
    gene: str
    attribute: str
    mean: Optional[float] = None
    median: Optional[float] = None
    min: Optional[float] = None
    proportion_covered: Optional[float] = None


class NiptTargetCoverageOut(BaseModel):
    """The plasma's per-target coverage QC: every target, or a panel's or genes'. A target
    is weak when its mean depth is below ``critical_mean_depth`` or a base has no coverage."""

    targets: int
    median_mean: Optional[float] = None
    q05_mean: Optional[float] = None
    below_critical: int
    below_advisory: int
    zero_mean: int
    incomplete: int
    critical_mean_depth: float
    advisory_mean_depth: float
    genes: List[NiptTargetCoverageGeneOut] = Field(default_factory=list)


class NiptModelOut(BaseModel):
    """The model the analysis ran with: its validation, the cfDNA quality filter and the
    paternal genotype classes."""

    reference: str
    overdispersion: float
    maternal_het_bias: float
    min_quality: float
    min_alt_reads: int
    min_vaf: float
    vaf_ff_fraction: float
    max_strand_bias_fs: float
    father_het_min_vaf: float
    father_hom_alt_min_vaf: float
    min_father_depth: int


class NiptQcOut(BaseModel):
    """The NIPT quality checks: the de novo window, paternity, fetal sex, the plasma's sex
    profile and target coverage, and why cfDNA calls failed the quality filter."""

    de_novo_window: Optional[NiptDeNovoWindowOut] = None
    paternity: NiptPaternityOut
    fetal_sex: NiptFetalSexOut
    plasma_profile_status: Literal["pass", "warn", "fail", "unknown"]
    plasma_profile_message: str
    target_coverage: Optional[NiptTargetCoverageOut] = None
    quality_failures: Dict[str, int] = Field(default_factory=dict)
    model: NiptModelOut


class NiptSummaryOut(BaseModel):
    """Monogenic NIPT analysis summary: fetal fraction and category/filter counts."""

    family_id: str
    fetal_fraction: NiptFetalFractionOut
    category_counts: Dict[int, int]
    filter_counts: Dict[str, int]
    qc: Optional[NiptQcOut] = None


class NiptClassificationOut(BaseModel):
    """The NIPT classification block attached to each cfDNA variant.

    The variant payload itself is the full small-variant shape (``VariantOut``);
    this is the maternal/fetal interpretation layered on top. ``NiptVariantOut``
    (defined after ``VariantOut`` below) combines the two."""

    category: Optional[int] = None
    category_label: str
    maternal_state: str
    fetal_inheritance: str
    expected_vaf: float
    observed_vaf: Optional[float] = None
    confidence: float
    flags: List[str] = Field(default_factory=list)
    runner_up_category: Optional[int] = None
    runner_up_confidence: Optional[float] = None
    # The fetal-inheritance probabilities, where the site tells them.
    paternal_transmission_probability: Optional[float] = None
    maternal_allele_probability: Optional[float] = None
    fetal_hom_alt_probability: Optional[float] = None
    fetal_genotype_posterior: Optional[Dict[str, float]] = None
    quality_failures: List[str] = Field(default_factory=list)
    # The reads behind the call: the plasma's (estimated from its coverage when it has no
    # call there) and the father's genotype class, allele fraction and depth.
    cf_alt_reads: Optional[int] = None
    cf_depth: Optional[int] = None
    cf_depth_estimated: bool = False
    father_state: Optional[str] = None
    father_vaf: Optional[float] = None
    father_depth: Optional[int] = None
    de_novo: Optional["NiptDeNovoTriageOut"] = None


class NiptDeNovoTriageOut(BaseModel):
    """A de novo candidate's triage (the R NIPT-M rules): where its allele fraction falls
    in the fetal window, its score and priority, and what lowered or raised it."""

    window: Literal["strict", "loose", "below", "above"]
    score: int
    label: Literal["high", "medium", "low", "excluded_recurrent", "outside_window"]
    reasons: List[str] = Field(default_factory=list)
    other_cfdna_carriers: int = 0


class NiptRecessiveAlleleOut(BaseModel):
    variant_id: str
    inherited_probability: Optional[float] = None
    category: Optional[int] = None
    note: Optional[str] = None


class NiptRecessiveGeneOut(BaseModel):
    """A gene where both parents carry an allele: whether the fetus inherited each, and
    the probability it inherited one of each (a carrier couple's prior is 25%)."""

    gene: str
    maternal: List[NiptRecessiveAlleleOut]
    paternal: List[NiptRecessiveAlleleOut]
    risk: Optional[float] = None
    maternal_variant_id: Optional[str] = None
    paternal_variant_id: Optional[str] = None
    risk_uses_prior: bool = False


class NiptVariantPage(BaseModel):
    """A page of classified cfDNA variants plus the family's fetal fraction."""

    family_id: str
    total: int
    # True when more variants matched the scope than the list classifies at once
    # (``count_limit``, the first ones in genomic order): ``total`` is then a lower bound,
    # and the list stops part-way through the genome.
    total_is_estimated: bool = False
    count_limit: Optional[int] = None
    fetal_fraction: NiptFetalFractionOut
    # NiptVariantOut subclasses VariantOut, which is defined later in this module.
    variants: List["NiptVariantOut"]
    # The recessive view: the genes where both parents carry an allele, highest risk first.
    recessive_genes: List[NiptRecessiveGeneOut] = Field(default_factory=list)


class NiptCoverageRegionOut(BaseModel):
    """On-target coverage for a single target region."""

    label: str
    chr: str
    start: int
    end: int
    median_coverage: Optional[float] = None
    covered_bases: int
    target_bases: int


class NiptCoverageLowRegionOut(BaseModel):
    """A panel gene / target region flagged as inadequately interrogated."""

    label: str
    chr: str
    median_coverage: Optional[float] = None
    covered_fraction: float
    reason: str  # "no_coverage" | "low_depth" | "partial_coverage"


class NiptCoverageSummaryOut(BaseModel):
    """Median on-target coverage for a monogenic NIPT family's cfDNA sample.

    ``low_coverage_regions`` is a compact QC list (only the failing genes, not
    the full per-region table) flagging genes that may harbour silent gaps.
    """

    family_id: str
    overall_median_on_target: Optional[float] = None
    target_region_count: int
    per_region: List[NiptCoverageRegionOut]
    min_depth: float
    min_covered_fraction: float
    low_coverage_regions: List[NiptCoverageLowRegionOut] = Field(default_factory=list)
    # The capture targets' QC when the plasma has a per-target coverage table; the fields
    # above then stay empty.
    targets: Optional[NiptTargetCoverageOut] = None


class NiptArtifactOut(BaseModel):
    """A recurrent-artifact (panel-of-normals) entry for monogenic NIPT."""

    id: str
    assembly_id: str
    assay_key: str
    variant_id: str
    recurrence_count: int
    source: str
    label: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class NiptArtifactCreate(BaseModel):
    """Create/update a NIPT artifact within an assembly + assay/panel scope."""

    assembly_id: str
    variant_id: str
    assay_key: str = "nipt_cfdna"
    label: Optional[str] = None
    source: Literal["curated", "auto"] = "curated"
    recurrence_count: int = 0


class NiptArtifactAutoSeed(BaseModel):
    """Seed the artifact list from internal cohort recurrence."""

    assembly_id: str
    assay_key: str = "nipt_cfdna"
    min_carrier_samples: int = Field(default=5, ge=1)


class NiptArtifactAutoSeedOut(BaseModel):
    seeded: int
    min_carrier_samples: int


class NiptArtifactImportOut(BaseModel):
    """What an artefact-table import did: the rows it read, those the table does not flag
    as artefacts or could not read, the alleles it added, and those it refused because
    their annotation is common or may assert pathogenic in ClinVar."""

    rows_read: int
    not_flagged: int
    invalid: int
    imported: int
    protected_common: int
    protected_clinvar: int


class NiptVariantOut(VariantOut):
    """A classified cfDNA variant: the full small-variant payload plus the NIPT
    classification block. Defined here so it can extend ``VariantOut`` (declared
    earlier in this module); ``NiptVariantPage`` forward-references it."""

    nipt: NiptClassificationOut


NiptVariantPage.model_rebuild()
NiptClassificationOut.model_rebuild()
NiptTargetCoverageGeneOut.model_rebuild()
