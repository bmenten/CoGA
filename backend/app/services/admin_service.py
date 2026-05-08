from __future__ import annotations

from dataclasses import replace
import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.sql import uuid_list_bindparam, uuid_values
from ..schemas import (
    ClickHouseVariantAssemblyListOut,
    ClickHouseVariantAssemblyStatusOut,
    FamilyInventoryDetailOut,
    FamilyInventoryPageOut,
    FamilyInventorySummaryOut,
    ProjectsUpdate,
    SampleInventoryOut,
)
from .clickhouse_family_variants import _fetch_small_variant_rows, _fetch_structural_variant_rows
from .clickhouse_interval_tracks import (
    delete_interval_track_sources,
    delete_interval_tracks,
    interval_counts_by_family,
    interval_counts_by_sample,
)
from .clickhouse_variant_storage import (
    count_family_small_variants,
    count_family_structural_variants,
    count_family_structural_variants_by_sample,
    delete_family_small_variants,
    delete_family_structural_variants,
    ensure_clickhouse_variant_storage_ready,
    get_clickhouse_variant_storage_status,
    list_clickhouse_variant_assemblies,
    optimize_clickhouse_variant_tables,
    replace_family_small_variants,
    replace_family_structural_variants,
)
from .family_metadata_context import FamilyMetadataContext
from .family_variant_filters import SmallVariantQueryFilters, StructuralVariantQueryFilters

BED_TRACK_TYPES = ("coverage", "segments", "apcad", "haplotype")
SAMPLE_TRACK_TYPES = (*BED_TRACK_TYPES, "structural_variants", "repeat_expansions")
FAMILY_TRACK_TYPES = ("small_variants", "structural_variants", "repeat_expansions", *BED_TRACK_TYPES)


def _empty_track_counts(track_types: tuple[str, ...]) -> dict[str, int]:
    return {track_type: 0 for track_type in track_types}


def _track_presence(track_counts: dict[str, int]) -> dict[str, bool]:
    return {track_type: count > 0 for track_type, count in track_counts.items()}


def _search_pattern(search: str | None) -> str | None:
    term = str(search or "").strip()
    if not term:
        return None
    return f"%{term.lower()}%"


