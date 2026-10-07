from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import (
    SmallVariantTagDefinitionCreate,
    SmallVariantTagDefinitionOut,
    SmallVariantTagDefinitionUpdate,
)
from .access_control import CurrentUser, is_admin_user


DEFAULT_SMALL_VARIANT_TAGS: list[dict[str, str]] = [
    {
        "key": "review",
        "label": "Review",
        "group": "collaboration",
        "color": "#2563eb",
        "sort_order": "10",
        "description": "Marked for active analyst review.",
    },
    {
        "key": "send_for_validation",
        "label": "Send for validation",
        "group": "collaboration",
        "color": "#b7791f",
        "sort_order": "20",
        "description": "Needs orthogonal validation or confirmation.",
    },
    {
        "key": "validated",
        "label": "Validated",
        "group": "collaboration",
        "color": "#2f855a",
        "sort_order": "30",
        "description": "Variant has been validated successfully.",
    },
    {
        "key": "validation_not_confirmed",
        "label": "Validation did not confirm",
        "group": "collaboration",
        "color": "#7c2034",
        "sort_order": "40",
        "description": "Follow-up validation did not confirm the call.",
    },
    {
        "key": "confident_ar_single_hit",
        "label": "Confident AR single hit",
        "group": "collaboration",
        "color": "#7c3aed",
        "sort_order": "50",
        "description": "Strong recessive single-hit candidate kept for follow-up.",
    },
    {
        "key": "excluded",
        "label": "Excluded",
        "group": "collaboration",
        "color": "#6b7280",
        "sort_order": "60",
        "description": "Reviewed and excluded from reporting. Add a note with the reason.",
    },
    {
        "key": "report",
        "label": "Report",
        "group": "collaboration",
        "color": "#0f766e",
        "sort_order": "70",
        "description": "Selected for the clinical report. Appears on the family report template.",
    },
    {
        "key": "acmg_class_5",
        "label": "Pathogenic - class 5",
        "group": "classification",
        "color": "#b42318",
        "sort_order": "110",
        "description": "ACMG/AMP class 5 pathogenic classification.",
    },
    {
        "key": "acmg_class_4",
        "label": "Likely Pathogenic - class 4",
        "group": "classification",
        "color": "#ea580c",
        "sort_order": "120",
        "description": "ACMG/AMP class 4 likely pathogenic classification.",
    },
    {
        "key": "acmg_class_3",
        "label": "VUS - class 3",
        "group": "classification",
        "color": "#db2777",
        "sort_order": "130",
        "description": "ACMG/AMP class 3 variant of uncertain significance.",
    },
    {
        "key": "acmg_class_2",
        "label": "Likely benign - class 2",
        "group": "classification",
        "color": "#7dd3fc",
        "sort_order": "140",
        "description": "ACMG/AMP class 2 likely benign classification.",
    },
    {
        "key": "acmg_class_1",
        "label": "Benign - class 1",
        "group": "classification",
        "color": "#2563eb",
        "sort_order": "150",
        "description": "ACMG/AMP class 1 benign classification.",
    },
    {
        "key": "acmg_vus_hot",
        "label": "VUS — Hot",
        "group": "classification",
        "color": "#e11d48",
        "sort_order": "131",
        "description": "VUS leaning pathogenic (4–5 points) — MAGI-ACMG hot tier.",
    },
    {
        "key": "acmg_vus_warm",
        "label": "VUS — Warm",
        "group": "classification",
        "color": "#f59e0b",
        "sort_order": "132",
        "description": "VUS with intermediate evidence (2–3 points) — MAGI-ACMG warm tier.",
    },
    {
        "key": "acmg_vus_cold",
        "label": "VUS — Cold",
        "group": "classification",
        "color": "#38bdf8",
        "sort_order": "133",
        "description": "VUS leaning benign / low evidence (0–1 points) — MAGI-ACMG cold tier.",
    },
    {
        "key": "secondary_finding",
        "label": "Secondary finding",
        "group": "classification",
        "color": "#d4a017",
        "sort_order": "160",
        "description": "Potential ACMG secondary finding or incidental reportable finding.",
    },
]


DEFAULT_SMALL_VARIANT_TAG_KEYS = {entry["key"] for entry in DEFAULT_SMALL_VARIANT_TAGS}


