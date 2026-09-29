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

Structural-variant / CNV classifications are checked the same way, under ``structural``:
each CNV (ClinGen) scoring's frozen ``cnv_evidence_snapshot`` against the SV as it is now
(``structural_variant_evidence``), the evidence hash being the drift key and the inputs
that moved (genes, pLI, inheritance, type, locus) the specifics.
"""

from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .clickhouse_small_variants import get_small_variant_family_record
from .family_metadata_context import FamilyMetadataContext, build_family_metadata_context
from .access_control import CurrentUser
from .small_variant_review_acmg import build_evidence_snapshot
from .structural_variant_evidence import diff_structural_evidence, fetch_structural_variant_record


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

    # A signed report must be verifiably bound to the evidence behind each reported
    # classification. When either hash is absent the binding CANNOT be verified as
    # unchanged, so fail safe to "unknown" — which the sign-out drift gate counts like a
    # real drift (requiring acknowledgement) — rather than silently reporting "current".
    # Previously a present-but-hashless current record read as "current" and cleared the
    # gate, letting a stale classification freeze into a report unchallenged.
    if not snap_hash or not cur_hash:
        status = "unknown"
    elif snap_hash != cur_hash:
        status = "drifted"
    else:
        status = "current"
    return {
        "status": status,
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
        "structural": await evaluate_structural_classification_drift(session, context),
    }


def _structural_drift_entry(row: dict[str, Any], diff: dict[str, Any]) -> dict[str, Any]:
    """One SV/CNV drift entry: the review's identity, then the comparison."""
    return {
        "variant_id": str(row.get("variant_id") or ""),
        "classification": row.get("classification"),
        "cnv_class": row.get("cnv_class"),
        "classified_by": row.get("updated_by"),
        "classified_at": row.get("updated_at"),
        **diff,
    }


async def evaluate_structural_classification_drift(
    session: AsyncSession, context: FamilyMetadataContext
) -> dict[str, Any]:
    """Evidence drift of the family's SV/CNV classifications: every review whose CNV
    scoring froze an evidence snapshot, compared with the SV as it is now.

    A snapshot that cannot be read counts as ``unknown`` rather than being skipped, so it
    can never clear the sign-out gate unexamined. Entries are sorted by variant id, which
    the signed record needs for a stable content hash.
    """
    rows: Sequence[Any] = (
        await session.execute(
            text(
                """
                SELECT variant_id, classification, cnv_class, cnv_evidence_snapshot,
                       updated_by, updated_at
                FROM structural_variant_reviews
                WHERE family_id = CAST(:family_uuid AS uuid)
                  AND variant_id IS NOT NULL
                  AND cnv_evidence_snapshot IS NOT NULL
                ORDER BY variant_id
                """
            ),
            {"family_uuid": context.family_uuid},
        )
    ).mappings().all()

    drifted: list[dict[str, Any]] = []
    checked = 0
    for row in rows:
        snapshot = row.get("cnv_evidence_snapshot")
        if snapshot is None:
            continue
        checked += 1
        current_record = await fetch_structural_variant_record(context, str(row["variant_id"]))
        diff = diff_structural_evidence(snapshot, current_record)
        if diff["status"] == "current":
            continue
        drifted.append(_structural_drift_entry(dict(row), diff))
    drifted.sort(key=lambda item: item["variant_id"])
    return {"checked": checked, "drifted_count": len(drifted), "drifted": drifted}