def _string_list(values: list[Any] | tuple[Any, ...] | None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


async def _family_rows(
    session: AsyncSession,
    *,
    search: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    pattern = _search_pattern(search)
    where_clause = ""
    params: dict[str, Any] = {}
    if pattern:
        where_clause = (
            """
            WHERE lower(f.family_id) LIKE :pattern
               OR EXISTS (
                    SELECT 1
                    FROM family_members fm2
                    JOIN samples s2 ON s2.id = fm2.sample_id
                    WHERE fm2.family_id = f.id
                      AND lower(s2.sample_id) LIKE :pattern
               )
            """
        )
        params["pattern"] = pattern

    total_result = await session.execute(
        text(
            f"""
            SELECT COUNT(*)
            FROM families f
            {where_clause}
            """
        ),
        params,
    )
    total = int(total_result.scalar_one() or 0)

    pagination = ""
    if page is not None and page_size is not None:
        pagination = " OFFSET :offset LIMIT :limit"
        params["offset"] = max(page - 1, 0) * page_size
        params["limit"] = page_size

    result = await session.execute(
        text(
            f"""
            SELECT
                f.id::text AS family_uuid,
                f.family_id,
                f.metadata,
                COALESCE(
                    ARRAY_AGG(DISTINCT fp.project_id::text)
                    FILTER (WHERE fp.project_id IS NOT NULL),
                    '{{}}'::text[]
                ) AS project_ids,
                COALESCE(
                    ARRAY_AGG(DISTINCT a.assembly_name)
                    FILTER (WHERE a.assembly_name IS NOT NULL),
                    '{{}}'::text[]
                ) AS assembly_names,
                COUNT(DISTINCT fm.sample_id) AS sample_count
            FROM families f
            LEFT JOIN family_projects fp ON fp.family_id = f.id
            LEFT JOIN projects p ON p.id = fp.project_id
            LEFT JOIN assemblies a ON a.id = p.assembly_id
            LEFT JOIN family_members fm ON fm.family_id = f.id
            {where_clause}
            GROUP BY f.id
            ORDER BY lower(f.family_id)
            {pagination}
            """
        ),
        params,
    )
    return total, [dict(row) for row in result.mappings().all()]


async def _sample_rows_by_family(
    session: AsyncSession,
    family_uuids: list[str],
) -> dict[str, list[dict[str, Any]]]:
    if not family_uuids:
        return {}
    result = await session.execute(
        text(
            """
            SELECT
                fm.family_id::text AS family_uuid,
                s.id::text AS sample_uuid,
                s.sample_id,
                s.sex,
                fm.role,
                fm.affected
            FROM family_members fm
            JOIN samples s ON s.id = fm.sample_id
            WHERE fm.family_id IN :family_uuids
            ORDER BY lower(s.sample_id)
            """
        ).bindparams(uuid_list_bindparam("family_uuids")),
        {"family_uuids": uuid_values(family_uuids)},
    )
    grouped: dict[str, list[dict[str, Any]]] = {family_uuid: [] for family_uuid in family_uuids}
    for row in result.mappings().all():
        grouped.setdefault(row["family_uuid"], []).append(dict(row))
    return grouped


async def _assembly_project_rows(
    session: AsyncSession,
    family_uuid: str,
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT DISTINCT
                p.id::text AS project_id,
                a.id::text AS assembly_id,
                a.assembly_name
            FROM family_projects fp
            JOIN projects p ON p.id = fp.project_id
            JOIN assemblies a ON a.id = p.assembly_id
            WHERE fp.family_id = CAST(:family_uuid AS uuid)
            ORDER BY a.assembly_name, project_id
            """
        ),
        {"family_uuid": family_uuid},
    )
    return [dict(row) for row in result.mappings().all()]


async def _family_assembly_contexts(
    session: AsyncSession,
    *,
    family_uuid: str,
    family_id: str,
    sample_rows: list[dict[str, Any]],
) -> list[FamilyMetadataContext]:
    project_rows = await _assembly_project_rows(session, family_uuid)
    sample_uuid_to_name = {row["sample_uuid"]: row["sample_id"] for row in sample_rows}
    sample_name_to_uuid = {row["sample_id"]: row["sample_uuid"] for row in sample_rows}
    affected_sample_names = [row["sample_id"] for row in sample_rows if bool(row.get("affected"))]
    if not project_rows:
        return [
            FamilyMetadataContext(
                family_uuid=family_uuid,
                family_id=family_id,
                project_ids=[],
                sample_rows=sample_rows,
                sample_uuid_to_name=sample_uuid_to_name,
                sample_name_to_uuid=sample_name_to_uuid,
                affected_sample_names=affected_sample_names,
                assembly_id=None,
                assembly_name=None,
            )
        ]
    grouped: dict[tuple[str, str], list[str]] = {}
    for row in project_rows:
        grouped.setdefault((row["assembly_id"], row["assembly_name"]), []).append(row["project_id"])
    return [
        FamilyMetadataContext(
            family_uuid=family_uuid,
            family_id=family_id,
            project_ids=project_ids,
            sample_rows=sample_rows,
            sample_uuid_to_name=sample_uuid_to_name,
            sample_name_to_uuid=sample_name_to_uuid,
            affected_sample_names=affected_sample_names,
            assembly_id=assembly_id,
            assembly_name=assembly_name,
        )
        for (assembly_id, assembly_name), project_ids in grouped.items()
    ]


async def _interval_counts_by_family(
    session: AsyncSession,
    family_uuids: list[str],
) -> dict[str, dict[str, int]]:
    return await interval_counts_by_family(session, family_uuids, BED_TRACK_TYPES)


async def _interval_counts_by_sample(
    session: AsyncSession,
    sample_uuids: list[str],
) -> dict[str, dict[str, int]]:
    return await interval_counts_by_sample(session, sample_uuids, BED_TRACK_TYPES)


async def _repeat_counts_by_family(
    session: AsyncSession,
    family_uuids: list[str],
) -> dict[str, int]:
    if not family_uuids:
        return {}
    result = await session.execute(
        text(
            """
            SELECT family_id::text AS family_uuid, COUNT(*) AS count
            FROM repeat_expansions
            WHERE family_id IN :family_uuids
            GROUP BY family_id
            """
        ).bindparams(uuid_list_bindparam("family_uuids")),
        {"family_uuids": uuid_values(family_uuids)},
    )
    return {row["family_uuid"]: int(row["count"]) for row in result.mappings().all()}


async def _repeat_counts_by_sample(
    session: AsyncSession,
    sample_uuids: list[str],
) -> dict[str, int]:
    if not sample_uuids:
        return {}
    result = await session.execute(
        text(
            """
            SELECT sample_id::text AS sample_uuid, COUNT(*) AS count
            FROM repeat_expansions
            WHERE sample_id IN :sample_uuids
            GROUP BY sample_id
            """
        ).bindparams(uuid_list_bindparam("sample_uuids")),
        {"sample_uuids": uuid_values(sample_uuids)},
    )
    return {row["sample_uuid"]: int(row["count"]) for row in result.mappings().all()}


async def _structural_sample_counts(
    contexts: list[FamilyMetadataContext],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for context in contexts:
        if not context.assembly_name:
            continue
        sample_counts = await count_family_structural_variants_by_sample(
            context.assembly_name,
            context.family_uuid,
            sample_ids=context.sample_name_to_uuid.keys(),
            project_ids=context.project_ids,
        )
        for sample_name, count in sample_counts.items():
            sample_uuid = context.sample_name_to_uuid.get(sample_name)
            if sample_uuid is None:
                continue
            counts[sample_uuid] = counts.get(sample_uuid, 0) + count
    return counts


async def _small_variant_records_without_sample(
    contexts: list[FamilyMetadataContext],
    sample_name: str,
) -> list[tuple[FamilyMetadataContext, list[Any]]]:
    replacements: list[tuple[FamilyMetadataContext, list[Any]]] = []
    for context in contexts:
        if not context.assembly_name:
            replacements.append((context, []))
            continue
        records = await _fetch_small_variant_rows(
            context,
            SmallVariantQueryFilters(page=1, page_size=1),
        )
        rewritten = []
        for record in records:
            remaining_calls = [call for call in record.calls if call.sample != sample_name]
            if remaining_calls:
                rewritten.append(replace(record, calls=remaining_calls))
        replacements.append((context, rewritten))
    return replacements


async def _structural_variant_records_without_sample(
    contexts: list[FamilyMetadataContext],
    sample_name: str,
) -> list[tuple[FamilyMetadataContext, list[Any]]]:
    replacements: list[tuple[FamilyMetadataContext, list[Any]]] = []
    for context in contexts:
        if not context.assembly_name:
            replacements.append((context, []))
            continue
        records = await _fetch_structural_variant_rows(
            context,
            StructuralVariantQueryFilters(page=1, page_size=1),
        )
        rewritten = []
        for record in records:
            remaining_calls = [call for call in record.calls if call.sample != sample_name]
            if remaining_calls:
                rewritten.append(replace(record, calls=remaining_calls))
        replacements.append((context, rewritten))
    return replacements


def _build_family_inventory_summary(
    family_row: dict[str, Any],
    bed_track_counts: dict[str, int],
    small_variant_count: int,
    structural_variant_count: int,
    repeat_expansion_count: int,
) -> FamilyInventorySummaryOut:
    track_counts = _empty_track_counts(FAMILY_TRACK_TYPES)
    track_counts.update(bed_track_counts)
    track_counts["small_variants"] = small_variant_count
    track_counts["structural_variants"] = structural_variant_count
    track_counts["repeat_expansions"] = repeat_expansion_count
    return FamilyInventorySummaryOut(
        family_id=family_row["family_id"],
        metadata=family_row.get("metadata") or {},
        projects=_string_list(family_row.get("project_ids")),
        sample_count=int(family_row.get("sample_count") or 0),
        track_counts=track_counts,
        total_records=sum(track_counts.values()),
    )


async def list_data_inventory_page(
    session: AsyncSession,
    *,
    page: int,
    page_size: int,
    search: str | None = None,
) -> FamilyInventoryPageOut:
    total, family_rows = await _family_rows(
        session,
        search=search,
        page=page,
        page_size=page_size,
    )
    family_uuids = [row["family_uuid"] for row in family_rows]
    interval_counts = await _interval_counts_by_family(session, family_uuids)
    repeat_counts = await _repeat_counts_by_family(session, family_uuids)
    items: list[FamilyInventorySummaryOut] = []
    for family_row in family_rows:
        small_variant_count = 0
        structural_variant_count = 0
        for assembly_name in _string_list(family_row.get("assembly_names")):
            small_variant_count += await count_family_small_variants(
                assembly_name,
                family_row["family_uuid"],
                project_ids=_string_list(family_row.get("project_ids")),
            )
            structural_variant_count += await count_family_structural_variants(
                assembly_name,
                family_row["family_uuid"],
                project_ids=_string_list(family_row.get("project_ids")),
            )
        items.append(
            _build_family_inventory_summary(
                family_row=family_row,
                bed_track_counts=interval_counts.get(
                    family_row["family_uuid"],
                    _empty_track_counts(BED_TRACK_TYPES),
                ),
                small_variant_count=small_variant_count,
                structural_variant_count=structural_variant_count,
                repeat_expansion_count=repeat_counts.get(family_row["family_uuid"], 0),
            )
        )
    return FamilyInventoryPageOut(total=total, page=page, page_size=page_size, items=items)


async def get_family_data_inventory_detail(
    session: AsyncSession,
    *,
    family_id: str,
) -> FamilyInventoryDetailOut:
    total, family_rows = await _family_rows(session, search=family_id)
    family_row = next((row for row in family_rows if row["family_id"] == family_id), None)
    if family_row is None:
        result = await session.execute(
            text(
                """
                SELECT
                    f.id::text AS family_uuid,
                    f.family_id,
                    f.metadata,
                    COALESCE(
                        ARRAY_AGG(DISTINCT fp.project_id::text)
                        FILTER (WHERE fp.project_id IS NOT NULL),
                        '{}'::text[]
                    ) AS project_ids,
                    COALESCE(
                        ARRAY_AGG(DISTINCT a.assembly_name)
                        FILTER (WHERE a.assembly_name IS NOT NULL),
                        '{}'::text[]
                    ) AS assembly_names,
                    COUNT(DISTINCT fm.sample_id) AS sample_count
                FROM families f
                LEFT JOIN family_projects fp ON fp.family_id = f.id
                LEFT JOIN projects p ON p.id = fp.project_id
                LEFT JOIN assemblies a ON a.id = p.assembly_id
                LEFT JOIN family_members fm ON fm.family_id = f.id
                WHERE f.family_id = :family_id
                GROUP BY f.id
                """
            ),
            {"family_id": family_id},
        )
        family_row = result.mappings().first()
        if family_row is None:
            raise HTTPException(status_code=404, detail="Family not found")
        family_row = dict(family_row)

    sample_rows = (await _sample_rows_by_family(session, [family_row["family_uuid"]]))[family_row["family_uuid"]]
    contexts = await _family_assembly_contexts(
        session,
        family_uuid=family_row["family_uuid"],
        family_id=family_row["family_id"],
        sample_rows=sample_rows,
    )
    interval_counts = await _interval_counts_by_family(session, [family_row["family_uuid"]])
    sample_interval_counts = await _interval_counts_by_sample(
        session,
        [row["sample_uuid"] for row in sample_rows],
    )
    repeat_counts = await _repeat_counts_by_family(session, [family_row["family_uuid"]])
    sample_repeat_counts = await _repeat_counts_by_sample(
        session,
        [row["sample_uuid"] for row in sample_rows],
    )
    sample_structural_counts = await _structural_sample_counts(contexts)

    small_variant_count = 0
    structural_variant_count = 0
    for context in contexts:
        if not context.assembly_name:
            continue
        small_variant_count += await count_family_small_variants(
            context.assembly_name,
            context.family_uuid,
            project_ids=context.project_ids,
        )
        structural_variant_count += await count_family_structural_variants(
            context.assembly_name,
            context.family_uuid,
            project_ids=context.project_ids,
        )

    family_track_counts = _empty_track_counts(FAMILY_TRACK_TYPES)
    family_track_counts.update(
        interval_counts.get(family_row["family_uuid"], _empty_track_counts(BED_TRACK_TYPES))
    )
    family_track_counts["small_variants"] = small_variant_count
    family_track_counts["structural_variants"] = structural_variant_count
    family_track_counts["repeat_expansions"] = repeat_counts.get(family_row["family_uuid"], 0)

    samples: list[SampleInventoryOut] = []
    for sample_row in sample_rows:
        track_counts = _empty_track_counts(SAMPLE_TRACK_TYPES)
        track_counts.update(
            sample_interval_counts.get(sample_row["sample_uuid"], _empty_track_counts(BED_TRACK_TYPES))
        )
        track_counts["structural_variants"] = sample_structural_counts.get(sample_row["sample_uuid"], 0)
        track_counts["repeat_expansions"] = sample_repeat_counts.get(sample_row["sample_uuid"], 0)
        samples.append(
            SampleInventoryOut(
                sample_id=sample_row["sample_id"],
                role=sample_row["role"],
                affected=bool(sample_row["affected"]),
                sex=sample_row["sex"],
                projects=_string_list(family_row.get("project_ids")),
                track_counts=track_counts,
                total_records=sum(track_counts.values()),
            )
        )

    return FamilyInventoryDetailOut(
        family_id=family_row["family_id"],
        metadata=family_row.get("metadata") or {},
        projects=_string_list(family_row.get("project_ids")),
        sample_count=len(sample_rows),
        track_counts=family_track_counts,
        total_records=sum(family_track_counts.values()),
        samples=samples,
    )


async def update_sample_projects_data(
    session: AsyncSession,
    sample_id: str,
    update: ProjectsUpdate,
) -> dict[str, Any]:
    result = await session.execute(
        text("SELECT 1 FROM samples WHERE sample_id = :sample_id"),
        {"sample_id": sample_id},
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Sample not found")
    raise HTTPException(
        status_code=400,
        detail="Samples inherit project access from their family",
    )


async def list_clickhouse_variant_status(
    session: AsyncSession,
) -> ClickHouseVariantAssemblyListOut:
    assembly_result = await session.execute(
        text(
            """
            SELECT DISTINCT assembly_name
            FROM assemblies
            WHERE assembly_name IS NOT NULL
            ORDER BY assembly_name
            """
        )
    )
    known_assemblies = {
        str(assembly_name)
        for (assembly_name,) in assembly_result.all()
        if str(assembly_name or "").strip()
    }
    known_assemblies.update(await list_clickhouse_variant_assemblies())
    statuses = [
        ClickHouseVariantAssemblyStatusOut.model_validate(
            await get_clickhouse_variant_storage_status(assembly_name)
        )
        for assembly_name in sorted(known_assemblies)
    ]
    return ClickHouseVariantAssemblyListOut(assemblies=statuses)


async def ensure_clickhouse_variant_status(
    assembly_name: str,
) -> ClickHouseVariantAssemblyStatusOut:
    return ClickHouseVariantAssemblyStatusOut.model_validate(
        await ensure_clickhouse_variant_storage_ready(assembly_name)
    )


async def optimize_clickhouse_variant_status(
    assembly_name: str,
    *,
    final: bool = False,
) -> ClickHouseVariantAssemblyStatusOut:
    return ClickHouseVariantAssemblyStatusOut.model_validate(
        await optimize_clickhouse_variant_tables(assembly_name, final=final)
    )


async def list_data_inventory(
    session: AsyncSession,
) -> list[dict[str, Any]]:
    page = await list_data_inventory_page(session, page=1, page_size=10_000)
    families: list[dict[str, Any]] = []
    for summary in page.items:
        detail = await get_family_data_inventory_detail(session, family_id=summary.family_id)
        payload = detail.model_dump()
        payload["data"] = _track_presence(payload["track_counts"])
        for sample in payload["samples"]:
            sample["data"] = _track_presence(sample["track_counts"])
        families.append(payload)
    return families


async def _sample_row_or_404(session: AsyncSession, sample_id: str) -> dict[str, Any]:
    result = await session.execute(
        text(
            """
            SELECT
                s.id::text AS sample_uuid,
                s.sample_id,
                s.family_id::text AS family_uuid,
                f.family_id
            FROM samples s
            JOIN families f ON f.id = s.family_id
            WHERE s.sample_id = :sample_id
            """
        ),
        {"sample_id": sample_id},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Sample not found")
    return dict(row)


async def _delete_interval_track_type(
    session: AsyncSession,
    *,
    assembly_names: list[str],
    sample_uuid: str,
    data_type: str,
) -> int:
    for assembly_name in assembly_names:
        await delete_interval_tracks(
            assembly_name,
            sample_uuid=sample_uuid,
            track_type=data_type,
        )
    return await delete_interval_track_sources(
        session,
        sample_uuid=sample_uuid,
        track_type=data_type,
    )


async def delete_sample_data_by_type(
    session: AsyncSession,
    sample_id: str,
    data_type: str,
    confirm: bool,
) -> dict[str, Any]:
    sample_row = await _sample_row_or_404(session, sample_id)
    if not confirm:
        raise HTTPException(status_code=400, detail="Confirmation required")
    if data_type in BED_TRACK_TYPES:
        sample_rows = (await _sample_rows_by_family(session, [sample_row["family_uuid"]]))[sample_row["family_uuid"]]
        contexts = await _family_assembly_contexts(
            session,
            family_uuid=sample_row["family_uuid"],
            family_id=sample_row["family_id"],
            sample_rows=sample_rows,
        )
        deleted = await _delete_interval_track_type(
            session,
            assembly_names=[context.assembly_name for context in contexts if context.assembly_name],
            sample_uuid=sample_row["sample_uuid"],
            data_type=data_type,
        )
        await session.commit()
        return {"deleted": deleted}
    if data_type == "repeat_expansions":
        result = await session.execute(
            text(
                """
                DELETE FROM repeat_expansions
                WHERE sample_id = CAST(:sample_uuid AS uuid)
                RETURNING id
                """
            ),
            {"sample_uuid": sample_row["sample_uuid"]},
        )
        await session.commit()
        return {"deleted": len(result.fetchall())}
    if data_type == "structural_variants":
        sample_rows = (await _sample_rows_by_family(session, [sample_row["family_uuid"]]))[sample_row["family_uuid"]]
        contexts = await _family_assembly_contexts(
            session,
            family_uuid=sample_row["family_uuid"],
            family_id=sample_row["family_id"],
            sample_rows=sample_rows,
        )
        replacements = await _structural_variant_records_without_sample(contexts, sample_row["sample_id"])
        before = 0
        for context, records in replacements:
            if not context.assembly_name:
                continue
            before += await count_family_structural_variants(
                context.assembly_name,
                context.family_uuid,
                project_ids=context.project_ids,
            )
            await replace_family_structural_variants(
                context.assembly_name,
                context.family_uuid,
                context.project_ids,
                records,
            )
        after = 0
        for context, _records in replacements:
            if not context.assembly_name:
                continue
            after += await count_family_structural_variants(
                context.assembly_name,
                context.family_uuid,
                project_ids=context.project_ids,
            )
        await session.commit()
        return {"deleted": max(before - after, 0)}
    raise HTTPException(status_code=400, detail="Invalid data type")


async def delete_family_data_by_type(
    session: AsyncSession,
    family_id: str,
    data_type: str,
    confirm: bool,
) -> dict[str, Any]:
    if data_type != "small_variants":
        raise HTTPException(status_code=400, detail="Invalid data type")
    if not confirm:
        raise HTTPException(status_code=400, detail="Confirmation required")
    detail = await get_family_data_inventory_detail(session, family_id=family_id)
    result = await session.execute(
        text("SELECT id::text AS family_uuid FROM families WHERE family_id = :family_id"),
        {"family_id": family_id},
    )
    family_uuid = result.scalar_one_or_none()
    if family_uuid is None:
        raise HTTPException(status_code=404, detail="Family not found")
    sample_rows = (await _sample_rows_by_family(session, [family_uuid]))[family_uuid]
    contexts = await _family_assembly_contexts(
        session,
        family_uuid=family_uuid,
        family_id=family_id,
        sample_rows=sample_rows,
    )
    deleted = 0
    for context in contexts:
        if not context.assembly_name:
            continue
        deleted += await count_family_small_variants(
            context.assembly_name,
            context.family_uuid,
            project_ids=context.project_ids,
        )
        await delete_family_small_variants(context.assembly_name, context.family_uuid)
    await session.commit()
    return {"deleted": deleted}


async def delete_sample_with_data(
    session: AsyncSession,
    sample_id: str,
    confirm: bool,
) -> dict[str, Any]:
    sample_row = await _sample_row_or_404(session, sample_id)
    if not confirm:
        raise HTTPException(status_code=400, detail="Confirmation required")
    sample_rows = (await _sample_rows_by_family(session, [sample_row["family_uuid"]]))[sample_row["family_uuid"]]
    contexts = await _family_assembly_contexts(
        session,
        family_uuid=sample_row["family_uuid"],
        family_id=sample_row["family_id"],
        sample_rows=sample_rows,
    )

    bed_deleted = 0
    for context in contexts:
        if not context.assembly_name:
            continue
        await delete_interval_tracks(
            context.assembly_name,
            sample_uuid=sample_row["sample_uuid"],
        )
    bed_deleted = await delete_interval_track_sources(
        session,
        sample_uuid=sample_row["sample_uuid"],
    )
    repeat_deleted_result = await session.execute(
        text(
            """
            DELETE FROM repeat_expansions
            WHERE sample_id = CAST(:sample_uuid AS uuid)
            RETURNING id
            """
        ),
        {"sample_uuid": sample_row["sample_uuid"]},
    )

    small_replacements = await _small_variant_records_without_sample(contexts, sample_row["sample_id"])
    structural_replacements = await _structural_variant_records_without_sample(
        contexts,
        sample_row["sample_id"],
    )
    small_before = 0
    structural_before = 0
    for context, records in small_replacements:
        if not context.assembly_name:
            continue
        small_before += await count_family_small_variants(
            context.assembly_name,
            context.family_uuid,
            project_ids=context.project_ids,
        )
        await replace_family_small_variants(
            context.assembly_name,
            context.family_uuid,
            context.project_ids,
            records,
        )
    for context, records in structural_replacements:
        if not context.assembly_name:
            continue
        structural_before += await count_family_structural_variants(
            context.assembly_name,
            context.family_uuid,
            project_ids=context.project_ids,
        )
        await replace_family_structural_variants(
            context.assembly_name,
            context.family_uuid,
            context.project_ids,
            records,
        )
    small_after = 0
    structural_after = 0
    for context in contexts:
        if not context.assembly_name:
            continue
        small_after += await count_family_small_variants(
            context.assembly_name,
            context.family_uuid,
            project_ids=context.project_ids,
        )
        structural_after += await count_family_structural_variants(
            context.assembly_name,
            context.family_uuid,
            project_ids=context.project_ids,
        )

    await session.execute(
        text("DELETE FROM samples WHERE id = CAST(:sample_uuid AS uuid)"),
        {"sample_uuid": sample_row["sample_uuid"]},
    )
    await session.commit()
    return {
        "deleted": {
            "samples": 1,
            "bed": bed_deleted,
            "structural_variants": max(structural_before - structural_after, 0),
            "small_variants": max(small_before - small_after, 0),
            "repeat_expansions": len(repeat_deleted_result.fetchall()),
        }
    }


async def delete_family_with_data(
    session: AsyncSession,
    family_id: str,
    confirm: bool,
) -> dict[str, Any]:
    if not confirm:
        raise HTTPException(status_code=400, detail="Confirmation required")
    result = await session.execute(
        text("SELECT id::text AS family_uuid FROM families WHERE family_id = :family_id"),
        {"family_id": family_id},
    )
    family_uuid = result.scalar_one_or_none()
    if family_uuid is None:
        raise HTTPException(status_code=404, detail="Family not found")
    sample_rows = (await _sample_rows_by_family(session, [family_uuid]))[family_uuid]
    contexts = await _family_assembly_contexts(
        session,
        family_uuid=family_uuid,
        family_id=family_id,
        sample_rows=sample_rows,
    )

    small_deleted = 0
    structural_deleted = 0
    for context in contexts:
        if not context.assembly_name:
            continue
        small_deleted += await count_family_small_variants(
            context.assembly_name,
            context.family_uuid,
            project_ids=context.project_ids,
        )
        structural_deleted += await count_family_structural_variants(
            context.assembly_name,
            context.family_uuid,
            project_ids=context.project_ids,
        )
        await delete_family_small_variants(context.assembly_name, context.family_uuid)
        await delete_family_structural_variants(context.assembly_name, context.family_uuid)
        await delete_interval_tracks(
            context.assembly_name,
            family_uuid=family_uuid,
        )

    bed_deleted = await delete_interval_track_sources(
        session,
        family_uuid=family_uuid,
    )
    repeat_result = await session.execute(
        text(
            """
            DELETE FROM repeat_expansions
            WHERE family_id = CAST(:family_uuid AS uuid)
            RETURNING id
            """
        ),
        {"family_uuid": family_uuid},
    )
    sample_result = await session.execute(
        text(
            """
            DELETE FROM samples
            WHERE family_id = CAST(:family_uuid AS uuid)
            RETURNING id
            """
        ),
        {"family_uuid": family_uuid},
    )
    family_result = await session.execute(
        text("DELETE FROM families WHERE id = CAST(:family_uuid AS uuid) RETURNING id"),
        {"family_uuid": family_uuid},
    )
    await session.commit()
    return {
        "deleted": {
            "families": len(family_result.fetchall()),
            "samples": len(sample_result.fetchall()),
            "bed": bed_deleted,
            "structural_variants": structural_deleted,
            "small_variants": small_deleted,
            "repeat_expansions": len(repeat_result.fetchall()),
        }
    }
