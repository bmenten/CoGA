"""Public API contracts for the separate exploratory PGS module."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class PgsVersionOut(BaseModel):
    id: str
    pgs_id: str
    version: str
    revision: int
    archived: bool
    study: str
    source_url: str
    genome_build: Literal["GRCh38"] = "GRCh38"
    weights_sha256: str
    resolved_sha256: str
    reference: dict[str, str]
    variant_count: int
    created_at: datetime


class PgsCatalogueOut(BaseModel):
    shortlist: list[str]
    versions: list[PgsVersionOut]


class PgsArchiveIn(BaseModel):
    archived: bool


class PgsSourceOut(BaseModel):
    id: str
    import_job_id: str | None = None
    package_path: str
    relative_vcf: str
    sha256: str
    storage_version: str
    storage_fingerprint: str
    file_size: int
    file_mtime_ns: int
    samples: list[str]
    absent_record_is_hom_ref: bool
    imported_at: datetime


class PgsRunOut(BaseModel):
    id: str
    source_id: str
    version_ids: list[str]
    status: Literal["queued", "running", "completed", "failed", "interrupted"]
    error: str | None = None
    requested_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class PgsResultOut(BaseModel):
    id: str
    run_id: str
    source_id: str
    version_id: str
    sample_id: str
    raw_score: float | None = None
    status: Literal["complete", "incomplete"]
    qc: dict[str, int]
    calculation_version: str
    assumptions: dict[str, str | bool]
    created_at: datetime
    current_source: bool = False


class FamilyPgsOut(BaseModel):
    project_id: str
    source_state: str
    sources: list[PgsSourceOut] = Field(default_factory=list)
    runs: list[PgsRunOut] = Field(default_factory=list)
    results: list[PgsResultOut] = Field(default_factory=list)
    versions: list[PgsVersionOut] = Field(default_factory=list)
    pending_version_ids: list[str] = Field(default_factory=list)
    can_calculate: bool = False
    notices: list[str] = Field(default_factory=list)
