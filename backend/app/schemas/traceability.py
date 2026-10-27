"""Annotation manifest, classification drift, clinical audit, report sign-out, integrity anchors, request and UI event logs."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AnnotationModuleOut(BaseModel):
    """One annotation/reference module and the version that backed the data."""

    key: str
    label: str
    version: Optional[str] = None
    detail: Optional[str] = None
    layer: str  # "pipeline" (per-family upstream) | "reference" (platform-loaded)
    # Per-modality versions (issue #294): {modality: version} when the same database
    # was cited at different releases by different pipelines (e.g. SNV vs SV GENCODE).
    by_modality: Optional[Dict[str, str]] = None


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
    # Ignored: a replacement is always recorded as 'manual'. Kept so the request schema
    # and the generated API types stay stable.
    source: Optional[str] = "manual"


class ClassificationDriftItem(BaseModel):
    """A classification whose backing annotation changed since it was made."""

    variant_id: str
    acmg_class: Optional[str] = None
    classified_by: Optional[str] = None
    classified_at: Optional[datetime] = None
    status: str  # "drifted" | "unknown" | "variant_missing"
    annotation_version_from: Optional[str] = None
    annotation_version_to: Optional[str] = None
    clinvar_from: Optional[str] = None
    clinvar_to: Optional[str] = None
    # What moved (CLIN-7): "clinvar"; "transcript_id" (the primary transcript the values
    # are read from); its consequence ("effect", "impact", "lof": the LOFTEE call); a
    # population frequency ("gnomad_af", "gnomad_exomes_af", "gnomad_genomes_af",
    # "gnomad_popmax_af", "topmed_af", "gnomad_ac", "gnomad_hom_count",
    # "gnomad_hemi_count"); an in-silico score ("cadd_phred", "revel", "spliceai_max",
    # "sift", "polyphen", "alpha_missense_class", "alpha_missense_pathogenicity"); a
    # missense variant's gene "gene_missense_z"; or "annotations", the annotation set.
    changed: List[str] = Field(default_factory=list)
    # The transcript, consequence, frequencies, in-silico scores and (a missense variant's)
    # gene missense Z as frozen (None when the snapshot froze none, being older; a snapshot
    # frozen before the consequence and missense Z were holds the others) and as they are
    # now (None when the variant is gone).
    evidence_from: Optional[Dict[str, Any]] = None
    evidence_to: Optional[Dict[str, Any]] = None


class StructuralClassificationDriftItem(BaseModel):
    """A structural-variant / CNV classification whose frozen evidence changed since it
    was made (services/structural_variant_evidence.py)."""

    variant_id: str
    classification: Optional[str] = None
    cnv_class: Optional[str] = None
    classified_by: Optional[str] = None
    classified_at: Optional[datetime] = None
    status: str  # "drifted" | "unknown" | "variant_missing"
    # What moved: "source", "sv_type", "locus", "gene_symbols", "pli", "inheritance",
    # "annotations".
    changed: List[str] = Field(default_factory=list)
    # The evidence as frozen and as it is now: source, sv_type, locus, gene_symbols,
    # gene_count, pli, inheritance, annotation_hash. None when not available.
    evidence_from: Optional[Dict[str, Any]] = None
    evidence_to: Optional[Dict[str, Any]] = None


class StructuralClassificationDriftOut(BaseModel):
    """Evidence-drift summary for a family's structural-variant / CNV classifications."""

    checked: int = 0
    drifted_count: int = 0
    drifted: List[StructuralClassificationDriftItem] = Field(default_factory=list)


class ClassificationDriftOut(BaseModel):
    """Evidence-drift summary for a family's ACMG classifications: the small variants at
    the top level, the structural variants and CNVs under ``structural``."""

    family_id: str
    checked: int
    drifted_count: int
    drifted: List[ClassificationDriftItem] = Field(default_factory=list)
    structural: StructuralClassificationDriftOut = Field(
        default_factory=StructuralClassificationDriftOut
    )


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
    """A page of a family's clinical audit timeline (most recent first).

    ``total`` is how many events the family's trail holds, read with the page. When
    ``has_more`` is true, older events remain: ask for them with ``before`` set to the id
    of the page's last event.
    """

    family_id: str
    total: int
    has_more: bool
    events: List[ClinicalAuditEventOut] = Field(default_factory=list)


# The fingerprint of the concerns a gate's refusal listed: a SHA-256, in lower-case hex.
_CONCERNS_FINGERPRINT = r"^[0-9a-f]{64}$"


class ReportSignoutRequest(BaseModel):
    """Sign out the current report.

    Evidence drift, a failing sample-integrity QC and an incomplete import (a family
    package import that partly failed) must each be explicitly acknowledged, and each
    acknowledgement requires a reason and the fingerprint of the concerns it covers: the
    ``concerns_fingerprint`` of the 409 that listed them (TRACE-19). A sign-out whose
    concerns are no longer those is refused with them as they are now.
    """

    acknowledge_drift: bool = False
    drift_acknowledgement_reason: Optional[str] = None
    drift_concerns_fingerprint: Optional[str] = Field(default=None, pattern=_CONCERNS_FINGERPRINT)
    acknowledge_qc: bool = False
    qc_acknowledgement_reason: Optional[str] = None
    qc_concerns_fingerprint: Optional[str] = Field(default=None, pattern=_CONCERNS_FINGERPRINT)
    acknowledge_import_incomplete: bool = False
    import_incomplete_acknowledgement_reason: Optional[str] = None
    import_incomplete_concerns_fingerprint: Optional[str] = Field(
        default=None, pattern=_CONCERNS_FINGERPRINT
    )


