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


class FamilyRelationshipOut(BaseModel):
    """Canonical family graph edge between two members."""

    id: ApiId
    relationship_type: Literal["parent_child", "couple"]
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


class UserRefOut(BaseModel):
    """Minimal user reference used for assignment/reviewer pickers."""

    id: ApiId
    username: str
    email: EmailStr
    first_name: str = ""
    last_name: str = ""

    model_config = ConfigDict(arbitrary_types_allowed=True)


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

    model_config = ConfigDict(arbitrary_types_allowed=True)


class NiptFetalFractionOut(BaseModel):
    """Fetal-fraction estimate for a monogenic NIPT family."""

    ff: float
    ff_computed: Optional[float] = None
    ff_external: Optional[float] = None
    ff_median: Optional[float] = None
    ci_low: Optional[float] = None
    ci_high: Optional[float] = None
    n_sites: int
    method: str
    low_confidence: bool
    disagreement: bool


class NiptSummaryOut(BaseModel):
    """Monogenic NIPT analysis summary: fetal fraction and category/filter counts."""

    family_id: str
    fetal_fraction: NiptFetalFractionOut
    category_counts: Dict[int, int]
    filter_counts: Dict[str, int]


class NiptClassificationOut(BaseModel):
    """The NIPT classification block attached to each cfDNA variant.

    The variant payload itself is the full small-variant shape (``VariantOut``);
    this is the maternal/fetal interpretation layered on top. ``NiptVariantOut``
    (defined after ``VariantOut`` below) combines the two."""

    category: Optional[int] = None
    category_label: str
    maternal_state: str
    fetal_inheritance: str
    expected_vaf: float
    observed_vaf: Optional[float] = None
    confidence: float
    flags: List[str] = Field(default_factory=list)


class NiptVariantPage(BaseModel):
    """A page of classified cfDNA variants plus the family's fetal fraction."""

    family_id: str
    total: int
    fetal_fraction: NiptFetalFractionOut
    # NiptVariantOut subclasses VariantOut, which is defined later in this module.
    variants: List["NiptVariantOut"]


class NiptCoverageRegionOut(BaseModel):
    """On-target coverage for a single target region."""

    label: str
    chr: str
    start: int
    end: int
    median_coverage: Optional[float] = None
    covered_bases: int
    target_bases: int


class NiptCoverageLowRegionOut(BaseModel):
    """A panel gene / target region flagged as inadequately interrogated."""

    label: str
    chr: str
    median_coverage: Optional[float] = None
    covered_fraction: float
    reason: str  # "no_coverage" | "low_depth" | "partial_coverage"


class NiptCoverageSummaryOut(BaseModel):
    """Median on-target coverage for a monogenic NIPT family's cfDNA sample.

    ``low_coverage_regions`` is a compact QC list (only the failing genes, not
    the full per-region table) flagging genes that may harbour silent gaps.
    """

    family_id: str
    overall_median_on_target: Optional[float] = None
    target_region_count: int
    per_region: List[NiptCoverageRegionOut]
    min_depth: float
    min_covered_fraction: float
    low_coverage_regions: List[NiptCoverageLowRegionOut] = Field(default_factory=list)


class SampleIntegritySexCheckOut(BaseModel):
    """Genotype-inferred sex vs the recorded sex for one sample."""

    sample_id: str
    recorded_sex: str
    inferred_sex: str  # "male" | "female" | "indeterminate"
    x_het_rate: Optional[float] = None
    x_sites: int
    status: str  # "pass" | "warn" | "fail" | "skip"
    message: str


class SampleIntegrityRelatednessCheckOut(BaseModel):
    """KING-robust relatedness for a sample pair vs the pedigree's assertion."""

    sample_a: str
    sample_b: str
    expected_relationship: str
    inferred_relationship: str
    kinship: float
    ibs0_rate: float
    informative_sites: int
    status: str
    message: str


class SampleIntegrityMendelianCheckOut(BaseModel):
    """Mendelian-error rate for a child against its present parent(s)."""

    child: str
    parents: List[str]
    informative_sites: int
    mendel_errors: int
    mendel_rate: float
    status: str
    message: str


class SampleIntegrityPaternityCheckOut(BaseModel):
    """NIPT paternity from the cfDNA classification (categories 7/8)."""

    father: str
    cat7_transmitted: int
    cat8_absent: int
    informative_sites: int
    status: str
    message: str


