"""Family-package and PED imports, raw import files."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common import (
    ApiDocumentModel,
    ApiId,
)
from .families import (
    FamilyStructureCoupleUpdate,
    PackageSourceChangeOut,
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


class FamilyImportDatasetProgress(BaseModel):
    """When a dataset's import ran and how far it got. ``fraction_read`` is the share of
    its input files' bytes read (of a gzip file, the compressed bytes), measured at
    ``measured_at``, by an importer that reports it; ``seconds_left`` is the time it should
    still take at that moment, at the pace it has read so far, and None until there is
    enough of a pace to tell."""

    started_at: datetime
    finished_at: Optional[datetime] = None
    measured_at: Optional[datetime] = None
    fraction_read: Optional[float] = None
    seconds_left: Optional[float] = None


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
    progress: Optional[FamilyImportDatasetProgress] = None


class FamilyPackageValidationOut(BaseModel):
    valid: bool
    family_id: Optional[str] = None
    sample_ids: List[str] = Field(default_factory=list)
    errors: List[FamilyImportValidationIssue] = Field(default_factory=list)
    warnings: List[FamilyImportValidationIssue] = Field(default_factory=list)
    datasets: List[FamilyImportDatasetSummary] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    # The fingerprint of the package folder, taken before the validation read it
    # (family_package_fingerprint.py); None for one that could not be read.
    package_fingerprint: Optional[str] = None


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
    """What Discover drafts a manifest for: a package folder, for the family its package file
    names (the folder's name when None). Not served: a delivery's proposal asks for it
    (``import_watcher._proposal``)."""

    model_config = ConfigDict(extra="forbid")

    folder_path: str = Field(min_length=1)
    family_id: Optional[str] = None


class FamilyPackageManifestBuildOut(BaseModel):
    """Discover's draft: the manifest, the members it found, what it found of each dataset, and
    what kept it from drafting one."""

    valid: bool
    family_id: Optional[str] = None
    ped_path: Optional[str] = None
    sample_ids: List[str] = Field(default_factory=list)
    manifest_yaml: str
    datasets: List[FamilyManifestDatasetAvailability] = Field(default_factory=list)
    errors: List[FamilyImportValidationIssue] = Field(default_factory=list)
    warnings: List[FamilyImportValidationIssue] = Field(default_factory=list)


class ImportedPackageFamily(BaseModel):
    """A family whose package record names a scanned folder as the one it was imported from."""

    family_id: str
    # When its last import registered it (families.metadata.package_import.registered_at).
    registered_at: Optional[datetime] = None
    # Its last import did not complete, or is still running: the family is flagged
    # import-incomplete, or carries an import's unfinished mark.
    import_incomplete: bool = False


class FamilyPackageCandidateOut(BaseModel):
    """A package folder under the import roots that can be added to Deliveries by hand."""

    folder_path: str
    name: str
    family_id: str
    has_ped: bool
    # The project folder it lies in (``<root>/<project>/<package>``); None directly in a root.
    project_folder: Optional[str] = None
    # The families imported from this folder, by their package record (none for a folder not
    # imported yet): the import page hides a folder whose families all imported completely.
    imported_families: List[ImportedPackageFamily] = Field(default_factory=list)
    # The folder's latest import was undone because the package changed while it was read: the
    # import page offers the folder again, whatever families were imported from it before.
    changed_during_import: bool = False


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
    # The SHA-256 of the manifest CoGA keeps for the job: the one an approved delivery's package
    # makes (import_delivery_review.py).
    manifest_sha256: Optional[str] = None
    # The fingerprint of the package folder when the job was queued: the job imports nothing
    # when the folder's differs as it starts. None for a folder the queue could not read.
    package_fingerprint: Optional[str] = None


ImportDeliveryState = Literal[
    "incomplete",
    "ready",
    "needs_attention",
    "queued",
    "imported",
    "failed",
    "refused",
    "superseded",
    "changed_after_import",
    "archived",
    "gone",
]


class ImportDeliveryIssue(BaseModel):
    """Why a delivery needs attention, or what an approver should see: a code and a sentence;
    ``path`` a path in the package when it is about one file."""

    code: str
    message: str
    path: Optional[str] = None


class ImportDeliveryOut(ApiDocumentModel):
    """A package a pipeline delivered into a watched import folder (import_watcher.py): the
    folder, its fingerprint and package file (``marker_sha256``: the package file's SHA-256), what
    the package file says it is, and where the delivery stands."""

    watch_root: str
    folder_path: str
    folder_name: str
    state: ImportDeliveryState
    package_fingerprint: str
    marker_sha256: Optional[str] = None
    run_id: Optional[str] = None
    family_id: Optional[str] = None
    project_id: Optional[ApiId] = None
    project: Optional[str] = None
    analysis_type: Optional[str] = None
    pipeline_name: Optional[str] = None
    pipeline_version: Optional[str] = None
    request_id: Optional[str] = None
    issues: List[ImportDeliveryIssue] = Field(default_factory=list)
    warnings: List[ImportDeliveryIssue] = Field(default_factory=list)
    manifest_sha256: Optional[str] = None
    # How many corrections were made to its package in CoGA (import_delivery_revisions.py): the
    # revision an approval names.
    revision: int = 0
    # ``watched``: a package the import watcher found; ``manual``: a folder a person added by hand
    # (import_manual_delivery.py), whose package CoGA made from the folder.
    origin: Literal["watched", "manual"] = "watched"
    # The last import job the delivery was approved for (a dry run, or the import), and where it
    # stands.
    job_id: Optional[ApiId] = None
    job_status: Optional[Literal["queued", "validating", "running", "completed", "failed"]] = None
    job_dry_run: Optional[bool] = None
    first_seen_at: datetime
    last_seen_at: datetime
    state_changed_at: datetime
    # An imported delivery whose followed folder changed, or went, after its import
    # (``changed_after_import``), or whose gone folder was confirmed as archived (``archived``):
    # what changed, and the archiving. None in every other state.
    source_change: Optional[PackageSourceChangeOut] = None
    # An imported delivery that may be approved again, with overwrite only: its family's record
    # names it as its last import, made with update, while the family's reference delivery is
    # ``changed_after_import`` (``import_delivery_review.approve_delivery``).
    overwrite_approvable: bool = False


class ImportDeliveryApprove(BaseModel):
    """A delivery approved for import: as a dry run or for real; ``cancel`` for a new family,
    ``update`` or ``overwrite`` (an administrator's) for one whose family exists; its warnings
    acknowledged when it has any. ``revision`` is the revision of its package the approver saw (0
    when it was not corrected): an approval of another is refused."""

    revision: int = Field(ge=0)
    dry_run: bool = False
    conflict_mode: Literal["cancel", "update", "overwrite"] = "cancel"
    acknowledge_warnings: bool = False


class ImportDeliveryAdd(BaseModel):
    """A package folder added to Deliveries by hand (import_manual_delivery.py): the folder under
    the import roots, the project it belongs to, and, where they differ from what CoGA reads in
    the folder, the family ID (the folder's name by default) and the analysis."""

    folder_path: str = Field(min_length=1, max_length=4096)
    project_id: str = Field(min_length=1)
    family_id: Optional[str] = Field(default=None, max_length=512)
    analysis_type: Optional[
        Literal[
            "long_read_wgs",
            "long_read_adaptive_sampling",
            "short_read_wgs",
            "pgt",
            "expanded_carrier_screening",
            "monogenic_nipt",
        ]
    ] = None


class ImportDeliveryRefuse(BaseModel):
    """A delivery refused, and why."""

    reason: str = Field(min_length=1, max_length=2000)


class ImportDeliveryArchive(BaseModel):
    """A gone package folder confirmed as deliberately gone (archived), and why."""

    reason: str = Field(min_length=1, max_length=2000)


class ImportDeliveryApproveOut(BaseModel):
    delivery: ImportDeliveryOut
    job: FamilyPackageImportJobOut


class ImportDeliveryChange(BaseModel):
    """One field of a delivery's package set to a value, or removed (``value`` null). ``path``
    names it: ``["project"]``, ``["analysis_type"]``, ``["family", "roi"]``, ``["family", "pgt"]``
    or one of its fields, ``["members", <sample ID>]`` (a member added, its fields the value, or
    removed), ``["members", <sample ID>, <field>]``, ``["couples"]``, or ``["datasets", <dataset>]``
    (import_delivery_revisions.py says which may change)."""

    path: List[str] = Field(min_length=1, max_length=3)
    value: Any = None


class ImportDeliveryRevise(BaseModel):
    """A correction of a delivery's package: made on ``revision`` (the latest the person saw, 0
    when there is none), with its reason. It is the ``changes``, or the whole ``package`` as the
    person edited it, whose changes CoGA works out field by field: one of the two."""

    revision: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=2000)
    changes: List[ImportDeliveryChange] = Field(default_factory=list, max_length=200)
    package: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def _changes_or_package(self) -> "ImportDeliveryRevise":
        if bool(self.changes) == (self.package is not None):
            raise ValueError("give the changes or the edited package, one of the two")
        return self


class ImportDeliveryRevisionChange(BaseModel):
    """A field a correction changed: its path, its kind (the laboratory's, or one the pipeline
    used), and its value before and after (null: not set)."""

    path: List[str]
    kind: Literal["laboratory", "pipeline_used"]
    before: Any = None
    after: Any = None


class ImportDeliveryRevisionOut(BaseModel):
    """A correction of a delivery's package, as kept: its number, what it changed, why, by whom
    and when, and the SHA-256 of the package it ends with."""

    revision: int
    changes: List[ImportDeliveryRevisionChange]
    reason: str
    created_by_name: str
    created_at: datetime
    package_sha256: str


class ImportDeliveryPackageOut(BaseModel):
    """A delivery's package as its import would read it now: the latest revision's (``revision``,
    0 for none), or the package as checked; the pipeline's file as written (``original``); the
    sections CoGA proposed from the folder; and every correction."""

    delivery_id: str
    revision: int
    package: Optional[Dict[str, Any]] = None
    package_sha256: Optional[str] = None
    original: Optional[Dict[str, Any]] = None
    proposed: List[str] = Field(default_factory=list)
    revisions: List[ImportDeliveryRevisionOut] = Field(default_factory=list)
    # ``manual``: a folder added by hand, whose package CoGA made: members are added and removed
    # freely, and the package is edited as JSON.
    origin: Literal["watched", "manual"] = "watched"
    # The samples the package's files name and no member is: the members a correction may add.
    addable_members: List[str] = Field(default_factory=list)
    # The members of the package as checked: a pipeline's members are not removed in CoGA.
    members_as_checked: List[str] = Field(default_factory=list)


class ImportDeliveryRevisedOut(BaseModel):
    delivery: ImportDeliveryOut
    package: ImportDeliveryPackageOut


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


class ManualPedFamilyCreate(BaseModel):
    family_id: str = Field(min_length=1)
    members: List[ManualPedMemberCreate] = Field(min_length=1)
    # A couple as the structure edit takes one: the same two partners, context and metadata.
    couples: List[FamilyStructureCoupleUpdate] = Field(default_factory=list)
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
    # Whether the file is at its storage path.
    exists: bool = False
    download_available: bool = False
    # Whether the recorded location lies outside the folders CoGA reads the family's source
    # files from (its package folder and the upload folder): such a file is neither downloaded
    # nor verified.
    outside_read_folders: bool = False


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
