"""Families, members, relationships, pedigree structure and family statuses."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from .auth import (
    UserRefOut,
)
from .common import (
    ApiDocumentModel,
    ApiId,
)
from .hpo import (
    HpoAnnotationOut,
)
from .qc import (
    SampleSequencingQcEvaluationOut,
)


class FamilyMemberOut(BaseModel):
    """Schema for a family member with a human-friendly sample ID."""

    sample_id: str
    role: Literal["proband", "father", "mother", "sibling", "embryo", "relative"]
    affected: bool
    sex: Literal["male", "female", "und"] = "und"
    clinical_status: Literal["unknown", "unaffected", "affected"] = "unknown"
    carrier_status: Literal["unknown", "not_carrier", "carrier"] = "unknown"
    carrier_type: Optional[Literal["obligate", "proven", "reported", "inferred"]] = None
    carrier_evidence: Dict[str, Any] = Field(default_factory=dict)
    active: bool = True
    sample_metadata: Dict[str, Any] = Field(default_factory=dict)
    # Sequencing QC judged against the family's threshold profile. Absent when the
    # sample carries no recorded QC. Evaluated server-side so the workspace, the
    # sample-QC page and a report all read the same verdict rather than each
    # re-deriving it from the raw numbers.
    sequencing_qc: Optional["SampleSequencingQcEvaluationOut"] = None


class FamilyRelationshipOut(BaseModel):
    """Canonical family graph edge between two members.

    ``relative`` links a member to the family through another member (``sample_id_a``),
    by a degree that is not known: a PGT index known to be on the mother's side, say.
    """

    id: ApiId
    relationship_type: Literal["parent_child", "couple", "relative"]
    sample_id_a: str
    sample_id_b: str
    role_a: Optional[str] = None
    role_b: Optional[str] = None
    source: str = "manual"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class FamilyStructureVersionOut(BaseModel):
    version: int = 0
    structure_hash: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class FamilyRegionOfInterestOut(BaseModel):
    query: str
    label: str
    source: Literal["gene", "region"]
    assembly_id: Optional[ApiId] = None
    chr: str
    start: int
    end: int


class FamilyRegionOfInterestUpdate(BaseModel):
    query: Optional[str] = None
    project_id: Optional[str] = None


class FamilyStatusRef(BaseModel):
    """Compact status reference embedded in a family record."""

    key: str
    label: str
    color: str


class PackageSourceArchivedOut(BaseModel):
    """A gone package folder a superuser confirmed as deliberately gone (archived): when, by whom
    and why (package_changed_after_import.archive_gone_delivery)."""

    archived_at: str
    archived_by: str
    reason: str


class PackageSourceChangeOut(BaseModel):
    """What changed of the package folder CoGA serves a family's source files from, against the
    delivery the family's data was last imported from whole, as a new family or with overwrite
    (package_changed_after_import.py): ``changed``, its fingerprint now (None for a folder too
    large to fingerprint) and the paths added, removed and changed (in size or modification time),
    each list to 50 paths with its count (None, with ``listed`` false, when the folder holds more
    entries than a listing keeps); or ``gone``, its folder gone or unreadable, so the source files
    (read files, signal tracks, QC report) are no longer available. ``archived``: a gone folder a
    superuser confirmed as deliberately gone, which no longer gates a sign-out. ``detected_at`` is
    when the watcher found it; ``found_at_sign_out``: found by a sign-out's own check of the
    folder, before the watcher (``detected_at`` is then None)."""

    kind: Literal["changed", "gone"]
    fingerprint: Optional[str] = None
    listed: bool = False
    added: List[str] = Field(default_factory=list)
    removed: List[str] = Field(default_factory=list)
    changed: List[str] = Field(default_factory=list)
    added_count: Optional[int] = None
    removed_count: Optional[int] = None
    changed_count: Optional[int] = None
    detected_at: Optional[str] = None
    found_at_sign_out: bool = False
    archived: Optional[PackageSourceArchivedOut] = None


class FamilyPackageSourceChangeOut(PackageSourceChangeOut):
    """The change of the package folder a family's source files are served from, against the
    delivery its data was last imported from whole (its id, its folder's name, and the fingerprint
    it was imported with), which is ``changed_after_import`` or, for a gone folder confirmed as
    deliberately gone, ``archived``. The change ends when the folder is as so imported again, when
    a delivery of the family is imported with overwrite, or when the folder's new delivery is
    refused with a reason; an update import keeps it."""

    delivery_id: str
    folder: str
    imported_fingerprint: Optional[str] = None


class FamilyOut(ApiDocumentModel):
    """Schema for families returned by the API."""

    family_id: str
    created_at: datetime
    members: List[FamilyMemberOut] = Field(default_factory=list)
    relationships: List[FamilyRelationshipOut] = Field(default_factory=list)
    structure_version: Optional[FamilyStructureVersionOut] = None
    pedigree: Optional[str] = None
    roi: Optional[FamilyRegionOfInterestOut] = None
    projects: List[ApiId] = Field(default_factory=list)
    status: Optional[FamilyStatusRef] = None
    assigned_to: Optional[UserRefOut] = None
    reviewed_by: Optional[UserRefOut] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    # The caller's own access: True when they may read the family but change nothing: their
    # role is viewer, or they are a reader in each of the family's projects they belong to.
    read_only: bool = False
    # Whether the caller may sign the family's analysis out: they may change the family, and
    # their role is supervisor or higher (TF-06 H15).
    can_sign_out: bool = False
    # The package folder the family's source files are served from is no longer as its data was
    # imported whole (as a new family or with overwrite), or is gone: the family page says so, and a
    # sign-out warns of it; or that gone folder was confirmed as archived (``archived``), which the
    # page says and a sign-out records (package_changed_after_import.py). None: as imported, or the
    # family was not imported from a delivery.
    package_source_change: Optional[FamilyPackageSourceChangeOut] = None


class FamilyMetadataUpdate(BaseModel):
    """Partial update of a family's workflow metadata.

    Only the fields actually present in the request body are applied (tracked
    via ``model_fields_set``); a field sent explicitly as ``null`` clears it.
    """

    status_key: Optional[str] = None
    assigned_to: Optional[ApiId] = None
    reviewed_by: Optional[ApiId] = None


class FamilyStatusOut(BaseModel):
    """An admin-managed family status value."""

    id: ApiId
    key: str
    label: str
    description: Optional[str] = None
    color: str
    sort_order: int
    is_active: bool


class FamilyStatusCreate(BaseModel):
    label: str = Field(min_length=1)
    description: Optional[str] = None
    color: Optional[str] = None
    sort_order: Optional[int] = None


class FamilyStatusUpdate(BaseModel):
    label: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


class FamilyStructureMemberCreate(BaseModel):
    sample_id: str = Field(min_length=1)
    sex: Literal["male", "female", "und"] = "und"
    role: Literal["proband", "father", "mother", "sibling", "embryo", "relative"] = "relative"
    clinical_status: Literal["unknown", "unaffected", "affected"] = "unknown"
    carrier_status: Literal["unknown", "not_carrier", "carrier"] = "unknown"
    carrier_type: Optional[Literal["obligate", "proven", "reported", "inferred"]] = None
    carrier_evidence: Dict[str, Any] = Field(default_factory=dict)


class FamilyStructureMemberUpdate(BaseModel):
    sample_id: str = Field(min_length=1)
    # The member's new sample ID, when the edit renames it. The rest of the structure update
    # names the member by its stored ID, `sample_id`.
    new_sample_id: Optional[str] = None
    sex: Optional[Literal["male", "female", "und"]] = None
    role: Optional[Literal["proband", "father", "mother", "sibling", "embryo", "relative"]] = None
    clinical_status: Optional[Literal["unknown", "unaffected", "affected"]] = None
    carrier_status: Optional[Literal["unknown", "not_carrier", "carrier"]] = None
    carrier_type: Optional[Literal["obligate", "proven", "reported", "inferred"]] = None
    carrier_evidence: Optional[Dict[str, Any]] = None
    active: Optional[bool] = None


class FamilyStructureParentChildUpdate(BaseModel):
    parent: str = Field(min_length=1)
    child: str = Field(min_length=1)
    parent_role: Literal["father", "mother", "parent"] = "parent"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class FamilyStructureCoupleUpdate(BaseModel):
    partners: List[str] = Field(min_length=2, max_length=2)
    context: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class FamilyStructureRelativeUpdate(BaseModel):
    """A member related to the family through another member, by an unknown degree."""

    member: str = Field(min_length=1)
    related_to: str = Field(min_length=1)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class FamilyStructureRelationshipsUpdate(BaseModel):
    parent_child: List[FamilyStructureParentChildUpdate] = Field(default_factory=list)
    couples: List[FamilyStructureCoupleUpdate] = Field(default_factory=list)
    # None keeps the family's links of unknown degree; a list replaces them.
    relatives: Optional[List[FamilyStructureRelativeUpdate]] = None


class FamilyStructureUpdate(BaseModel):
    expected_structure_version: Optional[int] = None
    change_reason: Optional[str] = None
    clear_existing_genomic_data: bool = False
    add_members: List[FamilyStructureMemberCreate] = Field(default_factory=list)
    members: List[FamilyStructureMemberUpdate] = Field(default_factory=list)
    remove_members: List[str] = Field(default_factory=list)
    relationships: Optional[FamilyStructureRelationshipsUpdate] = None


class _FamilyMutationResultOut(BaseModel):
    family: FamilyOut
    warnings: List[str] = Field(default_factory=list)
    stale_analysis_scopes: List[str] = Field(default_factory=list)
    data_counts: Dict[str, int] = Field(default_factory=dict)
    cleared_data_counts: Dict[str, int] = Field(default_factory=dict)


class FamilyStructureUpdateOut(_FamilyMutationResultOut):
    pass


class FamilyMemberImpactOut(BaseModel):
    sample_id: str
    pedigree_references: Dict[str, int] = Field(default_factory=dict)
    data_counts: Dict[str, int] = Field(default_factory=dict)
    affected_analysis_scopes: List[str] = Field(default_factory=list)
    stale_analysis_scopes: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    destructive: bool = False
    requires_manual_recalculation: bool = False


class FamilyMemberDetailOut(BaseModel):
    member: FamilyMemberOut
    father_id: Optional[str] = None
    mother_id: Optional[str] = None
    hpo_annotations: List["HpoAnnotationOut"] = Field(default_factory=list)
    impact: FamilyMemberImpactOut


class FamilyMemberUpdate(BaseModel):
    sample_id: Optional[str] = None
    sex: Optional[Literal["male", "female", "und"]] = None
    role: Optional[Literal["proband", "father", "mother", "sibling", "embryo", "relative"]] = None
    # Phenotype and carrier status are independent axes and are stored
    # separately (clinical_status = phenotype, carrier_status = genotype).
    clinical_status: Optional[Literal["unknown", "unaffected", "affected"]] = None
    carrier_status: Optional[Literal["unknown", "not_carrier", "carrier"]] = None
    carrier_type: Optional[Literal["obligate", "proven", "reported", "inferred"]] = None
    father_id: Optional[str] = None
    mother_id: Optional[str] = None
    expected_structure_version: Optional[int] = None
    change_reason: Optional[str] = None


class FamilyMemberBatchUpdateItem(BaseModel):
    sample_id: str = Field(min_length=1)
    new_sample_id: Optional[str] = None
    sex: Optional[Literal["male", "female", "und"]] = None
    role: Optional[Literal["proband", "father", "mother", "sibling", "embryo", "relative"]] = None
    clinical_status: Optional[Literal["unknown", "unaffected", "affected"]] = None
    carrier_status: Optional[Literal["unknown", "not_carrier", "carrier"]] = None
    carrier_type: Optional[Literal["obligate", "proven", "reported", "inferred"]] = None
    father_id: Optional[str] = None
    mother_id: Optional[str] = None


class FamilyMemberBatchUpdate(BaseModel):
    expected_structure_version: Optional[int] = None
    change_reason: Optional[str] = None
    updates: List[FamilyMemberBatchUpdateItem] = Field(default_factory=list)


class FamilyMemberUpdateOut(_FamilyMutationResultOut):
    member: FamilyMemberOut
    father_id: Optional[str] = None
    mother_id: Optional[str] = None
    impact: FamilyMemberImpactOut


class FamilyMemberDeleteOut(_FamilyMutationResultOut):
    impact: FamilyMemberImpactOut


class FamilyMemberBatchUpdateOut(_FamilyMutationResultOut):
    pass
