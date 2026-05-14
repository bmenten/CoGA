from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.sql import uuid_list_bindparam, uuid_values
from .metadata_service import (
    CurrentUser,
    get_accessible_family_mapping,
    get_accessible_sample_mapping,
)


def _string_list(values: list[Any] | tuple[Any, ...] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        if value is None:
            continue
        text_value = str(value)
        if text_value and text_value not in result:
            result.append(text_value)
    return result


def _visible_project_ids(project_ids: list[str], user: CurrentUser) -> list[str]:
    if user.role == "admin":
        return project_ids
    allowed_project_ids = set(_string_list(user.metadata_project_ids))
    return [project_id for project_id in project_ids if project_id in allowed_project_ids]


@dataclass(slots=True)
class FamilyMetadataContext:
    family_uuid: str
    family_id: str
    project_ids: list[str]
    sample_rows: list[dict[str, Any]]
    sample_uuid_to_name: dict[str, str]
    sample_name_to_uuid: dict[str, str]
    affected_sample_names: list[str]
    assembly_id: str | None
    assembly_name: str | None


@dataclass(slots=True)
class SampleMetadataContext:
    sample_uuid: str
    sample_id: str
    family_uuid: str
    family_id: str
    sex: str
    project_ids: list[str]
    assembly_id: str | None
    assembly_name: str | None


async def _resolve_family_assembly(
    session: AsyncSession,
    *,
    family_uuid: str,
    project_id: str | None = None,
    project_ids: list[str] | None = None,
) -> tuple[str | None, str | None]:
    if project_id is None:
        if project_ids is None:
            result = await session.execute(
                text(
                    """
                    SELECT DISTINCT
                        a.id::text AS assembly_id,
                        a.assembly_name
                    FROM family_projects fp
                    JOIN projects p ON p.id = fp.project_id
                    JOIN assemblies a ON a.id = p.assembly_id
                    WHERE fp.family_id = CAST(:family_uuid AS uuid)
                    ORDER BY a.assembly_name
                    """
                ),
                {"family_uuid": family_uuid},
            )
        else:
            if not project_ids:
                return None, None
            result = await session.execute(
                text(
                    """
                    SELECT DISTINCT
                        a.id::text AS assembly_id,
                        a.assembly_name
                    FROM family_projects fp
                    JOIN projects p ON p.id = fp.project_id
                    JOIN assemblies a ON a.id = p.assembly_id
                    WHERE fp.family_id = CAST(:family_uuid AS uuid)
                      AND fp.project_id IN :project_ids
                    ORDER BY a.assembly_name
                    """
                ).bindparams(uuid_list_bindparam("project_ids")),
                {"family_uuid": family_uuid, "project_ids": uuid_values(project_ids)},
            )
    else:
        result = await session.execute(
            text(
                """
                SELECT
                    a.id::text AS assembly_id,
                    a.assembly_name
                FROM projects p
                JOIN assemblies a ON a.id = p.assembly_id
                WHERE p.id = CAST(:project_id AS uuid)
                """
            ),
            {"project_id": project_id},
        )
    rows = [dict(row) for row in result.mappings().all()]
    if not rows:
        return None, None
    if len(rows) > 1:
        return None, None
    return rows[0]["assembly_id"], rows[0]["assembly_name"]


async def build_family_metadata_context(
    session: AsyncSession,
    *,
    family_identifier: str,
    user: CurrentUser,
    project_id: str | None = None,
) -> FamilyMetadataContext:
    family_row = await get_accessible_family_mapping(session, family_identifier, user)
    family_uuid = str(family_row["id"])
    project_ids = _visible_project_ids(_string_list(family_row.get("project_ids")), user)

    if project_id is not None and project_id not in set(project_ids):
        raise HTTPException(status_code=400, detail="Project is not linked to this family")

    sample_result = await session.execute(
        text(
            """
            SELECT
                s.id::text AS sample_uuid,
                s.sample_id,
                s.sex,
                fm.role,
                fm.affected
            FROM family_members fm
            JOIN samples s ON s.id = fm.sample_id
            WHERE fm.family_id = CAST(:family_uuid AS uuid)
            ORDER BY lower(s.sample_id)
            """
        ),
        {"family_uuid": family_uuid},
    )
    sample_rows = [dict(row) for row in sample_result.mappings().all()]
    sample_uuid_to_name = {row["sample_uuid"]: row["sample_id"] for row in sample_rows}
    sample_name_to_uuid = {row["sample_id"]: row["sample_uuid"] for row in sample_rows}
    affected_sample_names = [
        row["sample_id"] for row in sample_rows if bool(row.get("affected"))
    ]
    assembly_id, assembly_name = await _resolve_family_assembly(
        session,
        family_uuid=family_uuid,
        project_id=project_id,
        project_ids=None if project_id is not None else project_ids,
    )
    return FamilyMetadataContext(
        family_uuid=family_uuid,
        family_id=str(family_row["family_id"]),
        project_ids=project_ids,
        sample_rows=sample_rows,
        sample_uuid_to_name=sample_uuid_to_name,
        sample_name_to_uuid=sample_name_to_uuid,
        affected_sample_names=affected_sample_names,
        assembly_id=assembly_id,
        assembly_name=assembly_name,
    )


async def build_sample_metadata_context(
    session: AsyncSession,
    *,
    sample_identifier: str,
    user: CurrentUser,
) -> SampleMetadataContext:
    sample_mapping = await get_accessible_sample_mapping(session, sample_identifier, user)
    sample_uuid = str(sample_mapping["id"])
    result = await session.execute(
        text(
            """
            SELECT
                s.id::text AS sample_uuid,
                s.sample_id,
                s.sex,
                f.id::text AS family_uuid,
                f.family_id,
                COALESCE(
                    ARRAY_AGG(DISTINCT sp.project_id::text)
                    FILTER (WHERE sp.project_id IS NOT NULL),
                    '{}'::text[]
                ) AS project_ids
            FROM samples s
            JOIN families f ON f.id = s.family_id
            LEFT JOIN sample_projects sp ON sp.sample_id = s.id
            WHERE s.id = CAST(:sample_uuid AS uuid)
            GROUP BY s.id, f.id
            """
        ),
        {"sample_uuid": sample_uuid},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Sample not found")

    project_ids = _visible_project_ids(_string_list(row["project_ids"]), user)
    assembly_id, assembly_name = await _resolve_family_assembly(
        session,
        family_uuid=str(row["family_uuid"]),
        project_id=None if len(project_ids) != 1 else project_ids[0],
        project_ids=None if len(project_ids) == 1 else project_ids,
    )
    return SampleMetadataContext(
        sample_uuid=str(row["sample_uuid"]),
        sample_id=row["sample_id"],
        family_uuid=str(row["family_uuid"]),
        family_id=row["family_id"],
        sex=row["sex"],
        project_ids=project_ids,
        assembly_id=assembly_id,
        assembly_name=assembly_name,
    )
