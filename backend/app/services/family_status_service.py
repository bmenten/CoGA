"""Admin-managed family status catalogue and per-family workflow metadata.

The status catalogue is a small lookup table (``family_statuses``) that admins
curate from the admin dashboard. Individual families reference a status by UUID
(``families.status_id``) plus optional ``assigned_to`` / ``reviewed_by`` users,
all editable from the family-detail page by any authenticated user with access
to the family.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.sql import canonical_uuid, require_uuid
from ..schemas import (
    FamilyMetadataUpdate,
    FamilyOut,
    FamilyStatusCreate,
    FamilyStatusOut,
    FamilyStatusUpdate,
    UserRefOut,
)
from .metadata_service import get_accessible_family_mapping, get_family_record
from .access_control import CurrentUser

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_DEFAULT_STATUS_COLOR = "#5b6b79"


def _normalize_color(color: str | None) -> str:
    candidate = (color or "").strip()
    if _HEX_COLOR_RE.match(candidate):
        return candidate.lower()
    return _DEFAULT_STATUS_COLOR


def _slugify_status(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", label.strip().lower()).strip("_")
    return slug or "status"


def _status_out_from_row(row: dict[str, Any]) -> FamilyStatusOut:
    return FamilyStatusOut(
        id=str(row["id"]),
        key=row["key"],
        label=row["label"],
        description=row.get("description"),
        color=row["color"],
        sort_order=int(row["sort_order"]),
        is_active=bool(row["is_active"]),
    )


async def list_family_statuses(
    session: AsyncSession,
    *,
    include_inactive: bool = False,
) -> list[FamilyStatusOut]:
    where_clause = "" if include_inactive else "WHERE is_active"
    result = await session.execute(
        text(
            f"""
            SELECT id::text AS id, key, label, description, color, sort_order, is_active
            FROM family_statuses
            {where_clause}
            ORDER BY sort_order, lower(label)
            """
        )
    )
    return [_status_out_from_row(dict(row)) for row in result.mappings().all()]


async def _unique_status_key(session: AsyncSession, label: str) -> str:
    base = _slugify_status(label)
    candidate = base
    suffix = 2
    while True:
        existing = await session.execute(
            text("SELECT 1 FROM family_statuses WHERE key = :key"),
            {"key": candidate},
        )
        if existing.scalar_one_or_none() is None:
            return candidate
        candidate = f"{base}_{suffix}"
        suffix += 1


async def create_family_status(
    session: AsyncSession,
    *,
    payload: FamilyStatusCreate,
    user: CurrentUser,
) -> FamilyStatusOut:
    label = payload.label.strip()
    if not label:
        raise HTTPException(status_code=400, detail="Status label is required")
    key = await _unique_status_key(session, label)

    sort_order = payload.sort_order
    if sort_order is None:
        max_row = await session.execute(
            text("SELECT COALESCE(MAX(sort_order), 0) AS max_sort FROM family_statuses")
        )
        sort_order = int(max_row.scalar_one()) + 10

    now = datetime.now(timezone.utc)
    created_by = canonical_uuid(user.id)
    result = await session.execute(
        text(
            """
            INSERT INTO family_statuses (
                key, label, description, color, sort_order, is_active,
                created_by, created_at, updated_at
            )
            VALUES (
                :key, :label, :description, :color, :sort_order, TRUE,
                CAST(:created_by AS uuid), :created_at, :updated_at
            )
            RETURNING id::text AS id, key, label, description, color, sort_order, is_active
            """
        ),
        {
            "key": key,
            "label": label,
            "description": (payload.description or "").strip() or None,
            "color": _normalize_color(payload.color),
            "sort_order": int(sort_order),
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        },
    )
    mapping = result.mappings().one()
    await session.commit()
    return _status_out_from_row(dict(mapping))


async def update_family_status(
    session: AsyncSession,
    *,
    key: str,
    payload: FamilyStatusUpdate,
) -> FamilyStatusOut:
    fields = payload.model_fields_set
    assignments: list[str] = ["updated_at = :updated_at"]
    params: dict[str, Any] = {"key": key, "updated_at": datetime.now(timezone.utc)}

    if "label" in fields and payload.label is not None:
        label = payload.label.strip()
        if not label:
            raise HTTPException(status_code=400, detail="Status label cannot be empty")
        assignments.append("label = :label")
        params["label"] = label
    if "description" in fields:
        assignments.append("description = :description")
        params["description"] = (payload.description or "").strip() or None
    if "color" in fields:
        assignments.append("color = :color")
        params["color"] = _normalize_color(payload.color)
    if "sort_order" in fields and payload.sort_order is not None:
        assignments.append("sort_order = :sort_order")
        params["sort_order"] = int(payload.sort_order)
    if "is_active" in fields and payload.is_active is not None:
        assignments.append("is_active = :is_active")
        params["is_active"] = bool(payload.is_active)

    result = await session.execute(
        text(
            f"""
            UPDATE family_statuses
            SET {", ".join(assignments)}
            WHERE key = :key
            RETURNING id::text AS id, key, label, description, color, sort_order, is_active
            """
        ),
        params,
    )
    mapping = result.mappings().one_or_none()
    if mapping is None:
        raise HTTPException(status_code=404, detail="Family status not found")
    await session.commit()
    return _status_out_from_row(dict(mapping))


async def delete_family_status(session: AsyncSession, *, key: str) -> None:
    # ON DELETE SET NULL on families.status_id clears the status from any family
    # that referenced it, so deleting is non-destructive to family rows.
    result = await session.execute(
        text("DELETE FROM family_statuses WHERE key = :key RETURNING id"),
        {"key": key},
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Family status not found")
    await session.commit()


async def list_assignable_users(session: AsyncSession) -> list[UserRefOut]:
    result = await session.execute(
        text(
            """
            SELECT id::text AS id, username, email, first_name, last_name
            FROM users
            WHERE is_active
            ORDER BY lower(first_name), lower(last_name), lower(username)
            """
        )
    )
    return [
        UserRefOut(
            id=row["id"],
            username=row["username"],
            email=row["email"],
            first_name=row.get("first_name") or "",
            last_name=row.get("last_name") or "",
        )
        for row in result.mappings().all()
    ]


async def _resolve_status_id(session: AsyncSession, status_key: str) -> str:
    result = await session.execute(
        text("SELECT id::text AS id FROM family_statuses WHERE key = :key"),
        {"key": status_key},
    )
    status_id = result.scalar_one_or_none()
    if status_id is None:
        raise HTTPException(status_code=404, detail=f"Unknown family status: {status_key}")
    return status_id


async def _validate_user_id(session: AsyncSession, user_id: str) -> str:
    user_uuid = require_uuid(user_id, "Invalid user id")
    result = await session.execute(
        text("SELECT id::text AS id FROM users WHERE id = CAST(:id AS uuid)"),
        {"id": user_uuid},
    )
    resolved = result.scalar_one_or_none()
    if resolved is None:
        raise HTTPException(status_code=404, detail="User not found")
    return resolved


async def update_family_metadata_for_user(
    session: AsyncSession,
    *,
    family_id: str,
    update: FamilyMetadataUpdate,
    user: CurrentUser,
) -> FamilyOut:
    # 404 if the family is unknown or outside the user's projects.
    family_row = await get_accessible_family_mapping(session, family_id, user)
    family_uuid = family_row["id"]

    fields = update.model_fields_set
    assignments: list[str] = []
    params: dict[str, Any] = {"family_uuid": family_uuid}

    if "status_key" in fields:
        status_id = (
            await _resolve_status_id(session, update.status_key) if update.status_key else None
        )
        assignments.append("status_id = CAST(:status_id AS uuid)")
        params["status_id"] = status_id
    if "assigned_to" in fields:
        assigned_id = (
            await _validate_user_id(session, update.assigned_to) if update.assigned_to else None
        )
        assignments.append("assigned_to_id = CAST(:assigned_to_id AS uuid)")
        params["assigned_to_id"] = assigned_id
    if "reviewed_by" in fields:
        reviewed_id = (
            await _validate_user_id(session, update.reviewed_by) if update.reviewed_by else None
        )
        assignments.append("reviewed_by_id = CAST(:reviewed_by_id AS uuid)")
        params["reviewed_by_id"] = reviewed_id

    if assignments:
        await session.execute(
            text(
                f"UPDATE families SET {', '.join(assignments)} "
                "WHERE id = CAST(:family_uuid AS uuid)"
            ),
            params,
        )
        await session.commit()

    return await get_family_record(session, family_id, user)
