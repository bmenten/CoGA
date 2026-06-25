"""Classification evidence drift (clinical traceability, Phase 1).

For each ACMG-classified small variant, compare the evidence frozen at
classification time (``small_variant_reviews.acmg_evidence_snapshot``) against the
variant's *current* annotation. When the annotation set changed since the
classification was made — a new ClinVar/gnomAD/annotation release — the
classification may be stale, so surface it as "evidence changed since you
classified this" (see docs/clinical-traceability.md).

The annotation-set hash is the authoritative drift key (it changes whenever any
annotation changes); the ClinVar significance is reported as the human-readable
specific when it is what moved.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .clickhouse_small_variants import get_small_variant_family_record
from .family_metadata_context import build_family_metadata_context
from .metadata_service import CurrentUser
from .small_variant_review_pg import build_evidence_snapshot


def _current_evidence(record: Any) -> dict[str, Any]:
    # Reuse the snapshot builder so "current" is extracted exactly like "frozen".
    # captured_at is irrelevant here; pass a sentinel that we never read.
    from datetime import datetime, timezone

    return build_evidence_snapshot(record, datetime.now(timezone.utc))


def _diff(snapshot: dict[str, Any], current: dict[str, Any] | None) -> dict[str, Any]:
    """Compare a frozen snapshot against current evidence.

    status is one of: ``current`` (unchanged), ``drifted`` (annotation changed),
    ``variant_missing`` (the variant is no longer present in the dataset).
    """
    snap_hash = snapshot.get("annotation_set_hash")
    snap_clinvar = snapshot.get("clinvar")
    snap_version = snapshot.get("annotation_version")

    if current is None:
        return {
            "status": "variant_missing",
            "annotation_version_from": snap_version,
            "annotation_version_to": None,
            "clinvar_from": snap_clinvar,
            "clinvar_to": None,
        }

    cur_hash = current.get("annotation_set_hash")
    cur_clinvar = current.get("clinvar")
    cur_version = current.get("annotation_version")

    # Only call it drift when both hashes are known and differ — a missing hash on
    # either side is "unknown", not "changed", to avoid false alarms.
    changed = bool(snap_hash) and bool(cur_hash) and snap_hash != cur_hash
    return {
        "status": "drifted" if changed else "current",
        "annotation_version_from": snap_version,
        "annotation_version_to": cur_version,
        "clinvar_from": snap_clinvar,
        "clinvar_to": cur_clinvar,
    }


async def evaluate_classification_drift(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    project_id: str | None = None,
) -> dict[str, Any]:
    context = await build_family_metadata_context(
        session, family_identifier=family_id, user=user, project_id=project_id
    )
    rows = (
        await session.execute(
            text(
                """
                SELECT variant_id, acmg_class, acmg_evidence_snapshot,
                       updated_by, updated_at
                FROM small_variant_reviews
                WHERE family_id = CAST(:family_uuid AS uuid)
                  AND acmg_evidence_snapshot IS NOT NULL
                ORDER BY updated_at DESC
                """
            ),
            {"family_uuid": context.family_uuid},
        )
    ).mappings().all()

    drifted: list[dict[str, Any]] = []
    checked = 0
    for row in rows:
        snapshot = row["acmg_evidence_snapshot"]
        if not isinstance(snapshot, dict):
            continue
        checked += 1
        current_record = None
        if context.assembly_name:
            current_record = await get_small_variant_family_record(
                assembly_name=context.assembly_name,
                family_guid=context.family_uuid,
                variant_id=row["variant_id"],
            )
        current = _current_evidence(current_record) if current_record is not None else None
        diff = _diff(snapshot, current)
        if diff["status"] == "current":
            continue
        drifted.append(
            {
                "variant_id": row["variant_id"],
                "acmg_class": row["acmg_class"],
                "classified_by": row["updated_by"],
                "classified_at": row["updated_at"],
                **diff,
            }
        )

    return {
        "family_id": context.family_id,
        "checked": checked,
        "drifted_count": len(drifted),
        "drifted": drifted,
    }