class SampleIntegrityFetalSexCheckOut(BaseModel):
    """NIPT fetal sex from paternal X transmission (cfDNA)."""

    inferred_sex: str  # "female" | "male" | "indeterminate"
    x_transmitted: int
    x_not_transmitted: int
    informative_sites: int
    status: str
    message: str


class SampleIntegrityCategoryQcOut(BaseModel):
    """NIPT category-distribution QC (de-novo / paternal-absent / maternal transmission)."""

    denovo: int
    paternal_absent: int
    maternal_informative: int
    maternal_inherited: int
    maternal_inherited_rate: float
    status: str
    message: str


class AnnotationModuleOut(BaseModel):
    """One annotation/reference module and the version that backed the data."""

    key: str
    label: str
    version: Optional[str] = None
    detail: Optional[str] = None
    layer: str  # "pipeline" (per-family upstream) | "reference" (platform-loaded)


class AnnotationManifestOut(BaseModel):
    """The annotation/reference version manifest for a family (report provenance)."""

    family_id: str
    assembly: Optional[str] = None
    source: Optional[str] = None  # "manifest" | "vcf_header" | "manual"
    recorded_at: Optional[datetime] = None
    recorded_by: Optional[str] = None
    modules: List[AnnotationModuleOut] = Field(default_factory=list)


class AnnotationManifestUpdate(BaseModel):
    """Record/override a family's upstream annotation module versions."""

    modules: Dict[str, Any] = Field(default_factory=dict)
    source: Optional[str] = "manual"


class ClassificationDriftItem(BaseModel):
    """A classification whose backing annotation changed since it was made."""

    variant_id: str
    acmg_class: Optional[str] = None
    classified_by: Optional[str] = None
    classified_at: Optional[datetime] = None
    status: str  # "drifted" | "variant_missing"
    annotation_version_from: Optional[str] = None
    annotation_version_to: Optional[str] = None
    clinvar_from: Optional[str] = None
    clinvar_to: Optional[str] = None


class ClassificationDriftOut(BaseModel):
    """Evidence-drift summary for a family's ACMG classifications."""

    family_id: str
    checked: int
    drifted_count: int
    drifted: List[ClassificationDriftItem] = Field(default_factory=list)


class ClinicalAuditEventOut(BaseModel):
    """One immutable clinical action (who did what, when, before -> after)."""

    id: str
    created_at: datetime
    variant_id: Optional[str] = None
    actor: str
    action: str
    summary: Optional[str] = None
    before: Optional[Dict[str, Any]] = None
    after: Optional[Dict[str, Any]] = None


class ClinicalAuditOut(BaseModel):
    """A family's clinical audit timeline (most recent first)."""

    family_id: str
    events: List[ClinicalAuditEventOut] = Field(default_factory=list)


class ReportSignoutRequest(BaseModel):
    """Sign out the current report. Drift must be explicitly acknowledged."""

    acknowledge_drift: bool = False


class ReportSignoutSummary(BaseModel):
    version: int
    signed_out_by: str
    signed_out_at: datetime
    content_hash: str


class ReportSignoutDetail(ReportSignoutSummary):
    """A frozen, content-hashed report snapshot."""

    snapshot: Optional[Dict[str, Any]] = None


class ReportSignoutListOut(BaseModel):
    family_id: str
    latest: Optional[ReportSignoutSummary] = None
    signouts: List[ReportSignoutSummary] = Field(default_factory=list)


class SampleIntegrityQcOut(BaseModel):
    """Per-family sample-integrity QC, adapted to the application.

    Catches sample swaps and mislabelled relationships before interpretation. The
    checks run depend on the application (``application``): full WGS pedigrees run
    sex + relatedness + Mendelian; PGT highlights embryo sex + parentage; NIPT runs
    paternity (cat 7/8) instead of genotype relatedness; couples run sex (+ an
    expected-unrelated confirmation); single samples run sex only.
    """

    family_id: str
    overall_status: str  # "pass" | "warn" | "fail" | "skip"
    application: str  # "wgs" | "pgt" | "nipt" | "couple" | "single" | "unknown"
    application_label: str = ""
    application_summary: str = ""
    genotype_source: Optional[str] = None
    sex_checks: List[SampleIntegritySexCheckOut] = Field(default_factory=list)
    relatedness_checks: List[SampleIntegrityRelatednessCheckOut] = Field(default_factory=list)
    mendelian_checks: List[SampleIntegrityMendelianCheckOut] = Field(default_factory=list)
    paternity_check: Optional[SampleIntegrityPaternityCheckOut] = None
    fetal_sex_check: Optional[SampleIntegrityFetalSexCheckOut] = None
    category_qc_check: Optional[SampleIntegrityCategoryQcOut] = None
    autosomal_sites: int = 0
    notes: List[str] = Field(default_factory=list)


