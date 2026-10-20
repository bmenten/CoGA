"""Whether TRGT's repeat count is the clinical repeat number, per catalogue locus.

TRGT counts the motif across the region its own catalogue gives a locus, which need not be the
stretch the clinical repeat number counts, and so the ranges CoGA classifies against
(STRchive's). Checked in the public HG002 TRGT output and the laboratory's long-read files:

- FMR1: TRGT counts the CGG copies only and leaves out the AGG interruptions, so an allele of
  about 31 triplets reads 29: a count may read 1 to 3 below the clinical repeat number, which
  matters at the 45, 55 and 201 boundaries (a 55-repeat premutation could read 53).
- HTT: TRGT also counts the CAG after the CAA interruption, so a pure 17-repeat allele reads 18,
  one above the usual count (boundaries 27 and 36).
- POLG: TRGT counts four flanking GCT copies with the tract, so the common 10-repeat allele
  reads 14.

QA and the laboratory director check, per catalogue locus, that TRGT's count matches the
clinical repeat number, and record corrections (owner's decision, 2026-10-14). Their record is
``repeat_locus_count_units``: a locus is validated or not, with the reason, who and when, and
every change is a hash-chained clinical audit event on its own chain
(``REPEAT_COUNT_UNITS_AUDIT_CHAIN``). A locus without a record is not validated.

Until a locus is validated, a count within ``COUNT_UNIT_MARGIN`` repeats of a boundary the
classification uses is marked (``count_unit_warning``): a difference of that size between
TRGT's count and the clinical number could change the status CoGA shows. The warning changes
no status; the counts are classified as they are.

Corrections are not applied yet. When QA supplies them (an offset per locus, or for FMR1 the
AGG interruptions TRGT reports beside the CGG copies), they belong in this record and are
applied to the count before it is classified; until then a locus whose count needs a
correction stays not validated, so its counts near a boundary stay marked.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .access_control import CurrentUser
from .clinical_audit_service import record_clinical_event

# How close to a boundary a count is marked, in repeat units, at every locus not validated.
# The largest difference found that reads a count on the less severe side is FMR1's: up to 3
# AGG interruptions left out, so a 55-repeat premutation can read 52 (grey zone). Symmetric,
# because at a locus nobody has checked the difference can go either way (HTT reads one
# above, POLG four above). A wider difference is no reason to widen it for every locus: a
# locus that has one is not validated, and QA records its correction instead.
COUNT_UNIT_MARGIN = 3

# The clinical audit chain of the validation record: not tied to a family. Verify it with
# GET /admin/integrity/verify?table=clinical_audit_events&family_id=system:repeat-loci.
REPEAT_COUNT_UNITS_AUDIT_CHAIN = "system:repeat-loci"

# What is known of TRGT's count at a gene, for the warning's text and the admin list: found in
# the public HG002 TRGT output and the laboratory's long-read files, not validated.
KNOWN_COUNT_UNIT_DIFFERENCES: dict[str, str] = {
    "FMR1": (
        "TRGT counts the CGG copies only, not the AGG interruptions: an allele reads 1 to 3 "
        "below its clinical repeat number (about 31 triplets read 29)."
    ),
    "HTT": (
        "TRGT also counts the CAG after the CAA interruption: an allele reads one above the "
        "usual count (a pure 17-repeat allele reads 18)."
    ),
    "POLG": (
        "TRGT counts four flanking GCT copies with the tract: the common 10-repeat allele "
        "reads 14."
    ),
}

_LOCUS_NOT_FOUND = "Repeat locus not found"
_REASON_REQUIRED = "A reason is required when recording whether a locus's counting units are validated."
# The validation history the admin page lists, newest first.
_CHANGES_LIMIT = 200


def known_count_unit_difference(gene: str | None) -> str | None:
    """What is known of TRGT's count at this gene, or None."""
    return KNOWN_COUNT_UNIT_DIFFERENCES.get(str(gene or "").strip().upper())


