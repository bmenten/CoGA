from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import (
    SmallVariantFilterPresetCreate,
    SmallVariantFilterPresetOut,
)
from .access_control import CurrentUser
from .review_pg_utils import _json_payload, _require_uuid


def _serialize_preset(document: dict[str, Any]) -> SmallVariantFilterPresetOut:
    return SmallVariantFilterPresetOut(
        id=str(document["id"]),
        family_id=str(document["family_id"]) if document.get("family_id") else None,
        scope=document["scope"],
        owner=document["owner"],
        name=document["name"],
        description=document.get("description"),
        filters=document.get("filters", {}),
        sample_filters=document.get("sample_filters", {}),
        sample_templates=document.get("sample_templates", {}),
        created_at=document["created_at"],
        updated_at=document["updated_at"],
    )


async def list_small_variant_filter_presets(
    session: AsyncSession,
    *,
    family_uuid: str,
    user: CurrentUser,
) -> list[SmallVariantFilterPresetOut]:
    result = await session.execute(
        text(
            """
            SELECT
                id::text AS id,
                family_id::text AS family_id,
                scope,
                owner,
                name,
                description,
                filters,
                sample_filters,
                sample_templates,
                created_at,
                updated_at
            FROM small_variant_filter_presets
            WHERE (scope = 'family' AND family_id = CAST(:family_id AS uuid) AND owner = :owner)
               OR (scope = 'global' AND owner = :owner)
            ORDER BY
                CASE WHEN scope = 'family' THEN 0 ELSE 1 END,
                lower(name)
            """
        ),
        {"family_id": family_uuid, "owner": user.username},
    )
    return [_serialize_preset(dict(row)) for row in result.mappings().all()]


async def list_small_variant_filter_presets_for_owner(
    session: AsyncSession,
    *,
    user: CurrentUser,
) -> list[SmallVariantFilterPresetOut]:
    result = await session.execute(
        text(
            """
            SELECT
                id::text AS id,
                family_id::text AS family_id,
                scope,
                owner,
                name,
                description,
                filters,
                sample_filters,
                sample_templates,
                created_at,
                updated_at
            FROM small_variant_filter_presets
            WHERE owner = :owner
            ORDER BY
                CASE WHEN scope = 'global' THEN 0 ELSE 1 END,
                lower(name),
                COALESCE(family_id::text, '')
            """
        ),
        {"owner": user.username},
    )
    return [_serialize_preset(dict(row)) for row in result.mappings().all()]


async def list_small_variant_filter_presets_for_admin(
    session: AsyncSession,
) -> list[SmallVariantFilterPresetOut]:
    result = await session.execute(
        text(
            """
            SELECT
                id::text AS id,
                family_id::text AS family_id,
                scope,
                owner,
                name,
                description,
                filters,
                sample_filters,
                sample_templates,
                created_at,
                updated_at
            FROM small_variant_filter_presets
            ORDER BY lower(owner), CASE WHEN scope = 'global' THEN 0 ELSE 1 END, lower(name), COALESCE(family_id::text, '')
            """
        )
    )
    return [_serialize_preset(dict(row)) for row in result.mappings().all()]