class NiptArtifactOut(BaseModel):
    """A recurrent-artifact (panel-of-normals) entry for monogenic NIPT."""

    id: str
    assembly_id: str
    assay_key: str
    variant_id: str
    recurrence_count: int
    source: str
    label: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class NiptArtifactCreate(BaseModel):
    """Create/update a NIPT artifact within an assembly + assay/panel scope."""

    assembly_id: str
    variant_id: str
    assay_key: str = "nipt_cfdna"
    label: Optional[str] = None
    source: Literal["curated", "auto"] = "curated"
    recurrence_count: int = 0


class NiptArtifactAutoSeed(BaseModel):
    """Seed the artifact list from internal cohort recurrence."""

    assembly_id: str
    assay_key: str = "nipt_cfdna"
    min_carrier_samples: int = Field(default=5, ge=1)


class NiptArtifactAutoSeedOut(BaseModel):
    seeded: int
    min_carrier_samples: int


class FamilyStatusOut(BaseModel):
    """An admin-managed family status value."""

    id: ApiId
    key: str
    label: str
    description: Optional[str] = None
    color: str
    sort_order: int
    is_active: bool

    model_config = ConfigDict(arbitrary_types_allowed=True)


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


class FamilyStructureRelationshipsUpdate(BaseModel):
    parent_child: List[FamilyStructureParentChildUpdate] = Field(default_factory=list)
    couples: List[FamilyStructureCoupleUpdate] = Field(default_factory=list)


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
    family_id: Optional[str] = None


class FamilyPackageManifestWriteOut(BaseModel):
    manifest_path: str
    validation: FamilyPackageValidationOut


class FamilyPackageCandidateOut(BaseModel):
    """A family package discovered under the configured import roots."""

    folder_path: str
    name: str
    family_id: str
    has_manifest: bool
    has_ped: bool
    analysis_type: Optional[str] = None


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
    clinical_status: Literal["unknown", "unaffected", "affected"] = "unknown"
    affected: bool = False
    is_proband: bool = False
    carrier_status: Literal["unknown", "not_carrier", "carrier"] = "unknown"
    carrier_type: Optional[Literal["obligate", "proven", "reported", "inferred"]] = None
    carrier_evidence: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ManualPedCoupleCreate(BaseModel):
    partners: List[str] = Field(min_length=2, max_length=2)
    context: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ManualPedFamilyCreate(BaseModel):
    family_id: str = Field(min_length=1)
    members: List[ManualPedMemberCreate] = Field(min_length=1)
    couples: List[ManualPedCoupleCreate] = Field(default_factory=list)
    project_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


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


class RawImportFileOut(BaseModel):
    id: str
    scope: str
    dataset: str = ""
    file_name: str
    file_type: str = ""
    sample_id: Optional[str] = None
    storage_path: str
    file_size: Optional[int] = None
    sha256: Optional[str] = None
    source: str = ""
    created_at: Optional[str] = None
    exists: bool = False
    download_available: bool = False


class FamilyRawFilesOut(BaseModel):
    family_id: str
    family_files: List[RawImportFileOut] = Field(default_factory=list)
    individual_files: List[RawImportFileOut] = Field(default_factory=list)


class RawImportFileVerifyOut(BaseModel):
    file_id: str
    status: str  # verified | mismatch | missing | unverifiable
    expected_sha256: Optional[str] = None
    computed_sha256: Optional[str] = None
    message: str = ""


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


class ClickHouseVariantTableCheckOut(BaseModel):
    name: str
    exists: bool
    passed: Optional[bool] = None
    failed_parts: int = 0
    messages: List[str] = Field(default_factory=list)


class ClickHouseDetachedPartOut(BaseModel):
    table: str
    reason: str
    count: int = 0


class ClickHouseGeneIndexConsistencyOut(BaseModel):
    checked: bool = False
    gene_index_keys: int = 0
    annotation_index_gene_keys: int = 0
    consistent: bool = True
    drift: int = 0


