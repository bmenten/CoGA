from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any, Iterable, Sequence
from uuid import UUID, uuid4

from fastapi.encoders import jsonable_encoder
from fastapi import HTTPException
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import (
    SmallVariantCompoundHetReviewOut,
    SmallVariantCompoundHetReviewUpdate,
    SmallVariantFilterPresetCreate,
    SmallVariantFilterPresetOut,
    SmallVariantReviewOut,
    SmallVariantReviewSummaryOut,
    SmallVariantReviewUpdate,
    SmallVariantTagDefinitionCreate,
    SmallVariantTagDefinitionUpdate,
    SmallVariantTagDefinitionOut,
)
from .clickhouse_small_variants import (
    get_small_variant_family_record,
    has_affected_het_call,
    variants_share_gene,
)
from .family_metadata_context import FamilyMetadataContext
from .metadata_service import CurrentUser


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
        "key": "secondary_finding",
        "label": "Secondary finding",
        "group": "classification",
        "color": "#d4a017",
        "sort_order": "160",
        "description": "Potential ACMG secondary finding or incidental reportable finding.",
    },
]
DEFAULT_SMALL_VARIANT_TAG_KEYS = {entry["key"] for entry in DEFAULT_SMALL_VARIANT_TAGS}
OBJECT_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{24}$")
POSTGRES_BIGINT_MIN = -(2**63)
POSTGRES_BIGINT_MAX = (2**63) - 1


def _require_uuid(value: str, detail: str) -> str:
    try:
        UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=detail) from exc
    return value


def _looks_like_object_id(value: str) -> bool:
    return bool(OBJECT_ID_PATTERN.fullmatch(str(value)))


def _postgres_bigint_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        int_value = int(value)
    except (TypeError, ValueError):
        return None
    if POSTGRES_BIGINT_MIN <= int_value <= POSTGRES_BIGINT_MAX:
        return int_value
    return None


def _normalize_tags(tags: Iterable[str]) -> list[str]:
    return sorted({str(tag).strip() for tag in tags if str(tag).strip()})


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


def _json_payload(value: Any) -> str:
    return json.dumps(jsonable_encoder(value if value is not None else {}))


def _serialize_tag_metadata(
    *,
    document: dict[str, Any],
    tags_key: str,
    metadata_key: str,
    fallback_user_key: str,
    fallback_time_key: str,
) -> dict[str, dict[str, Any]]:
    raw_metadata = document.get(metadata_key) or {}
    fallback_user = document.get(fallback_user_key)
    fallback_time = document.get(fallback_time_key)
    serialized: dict[str, dict[str, Any]] = {}
    for tag in _normalize_tags(document.get(tags_key, [])):
        entry = raw_metadata.get(tag) if isinstance(raw_metadata, dict) else None
        if isinstance(entry, dict):
            serialized[tag] = {
                "updated_by": entry.get("updated_by"),
                "updated_at": entry.get("updated_at"),
            }
        else:
            serialized[tag] = {
                "updated_by": fallback_user,
                "updated_at": fallback_time,
            }
    return serialized


def _merge_tag_metadata(
    *,
    existing_metadata: dict[str, Any] | None,
    previous_tags: Sequence[str],
    next_tags: Sequence[str],
    username: str,
    timestamp: datetime,
) -> dict[str, dict[str, Any]]:
    previous = set(_normalize_tags(previous_tags))
    merged: dict[str, dict[str, Any]] = {}
    for tag in _normalize_tags(next_tags):
        if tag in previous and isinstance((existing_metadata or {}).get(tag), dict):
            merged[tag] = {
                "updated_by": (existing_metadata or {})[tag].get("updated_by"),
                "updated_at": (existing_metadata or {})[tag].get("updated_at"),
            }
        else:
            merged[tag] = {
                "updated_by": username,
                "updated_at": timestamp,
            }
    return merged


def _compound_het_clear_payload() -> dict[str, Any]:
    return {
        "compound_het_group_id": None,
        "compound_het_partner_variant_keys": [],
        "compound_het_partner_variant_ids": [],
        "compound_het_gene": None,
        "compound_het_gene_id": None,
        "compound_het_classification": None,
        "compound_het_tags": [],
        "compound_het_tag_metadata": {},
        "compound_het_note": None,
        "compound_het_phase_status": None,
        "compound_het_updated_by": None,
        "compound_het_updated_at": None,
    }


