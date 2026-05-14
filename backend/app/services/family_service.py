from __future__ import annotations

import asyncio
from dataclasses import replace
import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.sql import uuid_list_bindparam, uuid_values
from ..schemas import (
    FamilyOut,
    FamilyRegionOfInterestUpdate,
    FamilyTrackAvailabilityOut,
    HaplotypeResponse,
    TrackAvailabilityOut,
    VariantLengthOut,
)
from .bed_service import (
    get_family_haplotypes_batch_response,
    get_family_haplotypes_response,
    get_track_presence_by_sample,
)
from .clickhouse_family_variants import (
    _fetch_gene_regions,
    _fetch_panel_regions,
    _fetch_small_variant_rows,
    _fetch_structural_variant_rows,
    _structural_record_matches,
)
from .data_scope import normalize_chromosome
from .family_metadata_context import build_family_metadata_context
from .family_variant_filters import SmallVariantQueryFilters, StructuralVariantQueryFilters
from .metadata_service import (
    CurrentUser,
    get_family_record,
    list_family_records,
    update_family_roi_record,
)

GENOMIC_REGION_PATTERN = re.compile(
    r"^(?P<chrom>(?:chr)?[A-Za-z0-9_]+):(?P<start>[0-9,]+)(?:-(?P<end>[0-9,]+))?$",
    re.IGNORECASE,
)
_REF_GT_VALUES = {"0/0", "0|0", "./.", ".|.", "", "."}
_NON_REF_SMALL_GT_VALUES = ("0/1", "1/0", "0|1", "1|0", "1/1", "1|1")


def _parse_region_of_interest(query: str) -> tuple[str, int, int] | None:
    match = GENOMIC_REGION_PATTERN.match(query.strip())
    if not match:
        return None
    chrom = normalize_chromosome(match.group("chrom"))
    start = int(match.group("start").replace(",", ""))
    end_raw = match.group("end")
    end = int(end_raw.replace(",", "")) if end_raw else start
    if start < 0 or end < 0:
        raise HTTPException(status_code=400, detail="ROI coordinates must be non-negative")
    if end < start:
        start, end = end, start
    return chrom, start, end


def _sample_specific_filters(sample_name: str, filters: list[str] | None) -> list[str]:
    prefix = f"{sample_name}:"
    return [
        entry
        for entry in (filters or [])
        if entry == sample_name or entry.startswith(prefix)
    ]


def _small_variant_presence_filters(sample_name: str, filters: list[str] | None) -> list[str]:
    base_filters = list(filters or [])
    specific_filters = _sample_specific_filters(sample_name, filters)
    if specific_filters:
        return base_filters
    return [*base_filters, f"{sample_name}:{'|'.join(_NON_REF_SMALL_GT_VALUES)}"]


async def _resolve_family_assembly(
    session: AsyncSession,
    *,
    family_uuid: str,
    project_id: str | None,
) -> str:
    if project_id:
        result = await session.execute(
            text(
                """
                SELECT a.id::text AS assembly_id
                FROM family_projects fp
                JOIN projects p ON p.id = fp.project_id
                JOIN assemblies a ON a.id = p.assembly_id
                WHERE fp.family_id = CAST(:family_id AS uuid)
                  AND fp.project_id = CAST(:project_id AS uuid)
                """
            ),
            {"family_id": family_uuid, "project_id": project_id},
        )
        assembly_id = result.scalar_one_or_none()
        if assembly_id is None:
            raise HTTPException(status_code=400, detail="Project is not linked to this family")
        return str(assembly_id)

    result = await session.execute(
        text(
            """
            SELECT DISTINCT a.id::text AS assembly_id
            FROM family_projects fp
            JOIN projects p ON p.id = fp.project_id
            JOIN assemblies a ON a.id = p.assembly_id
            WHERE fp.family_id = CAST(:family_id AS uuid)
            """
        ),
        {"family_id": family_uuid},
    )
    assembly_ids = [str(row[0]) for row in result.all() if row[0]]
    if not assembly_ids:
        raise HTTPException(status_code=400, detail="Family does not define an assembly")
    if len(assembly_ids) > 1:
        raise HTTPException(
            status_code=400,
            detail="Could not resolve a single assembly for this family",
        )
    return assembly_ids[0]


async def _build_family_roi_payload(
    session: AsyncSession,
    *,
    assembly_id: str,
    query: str,
) -> dict[str, Any]:
    cleaned_query = query.strip()
    region = _parse_region_of_interest(cleaned_query)
    if region:
        chrom, start, end = region
        return {
            "query": cleaned_query,
            "label": cleaned_query,
            "source": "region",
            "assembly_id": assembly_id,
            "chr": chrom,
            "start": start,
            "end": end,
        }
    gene_result = await session.execute(
        text(
            """
            SELECT hgnc_symbol, gene_id, chr, start, "end"
            FROM genes
            WHERE assembly_id = CAST(:assembly_id AS uuid)
              AND (
                lower(hgnc_symbol) = lower(:query)
                OR lower(gene_id) = lower(:query)
              )
            ORDER BY ("end" - start) DESC, hgnc_symbol
            LIMIT 1
            """
        ),
        {"assembly_id": assembly_id, "query": cleaned_query},
    )
    gene_row = gene_result.mappings().first()
    if gene_row is None:
        raise HTTPException(
            status_code=404,
            detail="ROI gene or genomic location could not be resolved",
        )
    return {
        "query": cleaned_query,
        "label": gene_row.get("hgnc_symbol") or gene_row.get("gene_id") or cleaned_query,
        "source": "gene",
        "assembly_id": assembly_id,
        "chr": normalize_chromosome(str(gene_row["chr"])),
        "start": int(gene_row["start"]),
        "end": int(gene_row["end"]),
    }


