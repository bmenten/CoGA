"""Genes, Monarch associations, gene panels, clinical CNVs and DGV."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, EmailStr, Field

from .common import (
    ApiDocumentModel,
    ApiId,
    GeneLocation,
)


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
    # Clinical-relevance flags, taken from the annotation's own tags. Previously the
    # Gene Explorer had to infer these by matching a transcript id against a single
    # value scraped from ClinGen's gene page or fetched from Ensembl per gene — both of
    # which only ran for genes dbNSFP did not cover, so the genes anyone actually looks
    # up never carried them.
    mane_select: bool = False
    mane_plus_clinical: bool = False
    ensembl_canonical: bool = False
    # CCDS membership: the consensus coding sequence agreed between Ensembl, NCBI, UCSC
    # and the reference projects. A per-transcript quality signal, so it belongs on the
    # transcript rather than on the gene, where only one arbitrary id could be shown.
    ccds_id: Optional[str] = None
    # Matching RefSeq transcript accessions (NM_/NR_/XM_/XR_). Protein accessions are
    # left out: this is a transcript table, and NP_ ids would not be selectable there.
    refseq_accessions: List[str] = Field(default_factory=list)


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
    # "not_consulted" means the source was never queried for this gene, which is not a
    # statement about its coverage. Omitting it here would 500 the gene profile for any
    # gene carrying one, since the cache has recorded them since the sources stopped
    # being consulted as a fallback.
    status: Literal["success", "missing", "not_consulted", "error"]
    fetched_at: datetime
    source_url: Optional[str] = None
    message: Optional[str] = None
    # Which issue of the source answered — dbNSFP's version, HGNC's newest modification
    # date, ClinGen's file date. Without this the page can show that a source answered
    # but not what it answered from.
    release: Optional[str] = None
    release_detail: Dict[str, Any] = Field(default_factory=dict)
    payload: Dict[str, Any] = Field(default_factory=dict)


class GeneExternalLinkOut(BaseModel):
    label: str
    href: str


class MonarchPhenotypeMatchOut(BaseModel):
    hpo_id: str
    label: Optional[str] = None


class GeneMonarchAssociationOut(BaseModel):
    mondo_id: str
    disease_label: Optional[str] = None
    predicate: str
    predicates: List[str] = Field(default_factory=list)
    sources: List[str] = Field(default_factory=list)
    causal: bool = False
    monarch_url: Optional[str] = None
    phenotype_count: int = 0
    matched_phenotypes: List[MonarchPhenotypeMatchOut] = Field(default_factory=list)


class GeneProfileOut(BaseModel):
    assembly_id: str
    assembly_name: str
    assembly_version: Optional[str] = None
    # Which annotation the loci and transcripts came from, e.g. "gencode v50 (Ensembl
    # 116)" or "ucsc ncbiRefSeq". Recorded per assembly at import, not per gene, so it
    # is read from the import record rather than the gene row.
    gene_annotation_source: Optional[str] = None
    gene_annotation_imported_at: Optional[datetime] = None
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
    monarch_associations: List[GeneMonarchAssociationOut] = Field(default_factory=list)
    extra: Dict[str, Any] = Field(default_factory=dict)
    updated_at: Optional[datetime] = None


class MonarchRefreshSummaryOut(BaseModel):
    release_version: Optional[str] = None
    # A new Monarch release re-versions the generated Mendeliome panel; a failure there
    # used to be only a log warning (#514).
    mendeliome_regenerated: bool = True
    mendeliome_error: Optional[str] = None
    files_loaded: int = 0
    gene_disease_pairs: int = 0
    genes: int = 0
    diseases: int = 0
    causal_pairs: int = 0
    disease_phenotype_pairs: int = 0
    phenotype_diseases: int = 0
    phenotypes: int = 0
    excluded_phenotype_pairs: int = 0
    completed_at: datetime
    duration_seconds: float = 0.0


class MonarchStatusOut(BaseModel):
    release_version: Optional[str] = None
    gene_disease_pairs: int = 0
    genes: int = 0
    diseases: int = 0
    causal_pairs: int = 0
    disease_phenotype_pairs: int = 0
    phenotype_diseases: int = 0
    phenotypes: int = 0
    last_updated_at: Optional[datetime] = None


class MonarchSearchGeneOut(BaseModel):
    gene_symbol: str
    hgnc_id: str
    predicate: Optional[str] = None
    causal: bool = False


class MonarchSearchPhenotypeOut(BaseModel):
    hpo_id: str
    phenotype_label: Optional[str] = None
    matched: bool = False


class MonarchSearchDiseaseOut(BaseModel):
    mondo_id: str
    disease_label: Optional[str] = None
    match_type: Literal["disease", "phenotype", "both"]
    gene_count: int = 0
    genes: List[MonarchSearchGeneOut] = Field(default_factory=list)
    phenotype_count: int = 0
    matched_phenotype_count: int = 0
    phenotypes: List[MonarchSearchPhenotypeOut] = Field(default_factory=list)


class MonarchGeneOverviewItemOut(BaseModel):
    gene_symbol: str
    hgnc_id: str
    causal: bool = False
    disease_count: int = 0


class MonarchGeneOverviewOut(BaseModel):
    total: int = 0
    genes: List[MonarchGeneOverviewItemOut] = Field(default_factory=list)


class MonarchSearchOut(BaseModel):
    query: str = ""
    total: int = 0
    diseases: List[MonarchSearchDiseaseOut] = Field(default_factory=list)
    gene_overview: MonarchGeneOverviewOut = Field(default_factory=MonarchGeneOverviewOut)


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
    # Genes this source was never queried for, kept apart from the genes it was queried
    # for and had nothing on — otherwise a skipped source is indistinguishable from an
    # empty one and reads as poor coverage.
    not_consulted_count: int = 0
    error_count: int = 0
    record_count: int = 0
    # Release behind the most recent fetch, and how many distinct releases the cache
    # still holds — more than one means a partial refresh left a mixed cache.
    release: Optional[str] = None
    release_count: int = 0


class GeneReferenceAdminStatusOut(BaseModel):
    active_job: Optional[GeneInfoRefreshJobOut] = None
    recent_jobs: List[GeneInfoRefreshJobOut] = Field(default_factory=list)
    source_summaries: List[GeneInfoSourceSummaryOut] = Field(default_factory=list)
    total_cached_records: int = 0
    human_gene_symbols: int = 0
    human_assemblies: int = 0
    last_completed_at: Optional[datetime] = None


class ClinicalCnvKbJobOut(ApiDocumentModel):
    assembly_id: str
    assembly_name: str
    status: Literal["queued", "running", "completed", "failed"]
    skip_clinvar: bool = False
    requested_by: Optional[str] = None
    requested_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    inserted: int = 0
    error: Optional[str] = None


class ClinicalCnvKbStatusOut(BaseModel):
    active_job: Optional[ClinicalCnvKbJobOut] = None
    recent_jobs: List[ClinicalCnvKbJobOut] = Field(default_factory=list)
    available: bool = True
    detail: Optional[str] = None


class ClinicalCnvKbRebuildRequest(BaseModel):
    assembly: str = Field(min_length=1)
    skip_clinvar: bool = False


class ClinicalCnvOut(ApiDocumentModel):
    chr: str
    start: int
    end: int
    type: Optional[str] = None
    label: str
    details_html: Optional[str] = None
    assembly: Optional[str] = None
    omim_id: Optional[str] = None
    omim_title: Optional[str] = None
    decipher_id: Optional[str] = None
    description: Optional[str] = None
    cytoband: Optional[str] = None
    source_id: Optional[str] = None
    orpha_id: Optional[str] = None
    orpha_name: Optional[str] = None
    # Pathogenic ClinVar CNVs overlapping the region by >= 30 % reciprocally, per side, and
    # their VariationIDs, from the knowledgebase build (#624). None: not recorded (built
    # without ClinVar, or loaded from a file without them), not zero.
    clinvar_pathogenic_loss_count: Optional[int] = None
    clinvar_pathogenic_gain_count: Optional[int] = None
    clinvar_pathogenic_accessions: Optional[List[str]] = None


class DgvVariantOut(BaseModel):
    chr: str
    start: int
    end: int
    accession: Optional[str] = None
    variant_type: Optional[str] = None
    variant_subtype: Optional[str] = None
    variant_class: str = "other"
    frequency: Optional[float] = None
    observed_gains: Optional[int] = None
    observed_losses: Optional[int] = None
    source: Optional[str] = None


class DgvDensityBin(BaseModel):
    start: int
    end: int
    gain: int = 0
    loss: int = 0
    mixed: int = 0
    other: int = 0


class DgvTrackOut(BaseModel):
    """DGV is too dense to draw every variant at chromosome scale. The server
    decides per request: `lines` (individual variants, when the in-view count is
    small) or `density` (per-bin gain/loss/mixed counts, when it is large)."""

    total: int
    mode: Literal["lines", "density"]
    bin_size: int = 0
    variants: List[DgvVariantOut] = Field(default_factory=list)
    bins: List[DgvDensityBin] = Field(default_factory=list)


class GenePanelOut(ApiDocumentModel):
    name: str
    version: int = 1
    genes: List[str] = Field(default_factory=list)
    gene_count: int = 0
    regions: List[GeneLocation] = Field(default_factory=list)
    created_by: ApiId
    created_by_email: Optional[EmailStr] = None
    created_at: datetime
    description: Optional[str] = None
    source: str = "local"
    external_id: Optional[str] = None
    external_version: Optional[str] = None
    external_url: Optional[str] = None
    source_updated_at: Optional[datetime] = None
    source_metadata: Dict[str, Any] = Field(default_factory=dict)


class GenePanelCreate(BaseModel):
    name: str = Field(min_length=1)
    genes: List[str] = Field(default_factory=list)
    description: Optional[str] = None


class GenePanelUpdate(BaseModel):
    """Edit a manually-curated panel's genes (the full desired set) → a new version."""

    genes: List[str] = Field(default_factory=list)
    description: Optional[str] = None