def _compound_het_field_names() -> list[str]:
    return list(_compound_het_clear_payload().keys())


def _preserve_existing_compound_het(document: dict[str, Any]) -> dict[str, Any]:
    return {key: document.get(key) for key in _compound_het_field_names()}


def _document_has_individual_review(document: dict[str, Any]) -> bool:
    return bool(
        str(document.get("classification") or "").strip()
        or _normalize_tags(document.get("tags", []))
        or str(document.get("note") or "").strip()
    )


def _document_has_compound_het_review(document: dict[str, Any]) -> bool:
    return bool(
        document.get("compound_het_group_id")
        or document.get("compound_het_partner_variant_ids")
        or str(document.get("compound_het_classification") or "").strip()
        or _normalize_tags(document.get("compound_het_tags", []))
        or str(document.get("compound_het_note") or "").strip()
    )


def _review_document_has_any_content(document: dict[str, Any]) -> bool:
    return _document_has_individual_review(document) or _document_has_compound_het_review(document)


def _serialize_compound_het(document: dict[str, Any]) -> SmallVariantCompoundHetReviewOut | None:
    group_id = document.get("compound_het_group_id")
    if not group_id:
        return None
    return SmallVariantCompoundHetReviewOut(
        group_id=group_id,
        partner_variant_ids=sorted(
            {
                str(variant_id)
                for variant_id in document.get("compound_het_partner_variant_ids", [])
                if variant_id is not None
            }
        ),
        gene=document.get("compound_het_gene"),
        gene_id=document.get("compound_het_gene_id"),
        classification=document.get("compound_het_classification"),
        tags=_normalize_tags(document.get("compound_het_tags", [])),
        tag_metadata=_serialize_tag_metadata(
            document=document,
            tags_key="compound_het_tags",
            metadata_key="compound_het_tag_metadata",
            fallback_user_key="compound_het_updated_by",
            fallback_time_key="compound_het_updated_at",
        ),
        note=document.get("compound_het_note"),
        phase_status=document.get("compound_het_phase_status"),
        updated_by=document.get("compound_het_updated_by"),
        updated_at=document.get("compound_het_updated_at"),
    )


def _serialize_review(document: dict[str, Any]) -> SmallVariantReviewOut:
    return SmallVariantReviewOut(
        variant_id=str(document["variant_id"]),
        classification=document.get("classification"),
        tags=_normalize_tags(document.get("tags", [])),
        tag_metadata=_serialize_tag_metadata(
            document=document,
            tags_key="tags",
            metadata_key="tag_metadata",
            fallback_user_key="updated_by",
            fallback_time_key="updated_at",
        ),
        note=document.get("note"),
        updated_by=document.get("updated_by"),
        updated_at=document.get("updated_at"),
        compound_het=_serialize_compound_het(document),
    )


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

    if user.role != "admin":
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


async def _fetch_review_row(
    session: AsyncSession,
    *,
    family_uuid: str,
    variant_id: str,
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            """
            SELECT
                id::text AS id,
                family_id::text AS family_id,
                variant_key,
                variant_id,
                classification,
                tags,
                tag_metadata,
                note,
                compound_het_group_id,
                compound_het_partner_variant_keys,
                compound_het_partner_variant_ids,
                compound_het_gene,
                compound_het_gene_id,
                compound_het_classification,
                compound_het_tags,
                compound_het_tag_metadata,
                compound_het_note,
                compound_het_phase_status,
                compound_het_updated_by,
                compound_het_updated_at,
                updated_by,
                created_at,
                updated_at
            FROM small_variant_reviews
            WHERE family_id = CAST(:family_id AS uuid)
              AND variant_id = :variant_id
            """
        ),
        {"family_id": family_uuid, "variant_id": variant_id},
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None