class ReportSnapshotGapOut(BaseModel):
    """A part of a signed snapshot that was frozen as unavailable, and why (#514)."""

    section: str
    item: str
    reason: str


class ReportSignoutSummary(BaseModel):
    version: int
    signed_out_by: str
    signed_out_at: datetime
    content_hash: str
    # The code the EPD report quotes for this record: made from the family, the version and
    # the content hash (services/report_code.py), so no column holds it. None only for a
    # record whose stored fields cannot make one.
    report_code: Optional[str] = None
    # Frozen software identity that produced this sign-out (NULL for sign-outs
    # created before version-binding shipped). Extracted from the JSONB snapshot.
    software_version: Optional[str] = None
    git_sha: Optional[str] = None
    # Frozen Sample-integrity QC verdict + override (NULL for sign-outs created before
    # the QC gate). Extracted from the JSONB snapshot like the software identity.
    qc_status: Optional[str] = None
    qc_acknowledged: Optional[bool] = None
    qc_acknowledgement_reason: Optional[str] = None
    # Frozen evidence-drift override and its reason (NULL for sign-outs made before a
    # drift override needed a reason).
    drift_acknowledged: Optional[bool] = None
    drift_acknowledgement_reason: Optional[str] = None
    # Frozen incomplete-import state and its override: the datasets a partly failed
    # package import had not loaded when the report was signed (NULL when the import was
    # complete), the import job whose record holds each dataset's error (NULL when not
    # recorded), and the acknowledgement. All NULL for sign-outs made before the gate.
    import_incomplete_failed_datasets: Optional[List[str]] = None
    import_incomplete_job_id: Optional[str] = None
    import_incomplete_acknowledged: Optional[bool] = None
    import_incomplete_acknowledgement_reason: Optional[str] = None
    # Re-verification of the stored content hash against the snapshot, done on detail
    # reads (None when not checked, e.g. in list views). False ⇒ snapshot was tampered.
    verified: Optional[bool] = None


class ReportSignoutDetail(ReportSignoutSummary):
    """A frozen, content-hashed report snapshot."""

    snapshot: Optional[Dict[str, Any]] = None
    # What this version's snapshot froze as unavailable or, being older, does not hold. The
    # report page renders a signed version from its snapshot alone, so it names these.
    not_captured: List[ReportSnapshotGapOut] = Field(default_factory=list)


class ReportCodeLookupOut(BaseModel):
    """The signed record a report code names (``GET /report-codes/{code}``).

    The EPD report refers to the record by its code; this says which family and version it
    is, to open it at ``/families/{family_id}/report?version={version}``.
    """

    report_code: str
    family_id: str
    version: int
    signed_out_at: datetime
    signed_out_by: str
    content_hash: str


class ReportSignoutListOut(BaseModel):
    family_id: str
    latest: Optional[ReportSignoutSummary] = None
    signouts: List[ReportSignoutSummary] = Field(default_factory=list)


class ReportSignoutCheckOut(BaseModel):
    """Whether the report, as it would be signed now, matches the latest sign-out.

    ``matches`` is None when the family has never been signed out. ``changed_sections``
    names the snapshot sections whose content differs from the signed one;
    ``not_compared`` those the signed snapshot predates and so cannot be compared (a
    reference module as ``modules.<key>``, e.g. ``modules.hpo``); ``not_captured`` the
    parts the signed snapshot records as unavailable or, being older, does not hold.
    """

    family_id: str
    version: Optional[int] = None
    content_hash: Optional[str] = None
    matches: Optional[bool] = None
    changed_sections: List[str] = Field(default_factory=list)
    not_compared: List[str] = Field(default_factory=list)
    not_captured: List[ReportSnapshotGapOut] = Field(default_factory=list)
    checked_at: datetime


class IntegrityVerifyOut(BaseModel):
    """Result of re-walking an append-only table's per-family tamper-evidence chain."""

    table: str
    family_id: str
    verified: bool
    rows_checked: int
    first_bad_row: Optional[str] = None
    reason: Optional[str] = None


class IntegrityAnchorOut(BaseModel):
    """A signed chain-head anchor (the verbatim signed blob is self-verifying)."""

    anchor_seq: int
    created_at: datetime
    prev_anchor_hash: Optional[str] = None
    anchor_root: str
    anchor_hash: str
    chain_count: int
    key_id: str
    algo: str
    public_key: Optional[str] = None
    signature: Optional[str] = None


class IntegrityAnchorVerifyOut(BaseModel):
    """Result of verifying the live chains against the latest signed anchor."""

    status: str  # ok | diverged | signature_invalid | unknown_key | unverifiable_unsigned | no_anchor
    anchor_seq: Optional[int] = None
    chain_count: int = 0
    diverged: List[Dict[str, Any]] = Field(default_factory=list)
    reason: Optional[str] = None


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
    # The id the browser gave the event (a UUID): the event is stored under it, once, also when
    # the browser sends its batch again (routers/ui_events.py).
    event_id: Optional[str] = None


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
