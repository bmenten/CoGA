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
    points: list[AncestryReferencePoint]


class AncestryRunOut(BaseModel):
    id: str
    source_id: str
    reference_sha256: str
    status: Literal["queued", "running", "completed", "failed", "interrupted"]
    error: str | None = None
    requested_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class AncestrySampleOut(BaseModel):
    sample_id: str
    status: Literal["projected", "insufficient_data"]
    pcs: list[float] | None = None
    call_rate: float
    qc: dict[str, int]


class AncestryAnalysisOut(BaseModel):
    run_id: str
    source_id: str
    reference_sha256: str
    samples: list[AncestrySampleOut]
    calculation_version: str
    assumptions: dict[str, str | bool | float | int]
    created_at: datetime
    current_source: bool = False
    current_reference: bool = False


class FamilyAncestryOut(BaseModel):
    project_id: str
    source_state: str
    can_calculate: bool = False
    active_reference_sha256: str | None = None
    reference: AncestryReferenceOut | None = None
    analysis: AncestryAnalysisOut | None = None
    runs: list[AncestryRunOut] = Field(default_factory=list)
    notices: list[str] = Field(default_factory=list)