def _slugify_tag(label: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", label.strip().lower()).strip("_")
    if not cleaned:
        raise HTTPException(status_code=400, detail="Tag label does not contain usable characters")
    return cleaned


def _normalize_hex_color(color: str | None) -> str:
    value = str(color or "").strip().lower()
    if not re.fullmatch(r"#[0-9a-f]{6}", value):
        raise HTTPException(status_code=400, detail="Tag color must be a 6-digit hex code")
    return value


def _preset_tag_definitions() -> list[SmallVariantTagDefinitionOut]:
    return [
        SmallVariantTagDefinitionOut(
            key=entry["key"],
            label=entry["label"],
            description=entry.get("description"),
            group=entry.get("group", "custom"),  # type: ignore[arg-type]
            color=entry.get("color", "#5b6b79"),
            sort_order=int(entry.get("sort_order", "500")),
            scope="system",
            is_custom=False,
        )
        for entry in DEFAULT_SMALL_VARIANT_TAGS
    ]


def _serialize_custom_tag_definition_row(row: dict[str, Any]) -> SmallVariantTagDefinitionOut:
    scope = str(row.get("scope") or "global")
    project_id = str(row["project_id"]) if row.get("project_id") else None
    shared_project_ids = [
        project for project in _string_list(row.get("shared_project_ids")) if project != project_id
    ]
    return SmallVariantTagDefinitionOut(
        key=row["key"],
        label=row["label"],
        description=row.get("description"),
        group=row.get("group", "custom"),
        color=row.get("color", "#5b6b79"),
        sort_order=int(row.get("sort_order", 500)),
        scope="project" if scope == "project" else "global",
        project_id=project_id,
        shared_project_ids=shared_project_ids,
        is_custom=True,
    )


def _string_list(values: Iterable[Any] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        if value is None:
            continue
        text_value = str(value).strip()
        if text_value and text_value not in result:
            result.append(text_value)
    return result


def _normalize_project_scope_ids(project_ids: Iterable[str] | None) -> list[str]:
    normalized: list[str] = []
    for project_id in project_ids or []:
        candidate = str(project_id).strip()
        if not candidate:
            continue
        try:
            UUID(candidate)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid project id: {candidate}") from exc
        if candidate not in normalized:
            normalized.append(candidate)
    return normalized


async def _ensure_projects_visible(
    session: AsyncSession,
    *,
    project_ids: Iterable[str],
    user: CurrentUser,
) -> list[str]:
    normalized = _normalize_project_scope_ids(project_ids)
    if not normalized:
        return []

    if not is_admin_user(user):
        visible = set(_string_list(getattr(user, "metadata_project_ids", [])))
        unauthorized = [project_id for project_id in normalized if project_id not in visible]
        if unauthorized:
            raise HTTPException(status_code=403, detail="Not authorized for one or more selected projects")

    result = await session.execute(
        text(
            """
            SELECT id::text AS id
            FROM projects
            WHERE id IN :project_ids
            """
        ).bindparams(bindparam("project_ids", expanding=True)),
        {"project_ids": normalized},
    )
    existing = {str(row["id"]) for row in result.mappings().all()}
    missing = [project_id for project_id in normalized if project_id not in existing]
    if missing:
        raise HTTPException(status_code=400, detail="One or more selected projects do not exist")
    return normalized


async def list_small_variant_tag_definitions(
    session: AsyncSession,
    *,
    family_uuid: str,
    project_ids: list[str],
    project_id: str | None = None,
    include_all_project_tags: bool = False,
) -> list[SmallVariantTagDefinitionOut]:
    del family_uuid
    if include_all_project_tags:
        result = await session.execute(
            text(
                """
                SELECT
                    d.key,
                    d.label,
                    d.description,
                    d.scope,
                    d.project_id::text AS project_id,
                    d."group",
                    d.color,
                    d.sort_order,
                    COALESCE(
                        ARRAY_AGG(DISTINCT l.project_id::text) FILTER (WHERE l.project_id IS NOT NULL),
                        '{}'::text[]
                    ) AS shared_project_ids
                FROM small_variant_tag_definitions d
                LEFT JOIN small_variant_tag_definition_project_links l ON l.tag_id = d.id
                WHERE d.is_active = TRUE
                GROUP BY d.id
                ORDER BY d."group", d.sort_order, lower(d.label)
                """
            )
        )
        custom_tags = [_serialize_custom_tag_definition_row(dict(row)) for row in result.mappings().all()]
        return _preset_tag_definitions() + custom_tags

    target_project_ids = _normalize_project_scope_ids([project_id] if project_id else project_ids)
    if target_project_ids:
        result = await session.execute(
            text(
                """
                SELECT
                    d.key,
                    d.label,
                    d.description,
                    d.scope,
                    d.project_id::text AS project_id,
                    d."group",
                    d.color,
                    d.sort_order,
                    COALESCE(
                        ARRAY_AGG(DISTINCT l.project_id::text) FILTER (WHERE l.project_id IS NOT NULL),
                        '{}'::text[]
                    ) AS shared_project_ids
                FROM small_variant_tag_definitions d
                LEFT JOIN small_variant_tag_definition_project_links l ON l.tag_id = d.id
                WHERE d.is_active = TRUE
                  AND (
                    d.scope = 'global'
                    OR (
                        d.scope = 'project'
                        AND (
                            d.project_id IN :project_ids
                            OR EXISTS (
                                SELECT 1
                                FROM small_variant_tag_definition_project_links x
                                WHERE x.tag_id = d.id
                                  AND x.project_id IN :project_ids
                            )
                        )
                    )
                  )
                GROUP BY d.id
                ORDER BY d."group", d.sort_order, lower(d.label)
                """
            ).bindparams(bindparam("project_ids", expanding=True)),
            {"project_ids": target_project_ids},
        )
    else:
        result = await session.execute(
            text(
                """
                SELECT
                    d.key,
                    d.label,
                    d.description,
                    d.scope,
                    d.project_id::text AS project_id,
                    d."group",
                    d.color,
                    d.sort_order,
                    '{}'::text[] AS shared_project_ids
                FROM small_variant_tag_definitions d
                WHERE d.is_active = TRUE
                  AND d.scope = 'global'
                ORDER BY d."group", d.sort_order, lower(d.label)
                """
            )
        )
    custom_tags = [_serialize_custom_tag_definition_row(dict(row)) for row in result.mappings().all()]
    return _preset_tag_definitions() + custom_tags


async def create_small_variant_tag_definition(
    session: AsyncSession,
    *,
    family_uuid: str,
    payload: SmallVariantTagDefinitionCreate,
    user: CurrentUser,
    default_project_id: str | None = None,
) -> SmallVariantTagDefinitionOut:
    del family_uuid
    if not is_admin_user(user):
        raise HTTPException(status_code=403, detail="Only admins can create variant tags")
    key = _slugify_tag(payload.label)
    if key in DEFAULT_SMALL_VARIANT_TAG_KEYS:
        raise HTTPException(status_code=409, detail="That tag label conflicts with a built-in variant tag")

    existing = await session.execute(
        text(
            """
            SELECT id
            FROM small_variant_tag_definitions
            WHERE key = :key
            """
        ),
        {"key": key},
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="A variant tag with that label already exists")

    scope = payload.scope
    primary_project_id = payload.project_id or default_project_id
    shared_project_ids = _string_list(payload.shared_project_ids)
    if scope == "project":
        if not primary_project_id:
            raise HTTPException(status_code=400, detail="Project-scoped tags require a project id")
        visible_project_ids = await _ensure_projects_visible(
            session,
            project_ids=[primary_project_id, *shared_project_ids],
            user=user,
        )
        primary_project_id = visible_project_ids[0]
        shared_project_ids = [project_id for project_id in visible_project_ids[1:] if project_id != primary_project_id]
    else:
        primary_project_id = None
        shared_project_ids = []

    now = datetime.now(timezone.utc)
    created_row = await session.execute(
        text(
            """
            INSERT INTO small_variant_tag_definitions (
                key,
                label,
                description,
                scope,
                project_id,
                "group",
                color,
                sort_order,
                created_by,
                created_at,
                updated_at,
                is_active
            )
            VALUES (
                :key,
                :label,
                :description,
                :scope,
                CAST(:project_id AS uuid),
                :group_name,
                :color,
                500,
                :created_by,
                :created_at,
                :updated_at,
                TRUE
            )
            RETURNING id::text AS id
            """
        ),
        {
            "key": key,
            "label": payload.label.strip(),
            "description": (payload.description or "").strip() or None,
            "scope": scope,
            "project_id": primary_project_id,
            "group_name": payload.group,
            "color": _normalize_hex_color(payload.color),
            "created_by": user.username,
            "created_at": now,
            "updated_at": now,
        },
    )
    created_id = created_row.scalar_one()
    if shared_project_ids:
        await session.execute(
            text(
                """
                INSERT INTO small_variant_tag_definition_project_links (tag_id, project_id)
                VALUES (CAST(:tag_id AS uuid), CAST(:project_id AS uuid))
                """
            ),
            [{"tag_id": created_id, "project_id": project_id} for project_id in shared_project_ids],
        )
    await session.commit()
    return SmallVariantTagDefinitionOut(
        key=key,
        label=payload.label.strip(),
        description=(payload.description or "").strip() or None,
        group=payload.group,
        color=_normalize_hex_color(payload.color),
        sort_order=500,
        scope=scope,
        project_id=primary_project_id,
        shared_project_ids=shared_project_ids,
        is_custom=True,
    )


async def update_small_variant_tag_definition(
    session: AsyncSession,
    *,
    family_uuid: str,
    tag_key: str,
    payload: SmallVariantTagDefinitionUpdate,
    user: CurrentUser,
    default_project_id: str | None = None,
) -> SmallVariantTagDefinitionOut:
    del family_uuid
    if not is_admin_user(user):
        raise HTTPException(status_code=403, detail="Only admins can edit variant tags")

    normalized_tag_key = str(tag_key).strip().lower()
    if not normalized_tag_key:
        raise HTTPException(status_code=404, detail="Variant tag not found")
    if normalized_tag_key in DEFAULT_SMALL_VARIANT_TAG_KEYS:
        raise HTTPException(status_code=400, detail="Built-in variant tags cannot be edited")

    # Every column is qualified: the links table has a project_id too, and Postgres refuses
    # an unqualified one as ambiguous, which failed every edit with a 500.
    result = await session.execute(
        text(
            """
            SELECT
                d.id::text AS id,
                d.key,
                d.label,
                d.description,
                d.scope,
                d.project_id::text AS project_id,
                d."group",
                d.color,
                d.sort_order,
                COALESCE(
                    ARRAY_AGG(DISTINCT l.project_id::text) FILTER (WHERE l.project_id IS NOT NULL),
                    '{}'::text[]
                ) AS shared_project_ids
            FROM small_variant_tag_definitions d
            LEFT JOIN small_variant_tag_definition_project_links l ON l.tag_id = d.id
            WHERE d.key = :key
              AND d.is_active = TRUE
            GROUP BY d.id
            """
        ),
        {"key": normalized_tag_key},
    )
    existing_row = result.mappings().first()
    if existing_row is None:
        raise HTTPException(status_code=404, detail="Variant tag not found")

    existing = dict(existing_row)

    if not payload.model_fields_set:
        raise HTTPException(status_code=400, detail="No tag fields were provided")

    next_label = existing["label"]
    next_key = existing["key"]
    if "label" in payload.model_fields_set:
        next_label = (payload.label or "").strip()
        if not next_label:
            raise HTTPException(status_code=400, detail="Tag label cannot be blank")
        next_key = _slugify_tag(next_label)
        if next_key in DEFAULT_SMALL_VARIANT_TAG_KEYS:
            raise HTTPException(status_code=409, detail="That tag label conflicts with a built-in variant tag")

    if next_key != existing["key"]:
        duplicate = await session.execute(
            text(
                """
                SELECT id
                FROM small_variant_tag_definitions
                WHERE key = :key
                  AND is_active = TRUE
                  AND id <> CAST(:tag_id AS uuid)
                """
            ),
            {"key": next_key, "tag_id": existing["id"]},
        )
        if duplicate.scalar_one_or_none() is not None:
            raise HTTPException(status_code=409, detail="A variant tag with that label already exists")

    next_description = existing.get("description")
    if "description" in payload.model_fields_set:
        next_description = (payload.description or "").strip() or None

    next_scope = existing.get("scope") or "global"
    if "scope" in payload.model_fields_set and payload.scope is not None:
        next_scope = payload.scope

    next_project_id = existing.get("project_id")
    if "project_id" in payload.model_fields_set:
        next_project_id = (payload.project_id or "").strip() or None

    if next_scope == "project" and not next_project_id and default_project_id:
        next_project_id = default_project_id
    if next_scope == "project" and not next_project_id:
        raise HTTPException(status_code=400, detail="Project-scoped tags require a project id")
    if next_scope == "global":
        next_project_id = None

    if payload.shared_project_ids is not None:
        requested_shared_project_ids = _string_list(payload.shared_project_ids)
    else:
        requested_shared_project_ids = _string_list(existing.get("shared_project_ids"))
    if next_scope == "project":
        project_scope_ids = await _ensure_projects_visible(
            session,
            project_ids=[next_project_id, *requested_shared_project_ids],
            user=user,
        )
        next_project_id = project_scope_ids[0]
        next_shared_project_ids = [project_id for project_id in project_scope_ids[1:] if project_id != next_project_id]
    else:
        next_shared_project_ids = []

    next_group = existing.get("group", "custom")
    if "group" in payload.model_fields_set:
        next_group = payload.group or "custom"

    next_color = _normalize_hex_color(existing.get("color"))
    if "color" in payload.model_fields_set:
        next_color = _normalize_hex_color(payload.color)

    now = datetime.now(timezone.utc)
    await session.execute(
        text(
            """
            UPDATE small_variant_tag_definitions
            SET
                key = :key,
                label = :label,
                description = :description,
                scope = :scope,
                project_id = CAST(:project_id AS uuid),
                "group" = :group_name,
                color = :color,
                updated_at = :updated_at
            WHERE id = CAST(:tag_id AS uuid)
            """
        ),
        {
            "tag_id": existing["id"],
            "key": next_key,
            "label": next_label,
            "description": next_description,
            "scope": next_scope,
            "project_id": next_project_id,
            "group_name": next_group,
            "color": next_color,
            "updated_at": now,
        },
    )
    await session.execute(
        text(
            """
            DELETE FROM small_variant_tag_definition_project_links
            WHERE tag_id = CAST(:tag_id AS uuid)
            """
        ),
        {"tag_id": existing["id"]},
    )
    if next_shared_project_ids:
        await session.execute(
            text(
                """
                INSERT INTO small_variant_tag_definition_project_links (tag_id, project_id)
                VALUES (CAST(:tag_id AS uuid), CAST(:project_id AS uuid))
                """
            ),
            [{"tag_id": existing["id"], "project_id": project_id} for project_id in next_shared_project_ids],
        )
    await session.commit()
    return _serialize_custom_tag_definition_row(
        {
            **existing,
            "key": next_key,
            "label": next_label,
            "description": next_description,
            "scope": next_scope,
            "project_id": next_project_id,
            "group": next_group,
            "color": next_color,
            "shared_project_ids": next_shared_project_ids,
        }
    )


async def delete_small_variant_tag_definition(
    session: AsyncSession,
    *,
    family_uuid: str,
    tag_key: str,
    user: CurrentUser,
) -> None:
    del family_uuid
    if not is_admin_user(user):
        raise HTTPException(status_code=403, detail="Only admins can delete variant tags")

    normalized_tag_key = str(tag_key).strip().lower()
    if not normalized_tag_key:
        raise HTTPException(status_code=404, detail="Variant tag not found")
    if normalized_tag_key in DEFAULT_SMALL_VARIANT_TAG_KEYS:
        raise HTTPException(status_code=400, detail="Built-in variant tags cannot be deleted")

    result = await session.execute(
        text(
            """
            SELECT id::text AS id
            FROM small_variant_tag_definitions
            WHERE key = :key
              AND is_active = TRUE
            """
        ),
        {"key": normalized_tag_key},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Variant tag not found")

    row_data = dict(row)

    await session.execute(
        text(
            """
            UPDATE small_variant_tag_definitions
            SET is_active = FALSE, updated_at = :updated_at
            WHERE id = CAST(:tag_id AS uuid)
            """
        ),
        {"tag_id": row_data["id"], "updated_at": datetime.now(timezone.utc)},
    )
    await session.execute(
        text(
            """
            DELETE FROM small_variant_tag_definition_project_links
            WHERE tag_id = CAST(:tag_id AS uuid)
            """
        ),
        {"tag_id": row_data["id"]},
    )
    await session.commit()
