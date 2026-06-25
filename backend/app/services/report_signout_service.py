"""Case sign-out — frozen, versioned, hashed report snapshot (clinical traceability,
Phase 3; see docs/clinical-traceability.md).

Signing out a case freezes the reported result to exactly what produced it: the
annotation/reference versions (the manifest), the reported variant list, each
classification with its frozen evidence snapshot, and the evidence-drift state at the
moment of sign-out. The snapshot is content-hashed (SHA-256) and written append-only
into ``report_signouts`` as a new version; the sign-out is recorded in the immutable
clinical audit trail.

Sign-out is gated on evidence drift: if any classification's backing annotation has
changed since it was made, the caller must explicitly acknowledge the drift.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .annotation_manifest_service import get_family_annotation_manifest
from .classification_drift_service import evaluate_classification_drift
from .clinical_audit_service import record_clinical_event
from .family_metadata_context import build_family_metadata_context
from .metadata_service import CurrentUser

_REPORT_TAG = "report"


def _canonical_hash(snapshot: dict[str, Any]) -> str:
    """SHA-256 over a canonical (sorted-key) JSON encoding — stable + tamper-evident."""
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def _reported_reviews(session: AsyncSession, family_uuid: str) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                """
                SELECT variant_id, acmg_class, acmg, tags, note, acmg_evidence_snapshot
                FROM small_variant_reviews
                WHERE family_id = CAST(:family_uuid AS uuid)
                  AND tags @> :report_tag
                ORDER BY variant_id
                """
            ),
            {"family_uuid": family_uuid, "report_tag": json.dumps([_REPORT_TAG])},
        )
    ).mappings().all()
    reported: list[dict[str, Any]] = []
    for row in rows:
        reported.append(
            {
                "variant_id": row["variant_id"],
                "acmg_class": row["acmg_class"],
                "acmg": row["acmg"],
                "tags": sorted(row["tags"] or []),
                "note": row["note"],
                "evidence_snapshot": row["acmg_evidence_snapshot"],
            }
        )
    return reported


async def build_report_snapshot(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Assemble (but do not persist) the frozen report snapshot for a family."""
    context = await build_family_metadata_context(
        session, family_identifier=family_id, user=user, project_id=project_id
    )
    manifest = await get_family_annotation_manifest(
        session, family_id=family_id, user=user, project_id=project_id
    )
    drift = await evaluate_classification_drift(
        session, family_id=family_id, user=user, project_id=project_id
    )
    reported = await _reported_reviews(session, context.family_uuid)
    return {
        "family_id": context.family_id,
        "assembly": manifest.get("assembly"),
        "modules": manifest.get("modules", []),
        "drift": {
            "checked": drift["checked"],
            "drifted_count": drift["drifted_count"],
            "drifted": drift["drifted"],
        },
        "reported_variants": reported,
    }


async def _next_version(session: AsyncSession, family_uuid: str) -> int:
    result = await session.execute(
        text(
            "SELECT COALESCE(MAX(version), 0) FROM report_signouts "
            "WHERE family_id = CAST(:family_uuid AS uuid)"
        ),
        {"family_uuid": family_uuid},
    )
    return int(result.scalar_one() or 0) + 1


def _serialize_signout(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": row["version"],
        "signed_out_by": row["signed_out_by"],
        "signed_out_at": row["signed_out_at"],
        "content_hash": row["content_hash"],
        "snapshot": row.get("snapshot"),
    }


async def sign_out_report(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    acknowledge_drift: bool = False,
    project_id: str | None = None,
) -> dict[str, Any]:
    context = await build_family_metadata_context(
        session, family_identifier=family_id, user=user, project_id=project_id
    )
    snapshot_body = await build_report_snapshot(
        session, family_id=family_id, user=user, project_id=project_id
    )

    drifted_count = snapshot_body["drift"]["drifted_count"]
    if drifted_count and not acknowledge_drift:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{drifted_count} classification(s) have evidence changes since they were "
                "made. Re-review, or acknowledge the drift to sign out anyway."
            ),
        )

    now = datetime.now(timezone.utc)
    version = await _next_version(session, context.family_uuid)
    actor = getattr(user, "username", None) or getattr(user, "email", "") or "unknown"

    snapshot = {
        **snapshot_body,
        "version": version,
        "generated_at": now.isoformat(),
        "signed_out_by": actor,
        "acknowledged_drift": bool(drifted_count) and acknowledge_drift,
    }
    content_hash = _canonical_hash(snapshot)

    await session.execute(
        text(
            """
            INSERT INTO report_signouts
                (family_id, family_identifier, version, signed_out_by, signed_out_by_id,
                 signed_out_at, content_hash, snapshot)
            VALUES
                (CAST(:family_id AS uuid), :family_identifier, :version, :signed_out_by,
                 CAST(:signed_out_by_id AS uuid), :signed_out_at, :content_hash,
                 CAST(:snapshot AS jsonb))
            """
        ),
        {
            "family_id": context.family_uuid,
            "family_identifier": context.family_id,
            "version": version,
            "signed_out_by": actor,
            "signed_out_by_id": getattr(user, "id", None),
            "signed_out_at": now,
            "content_hash": content_hash,
            "snapshot": json.dumps(snapshot, default=str),
        },
    )
    await record_clinical_event(
        session,
        family_uuid=context.family_uuid,
        family_identifier=context.family_id,
        variant_id=None,
        actor=actor,
        actor_id=getattr(user, "id", None),
        action="sign_out",
        summary=(
            f"Report signed out (v{version}) — {len(snapshot_body['reported_variants'])} "
            f"reported variant(s){', drift acknowledged' if snapshot['acknowledged_drift'] else ''}"
        ),
        after={
            "version": version,
            "content_hash": content_hash,
            "reported_count": len(snapshot_body["reported_variants"]),
            "drifted_count": drifted_count,
        },
    )
    await session.commit()
    return {
        "version": version,
        "signed_out_by": actor,
        "signed_out_at": now,
        "content_hash": content_hash,
        "snapshot": snapshot,
    }


async def list_report_signouts(
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
                SELECT version, signed_out_by, signed_out_at, content_hash
                FROM report_signouts
                WHERE family_id = CAST(:family_uuid AS uuid)
                ORDER BY version DESC
                """
            ),
            {"family_uuid": context.family_uuid},
        )
    ).mappings().all()
    signouts = [_serialize_signout(dict(row)) for row in rows]
    return {
        "family_id": context.family_id,
        "latest": signouts[0] if signouts else None,
        "signouts": signouts,
    }


async def get_report_signout(
    session: AsyncSession,
    *,
    family_id: str,
    version: int,
    user: CurrentUser,
    project_id: str | None = None,
) -> dict[str, Any]:
    context = await build_family_metadata_context(
        session, family_identifier=family_id, user=user, project_id=project_id
    )
    row = (
        await session.execute(
            text(
                """
                SELECT version, signed_out_by, signed_out_at, content_hash, snapshot
                FROM report_signouts
                WHERE family_id = CAST(:family_uuid AS uuid) AND version = :version
                """
            ),
            {"family_uuid": context.family_uuid, "version": version},
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Sign-out version not found")
    return _serialize_signout(dict(row))
