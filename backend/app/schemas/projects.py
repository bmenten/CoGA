"""Projects and project dashboards."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .common import (
    ApiDocumentModel,
    ApiId,
)
from .families import (
    FamilyOut,
)


class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    species_id: str
    assembly_id: str
    user_ids: List[str] = Field(default_factory=list)


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    species_id: Optional[str] = None
    assembly_id: Optional[str] = None
    user_ids: Optional[List[str]] = None


class ProjectOut(ApiDocumentModel):
    name: str
    description: Optional[str] = None
    species_id: ApiId
    assembly_id: ApiId
    user_ids: List[ApiId] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ProjectDashboardOut(ProjectOut):
    species_name: Optional[str] = None
    assembly_name: Optional[str] = None
    assembly_version: Optional[str] = None
    # Inside the validated scope (VALIDATED_ASSEMBLIES)? Drives the "not validated for
    # clinical use" label on family and report pages (TF-06 H12, #515).
    assembly_validated: bool = False
    families: List[FamilyOut] = Field(default_factory=list)
    samples: List[str] = Field(default_factory=list)


class ProjectsUpdate(BaseModel):
    project_ids: List[str] = Field(default_factory=list)
