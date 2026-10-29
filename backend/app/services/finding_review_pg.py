"""The analyst's review of a repeat locus and of a Paraphase region.

One review per family and assembly per repeat locus (the catalogue's ``locus_id``, a TRGT
TRID) and per Paraphase region (Paraphase's ``gene_symbol`` key), as small variants and
structural variants have one per variant (docs/repeat-paraphase-review-design.md, B1): tags
from the family's tag catalogue, a note, and a classification on the scale of its type (B4),
beside CoGA's automatic status, which a review never changes.

A review that carries a judgement the report rests on (a classification, or the Report tag)
freezes the calls it rests on (``evidence_snapshot``): for a repeat locus its entry as the
signed record freezes it (``signed_record_sections.repeat_expansion_calls``: each member's
call, alleles, near-boundary warnings, the locus's thresholds and QA's counting-units record),
for a Paraphase region its entry in ``signed_record_sections.paraphase_results`` without the
time each result was loaded. A save that changes the classification or adds the Report tag
freezes them again; a save of tags or the note alone keeps them, so it cannot clear a change
in the calls without a new judgement (as an ACMG or CNV classification keeps its evidence).
The sign-out compares the frozen calls with the calls as they are then (B5): see
:func:`finding_reviews_for_record`.

Saves are serialized per review and refused when the review changed since the client loaded
it (409, REQ-TRACE-010); each change is a clinical audit event in the same transaction
(REQ-TRACE-004).
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import (
    FamilyParaphaseTableOut,
    FamilyRepeatExpansionTableOut,
    FindingReviewCountsOut,
    FindingReviewDriftOut,
    FindingReviewOut,
    FindingReviewSummaryOut,
    ParaphaseReviewUpdate,
    RepeatExpansionReviewUpdate,
)
from .access_control import CurrentUser
from .clinical_audit_service import record_finding_review_changes
from .family_metadata_context import FamilyMetadataContext
from .hash_chain import canonical_hash
from .paraphase_pg import get_family_paraphase_table_response
from .repeat_expansion_pg import get_family_repeat_expansion_table_response
from .review_pg_utils import (
    _added_tags,
    _json_payload,
    _lock_review,
    _merge_tag_metadata,
    _normalize_tags,
    _raise_on_stale_review,
)
from .signed_record_sections import PARAPHASE, REPEAT_EXPANSIONS, paraphase_results, repeat_expansion_calls
from .small_variant_review_tags import list_small_variant_tag_definitions

# The tag that puts a finding on the report, as for the variants.
REPORT_TAG = "report"

REPEAT_CLASSIFICATION_LABELS: dict[str, str] = {
    "pathogenic_expansion": "Pathogenic expansion",
    "reduced_penetrance": "Reduced penetrance",
    "premutation": "Premutation",
    "intermediate": "Intermediate (grey zone)",
    "normal": "Normal",
    "uncertain": "Uncertain significance",
}
PARAPHASE_CLASSIFICATION_LABELS: dict[str, str] = {
    "affected": "Affected",
    "carrier": "Carrier",
    "risk_allele": "Risk allele",
    "normal": "Normal",
    "uncertain": "Uncertain significance",
}

# The statuses that make a call abnormal for the sign-out's gate of calls without a review
# (B8, B12): a grey-zone (intermediate) repeat call does not, unless one of its counts lies near
# a boundary at a locus whose TRGT counting units QA has not validated.
ABNORMAL_REPEAT_STATUSES = frozenset({"pathogenic", "premutation", "review", "not_assessed"})
ABNORMAL_PARAPHASE_STATUSES = frozenset({"pathogenic", "carrier", "review"})

# The sections the sign-out adds to the record (report_signout_service.build_report_snapshot).
REPEAT_REVIEWS_SECTION = "repeat_expansion_reviews"
PARAPHASE_REVIEWS_SECTION = "paraphase_reviews"
FINDING_DRIFT_SECTION = "finding_review_drift"
UNREVIEWED_FINDINGS_SECTION = "unreviewed_findings"

# How many differences a drift names, each "path: before → after"; more are counted.
_MAX_CHANGES = 20


@dataclass(frozen=True, slots=True)
class FindingKind:
    """A kind of finding a review is of. ``table`` and ``key_column`` are fixed names from this
    module, never a request's, so the SQL built from them stays parameterized."""

    modality: str
    table: str
    key_column: str
    lock_scope: str
    noun: str
    labels: Mapping[str, str]


