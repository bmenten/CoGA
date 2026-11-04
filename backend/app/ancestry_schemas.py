"""Exploratory ancestry projection, separate from clinical sign-out."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class AncestryReferencePoint(BaseModel):
    id: str
    population: str
    pcs: list[float]


class AncestryReferenceOut(BaseModel):
    sha256: str
    name: str
    genome_build: Literal["GRCh38"] = "GRCh38"
    source_url: str
    source_revision: str
    method: str
    variant_count: int
    sample_count: int
    explained_variance: list[float]
    populations: dict[str, str]
    # The markers an exome covers; a model without a mask reads no callset as an exome.
    exome_mask_sha256: str | None = None
    exome_mask_definition: str | None = None
    points: list[AncestryReferencePoint]


class AncestryRunOut(BaseModel):
    id: str
    # The WGS file source of a calculation from before the callsets were read (legacy).
    source_id: str | None = None
    trigger: Literal["user", "automatic"] = "user"
    reference_sha256: str
    status: Literal["queued", "running", "completed", "failed", "interrupted"]
    error: str | None = None
    requested_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


AncestryCallsetKind = Literal["complete", "genome", "exome", "partial"]


class AncestryCallsetScanOut(BaseModel):
    """What one stored callset holds of one individual at the reference markers."""

    callset: str
    label: str
    kind: AncestryCallsetKind
    # Markers where the individual has a non-reference call (any filter), and how many a fully
    # sequenced genome is expected to have: their ratio says how much of the markers it covers.
    carriers: int
    expected_carriers: float
    coverage_ratio: float
    exome_ratio: float | None = None
    outside_exome_ratio: float | None = None
    # Markers with any genotype call, reference ones included.
    explicit_calls: int
    accepts_unfiltered: bool
    # Markers the callset is taken to cover, and those of them with a usable genotype.
    covered_markers: int
    usable_markers: int
    call_rate: float
    qc: dict[str, int] = Field(default_factory=dict)
    projectable: bool
    reason: str | None = None


class AncestrySampleOut(BaseModel):
    sample_id: str
    status: Literal["projected", "insufficient_data", "excluded"]
    pcs: list[float] | None = None
    call_rate: float = 0.0
    qc: dict[str, int] = Field(default_factory=dict)
    # The callset the projection read, how it was read, and why an individual has none.
    callset: str | None = None
    kind: AncestryCallsetKind | None = None
    usable_markers: int | None = None
    reason: str | None = None
    candidates: list[AncestryCallsetScanOut] = Field(default_factory=list)


class AncestryAnalysisOut(BaseModel):
    run_id: str
    source_id: str | None = None
    reference_sha256: str
    samples: list[AncestrySampleOut]
    calculation_version: str
    assumptions: dict[str, str | bool | float | int]
    created_at: datetime
    # The family's small-variant data version the projection read (none for a legacy one).
    storage_version: str | None = None
    current_genotypes: bool = False
    current_reference: bool = False


class FamilyAncestryOut(BaseModel):
    project_id: str
    # ``ready`` when a calculation can run now, else why not (ancestry_service.STATE_NOTICES).
    state: str
    can_calculate: bool = False
    active_reference_sha256: str | None = None
    reference: AncestryReferenceOut | None = None
    analysis: AncestryAnalysisOut | None = None
    runs: list[AncestryRunOut] = Field(default_factory=list)
    notices: list[str] = Field(default_factory=list)