async def save_small_variant_filter_preset(
    session: AsyncSession,
    *,
    family_uuid: str,
    payload: SmallVariantFilterPresetCreate,
    user: CurrentUser,
) -> SmallVariantFilterPresetOut:
    normalized_name = payload.name.strip()
    if not normalized_name:
        raise HTTPException(status_code=400, detail="Preset name cannot be blank")

    now = datetime.now(timezone.utc)
    scoped_family_uuid = family_uuid if payload.scope == "family" else None
    result = await session.execute(
        text(
            """
            SELECT id::text AS id
            FROM small_variant_filter_presets
            WHERE scope = :scope
              AND owner = :owner
              AND name = :name
              AND (
                    (CAST(:family_id AS uuid) IS NULL AND family_id IS NULL)
                 OR family_id = CAST(:family_id AS uuid)
              )
            """
        ),
        {
            "scope": payload.scope,
            "owner": user.username,
            "name": normalized_name,
            "family_id": scoped_family_uuid,
        },
    )
    existing_id = result.scalar_one_or_none()
    params = {
        "family_id": scoped_family_uuid,
        "scope": payload.scope,
        "owner": user.username,
        "name": normalized_name,
        "description": (payload.description or "").strip() or None,
        "filters": payload.filters,
        "sample_filters": payload.sample_filters,
        "sample_templates": payload.sample_templates,
        "updated_at": now,
    }
    if existing_id is not None:
        await session.execute(
            text(
                """
                UPDATE small_variant_filter_presets
                SET
                    description = :description,
                    filters = CAST(:filters_json AS jsonb),
                    sample_filters = CAST(:sample_filters_json AS jsonb),
                    sample_templates = CAST(:sample_templates_json AS jsonb),
                    updated_at = :updated_at
                WHERE id = CAST(:preset_id AS uuid)
                """
            ),
            {
                **params,
                "filters_json": _json_payload(payload.filters),
                "sample_filters_json": _json_payload(payload.sample_filters),
                "sample_templates_json": _json_payload(payload.sample_templates),
                "preset_id": existing_id,
            },
        )
    else:
        await session.execute(
            text(
                """
                INSERT INTO small_variant_filter_presets (
                    family_id,
                    scope,
                    owner,
                    name,
                    description,
                    filters,
                    sample_filters,
                    sample_templates,
                    created_at,
                    updated_at
                )
                VALUES (
                    CAST(:family_id AS uuid),
                    :scope,
                    :owner,
                    :name,
                    :description,
                    CAST(:filters_json AS jsonb),
                    CAST(:sample_filters_json AS jsonb),
                    CAST(:sample_templates_json AS jsonb),
                    :created_at,
                    :updated_at
                )
                """
            ),
            {
                **params,
                "filters_json": _json_payload(payload.filters),
                "sample_filters_json": _json_payload(payload.sample_filters),
                "sample_templates_json": _json_payload(payload.sample_templates),
                "created_at": now,
            },
        )
    await session.commit()

    refreshed = await session.execute(
        text(
            """
            SELECT
                id::text AS id,
                family_id::text AS family_id,
                scope,
                owner,
                name,
                description,
                filters,
                sample_filters,
                sample_templates,
                created_at,
                updated_at
            FROM small_variant_filter_presets
            WHERE scope = :scope
              AND owner = :owner
              AND name = :name
              AND (
                    (CAST(:family_id AS uuid) IS NULL AND family_id IS NULL)
                 OR family_id = CAST(:family_id AS uuid)
              )
            """
        ),
        {
            "scope": payload.scope,
            "owner": user.username,
            "name": normalized_name,
            "family_id": scoped_family_uuid,
        },
    )
    row = refreshed.mappings().first()
    if row is None:
        raise HTTPException(status_code=500, detail="Preset update failed")
    return _serialize_preset(dict(row))


async def delete_small_variant_filter_preset(
    session: AsyncSession,
    *,
    family_uuid: str,
    preset_id: str,
    user: CurrentUser,
) -> None:
    preset_uuid = _require_uuid(preset_id, "Preset not found")
    result = await session.execute(
        text(
            """
            SELECT owner, scope, family_id::text AS family_id
            FROM small_variant_filter_presets
            WHERE id = CAST(:preset_id AS uuid)
            """
        ),
        {"preset_id": preset_uuid},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Preset not found")
    if row["owner"] != user.username:
        raise HTTPException(status_code=403, detail="Not authorized to delete this preset")
    if row["scope"] == "family" and row["family_id"] != family_uuid:
        raise HTTPException(status_code=404, detail="Preset not found")
    await session.execute(
        text("DELETE FROM small_variant_filter_presets WHERE id = CAST(:preset_id AS uuid)"),
        {"preset_id": preset_uuid},
    )
    await session.commit()


async def delete_small_variant_filter_preset_for_owner(
    session: AsyncSession,
    *,
    preset_id: str,
    user: CurrentUser,
) -> None:
    preset_uuid = _require_uuid(preset_id, "Preset not found")
    result = await session.execute(
        text(
            """
            SELECT owner
            FROM small_variant_filter_presets
            WHERE id = CAST(:preset_id AS uuid)
            """
        ),
        {"preset_id": preset_uuid},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Preset not found")
    if row["owner"] != user.username:
        raise HTTPException(status_code=403, detail="Not authorized to delete this preset")
    await session.execute(
        text("DELETE FROM small_variant_filter_presets WHERE id = CAST(:preset_id AS uuid)"),
        {"preset_id": preset_uuid},
    )
    await session.commit()