REPEAT = FindingKind(
    modality="repeat",
    table="repeat_expansion_reviews",
    key_column="locus_id",
    lock_scope="rptr",
    noun="repeat locus",
    labels=REPEAT_CLASSIFICATION_LABELS,
)
PARAPHASE_REGION = FindingKind(
    modality="paraphase",
    table="paraphase_reviews",
    key_column="gene_symbol",
    lock_scope="pphr",
    noun="Paraphase region",
    labels=PARAPHASE_CLASSIFICATION_LABELS,
)


def _select_sql(kind: FindingKind, *, one: bool) -> Any:
    return text(
        f"""
        SELECT id::text AS id, {kind.key_column} AS key, classification, tags, tag_metadata,
               note, evidence_snapshot, updated_by, updated_at
        FROM {kind.table}
        WHERE family_id = CAST(:family_id AS uuid)
          AND assembly_id = CAST(:assembly_id AS uuid)
          {f"AND {kind.key_column} = :key" if one else ""}
        ORDER BY {kind.key_column}
        """
    )


async def finding_review_rows(
    session: AsyncSession, kind: FindingKind, *, family_uuid: str, assembly_id: str | None
) -> dict[str, dict[str, Any]]:
    """The family's reviews of ``kind`` in its assembly, by key (none without an assembly)."""
    if not assembly_id:
        return {}
    result = await session.execute(
        _select_sql(kind, one=False), {"family_id": family_uuid, "assembly_id": assembly_id}
    )
    return {str(row["key"]): dict(row) for row in result.mappings().all()}


