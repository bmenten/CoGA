"""The conventional NIPT's result of one pregnancy of a family
(``GET /families/{family_id}/cnipt?analysis=``): each maternal plasma its package lists, with its
ccnv QC (``samples.metadata["ccnv"]``) and segments (its ``segments`` track, under the pregnancy's
own ``ccnv`` source), judged by the result rule (``cnipt_result``) with the bounds of the profile
``nipt_conventional``. A family holds one conventional NIPT per pregnancy, read one at a time. The
signed record's ``cnipt`` section (``signed_record_cnipt``) freezes the same results, from the same
code."""

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
from .family_analysis_types import pregnancy_of
from .family_metadata_context import FamilyMetadataContext, build_family_metadata_context
from .family_package_records import analysis_members, pregnancy_source
from .nipt import CONVENTIONAL_NIPT_ANALYSIS_TYPE, multiple_pregnancy_note, nipt_analysis_of, pregnancy_fetuses
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
    order: its maternal plasmas, of every pregnancy."""
    return [
        row
        for row in context.sample_rows
        if isinstance(row.get("sample_metadata"), dict) and isinstance(row["sample_metadata"].get("ccnv"), dict)
    ]


@dataclass(frozen=True, slots=True)
class CniptPregnancy:
    """What a conventional NIPT of one pregnancy reads: its analysis (by its key,
    ``conventional_nipt:ZW1``), the maternal plasmas its package lists that hold a ccnv output,
    the source its ccnv output is stored under (the pregnancy's own,
    ``family_package_records.pregnancy_source``) and the pregnancy's fetuses."""

    analysis: str
    plasmas: tuple[Mapping[str, Any], ...]
    source: str
    fetuses: tuple[str, ...]


def cnipt_pregnancy(context: FamilyMetadataContext, analysis: str) -> CniptPregnancy:
    """The plasmas, source and fetuses of the family's conventional NIPT ``analysis``: those its
    package lists. Another pregnancy's plasma, linked as the same woman, is never read with it."""
    listed = analysis_members(context.family_metadata, analysis)
    return CniptPregnancy(
        analysis=analysis,
        plasmas=tuple(row for row in ccnv_plasma_rows(context) if str(row.get("sample_id")) in listed),
        source=pregnancy_source(CCNV_SOURCE, pregnancy_of(analysis)),
        fetuses=pregnancy_fetuses(
            (
                (str(row.get("sample_id")), row.get("role"), row.get("sample_metadata"))
                for row in context.sample_rows
                if row.get("sample_id")
            ),
            listed,
        ),
    )


async def family_cnipt_results(
    session: AsyncSession, context: FamilyMetadataContext, pregnancy: CniptPregnancy
) -> list[CniptPlasmaResult]:
    """Each maternal plasma's result of the conventional NIPT of one pregnancy, in sample order;
    [] when its package lists no plasma with a ccnv output."""
    plasmas = pregnancy.plasmas
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
                source=pregnancy.source,
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
    analysis: str | None = None,
) -> FamilyCniptOut:
    """Each maternal plasma's result of the family's conventional NIPT ``analysis`` (one
    pregnancy's, by its key; without one the family's only conventional NIPT,
    ``nipt.nipt_analysis_of``), in sample order. A family without one, or whose package lists no
    plasma with ccnv output, answers 404: there is nothing to judge."""
    context = await build_family_metadata_context(
        session, family_identifier=family_id, user=user, project_id=project_id
    )
    own = nipt_analysis_of(context.family_metadata, CONVENTIONAL_NIPT_ANALYSIS_TYPE, analysis)
    if own is None:
        raise HTTPException(status_code=404, detail="This family holds no ccnv output")
    pregnancy = cnipt_pregnancy(context, own)
    results = await family_cnipt_results(session, context, pregnancy)
    if not results:
        raise HTTPException(status_code=404, detail="This family holds no ccnv output")
    return FamilyCniptOut(
        family_id=context.family_id,
        analysis=own,
        fetuses=list(pregnancy.fetuses),
        multiple_pregnancy_note=multiple_pregnancy_note(len(pregnancy.fetuses)),
        samples=[entry.result for entry in results],
    )
