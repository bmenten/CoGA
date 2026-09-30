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