async def _review_row(
    session: AsyncSession, kind: FindingKind, *, family_uuid: str, assembly_id: str, key: str
) -> dict[str, Any] | None:
    result = await session.execute(
        _select_sql(kind, one=True),
        {"family_id": family_uuid, "assembly_id": assembly_id, "key": key},
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None


def has_review_content(row: Mapping[str, Any] | None) -> bool:
    """Whether a review says anything: a classification, a tag or a note."""
    if not row:
        return False
    return bool(
        str(row.get("classification") or "").strip()
        or _normalize_tags(row.get("tags") or [])
        or str(row.get("note") or "").strip()
    )


def bears_judgement(row: Mapping[str, Any] | None) -> bool:
    """Whether a review carries a judgement the report rests on: a classification or the
    Report tag. Only these freeze their calls and are checked for drift (B13)."""
    if not row:
        return False
    return bool(str(row.get("classification") or "").strip()) or REPORT_TAG in _normalize_tags(
        row.get("tags") or []
    )


def _review_counts(rows: Mapping[str, Mapping[str, Any]]) -> FindingReviewCountsOut:
    counts = FindingReviewCountsOut()
    for row in rows.values():
        if not has_review_content(row):
            continue
        counts.reviewed_count += 1
        if str(row.get("classification") or "").strip():
            counts.classified_count += 1
        if str(row.get("note") or "").strip():
            counts.note_count += 1
        for tag in _normalize_tags(row.get("tags") or []):
            counts.tag_counts[tag] = counts.tag_counts.get(tag, 0) + 1
    return counts


async def finding_review_summary(
    session: AsyncSession, context: FamilyMetadataContext
) -> FindingReviewSummaryOut:
    """The family's repeat and Paraphase reviews in its assembly, counted for the workspace's
    curation lines (none without an assembly)."""
    assembly_id = getattr(context, "assembly_id", None)
    return FindingReviewSummaryOut(
        repeat=_review_counts(
            await finding_review_rows(session, REPEAT, family_uuid=context.family_uuid, assembly_id=assembly_id)
        ),
        paraphase=_review_counts(
            await finding_review_rows(
                session, PARAPHASE_REGION, family_uuid=context.family_uuid, assembly_id=assembly_id
            )
        ),
    )


# --- The calls a review rests on ---------------------------------------------------


def repeat_locus_evidence(section: Mapping[str, Any], locus_id: str) -> dict[str, Any] | None:
    """The locus's entry in the record's repeat section (``repeat_expansion_calls``), or None
    when the family has no call at it."""
    for locus in section.get("loci") or []:
        if isinstance(locus, Mapping) and str(locus.get("locus_id")) == locus_id:
            return copy.deepcopy(dict(locus))
    return None


def paraphase_region_evidence(section: Mapping[str, Any], gene_symbol: str) -> dict[str, Any] | None:
    """The region's entry in the record's Paraphase section (``paraphase_results``), without
    the time each result was loaded: loading the same result again changes no call."""
    for gene in section.get("genes") or []:
        if isinstance(gene, Mapping) and str(gene.get("gene")) == gene_symbol:
            entry = copy.deepcopy(dict(gene))
            entry["samples"] = [
                {key: value for key, value in sample.items() if key != "loaded_at"}
                for sample in entry.get("samples") or []
                if isinstance(sample, Mapping)
            ]
            return entry
    return None


def finding_evidence(kind: FindingKind, section: Any, key: str) -> dict[str, Any] | None:
    """The calls a review of ``kind`` at ``key`` rests on, from the record's section."""
    if not isinstance(section, Mapping):
        return None
    if kind is REPEAT:
        return repeat_locus_evidence(section, key)
    return paraphase_region_evidence(section, key)


def evidence_digest(snapshot: Mapping[str, Any] | None) -> str | None:
    """The SHA-256 of the frozen calls (canonical JSON), as the audit event records it."""
    if not snapshot or not isinstance(snapshot.get("evidence"), Mapping):
        return None
    return canonical_hash(snapshot["evidence"])


def _identity(item: Any, index: int) -> str:
    if isinstance(item, Mapping):
        for name in ("sample", "locus_id", "gene", "key"):
            if item.get(name) is not None:
                return str(item[name])
    return str(index)


def _flatten(value: Any, path: str, out: dict[str, Any]) -> None:
    if isinstance(value, Mapping):
        if not value:
            out[path] = {}
        for key in sorted(value):
            _flatten(value[key], f"{path}.{key}" if path else str(key), out)
    elif isinstance(value, list) and value and all(isinstance(item, Mapping) for item in value):
        for index, item in enumerate(value):
            _flatten(item, f"{path}[{_identity(item, index)}]", out)
    else:
        out[path] = value


def _shown(value: Any) -> str:
    """A value in a drift line: a list or record as compact JSON, cut past 80 characters."""
    if value is None:
        return "none"
    if isinstance(value, (list, dict)):
        shown = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        return shown if len(shown) <= 80 else f"{shown[:77]}..."
    return str(value)


def evidence_changes(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    """Each difference between the frozen and the current calls, as ``path: before → after``,
    sorted; a member's call is named by its sample (``calls[PROBAND].alleles[1].repeat_count``).
    Past ``_MAX_CHANGES``, the rest are counted."""
    old: dict[str, Any] = {}
    new: dict[str, Any] = {}
    _flatten(before, "", old)
    _flatten(after, "", new)
    absent = object()
    changes = [
        f"{path}: {_shown(old.get(path)) if path in old else 'absent'} → "
        f"{_shown(new.get(path)) if path in new else 'absent'}"
        for path in sorted(set(old) | set(new))
        if old.get(path, absent) != new.get(path, absent)
    ]
    if len(changes) > _MAX_CHANGES:
        rest = len(changes) - _MAX_CHANGES
        return [*changes[:_MAX_CHANGES], f"and {rest} other difference{'s' if rest != 1 else ''}"]
    return changes


def finding_drift(
    snapshot: Any, current: Mapping[str, Any] | None, *, readable: bool = True
) -> dict[str, Any]:
    """Whether the calls a review froze are the calls now: ``{"status", "changes"}`` with
    the status ``unchanged``, ``changed``, ``no_snapshot``, ``not_found`` or ``unverifiable``
    (FindingReviewDriftOut)."""
    if not isinstance(snapshot, Mapping) or not isinstance(snapshot.get("evidence"), Mapping):
        return {"status": "no_snapshot", "changes": []}
    if not readable:
        return {"status": "unverifiable", "changes": []}
    if current is None:
        return {"status": "not_found", "changes": []}
    frozen = snapshot["evidence"]
    if canonical_hash(frozen) == canonical_hash(current):
        return {"status": "unchanged", "changes": []}
    return {"status": "changed", "changes": evidence_changes(frozen, current)}


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def serialize_finding_review(
    row: Mapping[str, Any], drift: Mapping[str, Any] | None = None
) -> FindingReviewOut:
    snapshot = row.get("evidence_snapshot")
    captured = snapshot.get("captured_at") if isinstance(snapshot, Mapping) else None
    return FindingReviewOut(
        key=str(row.get("key") or ""),
        classification=row.get("classification"),
        tags=_normalize_tags(row.get("tags") or []),
        tag_metadata=row.get("tag_metadata") or {},
        note=row.get("note"),
        updated_by=row.get("updated_by"),
        updated_at=row.get("updated_at"),
        evidence_captured_at=_as_utc(captured),
        drift=FindingReviewDriftOut(**drift) if drift is not None else None,
    )


# --- The family's tables with their reviews ------------------------------------------


async def attach_repeat_reviews(
    session: AsyncSession, context: FamilyMetadataContext, table: FamilyRepeatExpansionTableOut
) -> FamilyRepeatExpansionTableOut:
    """``table`` with each locus's review in the family's assembly and its drift."""
    rows = await finding_review_rows(
        session, REPEAT, family_uuid=context.family_uuid, assembly_id=context.assembly_id
    )
    if not rows or not table.loci:
        return table
    section = await repeat_expansion_calls(session, context, table)
    for locus in table.loci:
        row = rows.get(locus.locus_id)
        if row is not None:
            locus.review = serialize_finding_review(
                row, finding_drift(row.get("evidence_snapshot"), repeat_locus_evidence(section, locus.locus_id))
            )
    return table


async def attach_paraphase_reviews(
    session: AsyncSession, context: FamilyMetadataContext, table: FamilyParaphaseTableOut
) -> FamilyParaphaseTableOut:
    """``table`` with each region's review in the family's assembly and its drift."""
    rows = await finding_review_rows(
        session, PARAPHASE_REGION, family_uuid=context.family_uuid, assembly_id=context.assembly_id
    )
    if not rows or not table.genes:
        return table
    section = await paraphase_results(session, context, table)
    for gene in table.genes:
        row = rows.get(gene.gene_symbol)
        if row is not None:
            gene.review = serialize_finding_review(
                row,
                finding_drift(row.get("evidence_snapshot"), paraphase_region_evidence(section, gene.gene_symbol)),
            )
    return table


async def _current_evidence(
    session: AsyncSession, kind: FindingKind, context: FamilyMetadataContext, key: str
) -> dict[str, Any] | None:
    if kind is REPEAT:
        repeats = await get_family_repeat_expansion_table_response(session, context=context)
        return repeat_locus_evidence(await repeat_expansion_calls(session, context, repeats), key)
    regions = await get_family_paraphase_table_response(session, context=context)
    return paraphase_region_evidence(await paraphase_results(session, context, regions), key)


# --- Saving a review -------------------------------------------------------------------


async def upsert_finding_review(
    session: AsyncSession,
    *,
    kind: FindingKind,
    context: FamilyMetadataContext,
    key: str,
    payload: RepeatExpansionReviewUpdate | ParaphaseReviewUpdate,
    user: CurrentUser,
) -> FindingReviewOut:
    """Save the review of the repeat locus or Paraphase region ``key`` in the family's assembly.

    A save that says nothing (no classification, tag or note) deletes the review. One that
    says something is refused for a locus or region the family holds no call at (404). Only
    a tag the save adds must be one the family may use (400).
    """
    normalized_key = str(key).strip()
    if not normalized_key:
        raise HTTPException(status_code=400, detail=f"A {kind.noun} is required")
    assembly_id = context.assembly_id
    if not assembly_id:
        raise HTTPException(
            status_code=422,
            detail=(
                f"This family has no reference assembly, so a review of a {kind.noun} cannot "
                "say which calls it is of. Link the family to a project with an assembly."
            ),
        )
    normalized_tags = _normalize_tags(payload.tags)
    normalized_note = (payload.note or "").strip() or None
    normalized_classification = payload.classification or None
    writes = bool(normalized_tags or normalized_note or normalized_classification)
    # The calls the review is of, read before the lock as the SV save reads its evidence.
    current = await _current_evidence(session, kind, context, normalized_key) if writes else None
    if writes and current is None:
        raise HTTPException(
            status_code=404, detail=f"This family holds no call at that {kind.noun}"
        )
    await _lock_review(
        session,
        scope=kind.lock_scope,
        family_uuid=context.family_uuid,
        variant_id=f"{assembly_id}:{normalized_key}",
    )
    existing = await _review_row(
        session, kind, family_uuid=context.family_uuid, assembly_id=assembly_id, key=normalized_key
    )
    _raise_on_stale_review(payload, existing, serialize_finding_review)
    added_tags = _added_tags(normalized_tags, (existing or {}).get("tags"))
    if added_tags:
        allowed_tags = {
            definition.key
            for definition in await list_small_variant_tag_definitions(
                session, family_uuid=context.family_uuid, project_ids=context.project_ids
            )
        }
        unknown_tags = [tag for tag in added_tags if tag not in allowed_tags]
        if unknown_tags:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown review tag(s): {', '.join(sorted(unknown_tags))}",
            )
    now = datetime.now(timezone.utc)

    async def _audit(new_state: Mapping[str, Any] | None, snapshot: Mapping[str, Any] | None) -> None:
        await record_finding_review_changes(
            session,
            family_uuid=context.family_uuid,
            family_identifier=context.family_id,
            modality=kind.modality,
            key=normalized_key,
            assembly_id=str(assembly_id),
            evidence_digest=evidence_digest(snapshot),
            labels=kind.labels,
            user=user,
            existing=existing,
            new_state=new_state,
        )

    if not writes:
        if existing is not None:
            await session.execute(
                text(f"DELETE FROM {kind.table} WHERE id = CAST(:review_id AS uuid)"),
                {"review_id": existing["id"]},
            )
            await _audit(None, None)
            await session.commit()
        return FindingReviewOut(key=normalized_key, tags=[])

    new_state = {
        "classification": normalized_classification,
        "tags": normalized_tags,
        "note": normalized_note,
    }
    stored_snapshot = (existing or {}).get("evidence_snapshot")
    previous_tags = _normalize_tags((existing or {}).get("tags") or [])
    refreeze = bears_judgement(new_state) and (
        not isinstance(stored_snapshot, Mapping)
        or normalized_classification != ((existing or {}).get("classification") or None)
        or (REPORT_TAG in normalized_tags and REPORT_TAG not in previous_tags)
    )
    snapshot = {"captured_at": now.isoformat(), "evidence": current} if refreeze else stored_snapshot
    fields = {
        "family_id": context.family_uuid,
        "assembly_id": assembly_id,
        "key": normalized_key,
        "classification": normalized_classification,
        "tags_json": _json_payload(normalized_tags),
        "tag_metadata_json": _json_payload(
            _merge_tag_metadata(
                existing_metadata=(existing or {}).get("tag_metadata"),
                previous_tags=previous_tags,
                next_tags=normalized_tags,
                username=user.username,
                timestamp=now,
            )
        ),
        "note": normalized_note,
        "snapshot_json": _json_payload(snapshot) if snapshot else None,
        "updated_by": user.username,
        "updated_at": now,
    }
    if existing is not None:
        await session.execute(
            text(
                f"""
                UPDATE {kind.table}
                SET classification = :classification,
                    tags = CAST(:tags_json AS jsonb),
                    tag_metadata = CAST(:tag_metadata_json AS jsonb),
                    note = :note,
                    evidence_snapshot = CAST(:snapshot_json AS jsonb),
                    updated_by = :updated_by,
                    updated_at = :updated_at
                WHERE id = CAST(:review_id AS uuid)
                """
            ),
            {**fields, "review_id": existing["id"]},
        )
    else:
        await session.execute(
            text(
                f"""
                INSERT INTO {kind.table} (
                    family_id, assembly_id, {kind.key_column}, classification, tags, tag_metadata,
                    note, evidence_snapshot, updated_by, created_at, updated_at
                )
                VALUES (
                    CAST(:family_id AS uuid), CAST(:assembly_id AS uuid), :key, :classification,
                    CAST(:tags_json AS jsonb), CAST(:tag_metadata_json AS jsonb), :note,
                    CAST(:snapshot_json AS jsonb), :updated_by, :updated_at, :updated_at
                )
                """
            ),
            fields,
        )
    await _audit(new_state, snapshot if isinstance(snapshot, Mapping) else None)
    await session.commit()
    saved = {
        **new_state,
        "key": normalized_key,
        "tag_metadata": _merge_tag_metadata(
            existing_metadata=(existing or {}).get("tag_metadata"),
            previous_tags=previous_tags,
            next_tags=normalized_tags,
            username=user.username,
            timestamp=now,
        ),
        "evidence_snapshot": snapshot,
        "updated_by": user.username,
        "updated_at": now,
    }
    return serialize_finding_review(saved, finding_drift(snapshot, current))


# --- What the sign-out freezes -----------------------------------------------------------


def _readable(section: Any) -> bool:
    return isinstance(section, Mapping) and "unavailable" not in section


def _recorded_review(
    kind: FindingKind, row: Mapping[str, Any], drift: Mapping[str, Any]
) -> dict[str, Any]:
    classification = row.get("classification") or None
    tags = _normalize_tags(row.get("tags") or [])
    updated_at = _as_utc(row.get("updated_at"))
    return {
        "key": str(row.get("key")),
        "classification": classification,
        "classification_label": kind.labels.get(classification) if classification else None,
        "tags": tags,
        "reported": REPORT_TAG in tags,
        "note": str(row.get("note") or "").strip() or None,
        "reviewed_by": row.get("updated_by"),
        "reviewed_at": updated_at.isoformat() if updated_at else None,
        "evidence_snapshot": row.get("evidence_snapshot"),
        "drift": dict(drift),
    }


def _repeat_unreviewed(section: Mapping[str, Any], reviewed: set[str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for locus in section.get("loci") or []:
        if not isinstance(locus, Mapping) or str(locus.get("locus_id")) in reviewed:
            continue
        samples = []
        for call in locus.get("calls") or []:
            if not isinstance(call, Mapping):
                continue
            near_boundary = any(
                isinstance(allele, Mapping) and allele.get("count_unit_warning")
                for allele in call.get("alleles") or []
            )
            if call.get("status") in ABNORMAL_REPEAT_STATUSES or near_boundary:
                samples.append(
                    {"sample": call.get("sample"), "status": call.get("status"), "near_boundary": near_boundary}
                )
        if samples:
            findings.append(
                {
                    "kind": REPEAT.modality,
                    "key": str(locus.get("locus_id")),
                    "gene": locus.get("gene"),
                    "status": locus.get("status"),
                    "samples": samples,
                }
            )
    return findings


def _paraphase_unreviewed(section: Mapping[str, Any], reviewed: set[str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for gene in section.get("genes") or []:
        if not isinstance(gene, Mapping) or str(gene.get("gene")) in reviewed:
            continue
        samples = [
            {"sample": sample.get("sample"), "status": sample.get("status")}
            for sample in gene.get("samples") or []
            if isinstance(sample, Mapping) and sample.get("status") in ABNORMAL_PARAPHASE_STATUSES
        ]
        if samples:
            findings.append(
                {
                    "kind": PARAPHASE_REGION.modality,
                    "key": str(gene.get("gene")),
                    "gene": gene.get("region") or gene.get("gene"),
                    "samples": samples,
                }
            )
    return findings


async def finding_reviews_for_record(
    session: AsyncSession, context: FamilyMetadataContext, sections: Mapping[str, Any]
) -> dict[str, Any]:
    """The four keys the sign-out adds to the record, from the record's own repeat and
    Paraphase sections (``sections``, as ``build_record_sections`` made them):

    * ``repeat_expansion_reviews`` / ``paraphase_reviews``: each review in the family's
      assembly that carries a classification or the Report tag, with the calls it froze and
      whether they still are the family's (REQ-TRACE-017);
    * ``finding_review_drift``: those whose calls changed, are gone, or cannot be compared,
      which the drift gate counts with the variants' (REQ-TRACE-015);
    * ``unreviewed_findings``: each repeat locus and Paraphase region with an abnormal call
      and no review, which the gate of calls without a review lists (REQ-TRACE-016). A section
      that could not be read is listed as such, as its calls cannot be checked.

    Every list is sorted, so identical content hashes identically.
    """
    record: dict[str, Any] = {}
    drifted: list[dict[str, Any]] = []
    checked = 0
    unreviewed: list[dict[str, Any]] = []
    for kind, section_key, record_key in (
        (REPEAT, REPEAT_EXPANSIONS.key, REPEAT_REVIEWS_SECTION),
        (PARAPHASE_REGION, PARAPHASE.key, PARAPHASE_REVIEWS_SECTION),
    ):
        section = sections.get(section_key)
        readable = _readable(section)
        rows = await finding_review_rows(
            session, kind, family_uuid=context.family_uuid, assembly_id=getattr(context, "assembly_id", None)
        )
        recorded = []
        for key, row in sorted(rows.items()):
            if not bears_judgement(row):
                continue
            current = finding_evidence(kind, section, key) if readable else None
            drift = finding_drift(row.get("evidence_snapshot"), current, readable=readable)
            entry = _recorded_review(kind, row, drift)
            recorded.append(entry)
            checked += 1
            if drift["status"] != "unchanged":
                drifted.append(
                    {
                        "kind": kind.modality,
                        "key": entry["key"],
                        "classification": entry["classification"],
                        "reported": entry["reported"],
                        "status": drift["status"],
                        "changes": drift["changes"],
                    }
                )
        record[record_key] = recorded
        reviewed = {key for key, row in rows.items() if has_review_content(row)}
        if not readable:
            if section is not None:
                unreviewed.append({"kind": kind.modality, "key": None, "status": "unreadable", "samples": []})
            continue
        assert isinstance(section, Mapping)
        unreviewed.extend(
            _repeat_unreviewed(section, reviewed)
            if kind is REPEAT
            else _paraphase_unreviewed(section, reviewed)
        )
    record[FINDING_DRIFT_SECTION] = {
        "checked": checked,
        "drifted_count": len(drifted),
        "drifted": sorted(drifted, key=lambda item: (item["kind"], item["key"])),
    }
    record[UNREVIEWED_FINDINGS_SECTION] = sorted(
        unreviewed, key=lambda item: (item["kind"], item["key"] or "")
    )
    return record


def unreviewed_finding_names(findings: Sequence[Mapping[str, Any]]) -> list[str]:
    """Each unreviewed finding in a line of words, for the gate's message."""
    names = []
    for finding in findings:
        noun = REPEAT.noun if finding.get("kind") == REPEAT.modality else PARAPHASE_REGION.noun
        if finding.get("key") is None:
            names.append(f"the {noun} calls could not be read")
            continue
        samples = ", ".join(
            f"{sample.get('sample')} {sample.get('status')}"
            + (" (near a boundary)" if sample.get("near_boundary") else "")
            for sample in finding.get("samples") or []
        )
        names.append(f"{finding.get('gene') or finding.get('key')} ({noun}): {samples}")
    return names
