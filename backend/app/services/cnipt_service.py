"""The conventional NIPT's result for a family (``GET /families/{family_id}/cnipt``): each
maternal plasma's ccnv QC (``samples.metadata["ccnv"]``) and segments (its ``segments`` track,
source ``ccnv``), judged by the result rule (``cnipt_result``) with the bounds of the profile
``nipt_conventional``. The signed record's ``cnipt`` section (``signed_record_cnipt``) freezes
the same results, from the same code."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import CniptSampleResultOut, FamilyCniptOut
from .access_control import CurrentUser
from .bed_service import _segment_details
from .ccnv_output import CCNV_SOURCE, stored_ccnv_qc
from .clickhouse_interval_tracks import fetch_interval_track_rows
from .cnipt_result import (
    CNIPT_THRESHOLD_PROFILE,
    CcnvSegment,
    ccnv_segment_rows,
    evaluate_cnipt,
    resolve_cnipt_bounds,
)
from .family_metadata_context import FamilyMetadataContext, build_family_metadata_context
from .qc_threshold_service import QC_METRICS_BY_KEY, resolve_profile_thresholds


@dataclass(frozen=True, slots=True)
class CniptPlasmaResult:
    """One maternal plasma's result as the cNIPT page shows it, with what it was computed from:
    the plasma's sample, the ccnv QC object as stored and the segments as read."""

    sample_uuid: str
    result: CniptSampleResultOut
    stored_qc: Mapping[str, Any]
    segments: tuple[CcnvSegment, ...]


def _read_metrics(payload: dict[str, Any]) -> dict[str, float]:
    """ccnv's read metrics that are numbers (reads kept, usable bins, the GC dispersion, …)."""
    metrics = payload.get("read_metrics")
    if not isinstance(metrics, dict):
        return {}
    return {
        str(key): float(value)
        for key, value in metrics.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }


def ccnv_plasma_rows(context: FamilyMetadataContext) -> list[Mapping[str, Any]]:
    """The family's samples that hold a ccnv output (``samples.metadata["ccnv"]``), in sample
    order: its maternal plasmas."""
    return [
        row
        for row in context.sample_rows
        if isinstance(row.get("sample_metadata"), dict) and isinstance(row["sample_metadata"].get("ccnv"), dict)
    ]


async def family_cnipt_results(
    session: AsyncSession, context: FamilyMetadataContext
) -> list[CniptPlasmaResult]:
    """Each maternal plasma's result, in sample order; [] for a family without ccnv output."""
    plasmas = ccnv_plasma_rows(context)
    if not plasmas:
        return []
    bounds = resolve_cnipt_bounds(await resolve_profile_thresholds(session, CNIPT_THRESHOLD_PROFILE))
    results: list[CniptPlasmaResult] = []
    for row in plasmas:
        stored = row["sample_metadata"]["ccnv"]
        qc = stored_ccnv_qc(stored)
        rows = (
            await fetch_interval_track_rows(
                context.assembly_name,
                sample_uuid=row["sample_uuid"],
                family_uuid=context.family_uuid,
                track_type="segments",
                chromosomes=[],
                source=CCNV_SOURCE,
                include_metadata=True,
            )
            if context.assembly_name
            else []
        )
        segments = ccnv_segment_rows(rows, _segment_details)
        result = evaluate_cnipt(qc, segments, bounds)
        result["bounds"] = [
            {
                **bound,
                "label": QC_METRICS_BY_KEY[bound["key"]].label,
                "unit": QC_METRICS_BY_KEY[bound["key"]].unit,
            }
            for bound in result["bounds"]
        ]
        results.append(
            CniptPlasmaResult(
                sample_uuid=str(row["sample_uuid"]),
                result=CniptSampleResultOut.model_validate(
                    {
                        "sample_id": row["sample_id"],
                        "qc_file": str(stored.get("qc_file") or ""),
                        "read_metrics": _read_metrics(qc.payload),
                        **result,
                    }
                ),
                stored_qc=stored,
                segments=tuple(segments),
            )
        )
    return results


async def get_family_cnipt_result(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    project_id: str | None = None,
) -> FamilyCniptOut:
    """Each maternal plasma's result, in sample order. A family without ccnv output answers 404:
    there is nothing to judge."""
    context = await build_family_metadata_context(
        session, family_identifier=family_id, user=user, project_id=project_id
    )
    results = await family_cnipt_results(session, context)
    if not results:
        raise HTTPException(status_code=404, detail="This family holds no ccnv output")
    return FamilyCniptOut(family_id=context.family_id, samples=[entry.result for entry in results])
