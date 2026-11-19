"""The conventional NIPT's result for a family (``GET /families/{family_id}/cnipt``): each
maternal plasma's ccnv QC (``samples.metadata["ccnv"]``) and segments (its ``segments`` track,
source ``ccnv``), judged by the result rule (``cnipt_result``) with the bounds of the profile
``nipt_conventional``."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import FamilyCniptOut
from .access_control import CurrentUser
from .bed_service import _segment_details
from .ccnv_output import CCNV_SOURCE, stored_ccnv_qc
from .clickhouse_interval_tracks import fetch_interval_track_rows
from .cnipt_result import CNIPT_THRESHOLD_PROFILE, ccnv_segment_rows, evaluate_cnipt, resolve_cnipt_bounds
from .family_metadata_context import build_family_metadata_context
from .qc_threshold_service import QC_METRICS_BY_KEY, resolve_profile_thresholds


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
    plasmas = [
        row
        for row in context.sample_rows
        if isinstance(row.get("sample_metadata"), dict) and isinstance(row["sample_metadata"].get("ccnv"), dict)
    ]
    if not plasmas:
        raise HTTPException(status_code=404, detail="This family holds no ccnv output")
    bounds = resolve_cnipt_bounds(await resolve_profile_thresholds(session, CNIPT_THRESHOLD_PROFILE))
    samples: list[dict[str, Any]] = []
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
        result = evaluate_cnipt(qc, ccnv_segment_rows(rows, _segment_details), bounds)
        result["bounds"] = [
            {
                **bound,
                "label": QC_METRICS_BY_KEY[bound["key"]].label,
                "unit": QC_METRICS_BY_KEY[bound["key"]].unit,
            }
            for bound in result["bounds"]
        ]
        samples.append(
            {
                "sample_id": row["sample_id"],
                "qc_file": str(stored.get("qc_file") or ""),
                "read_metrics": _read_metrics(qc.payload),
                **result,
            }
        )
    return FamilyCniptOut.model_validate({"family_id": context.family_id, "samples": samples})