async def _fetch_compound_het_group_rows(
    session: AsyncSession,
    *,
    family_uuid: str,
    group_id: str,
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT
                id::text AS id,
                family_id::text AS family_id,
                variant_key,
                variant_id,
                classification,
                tags,
                tag_metadata,
                note,
                compound_het_group_id,
                compound_het_partner_variant_keys,
                compound_het_partner_variant_ids,
                compound_het_gene,
                compound_het_gene_id,
                compound_het_classification,
                compound_het_tags,
                compound_het_tag_metadata,
                compound_het_note,
                compound_het_phase_status,
                compound_het_updated_by,
                compound_het_updated_at,
                updated_by,
                created_at,
                updated_at
            FROM small_variant_reviews
            WHERE family_id = CAST(:family_id AS uuid)
              AND compound_het_group_id = :group_id
            """
        ),
        {"family_id": family_uuid, "group_id": group_id},
    )
    return [dict(row) for row in result.mappings().all()]


async def _delete_review_row(session: AsyncSession, review_id: str) -> None:
    await session.execute(
        text("DELETE FROM small_variant_reviews WHERE id = CAST(:review_id AS uuid)"),
        {"review_id": review_id},
    )


async def _update_review_row(
    session: AsyncSession,
    *,
    review_id: str,
    fields: dict[str, Any],
) -> None:
    await session.execute(
        text(
            """
            UPDATE small_variant_reviews
            SET
                variant_key = :variant_key,
                variant_id = :variant_id,
                classification = :classification,
                tags = CAST(:tags_json AS jsonb),
                tag_metadata = CAST(:tag_metadata_json AS jsonb),
                note = :note,
                compound_het_group_id = :compound_het_group_id,
                compound_het_partner_variant_keys = CAST(:compound_het_partner_variant_keys_json AS jsonb),
                compound_het_partner_variant_ids = CAST(:compound_het_partner_variant_ids_json AS jsonb),
                compound_het_gene = :compound_het_gene,
                compound_het_gene_id = :compound_het_gene_id,
                compound_het_classification = :compound_het_classification,
                compound_het_tags = CAST(:compound_het_tags_json AS jsonb),
                compound_het_tag_metadata = CAST(:compound_het_tag_metadata_json AS jsonb),
                compound_het_note = :compound_het_note,
                compound_het_phase_status = :compound_het_phase_status,
                compound_het_updated_by = :compound_het_updated_by,
                compound_het_updated_at = :compound_het_updated_at,
                updated_by = :updated_by,
                updated_at = :updated_at
            WHERE id = CAST(:review_id AS uuid)
            """
        ),
        {
            **fields,
            "variant_key": _postgres_bigint_or_none(fields.get("variant_key")),
            "tags_json": _json_payload(fields.get("tags", [])),
            "tag_metadata_json": _json_payload(fields.get("tag_metadata", {})),
            "compound_het_partner_variant_keys_json": _json_payload(
                fields.get("compound_het_partner_variant_keys", [])
            ),
            "compound_het_partner_variant_ids_json": _json_payload(
                fields.get("compound_het_partner_variant_ids", [])
            ),
            "compound_het_tags_json": _json_payload(fields.get("compound_het_tags", [])),
            "compound_het_tag_metadata_json": _json_payload(
                fields.get("compound_het_tag_metadata", {})
            ),
            "review_id": review_id,
        },
    )


async def _insert_review_row(
    session: AsyncSession,
    *,
    family_uuid: str,
    fields: dict[str, Any],
    created_at: datetime,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO small_variant_reviews (
                family_id,
                variant_key,
                variant_id,
                classification,
                tags,
                tag_metadata,
                note,
                compound_het_group_id,
                compound_het_partner_variant_keys,
                compound_het_partner_variant_ids,
                compound_het_gene,
                compound_het_gene_id,
                compound_het_classification,
                compound_het_tags,
                compound_het_tag_metadata,
                compound_het_note,
                compound_het_phase_status,
                compound_het_updated_by,
                compound_het_updated_at,
                updated_by,
                created_at,
                updated_at
            )
            VALUES (
                CAST(:family_id AS uuid),
                :variant_key,
                :variant_id,
                :classification,
                CAST(:tags_json AS jsonb),
                CAST(:tag_metadata_json AS jsonb),
                :note,
                :compound_het_group_id,
                CAST(:compound_het_partner_variant_keys_json AS jsonb),
                CAST(:compound_het_partner_variant_ids_json AS jsonb),
                :compound_het_gene,
                :compound_het_gene_id,
                :compound_het_classification,
                CAST(:compound_het_tags_json AS jsonb),
                CAST(:compound_het_tag_metadata_json AS jsonb),
                :compound_het_note,
                :compound_het_phase_status,
                :compound_het_updated_by,
                :compound_het_updated_at,
                :updated_by,
                :created_at,
                :updated_at
            )
            """
        ),
        {
            **fields,
            "variant_key": _postgres_bigint_or_none(fields.get("variant_key")),
            "tags_json": _json_payload(fields.get("tags", [])),
            "tag_metadata_json": _json_payload(fields.get("tag_metadata", {})),
            "compound_het_partner_variant_keys_json": _json_payload(
                fields.get("compound_het_partner_variant_keys", [])
            ),
            "compound_het_partner_variant_ids_json": _json_payload(
                fields.get("compound_het_partner_variant_ids", [])
            ),
            "compound_het_tags_json": _json_payload(fields.get("compound_het_tags", [])),
            "compound_het_tag_metadata_json": _json_payload(
                fields.get("compound_het_tag_metadata", {})
            ),
            "family_id": family_uuid,
            "created_at": created_at,
        },
    )


