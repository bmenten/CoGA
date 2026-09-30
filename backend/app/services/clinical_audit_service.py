"""Immutable clinical audit trail (clinical traceability, Phase 2).

Records who classified / tagged / annotated which variant, when, and what changed
(before -> after), in the same transaction as the change itself, into the
append-only ``clinical_audit_events`` table (see docs/clinical-traceability.md).
Small-variant and structural-variant (SV / CNV) review saves and report sign-out
write here, all on the one per-family hash chain.

This is the clinical *action* log; ``audit_log_pg`` remains the HTTP *access* log.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import cnv_acmg_points
from .family_metadata_context import build_family_metadata_context
from .hash_chain import ChainVerification, chain_row_hash, verify_chain
from .access_control import CurrentUser


def _clinical_chain_payload(row: dict[str, Any]) -> dict[str, Any]:
    """The immutable content of a clinical_audit_event that the hash chain binds.

    EXCLUDES the FK columns the append-only trigger lets the SET-NULL cascade mutate
    (``actor_id`` / ``family_id``) and binds the denormalised identity (``actor`` /
    ``family_identifier``) instead, so a legitimate account/family deletion does not
    break the chain. ``created_at`` is rendered identically at write and verify time.
    """
    created_at = row["created_at"]
    return {
        "id": str(row["id"]),
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
        "family_identifier": row.get("family_identifier"),
        "variant_id": row.get("variant_id"),
        "actor": row.get("actor"),
        "action": row.get("action"),
        "summary": row.get("summary"),
        "before": row.get("before"),
        "after": row.get("after"),
        "metadata": row.get("metadata"),
    }

_ACMG_CLASS_LABELS: dict[str, str] = {
    "acmg_class_5": "Pathogenic (class 5)",
    "acmg_class_4": "Likely pathogenic (class 4)",
    "acmg_class_3": "VUS (class 3)",
    "acmg_class_2": "Likely benign (class 2)",
    "acmg_class_1": "Benign (class 1)",
}


def _acmg_label(value: Any) -> str:
    return _ACMG_CLASS_LABELS.get(value, value) if value else "unclassified"


def _criteria_codes(acmg: Any) -> list[str]:
    if not isinstance(acmg, dict):
        return []
    codes = [
        str(criterion.get("code"))
        for criterion in acmg.get("criteria", [])
        if isinstance(criterion, dict) and criterion.get("accepted") and criterion.get("code")
    ]
    return sorted(codes)


def _json_record(value: Any) -> dict[str, Any] | None:
    """A stored JSON record as a dict (a JSON string is parsed), or None."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