def count_unit_warning(
    repeat_count: int | None,
    status_at: Callable[[int], str],
    *,
    margin: int = COUNT_UNIT_MARGIN,
) -> dict[str, Any] | None:
    """The boundaries within ``margin`` repeats of a count, or None when there is none.

    ``status_at`` is the classification the count's status comes from, with the locus's
    thresholds bound, so a boundary is exactly where that classification changes: the benign
    range, the grey zone, the premutation and the pathogenic threshold, wherever the locus's
    classification uses them, and nothing it does not use. For each side, the nearest count
    within the margin that reads another status, and that status. A count is never below 0.
    """
    if repeat_count is None:
        return None
    count = int(repeat_count)
    own = status_at(count)
    boundaries: list[dict[str, Any]] = []
    for direction in (-1, 1):
        for step in range(1, margin + 1):
            other = count + direction * step
            if other < 0:
                break
            status = status_at(other)
            if status != own:
                boundaries.append({"repeat_count": other, "status": status})
                break
    if not boundaries:
        return None
    return {"repeat_count": count, "margin": margin, "boundaries": boundaries}


def _actor(user: CurrentUser) -> str:
    return getattr(user, "username", None) or getattr(user, "email", "") or "unknown"


def _state(locus: dict[str, Any], record: dict[str, Any] | None) -> dict[str, Any]:
    """What an audit event records of a locus's validation (JSON-safe)."""
    return {
        "locus_id": locus["locus_id"],
        "gene": locus.get("gene"),
        "validated": bool(record and record.get("validated")),
        "reason": (record or {}).get("reason"),
    }


def _summary(locus: dict[str, Any], before: dict[str, Any], after: dict[str, Any]) -> str:
    name = f"{locus['locus_id']} ({locus.get('gene')})" if locus.get("gene") else str(locus["locus_id"])
    word = {True: "validated", False: "not validated"}
    if before["validated"] == after["validated"]:
        return f"TRGT's counting units at {name}: {word[after['validated']]}, confirmed"
    return f"TRGT's counting units at {name}: {word[before['validated']]} → {word[after['validated']]}"


async def set_repeat_locus_count_units(
    session: AsyncSession,
    *,
    locus_id: str,
    validated: bool,
    reason: str,
    user: CurrentUser,
) -> dict[str, Any]:
    """Record whether TRGT's counting units at a catalogue locus are validated, and audit it.

    The reason is required: what the record cannot otherwise say is which check supports it
    (the validation report, the TRGT catalogue and version it covered). Every save is a
    clinical audit event, a confirmation of the same state too, since a new reason (a check
    against a new TRGT catalogue) is itself a deliberate act. A locus the catalogue lacks is
    answered 404, and nothing is written.
    """
    reason = (reason or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail=_REASON_REQUIRED)
    locus_row = (
        await session.execute(
            text(
                """
                SELECT locus_id, gene
                FROM repeat_loci
                WHERE locus_id = :locus_id
                FOR UPDATE
                """
            ),
            {"locus_id": locus_id},
        )
    ).mappings().first()
    if locus_row is None:
        raise HTTPException(status_code=404, detail=_LOCUS_NOT_FOUND)
    locus: dict[str, Any] = dict(locus_row)
    previous_row = (
        await session.execute(
            text(
                """
                SELECT validated, reason
                FROM repeat_locus_count_units
                WHERE locus_id = :locus_id
                """
            ),
            {"locus_id": locus["locus_id"]},
        )
    ).mappings().first()
    previous: dict[str, Any] | None = dict(previous_row) if previous_row is not None else None
    record_row = (
        await session.execute(
            text(
                """
                INSERT INTO repeat_locus_count_units (
                    locus_id, validated, reason, updated_by, updated_by_name, updated_at
                )
                VALUES (
                    :locus_id, :validated, :reason,
                    CAST(NULLIF(:user_id, '') AS uuid), :user_name, timezone('utc', now())
                )
                ON CONFLICT (locus_id) DO UPDATE SET
                    validated = EXCLUDED.validated,
                    reason = EXCLUDED.reason,
                    updated_by = EXCLUDED.updated_by,
                    updated_by_name = EXCLUDED.updated_by_name,
                    updated_at = EXCLUDED.updated_at
                RETURNING locus_id, validated, reason, updated_by_name, updated_at
                """
            ),
            {
                "locus_id": locus["locus_id"],
                "validated": bool(validated),
                "reason": reason,
                "user_id": getattr(user, "id", None) or "",
                "user_name": _actor(user),
            },
        )
    ).mappings().one()
    record: dict[str, Any] = dict(record_row)
    before = _state(locus, previous)
    after = _state(locus, record)
    await record_clinical_event(
        session,
        family_uuid=None,
        family_identifier=REPEAT_COUNT_UNITS_AUDIT_CHAIN,
        variant_id=None,
        actor=_actor(user),
        actor_id=getattr(user, "id", None),
        action="repeat_count_units_validated" if after["validated"] else "repeat_count_units_not_validated",
        summary=_summary(locus, before, after),
        before=before,
        after=after,
        metadata={"locus_id": locus["locus_id"], "gene": locus.get("gene")},
    )
    await session.commit()
    return {
        "locus_id": record["locus_id"],
        "gene": locus.get("gene"),
        "validated": bool(record["validated"]),
        "reason": record["reason"],
        "updated_by": record.get("updated_by_name"),
        "updated_at": record.get("updated_at"),
    }


