"""Admin inventory and ClickHouse maintenance."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SampleInventoryOut(BaseModel):
    sample_id: str
    role: str
    affected: bool
    sex: str
    projects: List[str] = Field(default_factory=list)
    track_counts: Dict[str, int] = Field(default_factory=dict)
    track_source_counts: Dict[str, Dict[str, int]] = Field(
        default_factory=dict,
        description=(
            "Interval track type → source (the caller that wrote it) → rows. A sample can hold "
            "one track type from several callers; each is deleted on its own with the delete's "
            "`source`."
        ),
    )
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


class ClickHouseIntegrityMonitorResultOut(BaseModel):
    """The scheduled integrity check's last result for one assembly."""

    assembly_name: str
    checked_at: datetime
    # The integrity report, or None when the check could not run (``error`` says so).
    report: Optional[ClickHouseVariantIntegrityOut] = None
    error: Optional[str] = None


class ClickHouseIntegrityMonitorOut(BaseModel):
    """The scheduled ClickHouse integrity check, as the running server last saw it."""

    enabled: bool
    interval_seconds: int
    last_sweep_at: Optional[datetime] = None
    # Set when the last sweep could not list the assemblies; the results are then older.
    last_sweep_error: Optional[str] = None
    results: List[ClickHouseIntegrityMonitorResultOut] = Field(default_factory=list)


class RepeatLocusCountUnitsOut(BaseModel):
    """A repeat catalogue locus with QA's record of whether TRGT's count there is the
    clinical repeat number its ranges count (services/repeat_count_units.py)."""

    locus_id: str
    gene: str
    display_name: str
    disease: str
    motif: Optional[str] = None
    warning_min: Optional[int] = None
    pathogenic_min: Optional[int] = None
    benign_min: Optional[int] = None
    benign_max: Optional[int] = None
    premutation_min: Optional[int] = None
    # "STRchive", or "built-in" for a row of repeat_expansion_catalog.py.
    source: str
    # False until QA records otherwise; with the reason, who and when of the last record.
    validated: bool = False
    # QA's correction: repeat units added to TRGT's count before it is classified (-1 turns
    # TRGT's 18 into 17); 0 for none, the default until QA records one.
    count_offset: int = 0
    reason: Optional[str] = None
    updated_by: Optional[str] = None
    updated_at: Optional[datetime] = None
    # How many stored calls name this row: the rows the laboratory's TRGT catalogue uses.
    call_count: int = 0
    # What is known of TRGT's count at the gene (FMR1, HTT, POLG), or None.
    known_difference: Optional[str] = None
    # Why a constant correction is wrong at the gene (FMR1), shown next to the field; or None.
    offset_note: Optional[str] = None


class RepeatLocusCountUnitsChangeOut(BaseModel):
    """One change of the validation record, from its clinical audit chain."""

    changed_at: datetime
    changed_by: Optional[str] = None
    locus_id: Optional[str] = None
    gene: Optional[str] = None
    validated: bool
    # The correction the change left (0: none).
    count_offset: int = 0
    summary: Optional[str] = None
    reason: Optional[str] = None


class RepeatLocusCountUnitsCatalogueOut(BaseModel):
    # How close to a boundary a count is marked while its locus is not validated.
    margin: int
    # The largest correction that can be recorded, either way, in repeat units.
    offset_limit: int
    loci: List[RepeatLocusCountUnitsOut] = Field(default_factory=list)
    # Newest first.
    changes: List[RepeatLocusCountUnitsChangeOut] = Field(default_factory=list)


class FamilyDeleteRequest(BaseModel):
    """Why an administrator deletes a family with its data (required): recorded on
    the family's last clinical audit event."""

    reason: str


class DataDeletionOut(BaseModel):
    """What deleting one kind of a sample's or a family's data removed: the number of tracks
    of a track type, of repeat calls, or of variants."""

    deleted: int


class SampleDeletionCountsOut(BaseModel):
    """What deleting a sample removed: its interval tracks (``bed``), the SVs and small
    variants that went with its calls, its repeat calls and the kept copies of its uploads."""

    samples: int
    bed: int
    structural_variants: int
    small_variants: int
    repeat_expansions: int
    raw_import_files: int


class SampleDeletionOut(BaseModel):
    deleted: SampleDeletionCountsOut


class FamilyDeletionCountsOut(BaseModel):
    """What deleting a family removed: the family, its samples, their interval tracks
    (``bed``), variants and repeat calls, and the kept copies of its uploads (``raw_files``)."""

    families: int
    samples: int
    bed: int
    structural_variants: int
    small_variants: int
    repeat_expansions: int
    raw_files: int


class FamilyDeletionOut(BaseModel):
    deleted: FamilyDeletionCountsOut


class FamilyProjectsUpdateOut(BaseModel):
    """A family's projects as an administrator set them: the family as the request named it."""

    family_id: str
    project_ids: List[str]
    message: str


class RepeatLocusCountUnitsUpdate(BaseModel):
    """A locus's whole record: its count correction, whether TRGT's counting units there are
    validated, and why (required). Every save states both, so none is left as it was by a
    client that did not say."""

    validated: bool
    # Repeat units added to TRGT's count before it is classified (0: none). Bounded by
    # repeat_count_units.COUNT_OFFSET_LIMIT, which the service checks too.
    count_offset: int = Field(ge=-20, le=20, strict=True)
    reason: str


class RepeatLocusCountUnitsSetOut(BaseModel):
    locus_id: str
    gene: Optional[str] = None
    validated: bool
    count_offset: int
    reason: str
    updated_by: Optional[str] = None
    updated_at: Optional[datetime] = None