def _audit_total(value: Any) -> int | float | None:
    """A point total as the chained payload records it: an integer stays one."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return _audit_points(value)


def _criteria_detail(record: dict[str, Any] | None, value_key: str) -> list[dict[str, Any]]:
    """Every criterion the stored record holds, as stored, in a fixed order.

    ``value_key`` is the criterion's weight: ``strength`` (ACMG) or ``points`` (ClinGen
    CNV). With whether it was accepted, its evidence text and whether it was an automatic
    suggestion, this is the whole record: any change to it is a change to the
    classification, so the audit sees it.
    """
    raw = (record or {}).get("criteria")
    entries: list[dict[str, Any]] = []
    for criterion in raw if isinstance(raw, list) else []:
        if not isinstance(criterion, dict) or not criterion.get("code"):
            continue
        weight = criterion.get(value_key)
        entries.append(
            {
                "code": str(criterion["code"]),
                value_key: _audit_points(weight) if value_key == "points" else (
                    str(weight) if weight is not None else None
                ),
                "accepted": bool(criterion.get("accepted")),
                "evidence": str(criterion.get("evidence") or "").strip() or None,
                "auto_suggested": bool(criterion.get("auto_suggested")),
            }
        )
    return sorted(
        entries,
        key=lambda item: (
            item["code"],
            str(item[value_key]),
            item["accepted"],
            item["evidence"] or "",
            item["auto_suggested"],
        ),
    )


def _acmg_state(review: dict[str, Any]) -> dict[str, Any]:
    """What a small-variant ``classification`` event records: the class, the accepted
    codes, the point total, the VUS tier and every stored criterion."""
    record = _json_record(review.get("acmg"))
    return {
        "acmg_class": review.get("acmg_class"),
        "criteria": _criteria_codes(record),
        "point_total": _audit_total((record or {}).get("point_total")),
        "vus_tier": (record or {}).get("vus_tier") or None,
        "criteria_detail": _criteria_detail(record, "strength"),
    }


_MAX_SUMMARY_CHANGES = 5


def _format_weight(value: Any, value_key: str) -> str:
    if value is None:
        return "none"
    if value_key == "points" and isinstance(value, (int, float)):
        return f"{value:g}"
    return str(value)


def _criteria_changes(
    before: list[dict[str, Any]], after: list[dict[str, Any]], value_key: str
) -> list[str]:
    """One phrase per criterion that changed, for the event's one-line summary."""
    old = {item["code"]: item for item in before}
    new = {item["code"]: item for item in after}
    phrases: list[str] = []
    for code in sorted(old.keys() | new.keys()):
        was, now = old.get(code), new.get(code)
        if was == now:
            continue
        if now is None:
            phrases.append(f"{code} removed")
            continue
        if was is None:
            state = "accepted" if now["accepted"] else "suggested" if now["auto_suggested"] else "added"
            phrases.append(f"{code} {state}")
            continue
        bits: list[str] = []
        if was["accepted"] != now["accepted"]:
            bits.append("accepted" if now["accepted"] else "rejected")
        if was[value_key] != now[value_key]:
            change = f"{_format_weight(was[value_key], value_key)} → {_format_weight(now[value_key], value_key)}"
            bits.append(f"{change} points" if value_key == "points" else change)
        if was["evidence"] != now["evidence"]:
            bits.append(
                "evidence added" if not was["evidence"]
                else "evidence removed" if not now["evidence"]
                else "evidence edited"
            )
        if was["auto_suggested"] != now["auto_suggested"]:
            bits.append("now suggested" if now["auto_suggested"] else "no longer suggested")
        phrases.append(f"{code} {', '.join(bits)}")
    return phrases


def _criteria_update_summary(
    headline: str,
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    detail_key: str,
    value_key: str,
    total_key: str,
    kind_key: str | None = None,
    tier_key: str | None = None,
) -> str:
    """``headline: what changed`` for a save that kept the class but changed its basis."""
    if kind_key and before[kind_key] != after[kind_key]:
        # Another kind is scored on another catalogue: every criterion differs.
        phrases = [f"kind {before[kind_key] or 'none'} → {after[kind_key] or 'none'}"]
    else:
        phrases = _criteria_changes(before[detail_key], after[detail_key], value_key)
        if len(phrases) > _MAX_SUMMARY_CHANGES:
            hidden = len(phrases) - _MAX_SUMMARY_CHANGES
            phrases = phrases[:_MAX_SUMMARY_CHANGES] + [f"and {hidden} more"]
    if before[total_key] != after[total_key]:
        phrases.append(
            f"total {_format_weight(before[total_key], 'points')} → "
            f"{_format_weight(after[total_key], 'points')}"
        )
    if tier_key and before[tier_key] != after[tier_key]:
        phrases.append(f"VUS tier {before[tier_key] or 'none'} → {after[tier_key] or 'none'}")
    return f"{headline}: {'; '.join(phrases)}" if phrases else headline


def diff_review_changes(
    existing: dict[str, Any] | None, new_state: dict[str, Any]
) -> list[dict[str, Any]]:
    """Compute the clinical audit events implied by a review save (before -> after).

    The ``classification`` event covers the whole stored ACMG record, not only the class
    and the accepted codes: a criterion added or removed, a strength or evidence text
    changed, a suggestion accepted or rejected. An unchanged re-save writes nothing.
    """
    prior = existing or {}
    events: list[dict[str, Any]] = []

    before = _acmg_state(prior)
    after = _acmg_state(new_state)
    if before != after:
        old_class, new_class = before["acmg_class"], after["acmg_class"]
        if old_class != new_class:
            summary = f"Classification {_acmg_label(old_class)} → {_acmg_label(new_class)}"
        else:
            summary = _criteria_update_summary(
                f"ACMG criteria updated ({_acmg_label(new_class)})",
                before,
                after,
                detail_key="criteria_detail",
                value_key="strength",
                total_key="point_total",
                tier_key="vus_tier",
            )
        events.append({"action": "classification", "summary": summary, "before": before, "after": after})

    events.extend(_tags_and_note_events(prior, new_state))
    return events


