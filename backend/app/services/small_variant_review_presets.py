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

# A small-variant preset is its owner's, reusable in every family they can open (#681).
_PRESET_COLUMNS = """
    id::text AS id,
    owner,
    name,
    description,
    filters,
    sample_filters,
    sample_templates,
    created_at,
    updated_at
"""


def _serialize_preset(document: dict[str, Any]) -> SmallVariantFilterPresetOut:
    return SmallVariantFilterPresetOut(
        id=str(document["id"]),
        owner=document["owner"],
        name=document["name"],
        description=document.get("description"),
        filters=document.get("filters", {}),
        sample_filters=document.get("sample_filters", {}),
        sample_templates=document.get("sample_templates", {}),
        created_at=document["created_at"],
        updated_at=document["updated_at"],
    )


async def list_small_variant_filter_presets_for_owner(
    session: AsyncSession,
    *,
    user: CurrentUser,
) -> list[SmallVariantFilterPresetOut]:
    result = await session.execute(
        text(
            f"""
            SELECT {_PRESET_COLUMNS}
            FROM small_variant_filter_presets
            WHERE owner = :owner
            ORDER BY lower(name)
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
            f"""
            SELECT {_PRESET_COLUMNS}
            FROM small_variant_filter_presets
            ORDER BY lower(owner), lower(name)
            """
        )
    )
    return [_serialize_preset(dict(row)) for row in result.mappings().all()]


async def save_small_variant_filter_preset(
    session: AsyncSession,
    *,
    payload: SmallVariantFilterPresetCreate,
    user: CurrentUser,
) -> SmallVariantFilterPresetOut:
    """Create the owner's preset of that name, or replace its content if it exists."""
    normalized_name = payload.name.strip()
    if not normalized_name:
        raise HTTPException(status_code=400, detail="Preset name cannot be blank")

    now = datetime.now(timezone.utc)
    params = {
        "owner": user.username,
        "name": normalized_name,
        "description": (payload.description or "").strip() or None,
        "filters_json": _json_payload(payload.filters),
        "sample_filters_json": _json_payload(payload.sample_filters),
        "sample_templates_json": _json_payload(payload.sample_templates),
        "now": now,
    }
    result = await session.execute(
        text(
            f"""
            INSERT INTO small_variant_filter_presets (
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
                :owner,
                :name,
                :description,
                CAST(:filters_json AS jsonb),
                CAST(:sample_filters_json AS jsonb),
                CAST(:sample_templates_json AS jsonb),
                :now,
                :now
            )
            ON CONFLICT (owner, name) DO UPDATE SET
                description = EXCLUDED.description,
                filters = EXCLUDED.filters,
                sample_filters = EXCLUDED.sample_filters,
                sample_templates = EXCLUDED.sample_templates,
                updated_at = EXCLUDED.updated_at
            RETURNING {_PRESET_COLUMNS}
            """
        ),
        params,
    )
    row = result.mappings().first()
    await session.commit()
    if row is None:
        raise HTTPException(status_code=500, detail="Preset update failed")
    return _serialize_preset(dict(row))


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
