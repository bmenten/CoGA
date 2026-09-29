"""HPO terms and annotations, phenotype matching."""

from datetime import date, datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from .common import (
    ApiId,
)


class HpoTermOut(BaseModel):
    hpo_id: str
    label: str
    definition: Optional[str] = None
    is_obsolete: bool = False


class HpoTermRelationOut(BaseModel):
    hpo_id: str
    label: str
    relation: str


class HpoTermDetailOut(HpoTermOut):
    replaced_by: Optional[str] = None
    release_version: Optional[str] = None
    release_date: Optional[date] = None
    synonyms: List[str] = Field(default_factory=list)
    parents: List[HpoTermRelationOut] = Field(default_factory=list)
    children: List[HpoTermRelationOut] = Field(default_factory=list)


class HpoAdminTermOut(HpoTermDetailOut):
    parent_count: int = 0
    child_count: int = 0


class HpoAdminSummaryOut(BaseModel):
    total_terms: int
    active_terms: int
    obsolete_terms: int
    release_version: Optional[str] = None
    release_date: Optional[date] = None
    last_sync_date: Optional[datetime] = None
    automatic_update_supported: bool = False
    ontology_loaded: bool = False
    # The ontology file the backend is configured to load (HPO_ONTOLOGY_PATH), which the
    # admin page offers as the file to synchronise; None when none is configured.
    ontology_path: Optional[str] = None


class HpoOntologySyncRequest(BaseModel):
    path: str = Field(min_length=1)
    release_version: Optional[str] = None
    release_date: Optional[date] = None
    preview_only: bool = True


class HpoOntologySyncOut(BaseModel):
    preview_only: bool
    release_version: Optional[str] = None
    release_date: Optional[date] = None
    current: HpoAdminSummaryOut
    preview: Dict[str, int] = Field(default_factory=dict)
    imported: Optional["HpoOntologyImportOut"] = None


class HpoAnnotationCreate(BaseModel):
    hpo_id: str = Field(min_length=1)
    status: Literal["present", "absent", "unknown"] = "present"
    onset: Optional[str] = None
    evidence: Optional[str] = None
    source: Optional[str] = None
    note: Optional[str] = None


class HpoAnnotationUpdate(BaseModel):
    hpo_id: Optional[str] = None
    status: Optional[Literal["present", "absent", "unknown"]] = None
    onset: Optional[str] = None
    evidence: Optional[str] = None
    source: Optional[str] = None
    note: Optional[str] = None


class HpoAnnotationOut(BaseModel):
    id: ApiId
    sample_id: str
    hpo_id: str
    label: str
    definition: Optional[str] = None
    status: Literal["present", "absent", "unknown"]
    onset: Optional[str] = None
    evidence: Optional[str] = None
    source: str
    note: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class HpoFamilyQueryOut(BaseModel):
    hpo_id: str
    include_descendants: bool
    sample_ids: List[str] = Field(default_factory=list)
    annotations: List[HpoAnnotationOut] = Field(default_factory=list)


class PhenotypeTermRefOut(BaseModel):
    hpo_id: str
    label: Optional[str] = None


class PhenotypeMatchResultOut(BaseModel):
    rank: int
    score: Optional[float] = None
    id: str
    name: str
    category: Optional[str] = None
    symbol: Optional[str] = None
    gene_in_platform: bool = False
    matching_phenotypes: List[PhenotypeTermRefOut] = Field(default_factory=list)
    extra_phenotypes: List[PhenotypeTermRefOut] = Field(default_factory=list)


class FamilyPhenotypeMatchOut(BaseModel):
    group: str
    sample_id: Optional[str] = None
    query_hpo_ids: List[str] = Field(default_factory=list)
    results: List[PhenotypeMatchResultOut] = Field(default_factory=list)
    source: str = "Monarch Initiative semsim"


class HpoOntologyImportRequest(BaseModel):
    path: str = Field(min_length=1)
    release_version: Optional[str] = None
    release_date: Optional[date] = None


class HpoOntologyImportOut(BaseModel):
    terms: int
    synonyms: int
    edges: int
    closure_rows: int