async def _repeat_expansion_presence_by_sample(
    session: AsyncSession,
    *,
    family_uuid: str,
    sample_uuid_to_name: dict[str, str],
    chromosomes: list[str],
    start: int | None,
    end: int | None,
) -> set[str]:
    sample_ids = list(sample_uuid_to_name)
    if not sample_ids:
        return set()
    chrom_values: list[str] = []
    seen: set[str] = set()
    for chrom in chromosomes:
        for candidate in (normalize_chromosome(chrom), f"chr{normalize_chromosome(chrom)}"):
            if candidate not in seen:
                seen.add(candidate)
                chrom_values.append(candidate)
    clauses = [
        "family_id = CAST(:family_id AS uuid)",
        "sample_id IN :sample_ids",
    ]
    bind_params = [uuid_list_bindparam("sample_ids")]
    params: dict[str, Any] = {
        "family_id": family_uuid,
        "sample_ids": uuid_values(sample_ids),
    }
    if chrom_values:
        clauses.append("chr IN :chromosomes")
        bind_params.append(bindparam("chromosomes", expanding=True))
        params["chromosomes"] = chrom_values
    if start is not None and end is not None and len(chromosomes) == 1:
        clauses.append('start <= :window_end AND "end" >= :window_start')
        params["window_start"] = start
        params["window_end"] = end
    result = await session.execute(
        text(
            f"""
            SELECT DISTINCT sample_id::text AS sample_uuid
            FROM repeat_expansions
            WHERE {' AND '.join(clauses)}
            """
        ).bindparams(*bind_params),
        params,
    )
    return {
        sample_uuid_to_name[row["sample_uuid"]]
        for row in result.mappings().all()
        if row["sample_uuid"] in sample_uuid_to_name
    }


async def list_families_for_user(
    session: AsyncSession,
    user: CurrentUser,
) -> list[FamilyOut]:
    return await list_family_records(session, user)


async def get_family_for_user(
    session: AsyncSession,
    family_id: str,
    user: CurrentUser,
) -> FamilyOut:
    return await get_family_record(session, family_id, user)


async def update_family_roi_for_admin(
    session: AsyncSession,
    family_id: str,
    update: FamilyRegionOfInterestUpdate,
    user: CurrentUser,
) -> FamilyOut:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        project_id=update.project_id,
    )
    query = (update.query or "").strip()
    if not query:
        await update_family_roi_record(session, family_identifier=family_id, roi=None)
        return await get_family_record(session, family_id, user)
    assembly_id = await _resolve_family_assembly(
        session,
        family_uuid=context.family_uuid,
        project_id=update.project_id,
    )
    roi_payload = await _build_family_roi_payload(
        session,
        assembly_id=assembly_id,
        query=query,
    )
    await update_family_roi_record(
        session,
        family_identifier=family_id,
        roi=roi_payload,
    )
    return await get_family_record(session, family_id, user)


async def get_family_haplotypes_for_user(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    chr: str,
    start: int | None = None,
    end: int | None = None,
) -> HaplotypeResponse:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
    )
    return await get_family_haplotypes_response(
        session,
        context=context,
        chr=chr,
        start=start,
        end=end,
    )


async def get_family_haplotypes_batch_for_user(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    chromosomes: list[str],
) -> HaplotypeResponse:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
    )
    return await get_family_haplotypes_batch_response(
        session,
        context=context,
        chromosomes=chromosomes,
    )


