from datetime import date, datetime
from typing import Optional, List, Any, Literal, Dict
from uuid import UUID

from pydantic import BaseModel, EmailStr, ConfigDict, Field
from pydantic_core import core_schema


class ApiId(str):
    """Storage-agnostic API identifier supporting UUID and synthetic string ids."""

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type, handler):
        return core_schema.no_info_after_validator_function(
            cls.validate,
            core_schema.union_schema(
                [
                    core_schema.is_instance_schema(UUID),
                    core_schema.str_schema(),
                ]
            ),
            serialization=core_schema.to_string_ser_schema(),
        )

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema_, handler):
        return {"type": "string"}

    @classmethod
    def validate(cls, value: Any) -> str:
        return str(value)


class ApiDocumentModel(BaseModel):
    id: ApiId = Field(alias="_id")

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
    )


class GeneLocation(BaseModel):
    gene: str
    chr: str
    start: int
    end: int


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    first_name: str
    last_name: str
    affiliation: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserRead(BaseModel):
    id: ApiId
    username: str
    email: EmailStr
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    affiliation: Optional[str] = None
    is_active: bool
    role: str
    projects: List[str] = Field(default_factory=list)
    created_at: datetime

    model_config = ConfigDict(arbitrary_types_allowed=True)


class UserUpdate(BaseModel):
    is_active: Optional[bool] = None
    projects: Optional[List[str]] = None


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str


class TokenData(BaseModel):
    email: Optional[EmailStr] = None


class FamilyMemberOut(BaseModel):
    """Schema for a family member with a human-friendly sample ID."""

    sample_id: str
    role: Literal["proband", "father", "mother", "sibling"]
    affected: bool
    sex: Literal["male", "female", "und"] = "und"


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


class FamilyOut(ApiDocumentModel):
    """Schema for families returned by the API."""

    family_id: str
    members: List[FamilyMemberOut] = Field(default_factory=list)
    pedigree: Optional[str] = None
    roi: Optional[FamilyRegionOfInterestOut] = None
    projects: List[ApiId] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


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


class FamilyPackageManifestWriteOut(BaseModel):
    manifest_path: str
    validation: FamilyPackageValidationOut


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
    affected: bool = False
    is_proband: bool = False


class ManualPedFamilyCreate(BaseModel):
    family_id: str = Field(min_length=1)
    members: List[ManualPedMemberCreate] = Field(min_length=1)
    project_id: Optional[str] = None


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
    families: List[FamilyOut] = Field(default_factory=list)
    samples: List[str] = Field(default_factory=list)


class ProjectsUpdate(BaseModel):
    project_ids: List[str] = Field(default_factory=list)


class SampleInventoryOut(BaseModel):
    sample_id: str
    role: str
    affected: bool
    sex: str
    projects: List[str] = Field(default_factory=list)
    track_counts: Dict[str, int] = Field(default_factory=dict)
    total_records: int = 0


class FamilyInventorySummaryOut(BaseModel):
    family_id: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    projects: List[str] = Field(default_factory=list)
    sample_count: int = 0
    track_counts: Dict[str, int] = Field(default_factory=dict)
    total_records: int = 0


class FamilyInventoryDetailOut(FamilyInventorySummaryOut):
    samples: List[SampleInventoryOut] = Field(default_factory=list)