class ClickHouseVariantIntegrityOut(BaseModel):
    assembly_name: str
    status: str  # "ok" | "degraded" | "corrupt" | "missing"
    table_checks: List[ClickHouseVariantTableCheckOut] = Field(default_factory=list)
    detached_broken_parts: List[ClickHouseDetachedPartOut] = Field(default_factory=list)
    gene_index_consistency: ClickHouseGeneIndexConsistencyOut = Field(
        default_factory=ClickHouseGeneIndexConsistencyOut
    )
    notes: List[str] = Field(default_factory=list)


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
    # Set when the assembly was imported but its gene table could not be
    # retrieved from UCSC (e.g. T2T/hs1). The assembly + cytobands are still
    # created; genes can be uploaded manually afterwards.
    gene_warning: Optional[str] = None


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
    assembly: Optional[str] = None
    omim_id: Optional[str] = None
    omim_title: Optional[str] = None
    decipher_id: Optional[str] = None
    description: Optional[str] = None
    cytoband: Optional[str] = None
    source_id: Optional[str] = None
    orpha_id: Optional[str] = None
    orpha_name: Optional[str] = None


class SegmentalDuplicationOut(ApiDocumentModel):
    chr: str
    start: int
    end: int
    label: str
    source: Optional[str] = None


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


class SmallVariantReviewUpdate(BaseModel):
    classification: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    note: Optional[str] = None
    compound_het: Optional[SmallVariantCompoundHetReviewUpdate] = None
    acmg: Optional[AcmgClassificationPayload] = None
    # CNV (ClinGen) classification — only populated for structural-variant reviews.
    cnv_acmg: Optional[CnvAcmgClassificationPayload] = None


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
    annotation_extra: Dict[str, Any] = Field(default_factory=dict)
    transcripts: List[SmallVariantTranscriptOut] = Field(default_factory=list)
    genotypes: List[GenotypeOut] = Field(default_factory=list)
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
    # Provenance of a prioritized ranking: whether it was served from the cache and when
    # the ranking was computed (so the UI can show a "from cache · N min ago" indicator).
    ranking_cached: bool = False
    ranking_computed_at: Optional[datetime] = None
    variants: List[VariantOut] = Field(default_factory=list)
    variant_groups: List[SmallVariantGroupOut] = Field(default_factory=list)
    summary: Optional[Dict[str, Dict[str, int]]] = None
    small_variant_summary: Optional[SmallVariantSummaryOut] = None


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
    page: int = 1
    page_size: int = 50
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


class HaplotypeSegment(BaseModel):
    chr: Optional[str] = None
    start: int
    end: int
    hap1: str
    hap2: str
    ps: Optional[int] = None
    # Pedigree-aware colour class for each lane: "paternal"/"maternal" (coloured
    # by the hap value's shade), "untransmitted"/"unknown" (grey). Absent for
    # data that predates lineage tagging — the frontend then falls back to the
    # role-based colourer.
    hap1_lineage: Optional[str] = None
    hap2_lineage: Optional[str] = None


class HaplotypeSample(BaseModel):
    sample: str
    segments: List[HaplotypeSegment] = Field(default_factory=list)


class HaplotypeResponse(BaseModel):
    chr: str
    start: Optional[int] = None
    end: Optional[int] = None
    samples: List[HaplotypeSample] = Field(default_factory=list)


class PhasedMarker(BaseModel):
    """One informative imputed marker (raw call), with the value drawn on each
    haplotype lane (0/1, null = uninformative):

    - For a child: ``hap1`` = the paternal homolog inherited, ``hap2`` = the
      maternal homolog inherited, oriented to match the stored haplotype blocks.
    - For a parent: ``hap1`` / ``hap2`` are the alleles on their own two phased
      homologs (the raw per-site version of their haplotype blocks).

    The father's and mother's genotype at a site (shown in the tooltip) are
    reconstructed client-side from the father/mother marker rows at that position,
    so they are not duplicated onto every marker."""

    pos: int
    hap1: Optional[int] = None
    hap2: Optional[int] = None


class PhasedSampleQc(BaseModel):
    """Per-child phasing QC summary for one region.

    Computed only for the index couple's own children (the members for whom
    parent-of-origin is defined). ``informative_sites`` counts sites where both
    parents and the child carry a valid phased genotype (the joint-informative
    denominator). ``mendel_errors`` counts the subset of those sites whose child
    genotype is impossible given the parents' alleles — a true Mendelian
    inconsistency (likely sample swap / wrong pedigree), NOT mere parent-of-origin
    ambiguity. ``mendel_rate`` = mendel_errors / informative_sites (0 when none)."""

    informative_sites: int = 0
    mendel_errors: int = 0
    mendel_rate: float = 0.0


