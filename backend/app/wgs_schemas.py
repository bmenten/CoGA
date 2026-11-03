"""Verified WGS inputs for ancestry projection."""
from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field


class WgsSourceOut(BaseModel):
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
    raw_file_id: str | None = None
    qualification: dict[str, Any] = Field(default_factory=dict)


class WgsSourceDeclaration(BaseModel):
    file_id: str
    full_wgs: Literal[True]
    genome_build: Literal["GRCh38"]
    absent_record_is_hom_ref: bool
    allow_unfiltered: bool = False


class WgsSourceCandidateOut(BaseModel):
    """A retained family small-variant upload that could be registered as the WGS source."""
    file_id: str
    file_name: str
    file_size: int | None = None


class WgsSourcesOut(BaseModel):
    candidates: list[WgsSourceCandidateOut] = Field(default_factory=list)
    can_register: bool = False