async def get_family_track_availability_for_user(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    chromosomes: list[str],
    start: int | None = None,
    end: int | None = None,
    variant_type: str | None = None,
    source: str | None = None,
    length: int | None = None,
    min_length: int | None = None,
    remote_chr: str | None = None,
    remote_start: int | None = None,
    panel_id: str | None = None,
    phase_set: int | None = None,
    sample_filters: list[str] | None = None,
    project_id: str | None = None,
    include_small_variants: bool = True,
) -> FamilyTrackAvailabilityOut:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        project_id=project_id,
    )
    availability = {
        row["sample_id"]: TrackAvailabilityOut()
        for row in context.sample_rows
    }
    if not availability:
        return FamilyTrackAvailabilityOut(samples={})

    coverage_task = get_track_presence_by_sample(
        session,
        context=context,
        track_type="coverage",
        chromosomes=chromosomes,
    )
    segment_task = get_track_presence_by_sample(
        session,
        context=context,
        track_type="segments",
        chromosomes=chromosomes,
    )
    apcad_task = get_track_presence_by_sample(
        session,
        context=context,
        track_type="apcad",
        chromosomes=chromosomes,
    )
    haplotype_task = get_track_presence_by_sample(
        session,
        context=context,
        track_type="haplotype",
        chromosomes=chromosomes,
        start=start,
        end=end,
    )
    repeat_task = _repeat_expansion_presence_by_sample(
        session,
        family_uuid=context.family_uuid,
        sample_uuid_to_name=context.sample_uuid_to_name,
        chromosomes=chromosomes,
        start=start,
        end=end,
    )
    coverage_ids, segment_ids, apcad_ids, haplotype_ids, repeat_ids = await asyncio.gather(
        coverage_task,
        segment_task,
        apcad_task,
        haplotype_task,
        repeat_task,
    )

    overlap = start is not None and end is not None and len(chromosomes) == 1
    structural_filters = StructuralVariantQueryFilters(
        page=1,
        page_size=1,
        chromosome=chromosomes[0] if len(chromosomes) == 1 else None,
        start=start,
        end=end,
        length=length,
        min_length=min_length,
        variant_type=variant_type,
        source=source,
        sample_filters=sample_filters or [],
        selected_samples=[],
        remote_chr=remote_chr,
        remote_start=remote_start,
        panel_id=panel_id,
        overlap=overlap,
    )
    small_filters = SmallVariantQueryFilters(
        page=1,
        page_size=1,
        chromosome=chromosomes[0] if len(chromosomes) == 1 else None,
        start=start,
        end=end,
        phase_set=phase_set,
        panel_id=panel_id,
        sample_filters=sample_filters or [],
        overlap=overlap,
    )
    include_regions = await _fetch_panel_regions(session, panel_id) if panel_id else []

    async def small_variant_presence_for_sample(sample_name: str) -> str | None:
        sample_small_filters = replace(
            small_filters,
            sample_filters=_small_variant_presence_filters(sample_name, sample_filters),
        )
        records = await _fetch_small_variant_rows(
            context,
            sample_small_filters,
            limit=1,
            include_regions=include_regions,
        )
        return sample_name if records else None

    small_presence_task = (
        asyncio.gather(
            *[
                small_variant_presence_for_sample(sample_name)
                for sample_name in availability
            ]
        )
        if include_small_variants
        else asyncio.sleep(0, result=[])
    )
    structural_records, small_presence_results = await asyncio.gather(
        _fetch_structural_variant_rows(context, structural_filters),
        small_presence_task,
    )
    small_presence = {
        sample_name
        for sample_name in small_presence_results
        if sample_name
    }

    for sample_name, sample_availability in availability.items():
        sample_availability.coverage = sample_name in coverage_ids
        sample_availability.segments = sample_name in segment_ids
        sample_availability.apcad = sample_name in apcad_ids
        sample_availability.haplotypes = sample_name in haplotype_ids
        sample_availability.repeat_expansions = sample_name in repeat_ids

        sample_structural_filters = StructuralVariantQueryFilters(
            page=1,
            page_size=1,
            chromosome=structural_filters.chromosome,
            start=structural_filters.start,
            end=structural_filters.end,
            length=structural_filters.length,
            min_length=structural_filters.min_length,
            variant_type=structural_filters.variant_type,
            source=structural_filters.source,
            sample_filters=_sample_specific_filters(sample_name, sample_filters),
            selected_samples=[sample_name],
            remote_chr=structural_filters.remote_chr,
            remote_start=structural_filters.remote_start,
            panel_id=structural_filters.panel_id,
            overlap=structural_filters.overlap,
        )
        sample_availability.variants = any(
            _structural_record_matches(
                record,
                sample_structural_filters,
                include_regions,
                [sample_name],
            )
            for record in structural_records
        )

        if include_small_variants:
            sample_availability.small_variants = sample_name in small_presence

    return FamilyTrackAvailabilityOut(samples=availability)


async def get_family_structural_variant_lengths_for_user(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    limit: int,
) -> list[VariantLengthOut]:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
    )
    records = await _fetch_structural_variant_rows(
        context,
        StructuralVariantQueryFilters(page=1, page_size=limit),
    )
    return [
        VariantLengthOut(
            length=int(record.sv_len if record.sv_len is not None else record.end - record.start),
            type=record.sv_type,
            source=record.source,
            chr=record.chr,
        )
        for record in records[:limit]
    ]


async def get_shared_family_structural_variant_counts_for_user(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
) -> dict[str, dict[str, int]]:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
    )
    names = [row["sample_id"] for row in context.sample_rows]
    counts: dict[str, dict[str, int]] = {name: {other: 0 for other in names} for name in names}
    records = await _fetch_structural_variant_rows(
        context,
        StructuralVariantQueryFilters(page=1, page_size=1),
    )
    for record in records:
        present_names = [
            call.sample
            for call in record.calls
            if call.sample in counts and call.gt not in _REF_GT_VALUES
        ]
        if len(present_names) == 1:
            sample_name = present_names[0]
            counts[sample_name][sample_name] += 1
            continue
        for index, left in enumerate(present_names):
            for right in present_names[index + 1 :]:
                counts[left][right] += 1
                counts[right][left] += 1
    return counts
