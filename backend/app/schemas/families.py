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
    clear_existing_genomic_data: bool = False
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