class PhasedMarkerSample(BaseModel):
    sample: str
    markers: List[PhasedMarker] = Field(default_factory=list)
    # True for the index parents, whose marker lanes are their own ref/alt alleles
    # (the reference phasing) rather than inherited-homolog indices. The client
    # colours these by their haplotype block (constant per lane) instead of by the
    # allele value, so they match the blocks.
    reference: bool = False
    # Per-child QC (informative-site count + Mendelian-error signal). Absent for
    # parents/relatives, for whom parent-of-origin (and thus a Mendel check) is
    # undefined.
    qc: Optional["PhasedSampleQc"] = None


class PhasedSite(BaseModel):
    """Raw phased genotypes at one imputed site, for the marker hover tooltip.

    ``gts`` is aligned to the response's ``samples`` order; each entry is that
    member's phased ``a|b`` genotype (allele indices as a string), decoded to
    nucleotides client-side via ``ref``/``alt``."""

    pos: int
    ref: str
    alt: str
    gts: List[str] = Field(default_factory=list)


class PhasedMarkerResponse(BaseModel):
    chr: str
    start: Optional[int] = None
    end: Optional[int] = None
    samples: List[PhasedMarkerSample] = Field(default_factory=list)
    sites: List[PhasedSite] = Field(default_factory=list)
    # The per-site fetch is capped (ORDER BY pos LIMIT) and deterministically drops
    # the highest-coordinate tail when the region holds more sites than the cap, so
    # a partial overlay would silently stop part-way across a full-length block. When
    # this is True the client must NOT render the markers/sites (which cover only
    # ``covered`` = [min_pos, max_pos] of the requested window) and should instead
    # prompt the user to zoom in. Blocks remain fully renderable.
    truncated: bool = False
    covered: Optional[List[int]] = None


class TrackAvailabilityOut(BaseModel):
    coverage: bool = False
    segments: bool = False
    apcad: bool = False
    apcad_pcf: bool = False
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
    # Number of loci with data. Always populated; with ``count_only`` the heavy
    # ``loci`` payload is skipped and only this presence count is returned.
    loci_count: int = 0


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


class ParaphaseClinicalOut(BaseModel):
    interpretation: Optional[str] = None
    normal: Optional[str] = None
    carrier: Optional[str] = None
    pathogenic: Optional[str] = None
    caveats: Optional[str] = None


class ParaphaseRegionInfoOut(BaseModel):
    region_id: str
    display_name: str
    genes: List[str] = Field(default_factory=list)
    summary: Optional[str] = None
    clinical: Optional[ParaphaseClinicalOut] = None
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
    # Clinical status derived from locus-specific copy-number rules where defined,
    # else "review" when a copy-number change is present: pathogenic | carrier |
    # normal | review | no_call | none.
    clinical_status: Optional[str] = None
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
    # Number of genes with data; with ``count_only`` only this count is returned.
    genes_count: int = 0


class MitoDNACoverageOut(BaseModel):
    mean_depth: Optional[float] = None
    min_depth: Optional[float] = None
    max_depth: Optional[float] = None
    breadth: Optional[float] = None
    source: Optional[str] = None
    regions: int = 0


class MitoDNAQcOut(BaseModel):
    status: Literal["pass", "warning", "fail", "unknown"] = "unknown"
    notes: List[str] = Field(default_factory=list)
    contamination: Optional[float] = None
    mean_depth: Optional[float] = None
    min_mean_depth: Optional[float] = None


class MitoDNASampleOut(BaseModel):
    sample_id: str
    role: Optional[str] = None
    affected: Optional[bool] = None
    sex: Optional[str] = None
    haplogroup: Optional[str] = None
    coverage: MitoDNACoverageOut = Field(default_factory=MitoDNACoverageOut)
    qc: MitoDNAQcOut = Field(default_factory=MitoDNAQcOut)


class MitoDNAVariantSampleCallOut(BaseModel):
    sample: str
    role: Optional[str] = None
    affected: Optional[bool] = None
    sex: Optional[str] = None
    genotype: str
    allele_fraction: Optional[float] = None
    depth: Optional[int] = None
    alt_depth: Optional[int] = None
    zygosity: Literal["homoplasmic", "heteroplasmic", "low_level", "reference", "no_call", "unknown"] = "unknown"
    display: str


