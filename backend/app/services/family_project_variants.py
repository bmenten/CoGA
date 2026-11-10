"""A family's variants follow it from project to project.

ClickHouse stores a family's small and structural variant rows once per project the family is
linked to (``_normalized_project_ids``: every writer writes each row under each of them, or under
``unassigned`` when it has none), and every reader of the family reads the rows of its
projects only. Linking a family to other projects changed only Postgres: its variants stayed
under the projects it left, where no reader of it looked, so its pages showed none, while the
import had said they were stored.

A change of the family's projects (``metadata_service.update_family_project_assignments``)
therefore runs in two steps, each under the family's variant-write locks:

1. before the new links commit, each project the family is added to gets a copy of its rows
   (:func:`clickhouse_variant_storage.copy_family_variants_to_projects`), which deletes no row
   of a project the family is linked to, so a step that fails leaves the family as it was;
2. once they have committed, the rows under the projects it left go
   (:func:`prune_left_project_variants`).

A project on another assembly than the family's stored variants is refused: its reads look in
that assembly's tables, so the family's variants would show in none of its projects.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.coga_logging import describe_error
from ..core.sql import uuid_list_bindparam, uuid_values
from .clickhouse_variant_storage import (
    copy_family_variants_to_projects,
    family_has_project_scoped_variants,
    prune_family_variant_projects,
)
from .family_variant_write_lock import VARIANT_TYPES, lock_family_variant_writes

logger = logging.getLogger(__name__)


async def linked_project_ids(session: AsyncSession, family_uuid: str) -> list[str] | None:
    """The projects the family is linked to; None when the family is gone."""
    result = await session.execute(
        text(
            """
            SELECT COALESCE(
                ARRAY_AGG(fp.project_id::text ORDER BY fp.project_id::text)
                FILTER (WHERE fp.project_id IS NOT NULL),
                '{}'::text[]
            )
            FROM families f
            LEFT JOIN family_projects fp ON fp.family_id = f.id
            WHERE f.id = CAST(:family_uuid AS uuid)
            GROUP BY f.id
            """
        ),
        {"family_uuid": family_uuid},
    )
    row = result.first()
    return None if row is None else [str(project_id) for project_id in row[0]]


async def _project_assembly_names(session: AsyncSession, project_ids: list[str]) -> set[str]:
    """The assemblies of ``project_ids``, by name."""
    if not project_ids:
        return set()
    result = await session.execute(
        text(
            """
            SELECT DISTINCT a.assembly_name
            FROM projects p
            JOIN assemblies a ON a.id = p.assembly_id
            WHERE p.id IN :project_ids
            """
        ).bindparams(uuid_list_bindparam("project_ids")),
        {"project_ids": uuid_values(project_ids)},
    )
    return {str(name) for name in result.scalars().all()}


async def family_variant_assemblies(
    session: AsyncSession,
    family_uuid: str,
    *,
    linked: list[str],
    project_ids: list[str],
) -> list[str]:
    """The assemblies the family's small and structural variants are stored on, among those of
    its projects before (``linked``) and after the change (``project_ids``). Refused (409) when
    the family is to be linked to a project on another assembly."""
    target_assemblies = await _project_assembly_names(session, project_ids)
    candidates = sorted(await _project_assembly_names(session, linked) | target_assemblies)
    stored = [name for name in candidates if await family_has_project_scoped_variants(name, family_uuid)]
    if target_assemblies and any(target_assemblies != {name} for name in stored):
        raise HTTPException(
            status_code=409,
            detail=(
                f"The family's variants are stored on {', '.join(stored)}. Link it only to "
                "projects on that assembly: in a project on another one its variants would not show."
            ),
        )
    return stored


async def copy_variants_to_projects(
    family_uuid: str,
    assemblies: list[str],
    *,
    linked: list[str],
    project_ids: list[str],
) -> None:
    """The first step: each project of ``project_ids`` gets the family's variants on each of
    ``assemblies``. The caller holds the family's variant-write locks, both types, and commits
    the new links after it."""
    for assembly_name in assemblies:
        await copy_family_variants_to_projects(
            assembly_name,
            family_uuid,
            project_ids=project_ids,
            linked_project_ids=linked,
        )


async def prune_left_project_variants(session: AsyncSession, family_uuid: str, assemblies: list[str]) -> None:
    """The last step, in a transaction of its own once the new links have committed: the
    family's variant rows under the projects it no longer belongs to go. Its projects are read
    again under its variant-write locks, so a change that committed in between decides what
    stays.

    Each project of the family holds every one of its variants by now, so a step that fails
    here leaves only rows no reader looks at: it is logged, and the next change of the
    family's projects removes them."""
    if not assemblies:
        return
    try:
        await lock_family_variant_writes(session, family_uuid, VARIANT_TYPES)
        linked = await linked_project_ids(session, family_uuid)
        if linked is not None:
            for assembly_name in assemblies:
                await prune_family_variant_projects(assembly_name, family_uuid, project_ids=linked)
        await session.commit()
    except Exception as exc:  # noqa: BLE001 - leaves only rows no reader looks at; no values logged
        await session.rollback()
        logger.warning(
            "Could not remove a family's variant rows under the projects it left: %s",
            describe_error(exc),
        )