_CATALOGUE_SQL = text(
    """
    SELECT
        repeat_loci.locus_id,
        repeat_loci.gene,
        repeat_loci.display_name,
        repeat_loci.disease,
        repeat_loci.motif,
        repeat_loci.warning_min,
        repeat_loci.pathogenic_min,
        (repeat_loci.metadata ->> 'benign_min')::int AS benign_min,
        (repeat_loci.metadata ->> 'benign_max')::int AS benign_max,
        (repeat_loci.metadata ->> 'premutation_min')::int AS premutation_min,
        repeat_loci.metadata ->> 'source' AS source,
        COALESCE(units.validated, false) AS validated,
        units.reason,
        units.updated_by_name AS updated_by,
        units.updated_at,
        (
            SELECT count(*)
            FROM repeat_expansions
            WHERE repeat_expansions.locus_id = repeat_loci.locus_id
        ) AS call_count
    FROM repeat_loci
    LEFT JOIN repeat_locus_count_units AS units ON units.locus_id = repeat_loci.locus_id
    ORDER BY repeat_loci.gene, repeat_loci.locus_id
    """
)

_CHANGES_SQL = text(
    """
    SELECT created_at, actor, action, summary, before, after
    FROM clinical_audit_events
    WHERE family_identifier = :chain
    ORDER BY created_at DESC, id DESC
    LIMIT :limit
    """
)


def _json_field(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


async def list_repeat_locus_count_units(session: AsyncSession) -> dict[str, Any]:
    """Every catalogue locus with its validation record, and the record's history.

    Each locus says how many stored calls name it, so QA can see which rows the laboratory's
    TRGT catalogue resolves to, and what is known of TRGT's count at its gene.
    """
    loci = [dict(row) for row in (await session.execute(_CATALOGUE_SQL)).mappings().all()]
    for locus in loci:
        locus["validated"] = bool(locus.get("validated"))
        locus["call_count"] = int(locus.get("call_count") or 0)
        locus["source"] = locus.get("source") or "built-in"
        locus["known_difference"] = known_count_unit_difference(locus.get("gene"))
    changes = []
    for row in (
        await session.execute(
            _CHANGES_SQL, {"chain": REPEAT_COUNT_UNITS_AUDIT_CHAIN, "limit": _CHANGES_LIMIT}
        )
    ).mappings().all():
        after = _json_field(row.get("after"))
        changes.append(
            {
                "changed_at": row["created_at"],
                "changed_by": row.get("actor"),
                "locus_id": after.get("locus_id"),
                "gene": after.get("gene"),
                "validated": bool(after.get("validated")),
                "summary": row.get("summary"),
                "reason": after.get("reason"),
            }
        )
    return {"margin": COUNT_UNIT_MARGIN, "loci": loci, "changes": changes}
