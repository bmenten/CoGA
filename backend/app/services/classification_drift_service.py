"""Classification evidence drift (clinical traceability, Phase 1).

For each ACMG-classified small variant, compare the evidence frozen at
classification time (``small_variant_reviews.acmg_evidence_snapshot``) against the
variant's *current* annotation. When the annotation set changed since the
classification was made — a new ClinVar/gnomAD/annotation release — the
classification may be stale, so surface it as "evidence changed since you
classified this" (see docs/clinical-traceability.md).

The annotation-set hash is the authoritative drift key (it changes whenever any
annotation changes); the ClinVar significance is reported as the human-readable
specific when it is what moved, and so is each population frequency and in-silico score
the snapshot froze (CLIN-7): ``changed`` names what moved, ``evidence_from`` and
``evidence_to`` hold the values. Only the fields a snapshot froze are compared, so a
snapshot frozen before the frequencies were (it holds none) is compared by its hash and
ClinVar significance alone.

Structural-variant / CNV classifications are checked the same way, under ``structural``:
each CNV (ClinGen) scoring's frozen ``cnv_evidence_snapshot`` against the row of the
caller it was frozen from as it is now (``structural_variant_evidence``), the evidence
hash being the drift key and the inputs that moved (genes, pLI, inheritance, type, locus)
the specifics.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .clickhouse_small_variants import get_small_variant_family_record
from .family_metadata_context import FamilyMetadataContext, build_family_metadata_context
from .access_control import CurrentUser
from .hash_chain import canonical_json
from .small_variant_review_acmg import (
    EVIDENCE_FREQUENCY_FIELDS,
    EVIDENCE_IN_SILICO_FIELDS,
    build_evidence_snapshot,
)
from .structural_variant_evidence import (
    diff_structural_evidence,
    fetch_structural_variant_records,
    frozen_evidence_source,
)

# The snapshot's groups of values, each compared field by field.
_EVIDENCE_GROUPS = ("frequencies", "in_silico")
# The order a drift names the frozen values in: the transcript they are read from, the
# frequencies, then the in-silico scores (``build_evidence_snapshot``).
_EVIDENCE_ORDER = {
    field: index
    for index, field in enumerate(
        ("transcript_id", *EVIDENCE_FREQUENCY_FIELDS, *EVIDENCE_IN_SILICO_FIELDS)
    )
}


def _current_evidence(record: Any) -> dict[str, Any]:
    # Reuse the snapshot builder so "current" is extracted exactly like "frozen".
    # captured_at is irrelevant here; pass a sentinel that we never read.
    from datetime import datetime, timezone

    return build_evidence_snapshot(record, datetime.now(timezone.utc))


def _evidence_values(snapshot: Mapping[str, Any]) -> tuple[dict[str, Any] | None, bool]:
    """The transcript, frequencies and in-silico scores a snapshot froze, as one mapping of
    field to value, and whether all of it can be read.

    None when the snapshot froze none of them, as one frozen before CLIN-7 did. A group
    that is there but is not a mapping cannot be read: its values cannot be shown
    unchanged.
    """
    values: dict[str, Any] = {}
    readable = True
    if "transcript_id" in snapshot:
        values["transcript_id"] = snapshot["transcript_id"]
    for group in _EVIDENCE_GROUPS:
        if group not in snapshot:
            continue
        frozen = snapshot[group]
        if isinstance(frozen, Mapping):
            values.update({str(field): value for field, value in frozen.items()})
        else:
            readable = False
    return (values or None), readable


def _same(left: Any, right: Any) -> bool:
    """Whether a frozen value and the current one are the same: numbers by value (JSONB
    keeps a number's digits, not whether it was an int or a float), anything else through
    the JSON encoding the stored snapshot went through."""
    if (
        isinstance(left, (int, float))
        and isinstance(right, (int, float))
        and not isinstance(left, bool)
        and not isinstance(right, bool)
    ):
        return float(left) == float(right)
    return canonical_json(json.loads(json.dumps(left, default=str))) == canonical_json(
        json.loads(json.dumps(right, default=str))
    )


def _moved_values(frozen: Mapping[str, Any], current: Mapping[str, Any]) -> list[str]:
    """The frozen values that are not the same now, in the order a reviewer reads them."""
    moved = [field for field, value in frozen.items() if not _same(value, current.get(field))]
    return sorted(moved, key=lambda field: (_EVIDENCE_ORDER.get(field, len(_EVIDENCE_ORDER)), field))


def _diff(snapshot: dict[str, Any], current: dict[str, Any] | None) -> dict[str, Any]:
    """Compare a frozen snapshot against current evidence.

    status is one of: ``current`` (unchanged), ``drifted`` (the annotation set, the ClinVar
    significance or a frozen frequency or in-silico score changed: ``changed`` names
    each, ``annotations`` for the annotation set), ``unknown`` (nothing is seen to have
    moved, but a hash is missing or a frozen group cannot be read, so the match cannot be
    verified) and ``variant_missing`` (the variant is no longer present in the dataset).
    Only what the snapshot froze is compared: a snapshot frozen before the frequencies and
    in-silico scores were holds none, and its ``evidence_from`` is None.
    """
    snap_hash = snapshot.get("annotation_set_hash")
    snap_clinvar = snapshot.get("clinvar")
    snap_version = snapshot.get("annotation_version")
    evidence_from, readable = _evidence_values(snapshot)

    if current is None:
        return {
            "status": "variant_missing",
            "annotation_version_from": snap_version,
            "annotation_version_to": None,
            "clinvar_from": snap_clinvar,
            "clinvar_to": None,
            "changed": [],
            "evidence_from": evidence_from,
            "evidence_to": None,
        }

    cur_hash = current.get("annotation_set_hash")
    cur_clinvar = current.get("clinvar")
    cur_version = current.get("annotation_version")
    evidence_to = _evidence_values(current)[0] or {}

    changed: list[str] = []
    if "clinvar" in snapshot and not _same(snap_clinvar, cur_clinvar):
        changed.append("clinvar")
    if evidence_from:
        changed.extend(_moved_values(evidence_from, evidence_to))
    if snap_hash and cur_hash and snap_hash != cur_hash:
        changed.append("annotations")

    # A signed report must be verifiably bound to the evidence behind each reported
    # classification. When either hash is absent, or a frozen group cannot be read, the
    # binding CANNOT be verified as unchanged, so fail safe to "unknown" — which the
    # sign-out drift gate counts like a real drift (requiring acknowledgement) — rather
    # than silently reporting "current". Previously a present-but-hashless current record
    # read as "current" and cleared the gate, letting a stale classification freeze into a
    # report unchallenged. A value seen to move is a drift whatever else is missing.
    if changed:
        status = "drifted"
    elif not snap_hash or not cur_hash or not readable:
        status = "unknown"
    else:
        status = "current"
    return {
        "status": status,
        "annotation_version_from": snap_version,
        "annotation_version_to": cur_version,
        "clinvar_from": snap_clinvar,
        "clinvar_to": cur_clinvar,
        "changed": changed,
        "evidence_from": evidence_from,
        "evidence_to": evidence_to,
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
    scoring froze an evidence snapshot, compared with the SV as it is now, in the row of
    the caller the snapshot was frozen from.

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
        # The row of the caller the evidence was frozen from: another caller's call of the
        # SV, under the same id, has evidence of its own (TRACE-11). A snapshot that names
        # none cannot be read, so any row only tells it apart from an SV no longer there.
        current_records = await fetch_structural_variant_records(
            context, str(row["variant_id"]), source=frozen_evidence_source(snapshot)
        )
        diff = diff_structural_evidence(snapshot, current_records[0] if current_records else None)
        if diff["status"] == "current":
            continue
        drifted.append(_structural_drift_entry(dict(row), diff))
    drifted.sort(key=lambda item: item["variant_id"])
    return {"checked": checked, "drifted_count": len(drifted), "drifted": drifted}
