"""Small, structural and copy-number variant rows, pages and carriers."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from .common import (
    ApiDocumentModel,
)
from .genes import (
    MonarchPhenotypeMatchOut,
)
from .reviews import (
    SmallVariantCompoundHetReviewOut,
    SmallVariantReviewOut,
)


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
    # Genotype quality. Already stored and filterable; returned so a reviewer can see
    # the value they are filtering on.
    gq: Optional[int] = None
    # Copy number (FORMAT/CN) from a depth-based CNV caller. GT alone cannot separate
    # a 3-copy from a 6-copy duplication.
    cn: Optional[int] = None


class SmallVariantTranscriptOut(BaseModel):
    gene: Optional[str] = None
    gene_id: Optional[str] = None
    transcript_id: Optional[str] = None
    transcript_source: Optional[str] = None
    feature_type: Optional[str] = None
    transcript_biotype: Optional[str] = None
    impact: Optional[str] = None
    effect: Optional[str] = None
    hgvsc: Optional[str] = None
    hgvsp: Optional[str] = None
    exon: Optional[str] = None
    intron: Optional[str] = None
    canonical: bool = False
    mane_select: bool = False
    mane_plus_clinical: bool = False
    primary: bool = False


class VariantInternalCohortOut(BaseModel):
    """Occurrence of a variant within the accessible CoGA cohort."""

    samples: int = 0
    het: int = 0
    hom: int = 0
    families: int = 0


class SvSecondHitOut(BaseModel):
    """The gene of this small variant is also hit by a structural variant — the cross-type
    "second hit" that can complete a recessive (compound-het) genotype."""

    sv_count: int
    sv_types: List[str] = Field(default_factory=list)
    # Which of the variant's genes the SV actually hits. Not always the variant's primary
    # symbol — a small variant can be annotated to several genes and the SV may sit on any
    # of them.
    gene: Optional[str] = None
    # Span covering the overlapping SVs. A link uses this rather than the gene: SV
    # annotation includes flanking genes while the SV search requires a real overlap with
    # a stored transcript, so a gene-filtered link lands on an empty page for roughly one
    # badge in six.
    chr: Optional[str] = None
    start: Optional[int] = None
    end: Optional[int] = None
    # Zygosity of the overlapping SV in affected individuals ("het" / "hom" / "mixed").
    affected_zygosity: Optional[str] = None
    # A deletion (or CNV) can remove the second copy and unmask a heterozygous SNV.
    has_deletion: bool = False
    # Phase of the SNV vs the SV: "trans" (compound-het candidate), "cis" (same allele),
    # or "unknown".
    phase: str = "unknown"
    # How the phase was determined: "read" (shared phase set on long-read data) or
    # "segregation" (trio/affected-unaffected); None when phase is unknown.
    phase_evidence: Optional[str] = None
    # A deletion in trans with a heterozygous SNV — effectively biallelic.
    deletion_unmasked: bool = False


class VariantPriorityOut(BaseModel):
    combined_score: float
    variant_score: float
    pathogenicity_score: float
    frequency_score: float
    segregation_weight: float
    phenotype_score: Optional[float] = None
    segregation_modes: List[str] = Field(default_factory=list)
    phenotype_gene: Optional[str] = None
    phenotype_matches: List[MonarchPhenotypeMatchOut] = Field(default_factory=list)
    rank: Optional[int] = None


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
    # Structural variants only: every overlapped gene symbol + the count, used to
    # drive ClinGen CNV section-3 gene-content scoring.
    gene_symbols: List[str] = Field(default_factory=list)
    gene_count: Optional[int] = None
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
    alpha_missense_pathogenicity: Optional[float] = None
    alpha_missense_class: Optional[str] = None
    # Resolved from the assembly's cytoband track at display time, not carried by the
    # VCF; only the page's variants are looked up.
    cytoband: Optional[str] = None
    annotation_extra: Dict[str, Any] = Field(default_factory=dict)
    transcripts: List[SmallVariantTranscriptOut] = Field(default_factory=list)
    genotypes: List[GenotypeOut] = Field(default_factory=list)
    # Small variants: on chrX or chrY outside the pseudo-autosomal regions of the family's
    # assembly, where a male carries one copy (#545, #621). False elsewhere, and on an
    # assembly whose PARs are not known.
    hemizygous_in_males: bool = False
    review: Optional[SmallVariantReviewOut] = None
    internal_cohort: Optional[VariantInternalCohortOut] = None
    priority: Optional[VariantPriorityOut] = None
    sv_second_hit: Optional[SvSecondHitOut] = None


class SmallVariantGroupOut(BaseModel):
    group_type: Literal["compound_het"] = "compound_het"
    group_key: str
    gene: Optional[str] = None
    gene_id: Optional[str] = None
    variants: List[VariantOut] = Field(default_factory=list)
    review: Optional[SmallVariantCompoundHetReviewOut] = None
    # Phase derived from the calls, not the curator's judgement (which is
    # `review.phase_status`). "trans" means the two alts were placed on opposite
    # haplotypes: by the reads (one phase set) or by the parents' genotypes (one hit
    # traced to each parent); `phase_evidence` says which. "unknown" means neither
    # resolved them. Cis pairs are excluded from the results, so they never appear here.
    phase: Literal["trans", "unknown"] = "unknown"
    phase_evidence: Optional[Literal["read", "segregation"]] = None


class SmallVariantSampleSummaryOut(BaseModel):
    sample_id: str
    non_ref_count: int = 0
    het_count: int = 0
    hom_alt_count: int = 0


class SmallVariantSummaryOut(BaseModel):
    total_variants: int = 0
    snv_count: int = 0
    indel_count: int = 0
    sample_counts: List[SmallVariantSampleSummaryOut] = Field(default_factory=list)


class VariantPage(BaseModel):
    total: int
    total_is_estimated: bool = False
    unfiltered_total: Optional[int] = None
    unfiltered_total_is_estimated: bool = False
    count_limit: Optional[int] = None
    # True when a prioritized request had more candidates than the ranking window, so
    # the ranking is incomplete and the user should narrow their filters.
    ranking_truncated: bool = False
    # True when the rows came from a capped candidate read (compound-het / recessive
    # pairing, expanded carrier screening, a Python-filtered SV search): a match beyond
    # the candidate window is absent from this result, however few rows it holds. A CSV
    # export over such a page is reported truncated.
    candidates_capped: bool = False
    # How many candidates the capped read or ranking window held, when ranking_truncated
    # or candidates_capped is set (None otherwise), so the UI can say where the search
    # stopped. The compound-het / recessive / carrier window grows with the page asked for.
    candidate_limit: Optional[int] = None
    # Provenance of a prioritized ranking: whether it was served from the cache and when
    # the ranking was computed (so the UI can show a "from cache · N min ago" indicator).
    ranking_cached: bool = False
    ranking_computed_at: Optional[datetime] = None
    variants: List[VariantOut] = Field(default_factory=list)
    variant_groups: List[SmallVariantGroupOut] = Field(default_factory=list)
    summary: Optional[Dict[str, Dict[str, int]]] = None
    small_variant_summary: Optional[SmallVariantSummaryOut] = None


class SmallVariantUploadResult(BaseModel):
    """What a family small-variant VCF upload stored."""

    inserted: int
    # Records skipped because their POS or sample columns could not be read.
    skipped_malformed: int
    # Records dropped because every FILTER value was in ``excluded_filters``.
    skipped_filtered: int
    excluded_filters: List[str] = Field(default_factory=list)
    haplotypes_inserted: int
    # The callset the rows are stored under (``auto`` resolves to clair3 or glimpse2).
    source_format: Literal["clair3", "glimpse2", "mito"]
    annotation_rows: int
    annotation_source: Optional[str] = None
    annotation_version: str
    # The tool and database versions the VCF header names, {module: {version, detail}}, as
    # merged into the family's annotation manifest.
    annotation_provenance: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    insert_batch_size: int


# --- Global Small Variant Explorer (variant-centric, cross-project aggregation) ---


class VariantExplorerAssemblyOut(BaseModel):
    """An assembly available to the user in the Global Small Variant Explorer."""

    assembly_id: str
    assembly_name: str
    version: Optional[str] = None
    species_name: Optional[str] = None
    project_count: int = 0


class GlobalVariantRowOut(BaseModel):
    """One unique variant aggregated across every project the user can access.

    ``key`` is the ClickHouse UInt64 variant key serialised as a string to avoid
    JavaScript precision loss on the frontend.
    """

    key: str
    variant_id: str
    chr: str
    pos: int
    ref: Optional[str] = None
    alt: Optional[str] = None
    rsid: Optional[str] = None
    type: str = "SNV"
    gene: Optional[str] = None
    gene_symbols: List[str] = Field(default_factory=list)
    impact: Optional[str] = None
    consequence: Optional[str] = None
    effects: List[str] = Field(default_factory=list)
    hgvsc: Optional[str] = None
    hgvsp: Optional[str] = None
    clinvar: Optional[str] = None
    classification: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    total_samples: int = 0
    het_samples: int = 0
    hom_samples: int = 0
    total_families: int = 0
    last_seen: Optional[datetime] = None


class GlobalVariantPageOut(BaseModel):
    total: int
    total_is_estimated: bool = False
    page_size: int = 50
    # Opaque keyset cursor (#274) for the NEXT page; null when this is the last page.
    next_cursor: Optional[str] = None
    assembly_id: Optional[str] = None
    assembly_name: Optional[str] = None
    variants: List[GlobalVariantRowOut] = Field(default_factory=list)


class VariantCarrierSampleOut(BaseModel):
    sample_id: str
    individual_name: Optional[str] = None
    role: Optional[str] = None
    genotype: str
    zygosity: str
    family_id: str
    family_uuid: str
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    phenotype_summary: Optional[str] = None


class VariantCarrierFamilyGroupOut(BaseModel):
    family_id: str
    family_uuid: str
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    carrier_count: int = 0
    samples: List[VariantCarrierSampleOut] = Field(default_factory=list)


class VariantCarriersOut(BaseModel):
    key: str
    variant_id: Optional[str] = None
    total_families: int = 0
    total_samples: int = 0
    het_samples: int = 0
    hom_samples: int = 0
    truncated: bool = False
    families: List[VariantCarrierFamilyGroupOut] = Field(default_factory=list)


class VariantLengthOut(BaseModel):
    """Length of a variant with optional type and source annotations."""

    length: int
    type: str
    source: Optional[str] = None
    chr: str