def _tags_and_note_events(prior: dict[str, Any], new_state: dict[str, Any]) -> list[dict[str, Any]]:
    """The ``tags`` and ``note`` events of a review save; shared by every variant type."""
    events: list[dict[str, Any]] = []
    old_tags = sorted(prior.get("tags") or [])
    new_tags = sorted(new_state.get("tags") or [])
    if old_tags != new_tags:
        added = [tag for tag in new_tags if tag not in old_tags]
        removed = [tag for tag in old_tags if tag not in new_tags]
        parts = []
        if added:
            parts.append("added " + ", ".join(added))
        if removed:
            parts.append("removed " + ", ".join(removed))
        events.append(
            {
                "action": "tags",
                "summary": "Tags " + "; ".join(parts) if parts else "Tags updated",
                "before": {"tags": old_tags},
                "after": {"tags": new_tags},
            }
        )

    old_note = (prior.get("note") or "").strip()
    new_note = (new_state.get("note") or "").strip()
    if old_note != new_note:
        if not new_note:
            note_summary = "Note removed"
        elif not old_note:
            note_summary = "Note added"
        else:
            note_summary = "Note updated"
        events.append(
            {
                "action": "note",
                "summary": note_summary,
                "before": {"note": old_note or None},
                "after": {"note": new_note or None},
            }
        )

    return events


# What a structural-variant review's ``classification`` event records: the reviewer's
# classification and, for a CNV, the ClinGen 2019 scoring behind it. Both are frozen
# into the signed report (``reported_structural_variants``), so both are audited.
_STRUCTURAL_CLASSIFICATION_FIELDS = (
    "classification",
    "cnv_class",
    "cnv_kind",
    "cnv_point_total",
    "cnv_criteria",
    "cnv_criteria_detail",
)


def _cnv_label(value: Any) -> str:
    return cnv_acmg_points.CLASS_LABELS.get(value, value) if value else "unclassified"


def _audit_points(value: Any) -> float | None:
    """A CNV point value as the chained payload records it: finite, and never -0.0.

    The payload is hashed when it is written and again after the JSONB round-trip when
    the chain is verified. Postgres numerics have no negative zero, so a -0.0 would read
    back as 0 and break the chain — and one is easy to reach: 0.30 + 0.15 - 0.45 sums to
    a hair below zero, which rounds to -0.0. Adding 0.0 turns -0.0 into 0.0 and leaves
    every other value exactly as it is.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        points = float(value)
    except (TypeError, ValueError):
        return None
    return points + 0.0 if math.isfinite(points) else None


def _accepted_cnv_criteria(cnv: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The accepted CNV criteria with the points given, in a fixed order: what the class
    rests on. The whole stored record is ``cnv_criteria_detail``."""
    raw = (cnv or {}).get("criteria")
    accepted = [
        {"code": str(criterion["code"]), "points": _audit_points(criterion.get("points"))}
        for criterion in (raw if isinstance(raw, list) else [])
        if isinstance(criterion, dict) and criterion.get("accepted") and criterion.get("code")
    ]
    return sorted(
        accepted,
        key=lambda item: (item["code"], -math.inf if item["points"] is None else item["points"]),
    )


def structural_review_state(review: dict[str, Any] | None) -> dict[str, Any]:
    """The audited content of a structural-variant (SV / CNV) review.

    ``review`` is a stored ``structural_variant_reviews`` row or the values a save writes
    (``classification``, ``cnv_acmg``, ``cnv_point_total``, ``cnv_class``, ``tags``,
    ``note``); ``None`` is no review.
    """
    prior = review or {}
    cnv = _json_record(prior.get("cnv_acmg"))
    return {
        "classification": str(prior.get("classification") or "").strip() or None,
        "cnv_class": prior.get("cnv_class") or None,
        "cnv_kind": (cnv or {}).get("kind") or None,
        "cnv_point_total": _audit_points(prior.get("cnv_point_total")),
        "cnv_criteria": _accepted_cnv_criteria(cnv),
        "cnv_criteria_detail": _criteria_detail(cnv, "points"),
        "tags": sorted(prior.get("tags") or []),
        "note": str(prior.get("note") or "").strip() or None,
    }


