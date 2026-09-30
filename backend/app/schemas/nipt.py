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
    ff_external: Optional[float] = None
    ff_median: Optional[float] = None
    ci_low: Optional[float] = None
    ci_high: Optional[float] = None
    n_sites: int
    method: str
    low_confidence: bool
    disagreement: bool


class NiptSummaryOut(BaseModel):
    """Monogenic NIPT analysis summary: fetal fraction and category/filter counts."""

    family_id: str
    fetal_fraction: NiptFetalFractionOut
    category_counts: Dict[int, int]
    filter_counts: Dict[str, int]


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


class NiptVariantOut(VariantOut):
    """A classified cfDNA variant: the full small-variant payload plus the NIPT
    classification block. Defined here so it can extend ``VariantOut`` (declared
    earlier in this module); ``NiptVariantPage`` forward-references it."""

    nipt: NiptClassificationOut


NiptVariantPage.model_rebuild()