class MitoDNAVariantAnnotationOut(BaseModel):
    region: str
    gene: Optional[str] = None
    consequence: Optional[str] = None
    # Normalised consequence terms (e.g. ``synonymous_variant``) parsed off the
    # underlying small-variant annotation so the UI can filter on consequence.
    consequence_terms: List[str] = Field(default_factory=list)
    # Numeric gnomAD allele frequency (chrM) when annotated; used to filter out
    # common variants. ``None`` when the variant carries no gnomAD frequency.
    gnomad_af: Optional[float] = None
    category: Optional[str] = None
    clinical_significance: Literal[
        "pathogenic",
        "likely_pathogenic",
        "benign",
        "likely_benign",
        "reported",
        "uncertain",
        "polymorphism",
        "unknown",
    ] = "unknown"
    disorders: List[str] = Field(default_factory=list)
    polymorphism_notes: List[str] = Field(default_factory=list)
    mitomap_query: str
    mitomap_url: str
    source_keys: List[str] = Field(default_factory=list)


class MitoDNAVariantOut(BaseModel):
    variant_id: str
    position: int
    ref: str
    alt: str
    label: str
    type: str
    rsid: Optional[str] = None
    annotation: MitoDNAVariantAnnotationOut
    calls: Dict[str, MitoDNAVariantSampleCallOut] = Field(default_factory=dict)
    maternal_transmission: Literal[
        "maternal_shared",
        "maternal_not_observed",
        "maternal_only",
        "father_only",
        "family_private",
        "no_alt_calls",
        "unknown",
    ] = "unknown"
    # MT variants share the small-variant review store, so the same ACMG
    # classification / tags / notes payload is attached here.
    review: Optional[SmallVariantReviewOut] = None


class FamilyMitoDNAAnalysisOut(BaseModel):
    samples: List[MitoDNASampleOut] = Field(default_factory=list)
    variants: List[MitoDNAVariantOut] = Field(default_factory=list)
    # Presence summary; with ``count_only`` the heavy ``variants``/``samples``
    # payload is skipped and only these two fields are returned.
    variant_count: int = 0
    has_coverage: bool = False
    qc_notes: List[str] = Field(default_factory=list)
    heteroplasmy_threshold: float = 0.02
    homoplasmy_threshold: float = 0.95


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


class UiEventIn(BaseModel):
    """A single client-side interaction event submitted by the frontend."""

    event_type: str
    category: Optional[str] = None
    label: Optional[str] = None
    target_id: Optional[str] = None
    path: Optional[str] = None
    to_path: Optional[str] = None
    href: Optional[str] = None
    component: Optional[str] = None
    session_id: Optional[str] = None
    occurred_at: Optional[datetime] = None
    detail: Optional[Dict[str, Any]] = None


# Max events accepted per /ui-events batch. Oversize batches are rejected with
# 422 rather than silently truncated; the telemetry client chunks keepalive
# flushes to stay within this cap.
UI_EVENT_BATCH_MAX_EVENTS = 100


class UiEventBatchIn(BaseModel):
    events: List[UiEventIn] = Field(
        default_factory=list, max_length=UI_EVENT_BATCH_MAX_EVENTS
    )


class UiEventOut(BaseModel):
    id: str
    created_at: datetime
    occurred_at: Optional[datetime] = None
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    user_role: Optional[str] = None
    event_type: str
    category: Optional[str] = None
    label: Optional[str] = None
    target_id: Optional[str] = None
    path: Optional[str] = None
    to_path: Optional[str] = None
    href: Optional[str] = None
    component: Optional[str] = None
    session_id: Optional[str] = None
    detail: Dict[str, Any] = Field(default_factory=dict)
    remote_ip: Optional[str] = None
    user_agent: Optional[str] = None


class UiEventPageOut(BaseModel):
    page: int
    page_size: int
    total: int
    items: List[UiEventOut] = Field(default_factory=list)


class UiEventIngestResult(BaseModel):
    accepted: int


class NiptVariantOut(VariantOut):
    """A classified cfDNA variant: the full small-variant payload plus the NIPT
    classification block. Defined here so it can extend ``VariantOut`` (declared
    earlier in this module); ``NiptVariantPage`` forward-references it."""

    nipt: NiptClassificationOut


# Resolve the forward reference in NiptVariantPage now that NiptVariantOut exists.
NiptVariantPage.model_rebuild()