def _structural_classification_summary(before: dict[str, Any], after: dict[str, Any]) -> str:
    parts: list[str] = []
    if before["classification"] != after["classification"]:
        parts.append(
            f"Classification {before['classification'] or 'unclassified'} → "
            f"{after['classification'] or 'unclassified'}"
        )
    if before["cnv_class"] != after["cnv_class"]:
        parts.append(
            f"CNV classification {_cnv_label(before['cnv_class'])} → "
            f"{_cnv_label(after['cnv_class'])}"
        )
    elif any(
        before[key] != after[key]
        for key in ("cnv_kind", "cnv_point_total", "cnv_criteria", "cnv_criteria_detail")
    ):
        parts.append(
            _criteria_update_summary(
                f"CNV criteria updated ({_cnv_label(after['cnv_class'])})",
                before,
                after,
                detail_key="cnv_criteria_detail",
                value_key="points",
                total_key="cnv_point_total",
                kind_key="cnv_kind",
            )
        )
    return "; ".join(parts)


def diff_structural_review_changes(
    existing: dict[str, Any] | None, new_state: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """The clinical audit events a structural-variant (SV / CNV) review save implies.

    As for small variants: one ``classification`` event when the reviewer's
    classification or the CNV (ClinGen) scoring changes, one ``tags`` and one ``note``
    event, each before -> after. ``new_state`` None is a review that was deleted.
    """
    before = structural_review_state(existing)
    after = structural_review_state(new_state)
    events: list[dict[str, Any]] = []
    if any(before[key] != after[key] for key in _STRUCTURAL_CLASSIFICATION_FIELDS):
        events.append(
            {
                "action": "classification",
                "summary": _structural_classification_summary(before, after),
                "before": {key: before[key] for key in _STRUCTURAL_CLASSIFICATION_FIELDS},
                "after": {key: after[key] for key in _STRUCTURAL_CLASSIFICATION_FIELDS},
            }
        )
    events.extend(_tags_and_note_events(before, after))
    return events


async def record_clinical_event(
    session: AsyncSession,
    *,
    family_uuid: str | None,
    family_identifier: str | None,
    variant_id: str | None,
    actor: str,
    actor_id: str | None,
    action: str,
    summary: str | None,
    before: Any = None,
    after: Any = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Append one immutable clinical audit event (no commit — the caller owns the tx).

    Hash-chained per family: under a per-family advisory lock we read the chain head,
    compute this row's ``row_hash = H(prev_row_hash ‖ canonical(content))`` and store
    it, so later deletion / reordering / editing of any event becomes detectable.
    """
    # Partition the chain on the IMMUTABLE family_identifier, not the mutable family_id
    # (an ON DELETE SET NULL cascade nulls family_id, which would otherwise fold a
    # deleted family's chain into the orphan partition and break verification).
    family_key = family_identifier or "orphan"
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"cae:{family_key}"}
    )
    head = (
        await session.execute(
            text(
                "SELECT created_at, row_hash FROM clinical_audit_events "
                "WHERE family_identifier IS NOT DISTINCT FROM :family_identifier "
                "ORDER BY created_at DESC, id DESC LIMIT 1"
            ),
            {"family_identifier": family_identifier},
        )
    ).mappings().first()

    now = datetime.now(timezone.utc)
    # Strictly-monotonic per family so the chain order is unambiguous even for events
    # written in the same microsecond (the advisory lock serialises writers per family).
    if head is not None and head["created_at"] is not None and now <= head["created_at"]:
        created_at = head["created_at"] + timedelta(microseconds=1)
    else:
        created_at = now
    prev_hash = head["row_hash"] if head is not None else None

    event_id = uuid4()
    meta = metadata or {}
    row_hash = chain_row_hash(
        prev_hash,
        _clinical_chain_payload(
            {
                "id": event_id,
                "created_at": created_at,
                "family_identifier": family_identifier,
                "variant_id": variant_id,
                "actor": actor,
                "action": action,
                "summary": summary,
                "before": before,
                "after": after,
                "metadata": meta,
            }
        ),
    )

    await session.execute(
        text(
            """
            INSERT INTO clinical_audit_events
                (id, created_at, family_id, family_identifier, variant_id, actor_id,
                 actor, action, summary, before, after, metadata, row_hash, prev_hash)
            VALUES
                (CAST(:id AS uuid), :created_at, CAST(:family_id AS uuid),
                 :family_identifier, :variant_id, CAST(:actor_id AS uuid), :actor,
                 :action, :summary, CAST(:before AS jsonb), CAST(:after AS jsonb),
                 CAST(:metadata AS jsonb), :row_hash, :prev_hash)
            """
        ),
        {
            "id": str(event_id),
            "created_at": created_at,
            "family_id": family_uuid,
            "family_identifier": family_identifier,
            "variant_id": variant_id,
            "actor_id": actor_id,
            "actor": actor,
            "action": action,
            "summary": summary,
            "before": json.dumps(before) if before is not None else None,
            "after": json.dumps(after) if after is not None else None,
            "metadata": json.dumps(meta),
            "row_hash": row_hash,
            "prev_hash": prev_hash,
        },
    )


async def verify_clinical_audit_chain(
    session: AsyncSession, family_identifier: str | None
) -> ChainVerification:
    """Re-walk a family's clinical-audit hash chain and report whether it is intact.

    Partitioned on the immutable ``family_identifier`` (not the mutable ``family_id``,
    which an ``ON DELETE SET NULL`` cascade nulls), so a family's chain stays walkable
    and intact after the family row itself is deleted.
    """
    rows = (
        await session.execute(
            text(
                "SELECT id::text AS id, created_at, family_identifier, variant_id, "
                "actor, action, summary, before, after, metadata, row_hash, prev_hash "
                "FROM clinical_audit_events "
                "WHERE family_identifier IS NOT DISTINCT FROM :family_identifier "
                "AND row_hash IS NOT NULL "
                "ORDER BY created_at ASC, id ASC"
            ),
            {"family_identifier": family_identifier},
        )
    ).mappings().all()
    return verify_chain([dict(row) for row in rows], _clinical_chain_payload)


async def record_review_changes(
    session: AsyncSession,
    *,
    family_uuid: str | None,
    family_identifier: str | None,
    variant_id: str,
    user: CurrentUser,
    existing: dict[str, Any] | None,
    new_state: dict[str, Any],
) -> None:
    """Record the clinical audit events for a small-variant review save."""
    await _record_review_events(
        session,
        diff_review_changes(existing, new_state),
        family_uuid=family_uuid,
        family_identifier=family_identifier,
        variant_id=variant_id,
        user=user,
    )


async def record_structural_review_changes(
    session: AsyncSession,
    *,
    family_uuid: str | None,
    family_identifier: str | None,
    variant_id: str,
    user: CurrentUser,
    existing: dict[str, Any] | None,
    new_state: dict[str, Any] | None,
) -> None:
    """Record the clinical audit events for a structural-variant (SV / CNV) review save.

    On the family's one chain, like the small-variant events; each is marked
    ``metadata.modality = "sv"`` so an SV id is never read as a small-variant id.
    ``new_state`` None records the deletion of the review.
    """
    await _record_review_events(
        session,
        diff_structural_review_changes(existing, new_state),
        family_uuid=family_uuid,
        family_identifier=family_identifier,
        variant_id=variant_id,
        user=user,
        metadata={"modality": "sv"},
    )


async def _record_review_events(
    session: AsyncSession,
    events: list[dict[str, Any]],
    *,
    family_uuid: str | None,
    family_identifier: str | None,
    variant_id: str,
    user: CurrentUser,
    metadata: dict[str, Any] | None = None,
) -> None:
    for event in events:
        await record_clinical_event(
            session,
            family_uuid=family_uuid,
            family_identifier=family_identifier,
            variant_id=variant_id,
            actor=getattr(user, "username", None) or getattr(user, "email", "") or "unknown",
            actor_id=getattr(user, "id", None),
            action=event["action"],
            summary=event["summary"],
            before=event["before"],
            after=event["after"],
            metadata=dict(metadata) if metadata else None,
        )


async def list_clinical_audit(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    limit: int = 200,
    project_id: str | None = None,
) -> dict[str, Any]:
    context = await build_family_metadata_context(
        session, family_identifier=family_id, user=user, project_id=project_id
    )
    rows = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, created_at, variant_id, actor,
                       action, summary, before, after
                FROM clinical_audit_events
                WHERE family_id = CAST(:family_uuid AS uuid)
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            {"family_uuid": context.family_uuid, "limit": limit},
        )
    ).mappings().all()
    return {
        "family_id": context.family_id,
        "events": [dict(row) for row in rows],
    }
