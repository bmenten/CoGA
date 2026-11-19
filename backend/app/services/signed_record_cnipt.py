"""The conventional NIPT section of the signed analysis record.

A family that holds a ccnv output (a maternal plasma with ``samples.metadata["ccnv"]``) is
signed out like every family: through the same sign-out, gates and report code
(``report_signout_service``). Its record also holds ``cnipt``: each maternal plasma's result
as the cNIPT page showed it to the analyst, computed by the backend (``cnipt_service``,
``cnipt_result``), not by the browser:

* the status (a result, or no result with its reasons) and the verdict of each target (each
  autosome, the sex chromosomes and the segmental CNVs) with its reasons;
* the findings the rule looked at, each with ccnv's values and its own verdict;
* the fetal fractions (the effective one the rule reads, and every other estimate), the fetal
  sex, the sex-chromosome call, the limits of detection, ccnv's read metrics and warnings;
* the rule's bounds as it applied them, each QA's or provisional, and what of the rule is fixed
  in code;
* the inputs it ran with: the QC file's name and a digest of its object as the import kept it,
  the number and a digest of the segments it read, and the segments files the track was loaded
  from, with their rows and when.

A family without a ccnv output holds no ``cnipt`` key. A plasma without a result, or a result
that applied a provisional bound, gates the sign-out under the Sample QC's acknowledgement
(``cnipt_signout_concerns``), as the monogenic NIPT's failed quality checks do.

Deterministic and JSON-safe, as every section is: the plasmas in sample order, each list in the
rule's own order, numbers that JSONB cannot hold (NaN, infinity) frozen as null, and -0.0 as
0.0.
"""

from __future__ import annotations

import dataclasses
import math
from datetime import datetime
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .ccnv_output import CCNV_SOURCE
from .cnipt_service import ccnv_plasma_rows, family_cnipt_results
from .family_metadata_context import FamilyMetadataContext
from .hash_chain import canonical_hash

# The files each plasma's segments track was loaded from.
_SEGMENT_FILES_SQL = text(
    """
    SELECT sample_id::text AS sample_uuid, filename, row_count, uploaded_at
    FROM sample_interval_track_sources
    WHERE family_id = CAST(:family_uuid AS uuid)
      AND track_type = 'segments'
      AND source = :source
    ORDER BY filename, uploaded_at
    """
)


def holds_ccnv_output(context: FamilyMetadataContext) -> bool:
    """Whether the family holds a ccnv output: a sample with its ccnv QC kept
    (``samples.metadata["ccnv"]``), as the cNIPT page reads it. Its record then holds the
    ``cnipt`` section."""
    return bool(ccnv_plasma_rows(context))


def _json_safe(value: Any) -> Any:
    """``value`` as JSONB keeps it: None for NaN and infinity, 0.0 for -0.0 (JSONB has no
    negative zero, so the content hash would not survive the round trip)."""
    if isinstance(value, float):
        return value + 0.0 if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value is not None else None


async def cnipt_results(session: AsyncSession, context: FamilyMetadataContext) -> dict[str, Any]:
    """Each maternal plasma's result as the cNIPT page shows it (the same object, from the same
    code), with the inputs it was computed from."""
    plasmas = await family_cnipt_results(session, context)
    files: dict[str, list[dict[str, Any]]] = {}
    for row in (
        await session.execute(
            _SEGMENT_FILES_SQL, {"family_uuid": context.family_uuid, "source": CCNV_SOURCE}
        )
    ).mappings():
        files.setdefault(str(row["sample_uuid"]), []).append(
            {
                "file": str(row.get("filename") or ""),
                "rows": None if row.get("row_count") is None else int(row["row_count"]),
                "loaded_at": _iso(row.get("uploaded_at")),
            }
        )
    samples: list[dict[str, Any]] = []
    for plasma in plasmas:
        segments = _json_safe([dataclasses.asdict(segment) for segment in plasma.segments])
        samples.append(
            {
                **_json_safe(plasma.result.model_dump(mode="json")),
                "inputs": {
                    "qc_digest": canonical_hash(_json_safe(plasma.stored_qc.get("qc"))),
                    "segments": len(segments),
                    "segments_digest": canonical_hash(segments),
                    "segment_files": files.get(plasma.sample_uuid, []),
                },
            }
        )
    return {"samples": samples}


# The catalogue's labels name the application first ("cNIPT: fetal fraction"); the concern names
# it in its subject.
_LABEL_PREFIX = "cNIPT: "


def _provisional_labels(bounds: Any) -> list[str]:
    if not isinstance(bounds, list):
        return []
    return [
        str(bound.get("label") or bound.get("key")).removeprefix(_LABEL_PREFIX)
        for bound in bounds
        if isinstance(bound, Mapping) and bound.get("provisional")
    ]


def cnipt_signout_concerns(section: Any) -> list[tuple[str | None, str]]:
    """What in a record's ``cnipt`` section gates the sign-out, each as (what it is about, why):
    a plasma without a result, with its reasons, and a result that applied a provisional bound,
    which QA has not set, with those bounds. They gate it like a failed Sample QC check, under
    the same acknowledgement. [] for a section that holds no plasma (none, or one that could not
    be read: the record's gaps name it)."""
    if not isinstance(section, Mapping) or section.get("unavailable"):
        return []
    concerns: list[tuple[str | None, str]] = []
    for sample in section.get("samples") or []:
        if not isinstance(sample, Mapping):
            continue
        subject = f"Sample {sample.get('sample_id')} (cNIPT maternal plasma)"
        if sample.get("status") == "no_result":
            reasons = [str(reason).rstrip(".") for reason in sample.get("no_result_reasons") or []]
            concerns.append((subject, f"No result — {'; '.join(reasons) or 'no reason recorded'}."))
        provisional = _provisional_labels(sample.get("bounds"))
        if provisional:
            concerns.append(
                (
                    subject,
                    "The result rests on provisional bounds, which QA has not set: "
                    f"{'; '.join(provisional)}.",
                )
            )
    return concerns
