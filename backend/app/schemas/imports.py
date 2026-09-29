"""Family-package and PED imports, raw import files."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from .common import (
    ApiDocumentModel,
    ApiId,
)


class PedFamilyResult(BaseModel):
    family_id: str
    samples: List[str] = Field(default_factory=list)


class PedUploadResult(BaseModel):
    families: List[PedFamilyResult] = Field(default_factory=list)


class FamilyImportValidationIssue(BaseModel):
    code: str
    message: str
    dataset: Optional[str] = None
    sample_id: Optional[str] = None
    path: Optional[str] = None


class FamilyImportDatasetSummary(BaseModel):
    dataset_type: str
    enabled: bool = True
    status: Literal[
        "pending",
        "valid",
        "warning",
        "error",
        "disabled",
        "skipped",
        "running",
        "registered",
        "imported",
        "failed",
    ] = "pending"
    files: List[str] = Field(default_factory=list)
    samples: List[str] = Field(default_factory=list)
    message: Optional[str] = None
    summary: Dict[str, Any] = Field(default_factory=dict)


class FamilyPackageValidationOut(BaseModel):
    valid: bool
    family_id: Optional[str] = None
    manifest_path: Optional[str] = None
    ped_path: Optional[str] = None
    sample_ids: List[str] = Field(default_factory=list)
    errors: List[FamilyImportValidationIssue] = Field(default_factory=list)
    warnings: List[FamilyImportValidationIssue] = Field(default_factory=list)
    datasets: List[FamilyImportDatasetSummary] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class FamilyManifestFileAvailability(BaseModel):
    role: str
    path: str
    exists: bool
    sample_id: Optional[str] = None


class FamilyManifestDatasetAvailability(BaseModel):
    dataset_type: str
    enabled: bool
    complete: bool
    files: List[FamilyManifestFileAvailability] = Field(default_factory=list)
    samples: List[str] = Field(default_factory=list)
    message: Optional[str] = None


class FamilyPackageManifestBuildRequest(BaseModel):
    folder_path: str = Field(min_length=1)
    ped_path: Optional[str] = None
    family_id: Optional[str] = None
    naming_scheme: str = "standard_v1"
    hpo_terms: List[str] = Field(default_factory=list)
    notes: Optional[str] = None


class FamilyPackageManifestBuildOut(BaseModel):
    valid: bool
    family_id: Optional[str] = None
    ped_path: Optional[str] = None
    manifest_path: str
    naming_scheme: str
    sample_ids: List[str] = Field(default_factory=list)
    manifest_yaml: str
    datasets: List[FamilyManifestDatasetAvailability] = Field(default_factory=list)
    errors: List[FamilyImportValidationIssue] = Field(default_factory=list)
    warnings: List[FamilyImportValidationIssue] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class FamilyPackageManifestWriteRequest(BaseModel):
    folder_path: str = Field(min_length=1)
    manifest_yaml: str = Field(min_length=1)
    overwrite: bool = False
    family_id: Optional[str] = None


class FamilyPackageManifestWriteOut(BaseModel):
    manifest_path: str
    validation: FamilyPackageValidationOut


class FamilyPackageCandidateOut(BaseModel):
    """A family package discovered under the configured import roots."""

    folder_path: str
    name: str
    family_id: str
    has_manifest: bool
    has_ped: bool
    analysis_type: Optional[str] = None


class FamilyPackageImportCreate(BaseModel):
    folder_path: str = Field(min_length=1)
    project_id: Optional[str] = None
    dry_run: bool = False
    family_id: Optional[str] = None
    conflict_mode: Literal["cancel", "update", "overwrite"] = "cancel"


class FamilyPackageImportJobOut(ApiDocumentModel):
    submitted_path: str
    family_id: Optional[str] = None
    project_id: Optional[ApiId] = None
    status: Literal["queued", "validating", "running", "completed", "failed"]
    dry_run: bool = False
    worker_id: Optional[str] = None
    requested_by: str
    requested_at: datetime
    started_at: Optional[datetime] = None
    heartbeat_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    validation_errors: List[FamilyImportValidationIssue] = Field(default_factory=list)
    validation_warnings: List[FamilyImportValidationIssue] = Field(default_factory=list)
    logs: List[str] = Field(default_factory=list)
    datasets: List[FamilyImportDatasetSummary] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None


class ManualPedMemberCreate(BaseModel):
    sample_id: str = Field(min_length=1)
    father_id: Optional[str] = None
    mother_id: Optional[str] = None
    sex: Literal["male", "female", "und"] = "und"
    clinical_status: Literal["unknown", "unaffected", "affected"] = "unknown"
    affected: bool = False
    is_proband: bool = False
    carrier_status: Literal["unknown", "not_carrier", "carrier"] = "unknown"
    carrier_type: Optional[Literal["obligate", "proven", "reported", "inferred"]] = None
    carrier_evidence: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ManualPedCoupleCreate(BaseModel):
    partners: List[str] = Field(min_length=2, max_length=2)
    context: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ManualPedFamilyCreate(BaseModel):
    family_id: str = Field(min_length=1)
    members: List[ManualPedMemberCreate] = Field(min_length=1)
    couples: List[ManualPedCoupleCreate] = Field(default_factory=list)
    project_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RawImportFileOut(BaseModel):
    id: str
    scope: str
    dataset: str = ""
    file_name: str
    file_type: str = ""
    sample_id: Optional[str] = None
    storage_path: str
    file_size: Optional[int] = None
    sha256: Optional[str] = None
    source: str = ""
    created_at: Optional[str] = None
    # Whether the file is at its local storage path. None for a file kept in an object
    # store (``in_object_store``): the list does not ask the store; Verify does.
    exists: Optional[bool] = None
    download_available: bool = False
    in_object_store: bool = False


class FamilyRawFilesOut(BaseModel):
    family_id: str
    family_files: List[RawImportFileOut] = Field(default_factory=list)
    individual_files: List[RawImportFileOut] = Field(default_factory=list)


class RawImportFileVerifyOut(BaseModel):
    file_id: str
    status: str  # verified | mismatch | missing | unverifiable
    expected_sha256: Optional[str] = None
    computed_sha256: Optional[str] = None
    message: str = ""