class GenePanelCreateResponse(BaseModel):
    panel: GenePanelOut
    message: str
    missing_genes: List[str] = Field(default_factory=list)


class GenePanelVersionSummary(BaseModel):
    version: int
    name: str
    source: Optional[str] = None
    external_version: Optional[str] = None
    gene_count: int = 0
    created_by_email: Optional[str] = None
    created_at: datetime


class GenePanelVersionDetail(GenePanelVersionSummary):
    description: Optional[str] = None
    genes: List[str] = Field(default_factory=list)
    regions: List[GeneLocation] = Field(default_factory=list)
    source_metadata: Dict[str, Any] = Field(default_factory=dict)


class GenePanelVersionListOut(BaseModel):
    panel_id: str
    current_version: int
    versions: List[GenePanelVersionSummary] = Field(default_factory=list)


class MendeliomeRegenerateResponse(BaseModel):
    panel: GenePanelOut
    message: str
    changed: bool
    version: int
    monarch_release: Optional[str] = None
    gene_count: int = 0
    missing_genes: List[str] = Field(default_factory=list)


class PanelAppPanelSummaryOut(BaseModel):
    panelapp_id: int
    name: str
    disease_group: Optional[str] = None
    disease_sub_group: Optional[str] = None
    status: Optional[str] = None
    version: Optional[str] = None
    version_created: Optional[str] = None
    relevant_disorders: List[str] = Field(default_factory=list)
    gene_count: int = 0
    str_count: int = 0
    region_count: int = 0
    types: List[str] = Field(default_factory=list)
    url: str


class PanelAppPanelSearchResponse(BaseModel):
    results: List[PanelAppPanelSummaryOut] = Field(default_factory=list)
    count: int = 0


class PanelAppImportRequest(BaseModel):
    panelapp_id: int = Field(ge=1)
    version: Optional[str] = None
    confidence_levels: List[Literal["1", "2", "3"]] = Field(default_factory=lambda: ["3"])
    include_regions: bool = True
    include_strs: bool = True
    assembly: Literal["GRCh38", "GRCh37"] = "GRCh38"
    name: Optional[str] = None


class PanelAppImportResponse(BaseModel):
    panel: GenePanelOut
    message: str
    missing_genes: List[str] = Field(default_factory=list)
    imported_gene_count: int = 0
    imported_region_count: int = 0
