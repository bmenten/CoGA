"""HPO terms and annotations, phenotype matching."""

from datetime import date, datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

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
    # The SHA-256 of the ontology file the loaded terms came from (TRACE-18); None when no
    # ontology is loaded.
    release_sha256: Optional[str] = None
    last_sync_date: Optional[datetime] = None
    ontology_loaded: bool = False
    # The ontology file the backend is configured to load (HPO_ONTOLOGY_PATH), which the
    # admin page offers as the file to synchronise; None when none is configured.
    ontology_path: Optional[str] = None


class HpoOntologySyncRequest(BaseModel):
    # The ontology file on the server (inside the configured ontology folders), or the HPO
    # release to download from its own PURL (its date, 2026-09-01): one of the two.
    path: Optional[str] = Field(default=None, min_length=1)
    download_release: Optional[str] = Field(default=None, min_length=1, max_length=40)
    # The release and date the admin expects the file to be: the release is read from the
    # file, and a file that states another is refused (TRACE-18). A downloaded release must
    # state the release asked for, so these are for a file on the server only.
    release_version: Optional[str] = None
    release_date: Optional[date] = None
    # The SHA-256 the preview showed: an apply that sends it imports only those bytes.
    expected_sha256: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    preview_only: bool = True

    @model_validator(mode="after")
    def _one_source(self) -> "HpoOntologySyncRequest":
        if (self.path is None) == (self.download_release is None):
            raise ValueError("Give either an ontology file path or an HPO release to download.")
        if self.download_release is not None and (self.release_version or self.release_date):
            raise ValueError(
                "A downloaded HPO release must state the release asked for; give no expected "
                "release or date with it."
            )
        return self


class HpoReleaseCheckOut(BaseModel):
    """The newest HPO release beside the loaded one and the vendored one, the startup's backup."""

    latest_release: Optional[str] = None
    latest_release_date: Optional[date] = None
    published_at: Optional[datetime] = None
    # The newest release's own PURL, which the sync downloads it from.
    download_url: Optional[str] = None
    loaded_release: Optional[str] = None
    loaded_release_date: Optional[date] = None
    pinned_release: str
    pinned_release_date: date
    newer_available: bool = False
    # Why the newest release could not be read (HPO unreachable); None when it was.
    error: Optional[str] = None


class HpoOntologySyncOut(BaseModel):
    preview_only: bool
    # The release the file states, and the SHA-256 of its bytes (TRACE-18).
    release_version: Optional[str] = None
    release_date: Optional[date] = None
    release_sha256: Optional[str] = None
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


class PhenotypeTermRefOut(BaseModel):
    """An HPO term named in a response: a Monarch match's terms, a gene's Monarch phenotypes,
    a variant's phenotype matches."""

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


class HpoOntologyImportOut(BaseModel):
    terms: int
    synonyms: int
    edges: int
    closure_rows: int