class FamilyInventoryPageOut(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[FamilyInventorySummaryOut] = Field(default_factory=list)


class ClickHouseVariantTableStatusOut(BaseModel):
    name: str
    variant_type: str
    kind: str
    exists: bool
    engine: str | None = None
    row_count: int = 0
    bytes_on_disk: int = 0
    pending_mutations: int = 0


class ClickHouseVariantAssemblyStatusOut(BaseModel):
    assembly_name: str
    health: str
    expected_table_count: int = 0
    existing_table_count: int = 0
    missing_tables: List[str] = Field(default_factory=list)
    pending_mutations: int = 0
    total_rows: int = 0
    total_bytes_on_disk: int = 0
    small_variant_rows: int = 0
    structural_variant_rows: int = 0
    tables: List[ClickHouseVariantTableStatusOut] = Field(default_factory=list)


class ClickHouseVariantAssemblyListOut(BaseModel):
    assemblies: List[ClickHouseVariantAssemblyStatusOut] = Field(default_factory=list)


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


class AssemblyReferenceStatusOut(BaseModel):
    assembly_id: str
    assembly_name: str
    chromosomes: int
    genes: int
    blacklist_regions: int
    clinical_cnvs: int


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
    cytobands_available: bool = True
    genes_available: bool = True
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


class ReferenceUploadResult(BaseModel):
    assembly_id: str
    assembly_name: str
    dataset_type: Literal["cytobands", "genes", "blacklist", "clinical_cnvs"]
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


class AlignmentManifestEntryOut(BaseModel):
    sample_id: str
    format: Literal["bam", "cram"]
    url: str
    index_url: str


class GeneExonOut(BaseModel):
    start: int
    end: int
    name: str


class GeneOut(ApiDocumentModel):
    gene_id: str
    hgnc_symbol: str
    chr: str
    start: int
    end: int
    exons: List[GeneExonOut] = Field(default_factory=list)
    strand: int


class GeneSearchResultOut(BaseModel):
    symbol: str
    gene_id: str
    chr: str
    start: int
    end: int
    transcript_count: int
    assembly_count: int = 1


class GeneTranscriptOut(BaseModel):
    transcript_id: str
    start: int
    end: int
    exon_count: int
    strand: int
    biotype: Optional[str] = None
    source: Optional[str] = None


class GenePanelMembershipOut(BaseModel):
    panel_id: str
    name: str
    gene_count: int


class GeneAssemblyLocationOut(BaseModel):
    assembly_id: str
    assembly_name: str
    assembly_version: Optional[str] = None
    chr: str
    start: int
    end: int
    transcript_count: int
    is_primary: bool = False
    is_family_context: bool = False


class GeneHomologOut(BaseModel):
    species_name: str
    common_name: Optional[str] = None
    symbol: Optional[str] = None
    ensembl_gene_id: Optional[str] = None
    homology_type: Optional[str] = None
    percent_id: Optional[float] = None
    percent_coverage: Optional[float] = None
    in_platform: bool = False


class GeneVariantCountsOut(BaseModel):
    small_variants: int = 0
    structural_variants: int = 0


class GeneInfoSourceStatusOut(BaseModel):
    status: Literal["success", "missing", "error"]
    fetched_at: datetime
    source_url: Optional[str] = None
    message: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class GeneExternalLinkOut(BaseModel):
    label: str
    href: str


class GeneProfileOut(BaseModel):
    assembly_id: str
    assembly_name: str
    assembly_version: Optional[str] = None
    species_name: str
    symbol: str
    gene_id: str
    display_name: Optional[str] = None
    summary: Optional[str] = None
    chr: str
    start: int
    end: int
    strand: int
    biotype: Optional[str] = None
    transcript_count: int
    transcripts: List[GeneTranscriptOut] = Field(default_factory=list)
    aliases: List[str] = Field(default_factory=list)
    previous_symbols: List[str] = Field(default_factory=list)
    ensembl_gene_id: Optional[str] = None
    ncbi_gene_id: Optional[str] = None
    hgnc_id: Optional[str] = None
    omim_gene_id: Optional[str] = None
    gene_type: Optional[str] = None
    location: Optional[str] = None
    assembly_locations: List[GeneAssemblyLocationOut] = Field(default_factory=list)
    homologs: List[GeneHomologOut] = Field(default_factory=list)
    panels: List[GenePanelMembershipOut] = Field(default_factory=list)
    family_counts: Optional[GeneVariantCountsOut] = None
    source_status: Dict[str, GeneInfoSourceStatusOut] = Field(default_factory=dict)
    external_links: List[GeneExternalLinkOut] = Field(default_factory=list)
    extra: Dict[str, Any] = Field(default_factory=dict)
    updated_at: Optional[datetime] = None


class GeneBulkRefreshOut(BaseModel):
    human_assemblies: int
    gene_symbols: int
    updated_records: int
    completed_at: datetime


class GeneInfoRefreshJobOut(ApiDocumentModel):
    scope: Literal["symbol", "all_human"]
    symbol: Optional[str] = None
    status: Literal["queued", "running", "completed", "failed"]
    active_slot: Optional[str] = None
    worker_id: Optional[str] = None
    requested_by: str
    requested_at: datetime
    started_at: Optional[datetime] = None
    heartbeat_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    total_symbols: int = 0
    completed_symbols: int = 0
    updated_records: int = 0
    human_assemblies: int = 0
    current_symbol: Optional[str] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class GeneInfoSourceSummaryOut(BaseModel):
    source: str
    latest_fetched_at: Optional[datetime] = None
    success_count: int = 0
    missing_count: int = 0
    error_count: int = 0
    record_count: int = 0


class GeneReferenceAdminStatusOut(BaseModel):
    active_job: Optional[GeneInfoRefreshJobOut] = None
    recent_jobs: List[GeneInfoRefreshJobOut] = Field(default_factory=list)
    source_summaries: List[GeneInfoSourceSummaryOut] = Field(default_factory=list)
    total_cached_records: int = 0
    human_gene_symbols: int = 0
    human_assemblies: int = 0
    last_completed_at: Optional[datetime] = None


class BlacklistRegionOut(ApiDocumentModel):
    chr: str
    start: int
    end: int
    label: str


class ClinicalCnvOut(ApiDocumentModel):
    chr: str
    start: int
    end: int
    type: Optional[str] = None
    label: str
    details_html: Optional[str] = None


class GenePanelOut(ApiDocumentModel):
    name: str
    genes: List[str] = Field(default_factory=list)
    gene_count: int = 0
    regions: List[GeneLocation] = Field(default_factory=list)
    created_by: ApiId
    created_by_email: Optional[EmailStr] = None
    created_at: datetime
    description: Optional[str] = None


class GenePanelCreate(BaseModel):
    name: str = Field(min_length=1)
    genes: List[str] = Field(default_factory=list)
    description: Optional[str] = None


class GenePanelCreateResponse(BaseModel):
    panel: GenePanelOut
    message: str
    missing_genes: List[str] = Field(default_factory=list)


class GenotypeOut(BaseModel):
    sample: str
    gt: str
    dp: Optional[int] = None
    ad: Optional[List[int]] = None
    af: Optional[List[float]] = None
    read_support: Optional[int] = None
    qual: Optional[float] = None
    filter: Optional[str] = None
    ps: Optional[int] = None


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


class SmallVariantReviewOut(BaseModel):
    variant_id: str
    classification: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    tag_metadata: Dict[str, Dict[str, Optional[datetime | str]]] = Field(default_factory=dict)
    note: Optional[str] = None
    updated_by: Optional[str] = None
    updated_at: Optional[datetime] = None
    compound_het: Optional[SmallVariantCompoundHetReviewOut] = None


class SmallVariantReviewUpdate(BaseModel):
    classification: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    note: Optional[str] = None
    compound_het: Optional[SmallVariantCompoundHetReviewUpdate] = None


class SmallVariantReviewSummaryOut(BaseModel):
    reviewed_variant_count: int = 0
    note_count: int = 0
    tag_counts: Dict[str, int] = Field(default_factory=dict)


class SmallVariantFilterPresetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    scope: Literal["family", "global"] = "family"
    description: Optional[str] = Field(default=None, max_length=240)
    filters: Dict[str, Any] = Field(default_factory=dict)
    sample_filters: Dict[str, Any] = Field(default_factory=dict)
    sample_templates: Dict[str, Any] = Field(default_factory=dict)


class SmallVariantFilterPresetOut(ApiDocumentModel):
    family_id: Optional[str] = None
    scope: Literal["family", "global"]
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


class VariantOut(ApiDocumentModel):
    chr: str
    start: int
    end: int
    length: int
    type: str
    source: Optional[str] = None
    qual: Optional[float] = None
    read_support: Optional[int] = None
    filter: Optional[str] = None
    remote_chr: Optional[str] = None
    remote_start: Optional[int] = None
    ref: Optional[str] = None
    alt: Optional[str] = None
    ps: Optional[int] = None
    gene: Optional[str] = None
    gene_id: Optional[str] = None
    transcript_id: Optional[str] = None
    feature_type: Optional[str] = None
    transcript_biotype: Optional[str] = None
    impact: Optional[str] = None
    effect: Optional[str] = None
    clinvar: Optional[str] = None
    rsid: Optional[str] = None
    hgvsc: Optional[str] = None
    hgvsp: Optional[str] = None
    canonical: bool = False
    mane_select: bool = False
    mane_plus_clinical: bool = False
    exon: Optional[str] = None
    intron: Optional[str] = None
    lof: Optional[str] = None
    lof_filter: Optional[str] = None
    lof_flags: Optional[str] = None
    gnomad_af: Optional[float] = None
    gnomad_hom_count: Optional[int] = None
    gene_pli: Optional[float] = None
    gene_missense_z: Optional[float] = None
    population_frequencies: Dict[str, float] = Field(default_factory=dict)
    cadd_raw: Optional[float] = None
    cadd_phred: Optional[float] = None
    revel: Optional[float] = None
    sift: Optional[str] = None
    polyphen: Optional[str] = None
    spliceai_ds_ag: Optional[float] = None
    spliceai_ds_al: Optional[float] = None
    spliceai_ds_dg: Optional[float] = None
    spliceai_ds_dl: Optional[float] = None
    spliceai_max: Optional[float] = None
    annotation_extra: Dict[str, Any] = Field(default_factory=dict)
    genotypes: List[GenotypeOut] = Field(default_factory=list)
    review: Optional[SmallVariantReviewOut] = None


class SmallVariantGroupOut(BaseModel):
    group_type: Literal["compound_het"] = "compound_het"
    group_key: str
    gene: Optional[str] = None
    gene_id: Optional[str] = None
    variants: List[VariantOut] = Field(default_factory=list)
    review: Optional[SmallVariantCompoundHetReviewOut] = None


class VariantPage(BaseModel):
    total: int
    total_is_estimated: bool = False
    unfiltered_total: Optional[int] = None
    unfiltered_total_is_estimated: bool = False
    count_limit: Optional[int] = None
    variants: List[VariantOut] = Field(default_factory=list)
    variant_groups: List[SmallVariantGroupOut] = Field(default_factory=list)
    summary: Optional[Dict[str, Dict[str, int]]] = None


class VariantLengthOut(BaseModel):
    """Length of a variant with optional type and source annotations."""

    length: int
    type: str
    source: Optional[str] = None
    chr: str


class HaplotypeSegment(BaseModel):
    chr: Optional[str] = None
    start: int
    end: int
    hap1: str
    hap2: str


class HaplotypeSample(BaseModel):
    sample: str
    segments: List[HaplotypeSegment] = Field(default_factory=list)


class HaplotypeResponse(BaseModel):
    chr: str
    start: Optional[int] = None
    end: Optional[int] = None
    samples: List[HaplotypeSample] = Field(default_factory=list)


class TrackAvailabilityOut(BaseModel):
    coverage: bool = False
    segments: bool = False
    apcad: bool = False
    variants: bool = False
    small_variants: bool = False
    haplotypes: bool = False
    repeat_expansions: bool = False


class FamilyTrackAvailabilityOut(BaseModel):
    samples: Dict[str, TrackAvailabilityOut] = Field(default_factory=dict)


class RepeatExpansionMotifCountOut(BaseModel):
    motif: str
    count: int


class RepeatExpansionAlleleOut(BaseModel):
    repeat_count: Optional[int] = None
    bp_length: Optional[int] = None
    confidence_interval: Optional[str] = None
    support_reads: Optional[int] = None
    purity: Optional[float] = None
    methylation: Optional[float] = None
    motif_counts: List[RepeatExpansionMotifCountOut] = Field(default_factory=list)
    motif_spans: Optional[str] = None
    interrupted: bool = False
    interruption_label: Optional[str] = None
    status: Literal["normal", "intermediate", "pathogenic", "unknown"] = "unknown"


class RepeatExpansionSampleCallOut(BaseModel):
    sample: str
    role: Optional[str] = None
    affected: Optional[bool] = None
    sex: Optional[str] = None
    genotype: str
    allele_count: int = 0
    alleles: List[RepeatExpansionAlleleOut] = Field(default_factory=list)
    status: Literal["normal", "intermediate", "pathogenic", "unknown"] = "unknown"


class RepeatExpansionRowOut(BaseModel):
    locus_id: str
    gene: str
    display_name: str
    disease: str
    inheritance: Optional[str] = None
    chr: str
    start: int
    end: int
    motif: Optional[str] = None
    warning_min: Optional[int] = None
    pathogenic_min: Optional[int] = None
    status: Literal["normal", "intermediate", "pathogenic", "unknown"] = "unknown"
    calls: Dict[str, RepeatExpansionSampleCallOut] = Field(default_factory=dict)


class FamilyRepeatExpansionTableOut(BaseModel):
    samples: List[FamilyMemberOut] = Field(default_factory=list)
    loci: List[RepeatExpansionRowOut] = Field(default_factory=list)


class ParaphaseMetricOut(BaseModel):
    key: str
    label: str
    value: Optional[float] = None


class ParaphaseHaplotypeGroupOut(BaseModel):
    key: str
    label: str
    count: int = 0
    haplotypes: List[str] = Field(default_factory=list)


class ParaphaseDisorderOut(BaseModel):
    name: str
    omim_url: Optional[str] = None


class ParaphaseRegionInfoOut(BaseModel):
    region_id: str
    display_name: str
    genes: List[str] = Field(default_factory=list)
    summary: Optional[str] = None
    clinical_priority: int = 999
    key_copy_number_fields: List[str] = Field(default_factory=list)
    key_read_fields: List[str] = Field(default_factory=list)
    key_haplotype_fields: List[str] = Field(default_factory=list)
    key_extra_fields: List[str] = Field(default_factory=list)
    field_descriptions: Dict[str, str] = Field(default_factory=dict)
    notes: List[str] = Field(default_factory=list)
    disorders: List[ParaphaseDisorderOut] = Field(default_factory=list)


class ParaphaseExtraFieldOut(BaseModel):
    key: str
    label: str
    value: Any = None
    description: Optional[str] = None


class ParaphaseSampleResultOut(BaseModel):
    sample: str
    role: Optional[str] = None
    affected: Optional[bool] = None
    sex: Optional[str] = None
    total_cn: Optional[int] = None
    gene_cn: Optional[int] = None
    highest_total_cn: Optional[int] = None
    sample_sex: Optional[str] = None
    phase_region: Optional[str] = None
    region_depth: Dict[str, Any] = Field(default_factory=dict)
    genome_depth: Optional[float] = None
    final_haplotype_count: int = 0
    assembled_haplotype_count: int = 0
    variant_site_count: int = 0
    heterozygous_site_count: int = 0
    fusion_count: Optional[int] = None
    copy_number_signal: bool = False
    copy_number_metrics: List[ParaphaseMetricOut] = Field(default_factory=list)
    read_metrics: List[ParaphaseMetricOut] = Field(default_factory=list)
    haplotype_groups: List[ParaphaseHaplotypeGroupOut] = Field(default_factory=list)
    extra_fields: List[ParaphaseExtraFieldOut] = Field(default_factory=list)
    uploaded_at: Optional[datetime] = None


class ParaphaseGeneResultOut(BaseModel):
    gene_symbol: str
    is_medically_relevant: bool = False
    region_info: Optional[ParaphaseRegionInfoOut] = None
    max_total_cn: Optional[int] = None
    max_gene_cn: Optional[int] = None
    max_highest_total_cn: Optional[int] = None
    has_copy_number_signal: bool = False
    samples: Dict[str, ParaphaseSampleResultOut] = Field(default_factory=dict)


class FamilyParaphaseTableOut(BaseModel):
    samples: List[FamilyMemberOut] = Field(default_factory=list)
    genes: List[ParaphaseGeneResultOut] = Field(default_factory=list)


class RepeatExpansionTrackItemOut(BaseModel):
    sample: str
    locus_id: str
    gene: str
    display_name: str
    disease: str
    chr: str
    start: int
    end: int
    motif: Optional[str] = None
    warning_min: Optional[int] = None
    pathogenic_min: Optional[int] = None
    status: Literal["normal", "intermediate", "pathogenic", "unknown"] = "unknown"
    allele_repeat_counts: List[int] = Field(default_factory=list)
    allele_bp_lengths: List[int] = Field(default_factory=list)


class RepeatExpansionTrackResponse(BaseModel):
    items: List[RepeatExpansionTrackItemOut] = Field(default_factory=list)


class RepeatExpansionUploadResult(BaseModel):
    processed: int
    inserted: int
    source_format: Literal["trgt"]


class ReferenceSequenceOut(BaseModel):
    sequence: str


class ReferenceReadOut(BaseModel):
    pos: int
    seq: str


class ReferenceReadsOut(BaseModel):
    reads: List[ReferenceReadOut] = Field(default_factory=list)


class GithubReleaseOut(BaseModel):
    version: str
    name: Optional[str] = None
    published_at: datetime
    summary: str
    url: str
    prerelease: bool = False


class GithubReleaseCatalogOut(BaseModel):
    repository: str
    repository_url: str
    releases_url: str
    issues_url: str
    repo_visibility: Literal["private", "public", "unknown"] = "unknown"
    sync_status: Literal["ok", "unavailable"] = "unavailable"
    sync_error: Optional[str] = None
    fetched_at: Optional[datetime] = None
    releases: List[GithubReleaseOut] = Field(default_factory=list)


class AuditLogEventOut(BaseModel):
    id: str
    created_at: datetime
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    user_role: Optional[str] = None
    method: str
    route_path: Optional[str] = None
    path: str
    query_string: Optional[str] = None
    status_code: int
    duration_ms: int
    remote_ip: Optional[str] = None
    user_agent: Optional[str] = None
    referer: Optional[str] = None
    protocol: Optional[str] = None
    request_body: Optional[Any] = None
    request_meta: Dict[str, Any] = Field(default_factory=dict)
    db_update: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class AuditLogPageOut(BaseModel):
    page: int
    page_size: int
    total: int
    items: List[AuditLogEventOut] = Field(default_factory=list)
