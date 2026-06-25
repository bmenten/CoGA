"""Immutable clinical audit trail (clinical traceability, Phase 2).

Records who classified / tagged / annotated which variant, when, and what changed
(before -> after), in the same transaction as the change itself, into the
append-only ``clinical_audit_events`` table (see docs/clinical-traceability.md).

This is the clinical *action* log; ``audit_log_pg`` remains the HTTP *access* log.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .family_metadata_context import build_family_metadata_context
from .metadata_service import CurrentUser

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


def diff_review_changes(
    existing: dict[str, Any] | None, new_state: dict[str, Any]
) -> list[dict[str, Any]]:
    """Compute the clinical audit events implied by a review save (before -> after)."""
    prior = existing or {}
    events: list[dict[str, Any]] = []

    old_class = prior.get("acmg_class")
    new_class = new_state.get("acmg_class")
    old_codes = _criteria_codes(prior.get("acmg"))
    new_codes = _criteria_codes(new_state.get("acmg"))
    if old_class != new_class or old_codes != new_codes:
        if old_class != new_class:
            summary = f"Classification {_acmg_label(old_class)} → {_acmg_label(new_class)}"
        else:
            summary = f"ACMG criteria updated ({_acmg_label(new_class)})"
        events.append(
            {
                "action": "classification",
                "summary": summary,
                "before": {"acmg_class": old_class, "criteria": old_codes},
                "after": {"acmg_class": new_class, "criteria": new_codes},
            }
        )

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
    """Append one immutable clinical audit event (no commit — the caller owns the tx)."""
    await session.execute(
        text(
            """
            INSERT INTO clinical_audit_events
                (family_id, family_identifier, variant_id, actor_id, actor,
                 action, summary, before, after, metadata)
            VALUES
                (CAST(:family_id AS uuid), :family_identifier, :variant_id,
                 CAST(:actor_id AS uuid), :actor, :action, :summary,
                 CAST(:before AS jsonb), CAST(:after AS jsonb), CAST(:metadata AS jsonb))
            """
        ),
        {
            "family_id": family_uuid,
            "family_identifier": family_identifier,
            "variant_id": variant_id,
            "actor_id": actor_id,
            "actor": actor,
            "action": action,
            "summary": summary,
            "before": json.dumps(before) if before is not None else None,
            "after": json.dumps(after) if after is not None else None,
            "metadata": json.dumps(metadata or {}),
        },
    )


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
    for event in diff_review_changes(existing, new_state):
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
