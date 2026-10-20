"""Admin-managed family status catalogue and per-family workflow metadata.

The status catalogue is a small lookup table (``family_statuses``) that admins
curate from the admin dashboard. Individual families reference a status by UUID
(``families.status_id``) plus optional ``assigned_to`` / ``reviewed_by`` users,
all editable from the family-detail page by an analyst of the family's projects or
an admin (a reader is refused at the family checkpoint, SEC-1).

The case is assigned to, and recorded as reviewed by, someone who may work on it: an
active analyst of one of the family's projects, or an active administrator. The pickers
offer only them (``list_family_assignable_users``) and a save that names anyone else is
refused (422); a value the family already holds stays until it is changed, even when its
user has since become a reader.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Mapping

from fastapi import HTTPException
from sqlalchemy import String, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.sql import canonical_uuid, require_uuid, uuid_list_bindparam, uuid_values
from ..schemas import (
    FamilyMetadataUpdate,
    FamilyOut,
    FamilyStatusCreate,
    FamilyStatusOut,
    FamilyStatusUpdate,
    UserRefOut,
)
from .metadata_service import get_accessible_family_mapping, get_family_record
from .access_control import ADMIN_ROLES, ANALYST, READER, CurrentUser

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


def _user_ref(row: Mapping[str, Any]) -> UserRefOut:
    return UserRefOut(
        id=row["id"],
        username=row["username"],
        email=row["email"],
        first_name=row.get("first_name") or "",
        last_name=row.get("last_name") or "",
    )


async def list_assignable_users(session: AsyncSession) -> list[UserRefOut]:
    """Every active user (``GET /api/users``). The case pickers read the family's own list,
    ``list_family_assignable_users``."""
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
    return [_user_ref(row) for row in result.mappings().all()]


# The users a family's case may be assigned to, or recorded as reviewed by: every active
# administrator, and every active analyst of one of the family's projects. A reader of the
# projects is not one of them: a reader reads the family and changes nothing (SEC-1).
_FAMILY_ASSIGNABLE_USERS = text(
    """
    SELECT u.id::text AS id, u.username, u.email, u.first_name, u.last_name
    FROM users u
    WHERE u.is_active
      AND (
          u.role IN :admin_roles
          OR EXISTS (
              SELECT 1
              FROM project_users pu
              WHERE pu.user_id = u.id
                AND pu.access_level = :analyst
                AND pu.project_id IN :project_ids
          )
      )
    ORDER BY lower(u.first_name), lower(u.last_name), lower(u.username), u.id
    """
).bindparams(bindparam("admin_roles", expanding=True, type_=String()), uuid_list_bindparam("project_ids"))

# A user a save names for the case, with what decides whether they may be named: whether
# the account is active, its role, and its access level in each of the family's projects
# it is a member of.
_CANDIDATE_USER = text(
    """
    SELECT
        u.id::text AS id,
        u.username,
        u.email,
        u.first_name,
        u.last_name,
        u.is_active,
        u.role,
        ARRAY(
            SELECT DISTINCT pu.access_level
            FROM project_users pu
            WHERE pu.user_id = u.id
              AND pu.project_id IN :project_ids
        ) AS access_levels
    FROM users u
    WHERE u.id = CAST(:user_id AS uuid)
    """
).bindparams(uuid_list_bindparam("project_ids"))

# What the user a save names would be, per field, in the refusal.
_CASE_ROLE = {"assigned_to": "assigned this case", "reviewed_by": "recorded as this case's reviewer"}
_ELIGIBLE_USERS_HINT = "Choose an analyst of one of the family's projects or an administrator."


def _family_project_ids(family_row: Mapping[str, Any]) -> list[str]:
    return [str(project_id) for project_id in family_row.get("project_ids") or [] if project_id]


async def list_family_assignable_users(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
) -> list[UserRefOut]:
    """The users the family's case may be assigned to or recorded as reviewed by: its pickers'
    list. Answered like the family itself: 404 for a family outside the user's projects."""
    family_row = await get_accessible_family_mapping(session, family_id, user)
    result = await session.execute(
        _FAMILY_ASSIGNABLE_USERS,
        {
            "admin_roles": sorted(ADMIN_ROLES),
            "analyst": ANALYST,
            "project_ids": uuid_values(_family_project_ids(family_row)),
        },
    )
    return [_user_ref(row) for row in result.mappings().all()]