async def _clear_compound_het_group(
    session: AsyncSession,
    *,
    family_uuid: str,
    group_id: str,
) -> None:
    if not group_id:
        return
    documents = await _fetch_compound_het_group_rows(
        session,
        family_uuid=family_uuid,
        group_id=group_id,
    )
    clear_payload = _compound_het_clear_payload()
    for document in documents:
        updated_document = {**document, **clear_payload}
        if _review_document_has_any_content(updated_document):
            await _update_review_row(
                session,
                review_id=document["id"],
                fields={**document, **clear_payload},
            )
        else:
            await _delete_review_row(session, document["id"])


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
    if user.role != "admin":
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
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Only admins can edit variant tags")

    normalized_tag_key = str(tag_key).strip().lower()
    if not normalized_tag_key:
        raise HTTPException(status_code=404, detail="Variant tag not found")
    if normalized_tag_key in DEFAULT_SMALL_VARIANT_TAG_KEYS:
        raise HTTPException(status_code=400, detail="Built-in variant tags cannot be edited")

    result = await session.execute(
        text(
            """
            SELECT
                id::text AS id,
                key,
                label,
                description,
                scope,
                project_id::text AS project_id,
                "group",
                color,
                sort_order,
                COALESCE(
                    ARRAY_AGG(DISTINCT l.project_id::text) FILTER (WHERE l.project_id IS NOT NULL),
                    '{}'::text[]
                ) AS shared_project_ids
            FROM small_variant_tag_definitions
            LEFT JOIN small_variant_tag_definition_project_links l ON l.tag_id = small_variant_tag_definitions.id
            WHERE key = :key
              AND is_active = TRUE
            GROUP BY small_variant_tag_definitions.id
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
    if user.role != "admin":
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
                    (:family_id IS NULL AND family_id IS NULL)
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
                    (:family_id IS NULL AND family_id IS NULL)
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


async def get_small_variant_review_summary(
    session: AsyncSession,
    *,
    family_uuid: str,
) -> SmallVariantReviewSummaryOut:
    result = await session.execute(
        text(
            """
            SELECT
                variant_id,
                classification,
                tags,
                note,
                compound_het_group_id,
                compound_het_tags,
                compound_het_note,
                compound_het_classification
            FROM small_variant_reviews
            WHERE family_id = CAST(:family_id AS uuid)
            """
        ),
        {"family_id": family_uuid},
    )
    reviewed_variant_ids: set[str] = set()
    noted_variant_ids: set[str] = set()
    tag_variant_ids: dict[str, set[str]] = {}
    for row in result.mappings().all():
        document = dict(row)
        variant_id = document.get("variant_id")
        if variant_id is None:
            continue
        variant_key = str(variant_id)
        if _review_document_has_any_content(document):
            reviewed_variant_ids.add(variant_key)
        if str(document.get("note") or "").strip() or str(document.get("compound_het_note") or "").strip():
            noted_variant_ids.add(variant_key)
        for tag in _normalize_tags(document.get("tags", [])):
            tag_variant_ids.setdefault(tag, set()).add(variant_key)
        for tag in _normalize_tags(document.get("compound_het_tags", [])):
            tag_variant_ids.setdefault(tag, set()).add(variant_key)

    return SmallVariantReviewSummaryOut(
        reviewed_variant_count=len(reviewed_variant_ids),
        note_count=len(noted_variant_ids),
        tag_counts={
            tag: len(variant_ids)
            for tag, variant_ids in sorted(tag_variant_ids.items(), key=lambda entry: entry[0])
            if variant_ids
        },
    )


async def list_matching_small_variant_review_ids(
    session: AsyncSession,
    *,
    family_uuid: str,
    classifications: Sequence[str] | None = None,
    tags: Sequence[str] | None = None,
    has_notes: bool = False,
) -> set[str]:
    normalized_classifications = [
        value.strip() for value in (classifications or []) if str(value).strip()
    ]
    normalized_tags = {
        value.strip() for value in (tags or []) if str(value).strip()
    }
    if not normalized_classifications and not normalized_tags and not has_notes:
        return set()

    result = await session.execute(
        text(
            """
            SELECT
                variant_id,
                classification,
                tags,
                note,
                compound_het_classification,
                compound_het_tags,
                compound_het_note
            FROM small_variant_reviews
            WHERE family_id = CAST(:family_id AS uuid)
            """
        ),
        {"family_id": family_uuid},
    )
    matching_ids: set[str] = set()
    for row in result.mappings().all():
        document = dict(row)
        variant_id = str(document.get("variant_id") or "").strip()
        if not variant_id:
            continue
        matches_classification = not normalized_classifications or (
            str(document.get("classification") or "").strip() in normalized_classifications
            or str(document.get("compound_het_classification") or "").strip() in normalized_classifications
        )
        matches_tags = not normalized_tags or bool(
            set(_normalize_tags(document.get("tags", []))).intersection(normalized_tags)
            or set(_normalize_tags(document.get("compound_het_tags", []))).intersection(normalized_tags)
        )
        matches_notes = not has_notes or bool(
            str(document.get("note") or "").strip()
            or str(document.get("compound_het_note") or "").strip()
        )
        if matches_classification and matches_tags and matches_notes:
            matching_ids.add(variant_id)
    return matching_ids


async def get_small_variant_review_map(
    session: AsyncSession,
    *,
    family_uuid: str,
    variant_ids: Sequence[str],
) -> dict[str, SmallVariantReviewOut]:
    normalized_variant_ids = [
        str(variant_id).strip() for variant_id in variant_ids if str(variant_id).strip()
    ]
    if not normalized_variant_ids:
        return {}
    result = await session.execute(
        text(
            """
            SELECT
                variant_id,
                classification,
                tags,
                tag_metadata,
                note,
                compound_het_group_id,
                compound_het_partner_variant_ids,
                compound_het_gene,
                compound_het_gene_id,
                compound_het_classification,
                compound_het_tags,
                compound_het_tag_metadata,
                compound_het_note,
                compound_het_phase_status,
                compound_het_updated_by,
                compound_het_updated_at,
                updated_by,
                updated_at
            FROM small_variant_reviews
            WHERE family_id = CAST(:family_id AS uuid)
              AND variant_id IN :variant_ids
            """
        ).bindparams(bindparam("variant_ids", expanding=True)),
        {"family_id": family_uuid, "variant_ids": normalized_variant_ids},
    )
    return {
        str(document["variant_id"]): _serialize_review(document)
        for document in (dict(row) for row in result.mappings().all())
        if document.get("variant_id") is not None
    }


async def upsert_small_variant_review(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    variant_id: str,
    payload: SmallVariantReviewUpdate,
    user: CurrentUser,
) -> SmallVariantReviewOut:
    variant = None
    if context.assembly_name:
        variant = await get_small_variant_family_record(
            assembly_name=context.assembly_name,
            family_guid=context.family_uuid,
            variant_id=variant_id,
        )
    if variant is None and not _looks_like_object_id(variant_id):
        raise HTTPException(status_code=404, detail="Variant not found")

    allowed_tags = {
        definition.key
        for definition in await list_small_variant_tag_definitions(
            session,
            family_uuid=context.family_uuid,
            project_ids=context.project_ids,
        )
    }
    normalized_tags = _normalize_tags(payload.tags)
    unknown_tags = [tag for tag in normalized_tags if tag not in allowed_tags]
    if unknown_tags:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown small-variant tag(s): {', '.join(sorted(unknown_tags))}",
        )

    normalized_note = (payload.note or "").strip() or None
    normalized_classification = (payload.classification or "").strip() or None
    now = datetime.now(timezone.utc)
    existing = await _fetch_review_row(
        session,
        family_uuid=context.family_uuid,
        variant_id=variant_id,
    )
    compound_het_data: dict[str, Any] | None = None
    compound_het_requested = "compound_het" in payload.model_fields_set
    compound_het_payload: SmallVariantCompoundHetReviewUpdate | None = (
        payload.compound_het if compound_het_requested else None
    )

    if compound_het_requested and compound_het_payload is not None:
        normalized_compound_het_classification = (
            (compound_het_payload.classification or "").strip() or None
        )
        normalized_compound_het_tags = _normalize_tags(compound_het_payload.tags)
        normalized_compound_het_note = (compound_het_payload.note or "").strip() or None
        compound_het_partner_id = (compound_het_payload.partner_variant_id or "").strip() or None
        unknown_compound_het_tags = [
            tag for tag in normalized_compound_het_tags if tag not in allowed_tags
        ]
        if unknown_compound_het_tags:
            raise HTTPException(
                status_code=400,
                detail="Unknown small-variant tag(s): " + ", ".join(sorted(unknown_compound_het_tags)),
            )
        if compound_het_partner_id:
            if variant is None or not context.assembly_name:
                raise HTTPException(
                    status_code=400,
                    detail="Compound-het review requires a ClickHouse-backed variant identity",
                )
            if compound_het_partner_id == variant_id:
                raise HTTPException(status_code=400, detail="Compound-het partner must be a different variant")
            partner_variant = await get_small_variant_family_record(
                assembly_name=context.assembly_name,
                family_guid=context.family_uuid,
                variant_id=compound_het_partner_id,
            )
            if partner_variant is None:
                raise HTTPException(status_code=404, detail="Compound-het partner variant not found")
            if not variants_share_gene(variant, partner_variant):
                raise HTTPException(
                    status_code=400,
                    detail="Compound-het review currently requires both variants to share the same gene",
                )
            if not has_affected_het_call(variant, context.affected_sample_names) or not has_affected_het_call(
                partner_variant,
                context.affected_sample_names,
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Compound-het review requires both variants to be heterozygous in an affected family member",
                )
            partner_existing = await _fetch_review_row(
                session,
                family_uuid=context.family_uuid,
                variant_id=compound_het_partner_id,
            )
            existing_group_id = existing.get("compound_het_group_id") if existing else None
            partner_group_id = partner_existing.get("compound_het_group_id") if partner_existing else None
            target_group_id = None
            if (
                existing_group_id
                and existing_group_id == partner_group_id
                and compound_het_partner_id in (existing.get("compound_het_partner_variant_ids") or [])
                and variant_id in (partner_existing.get("compound_het_partner_variant_ids") or [])
            ):
                target_group_id = existing_group_id
            for group_id in {existing_group_id, partner_group_id} - {None, target_group_id}:
                await _clear_compound_het_group(
                    session,
                    family_uuid=context.family_uuid,
                    group_id=str(group_id),
                )

            target_group_id = target_group_id or uuid4().hex
            shared_compound_het_data = {
                "compound_het_group_id": target_group_id,
                "compound_het_gene": variant.gene_symbols[0] if variant.gene_symbols else None,
                "compound_het_gene_id": None,
                "compound_het_classification": normalized_compound_het_classification,
                "compound_het_tags": normalized_compound_het_tags,
                "compound_het_tag_metadata": _merge_tag_metadata(
                    existing_metadata=(existing or {}).get("compound_het_tag_metadata"),
                    previous_tags=(existing or {}).get("compound_het_tags", []),
                    next_tags=normalized_compound_het_tags,
                    username=user.username,
                    timestamp=now,
                ),
                "compound_het_note": normalized_compound_het_note,
                "compound_het_phase_status": "unknown",
                "compound_het_updated_by": user.username,
                "compound_het_updated_at": now,
            }
            compound_het_data = {
                **shared_compound_het_data,
                "compound_het_partner_variant_keys": [partner_variant.variant_key] if partner_variant.variant_key is not None else [],
                "compound_het_partner_variant_ids": [compound_het_partner_id],
            }
            partner_compound_het_data = {
                **shared_compound_het_data,
                "compound_het_partner_variant_keys": [variant.variant_key] if variant.variant_key is not None else [],
                "compound_het_partner_variant_ids": [variant_id],
            }
            partner_individual_data = {
                "variant_key": partner_variant.variant_key,
                "variant_id": compound_het_partner_id,
                "classification": partner_existing.get("classification") if partner_existing else None,
                "tags": _normalize_tags((partner_existing or {}).get("tags", [])),
                "tag_metadata": (partner_existing or {}).get("tag_metadata", {}),
                "note": (partner_existing or {}).get("note"),
                "updated_by": user.username,
                "updated_at": now,
            }
            partner_document_payload = {
                **partner_individual_data,
                **partner_compound_het_data,
            }
            if partner_existing is None:
                if _review_document_has_any_content(partner_document_payload):
                    await _insert_review_row(
                        session,
                        family_uuid=context.family_uuid,
                        fields=partner_document_payload,
                        created_at=now,
                    )
            else:
                merged_partner = {**partner_existing, **partner_document_payload}
                if _review_document_has_any_content(merged_partner):
                    await _update_review_row(
                        session,
                        review_id=partner_existing["id"],
                        fields=merged_partner,
                    )
                else:
                    await _delete_review_row(session, partner_existing["id"])
        else:
            if (
                normalized_compound_het_classification is not None
                or normalized_compound_het_tags
                or normalized_compound_het_note is not None
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Compound-het review requires a partner variant",
                )
            if existing and existing.get("compound_het_group_id"):
                await _clear_compound_het_group(
                    session,
                    family_uuid=context.family_uuid,
                    group_id=existing["compound_het_group_id"],
                )
            compound_het_data = _compound_het_clear_payload()
    elif compound_het_requested:
        if existing and existing.get("compound_het_group_id"):
            await _clear_compound_het_group(
                session,
                family_uuid=context.family_uuid,
                group_id=existing["compound_het_group_id"],
            )
        compound_het_data = _compound_het_clear_payload()

    if (
        normalized_note is None
        and normalized_classification is None
        and not normalized_tags
        and not compound_het_requested
    ):
        if existing is not None and _document_has_compound_het_review(existing):
            data = {
                "variant_key": variant.variant_key if variant is not None else None,
                "variant_id": variant_id,
                "classification": None,
                "tags": [],
                "tag_metadata": {},
                "note": None,
                "updated_by": user.username,
                "updated_at": now,
                **_preserve_existing_compound_het(existing),
            }
            await _update_review_row(
                session,
                review_id=existing["id"],
                fields=data,
            )
            await session.commit()
            updated = await _fetch_review_row(
                session,
                family_uuid=context.family_uuid,
                variant_id=variant_id,
            )
            if updated is None:
                raise HTTPException(status_code=500, detail="Review update failed")
            return _serialize_review(updated)
        if existing is not None:
            await _delete_review_row(session, existing["id"])
            await session.commit()
        return SmallVariantReviewOut(variant_id=variant_id, tags=[])

    data = {
        "variant_key": variant.variant_key if variant is not None else None,
        "variant_id": variant_id,
        "classification": normalized_classification,
        "tags": normalized_tags,
        "tag_metadata": _merge_tag_metadata(
            existing_metadata=(existing or {}).get("tag_metadata"),
            previous_tags=(existing or {}).get("tags", []),
            next_tags=normalized_tags,
            username=user.username,
            timestamp=now,
        ),
        "note": normalized_note,
        "updated_by": user.username,
        "updated_at": now,
    }
    if compound_het_data is not None:
        data.update(compound_het_data)
    elif not compound_het_requested and existing is not None and _document_has_compound_het_review(existing):
        data.update(_preserve_existing_compound_het(existing))

    if existing is not None:
        merged = {**existing, **data}
        if _review_document_has_any_content(merged):
            await _update_review_row(
                session,
                review_id=existing["id"],
                fields=merged,
            )
            await session.commit()
            updated = await _fetch_review_row(
                session,
                family_uuid=context.family_uuid,
                variant_id=variant_id,
            )
            if updated is None:
                raise HTTPException(status_code=500, detail="Review update failed")
            return _serialize_review(updated)
        await _delete_review_row(session, existing["id"])
        await session.commit()
        return SmallVariantReviewOut(variant_id=variant_id, tags=[])

    if _review_document_has_any_content(data):
        await _insert_review_row(
            session,
            family_uuid=context.family_uuid,
            fields={
                **_compound_het_clear_payload(),
                **data,
            },
            created_at=now,
        )
        await session.commit()
        created = await _fetch_review_row(
            session,
            family_uuid=context.family_uuid,
            variant_id=variant_id,
        )
        if created is None:
            raise HTTPException(status_code=500, detail="Review update failed")
        return _serialize_review(created)

    return SmallVariantReviewOut(variant_id=variant_id, tags=[])
