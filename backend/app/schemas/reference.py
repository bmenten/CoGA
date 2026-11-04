"""Species, assemblies, reference datasets and chromosome geometry."""

from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from .common import (
    ApiDocumentModel,
    ApiId,
)


class SpeciesCreate(BaseModel):
    name: str = Field(min_length=1)
    common_name: str = Field(min_length=1)
    tax_id: int = Field(gt=0)


class SpeciesOut(ApiDocumentModel):
    name: str
    common_name: str
    tax_id: int


class AssemblyCreate(BaseModel):
    species_id: str
    assembly_name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    release_date: date


class AssemblyOut(ApiDocumentModel):
    species_id: ApiId
    assembly_name: str
    version: str
    release_date: date


class ReferenceDatasetImportOut(BaseModel):
    dataset_type: str
    inserted: int = 0
    replaced: bool = False
    source: Optional[str] = None
    performed_by: Optional[str] = None
    performed_at: datetime


class ReferenceImportActivityOut(BaseModel):
    """One chronological entry for the "Recent reference activity" feed: which
    dataset was loaded for which assembly, how many rows, from where, by whom."""

    assembly_id: str
    assembly_name: str
    species_name: str
    dataset_type: str
    inserted: int = 0
    replaced: bool = False
    source: Optional[str] = None
    performed_by: Optional[str] = None
    performed_at: datetime


class AssemblyReferenceStatusOut(BaseModel):
    assembly_id: str
    assembly_name: str
    chromosomes: int
    genes: int
    blacklist_regions: int
    clinical_cnvs: int
    segmental_duplications: int
    dgv: int = 0
    last_imports: List[ReferenceDatasetImportOut] = Field(default_factory=list)


class ReferenceImportSourceOrganismOut(BaseModel):
    scientific_name: str
    common_name: str
    tax_id: int
    assembly_count: int


class ReferenceImportSourceAssemblyOut(BaseModel):
    scientific_name: str
    common_name: str
    tax_id: int
    ucsc_genome: str
    assembly_name: str
    assembly_version: str
    release_date: Optional[date] = None
    description: str
    source_name: str
    gene_source: str


class ReferenceAutoImportRequest(BaseModel):
    tax_id: int = Field(gt=0)
    ucsc_genome: str = Field(min_length=1)
    overwrite: bool = False


class ReferenceAutoImportResult(BaseModel):
    species_id: str
    species_name: str
    assembly_id: str
    assembly_name: str
    assembly_version: str
    ucsc_genome: str
    created_species: bool
    created_assembly: bool
    cytobands_inserted: int
    genes_inserted: int
    cytobands_replaced: bool
    genes_replaced: bool
    cytoband_source_url: str
    gene_source_url: str
    gene_source: str
    # Set when the assembly was imported but its gene table could not be
    # retrieved from UCSC (e.g. T2T/hs1). The assembly + cytobands are still
    # created; genes can be uploaded manually afterwards.
    gene_warning: Optional[str] = None
    # What the import started for the assembly, as sentences for the admin: the clinical CNV
    # knowledgebase build and the pinned DGV release's load, for an assembly that had neither,
    # or why one was not started (another one running).
    follow_up_notes: List[str] = Field(default_factory=list)


class DgvLoadJobOut(ApiDocumentModel):
    """A load of the pinned DGV release for an assembly (dgv_load_jobs)."""

    assembly_id: str
    assembly_name: str
    release: str
    source_url: str
    status: Literal["queued", "running", "completed", "failed"]
    requested_by: Optional[str] = None
    requested_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    # The download's progress: bytes read of the file's size.
    bytes_read: int = 0
    bytes_total: Optional[int] = None
    inserted: int = 0
    error: Optional[str] = None


class DgvPinnedReleaseOut(BaseModel):
    """The DGV release pinned for an assembly (DGV_PINNED_RELEASES), which a load reads."""

    assembly_name: str
    release: str
    source_url: str
    size_bytes: int


class DgvLoadStatusOut(BaseModel):
    active_job: Optional[DgvLoadJobOut] = None
    recent_jobs: List[DgvLoadJobOut] = Field(default_factory=list)
    releases: List[DgvPinnedReleaseOut] = Field(default_factory=list)


class DgvLoadRequest(BaseModel):
    assembly: str = Field(min_length=1, max_length=64)


class ReferenceUploadResult(BaseModel):
    assembly_id: str
    assembly_name: str
    dataset_type: Literal[
        "cytobands", "genes", "blacklist", "clinical_cnvs", "segmental_duplications", "dgv"
    ]
    inserted: int
    replaced: bool


class IdeogramBandOut(BaseModel):
    name: str
    start: int
    end: int
    stain: str


class ChromosomeOut(ApiDocumentModel):
    assembly_id: ApiId
    chr: str
    size: int
    bands: List[IdeogramBandOut] = Field(default_factory=list)


class ChromosomeSizeOut(BaseModel):
    chr: str
    size: int


class BlacklistRegionOut(ApiDocumentModel):
    chr: str
    start: int
    end: int
    label: str


class SegmentalDuplicationOut(ApiDocumentModel):
    chr: str
    start: int
    end: int
    label: str
    source: Optional[str] = None