def _shown_name(row: Mapping[str, Any]) -> str:
    name = f"{row.get('first_name') or ''} {row.get('last_name') or ''}".strip()
    return name or str(row.get("username") or row.get("email") or row["id"])


def _why_not_assignable(candidate: Mapping[str, Any]) -> str | None:
    """Why this user may not be named for a family's case, or None when they may: an active
    administrator, or an active analyst of one of the family's projects."""
    if not candidate.get("is_active"):
        return "their account is not active"
    if candidate.get("role") in ADMIN_ROLES:
        return None
    levels = set(candidate.get("access_levels") or [])
    if ANALYST in levels:
        return None
    if READER in levels:
        return "they are a reader in this family's projects"
    return "they are not a member of this family's projects"


async def _assignable_user_id(
    session: AsyncSession,
    family_row: Mapping[str, Any],
    *,
    field: str,
    user_id: str,
) -> str:
    """The user a save names for the case's ``field`` (``assigned_to`` or ``reviewed_by``),
    refused unless the case may be theirs: 404 for an unknown user, 422 saying why for one
    who is not an active analyst of one of the family's projects or an active administrator.

    The user the family already holds there is kept as it is: a save that leaves the field
    as it was is not refused because its user has since become a reader."""
    user_uuid = require_uuid(user_id, "Invalid user id")
    if user_uuid == canonical_uuid(family_row.get(f"{field}_id")):
        return user_uuid
    candidate = (
        await session.execute(
            _CANDIDATE_USER,
            {"user_id": user_uuid, "project_ids": uuid_values(_family_project_ids(family_row))},
        )
    ).mappings().first()
    if candidate is None:
        raise HTTPException(status_code=404, detail="User not found")
    problem = _why_not_assignable(candidate)
    if problem is not None:
        raise HTTPException(
            status_code=422,
            detail=f"{_shown_name(candidate)} cannot be {_CASE_ROLE[field]}: {problem}. {_ELIGIBLE_USERS_HINT}",
        )
    return str(candidate["id"])


async def _resolve_status_id(session: AsyncSession, status_key: str) -> str:
    result = await session.execute(
        text("SELECT id::text AS id FROM family_statuses WHERE key = :key"),
        {"key": status_key},
    )
    status_id = result.scalar_one_or_none()
    if status_id is None:
        raise HTTPException(status_code=404, detail=f"Unknown family status: {status_key}")
    return status_id


async def update_family_metadata_for_user(
    session: AsyncSession,
    *,
    family_id: str,
    update: FamilyMetadataUpdate,
    user: CurrentUser,
) -> FamilyOut:
    # 404 if the family is unknown or outside the user's projects; 403 for a reader.
    family_row = await get_accessible_family_mapping(session, family_id, user)
    family_uuid = family_row["id"]

    fields = update.model_fields_set
    assignments: list[str] = []
    params: dict[str, Any] = {"family_uuid": family_uuid}

    # Every field is checked before anything is written: a refused save changes nothing.
    if "status_key" in fields:
        status_id = (
            await _resolve_status_id(session, update.status_key) if update.status_key else None
        )
        assignments.append("status_id = CAST(:status_id AS uuid)")
        params["status_id"] = status_id
    for field in ("assigned_to", "reviewed_by"):
        if field not in fields:
            continue
        named = getattr(update, field)
        assignments.append(f"{field}_id = CAST(:{field}_id AS uuid)")
        params[f"{field}_id"] = (
            await _assignable_user_id(session, family_row, field=field, user_id=named) if named else None
        )

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
