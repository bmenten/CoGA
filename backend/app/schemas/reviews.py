"""Variant reviews, ACMG criteria, filter presets and review tags."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from .common import (
    ApiDocumentModel,
)


class SmallVariantCompoundHetReviewOut(BaseModel):
    group_id: str
    partner_variant_ids: List[str] = Field(default_factory=list)
    gene: Optional[str] = None
    gene_id: Optional[str] = None
    classification: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    tag_metadata: Dict[str, Dict[str, Optional[datetime | str]]] = Field(default_factory=dict)
    note: Optional[str] = None
    phase_status: Optional[str] = None
    updated_by: Optional[str] = None
    updated_at: Optional[datetime] = None


class SmallVariantCompoundHetReviewUpdate(BaseModel):
    partner_variant_id: Optional[str] = None
    classification: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    note: Optional[str] = None


class AcmgCriterionSelection(BaseModel):
    code: str
    strength: str
    accepted: bool = False
    evidence: Optional[str] = None
    auto_suggested: bool = False


class AcmgClassificationPayload(BaseModel):
    criteria: List[AcmgCriterionSelection] = Field(default_factory=list)
    # Derived fields; recomputed server-side on write so they cannot drift.
    point_total: Optional[int] = None
    classification: Optional[str] = None
    # MAGI-ACMG VUS sub-tier (hot/warm/cold); only set when the class is VUS.
    vus_tier: Optional[str] = None


class CnvAcmgCriterion(BaseModel):
    code: str
    points: float = 0.0
    accepted: bool = False
    evidence: Optional[str] = None
    auto_suggested: bool = False


class CnvAcmgClassificationPayload(BaseModel):
    """ClinGen 2019 copy-number classification (structural variants only)."""

    kind: Literal["loss", "gain"] = "loss"
    criteria: List[CnvAcmgCriterion] = Field(default_factory=list)
    # Derived fields; recomputed server-side on write so they cannot drift.
    point_total: Optional[float] = None
    classification: Optional[str] = None


class SmallVariantReviewOut(BaseModel):
    variant_id: str
    classification: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    tag_metadata: Dict[str, Dict[str, Optional[datetime | str]]] = Field(default_factory=dict)
    note: Optional[str] = None
    updated_by: Optional[str] = None
    updated_at: Optional[datetime] = None
    compound_het: Optional[SmallVariantCompoundHetReviewOut] = None
    acmg: Optional[AcmgClassificationPayload] = None
    # CNV (ClinGen) classification — only populated for structural-variant reviews.
    cnv_acmg: Optional[CnvAcmgClassificationPayload] = None
    # A classification record is stored but no longer validates, so ``acmg`` /
    # ``cnv_acmg`` is None although one exists — told apart from "never classified" so
    # the editor can warn before it is overwritten (#514).
    acmg_unreadable: bool = False


class SmallVariantReviewUpdate(BaseModel):
    # Optimistic concurrency (#513): the review's ``updated_at`` as the client loaded it,
    # or null when it loaded no review. On a mismatch the save is refused with 409 and
    # the current review, instead of silently overwriting another reviewer's edit. A
    # client that leaves the field out keeps the unconditional write.
    expected_updated_at: Optional[datetime] = None
    classification: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    note: Optional[str] = None
    compound_het: Optional[SmallVariantCompoundHetReviewUpdate] = None
    # For ``acmg`` and ``cnv_acmg`` alike: left out, the stored classification is kept;
    # sent, it replaces it; sent as null or with no criteria, it is cleared.
    acmg: Optional[AcmgClassificationPayload] = None
    # CNV (ClinGen) classification — only populated for structural-variant reviews.
    cnv_acmg: Optional[CnvAcmgClassificationPayload] = None


class SmallVariantReviewSummaryOut(BaseModel):
    reviewed_variant_count: int = 0
    note_count: int = 0
    tag_counts: Dict[str, int] = Field(default_factory=dict)


class SmallVariantFilterPresetCreate(BaseModel):
    # A small-variant preset is its owner's, reusable in every family they can open.
    name: str = Field(min_length=1, max_length=80)
    description: Optional[str] = Field(default=None, max_length=240)
    filters: Dict[str, Any] = Field(default_factory=dict)
    sample_filters: Dict[str, Any] = Field(default_factory=dict)
    sample_templates: Dict[str, Any] = Field(default_factory=dict)


class SmallVariantFilterPresetOut(ApiDocumentModel):
    owner: str
    name: str
    description: Optional[str] = None
    filters: Dict[str, Any] = Field(default_factory=dict)
    sample_filters: Dict[str, Any] = Field(default_factory=dict)
    sample_templates: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class SmallVariantTagDefinitionCreate(BaseModel):
    label: str = Field(min_length=1, max_length=40)
    description: Optional[str] = Field(default=None, max_length=160)
    scope: Literal["global", "project"] = "project"
    project_id: Optional[str] = None
    shared_project_ids: List[str] = Field(default_factory=list)
    group: Literal["collaboration", "classification", "custom"] = "custom"
    color: str = Field(default="#5b6b79", pattern=r"^#(?:[0-9a-fA-F]{6})$")


class SmallVariantTagDefinitionUpdate(BaseModel):
    label: Optional[str] = Field(default=None, min_length=1, max_length=40)
    description: Optional[str] = Field(default=None, max_length=160)
    scope: Optional[Literal["global", "project"]] = None
    project_id: Optional[str] = None
    shared_project_ids: Optional[List[str]] = None
    group: Optional[Literal["collaboration", "classification", "custom"]] = None
    color: Optional[str] = Field(default=None, pattern=r"^#(?:[0-9a-fA-F]{6})$")


class SmallVariantTagDefinitionOut(BaseModel):
    key: str
    label: str
    description: Optional[str] = None
    group: Literal["collaboration", "classification", "custom"] = "custom"
    color: str = "#5b6b79"
    sort_order: int = 500
    scope: Literal["system", "global", "project"] = "system"
    project_id: Optional[str] = None
    shared_project_ids: List[str] = Field(default_factory=list)
    is_custom: bool = False
