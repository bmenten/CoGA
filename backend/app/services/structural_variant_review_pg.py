from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Iterable, Sequence
from uuid import UUID

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import (
    SmallVariantFilterPresetCreate,
    SmallVariantFilterPresetOut,
    SmallVariantReviewOut,
    SmallVariantReviewSummaryOut,
    SmallVariantReviewUpdate,
)
from .family_metadata_context import FamilyMetadataContext
from .metadata_service import CurrentUser
from .small_variant_review_pg import list_small_variant_tag_definitions


def _require_uuid(value: str, detail: str) -> str:
    try:
        UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=detail) from exc
    return value


def _json_payload(value: Any) -> str:
    return json.dumps(jsonable_encoder(value if value is not None else {}))


def _normalize_tags(tags: Iterable[str]) -> list[str]:
    return sorted({str(tag).strip() for tag in tags if str(tag).strip()})


def _serialize_review(document: dict[str, Any]) -> SmallVariantReviewOut:
    return SmallVariantReviewOut(
        variant_id=str(document.get("variant_id") or ""),
        classification=document.get("classification"),
        tags=_normalize_tags(document.get("tags", [])),
        tag_metadata=document.get("tag_metadata") or {},
        note=document.get("note"),
        updated_by=document.get("updated_by"),
        updated_at=document.get("updated_at"),
    )


def _serialize_preset(row: dict[str, Any]) -> SmallVariantFilterPresetOut:
    return SmallVariantFilterPresetOut(
        _id=str(row["id"]),
        family_id=row.get("family_id"),
        scope=row["scope"],
        owner=row["owner"],
        name=row["name"],
        description=row.get("description"),
        filters=row.get("filters") or {},
        sample_filters=row.get("sample_filters") or {},
        sample_templates=row.get("sample_templates") or {},
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


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
            merged[tag] = {"updated_by": username, "updated_at": timestamp}
    return merged


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
                variant_key,
                variant_id,
                classification,
                tags,
                tag_metadata,
                note,
                updated_by,
                updated_at
            FROM structural_variant_reviews
            WHERE family_id = CAST(:family_id AS uuid)
              AND variant_id = :variant_id
            """
        ),
        {"family_id": family_uuid, "variant_id": variant_id},
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None


async def get_structural_variant_review_map(
    session: AsyncSession,
    *,
    family_uuid: str,
    variant_ids: Sequence[str],
) -> dict[str, SmallVariantReviewOut]:
    normalized_variant_ids = [str(variant_id).strip() for variant_id in variant_ids if str(variant_id).strip()]
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
                updated_by,
                updated_at
            FROM structural_variant_reviews
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


async def list_matching_structural_variant_review_ids(
    session: AsyncSession,
    *,
    family_uuid: str,
    classifications: Sequence[str] | None = None,
    tags: Sequence[str] | None = None,
    has_notes: bool = False,
) -> set[str]:
    normalized_classifications = {value.strip() for value in (classifications or []) if str(value).strip()}
    normalized_tags = {value.strip() for value in (tags or []) if str(value).strip()}
    if not normalized_classifications and not normalized_tags and not has_notes:
        return set()
    result = await session.execute(
        text(
            """
            SELECT variant_id, classification, tags, note
            FROM structural_variant_reviews
            WHERE family_id = CAST(:family_id AS uuid)
            """
        ),
        {"family_id": family_uuid},
    )
    matching_ids: set[str] = set()
    for document in (dict(row) for row in result.mappings().all()):
        variant_id = str(document.get("variant_id") or "")
        if not variant_id:
            continue
        matches_classification = (
            not normalized_classifications
            or str(document.get("classification") or "").strip() in normalized_classifications
        )
        matches_tags = not normalized_tags or bool(
            set(_normalize_tags(document.get("tags", []))).intersection(normalized_tags)
        )
        matches_notes = not has_notes or bool(str(document.get("note") or "").strip())
        if matches_classification and matches_tags and matches_notes:
            matching_ids.add(variant_id)
    return matching_ids


async def get_structural_variant_review_summary(
    session: AsyncSession,
    *,
    family_uuid: str,
) -> SmallVariantReviewSummaryOut:
    result = await session.execute(
        text(
            """
            SELECT variant_id, classification, tags, note
            FROM structural_variant_reviews
            WHERE family_id = CAST(:family_id AS uuid)
            """
        ),
        {"family_id": family_uuid},
    )
    reviewed_variant_ids: set[str] = set()
    noted_variant_ids: set[str] = set()
    tag_variant_ids: dict[str, set[str]] = {}

    for document in (dict(row) for row in result.mappings().all()):
        variant_id = str(document.get("variant_id") or "")
        if not variant_id:
            continue
        if (
            str(document.get("classification") or "").strip()
            or str(document.get("note") or "").strip()
            or _normalize_tags(document.get("tags", []))
        ):
            reviewed_variant_ids.add(variant_id)
        if str(document.get("note") or "").strip():
            noted_variant_ids.add(variant_id)
        for tag in _normalize_tags(document.get("tags", [])):
            tag_variant_ids.setdefault(tag, set()).add(variant_id)

    return SmallVariantReviewSummaryOut(
        reviewed_variant_count=len(reviewed_variant_ids),
        note_count=len(noted_variant_ids),
        tag_counts={
            tag: len(variant_ids)
            for tag, variant_ids in sorted(tag_variant_ids.items(), key=lambda entry: entry[0])
            if variant_ids
        },
    )


async def upsert_structural_variant_review(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    variant_id: str,
    payload: SmallVariantReviewUpdate,
    user: CurrentUser,
) -> SmallVariantReviewOut:
    normalized_variant_id = str(variant_id).strip()
    if not normalized_variant_id:
        raise HTTPException(status_code=400, detail="Variant id is required")
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
            detail=f"Unknown structural-variant tag(s): {', '.join(sorted(unknown_tags))}",
        )

    normalized_note = (payload.note or "").strip() or None
    normalized_classification = (payload.classification or "").strip() or None
    existing = await _fetch_review_row(
        session,
        family_uuid=context.family_uuid,
        variant_id=normalized_variant_id,
    )
    now = datetime.now(timezone.utc)

    if normalized_note is None and normalized_classification is None and not normalized_tags:
        if existing is not None:
            await session.execute(
                text("DELETE FROM structural_variant_reviews WHERE id = CAST(:review_id AS uuid)"),
                {"review_id": existing["id"]},
            )
            await session.commit()
        return SmallVariantReviewOut(variant_id=normalized_variant_id, tags=[])

    fields = {
        "variant_id": normalized_variant_id,
        "classification": normalized_classification,
        "tags_json": _json_payload(normalized_tags),
        "tag_metadata_json": _json_payload(
            _merge_tag_metadata(
                existing_metadata=(existing or {}).get("tag_metadata"),
                previous_tags=(existing or {}).get("tags", []),
                next_tags=normalized_tags,
                username=user.username,
                timestamp=now,
            )
        ),
        "note": normalized_note,
        "updated_by": user.username,
        "updated_at": now,
    }
    if existing is not None:
        await session.execute(
            text(
                """
                UPDATE structural_variant_reviews
                SET
                    classification = :classification,
                    tags = CAST(:tags_json AS jsonb),
                    tag_metadata = CAST(:tag_metadata_json AS jsonb),
                    note = :note,
                    updated_by = :updated_by,
                    updated_at = :updated_at
                WHERE id = CAST(:review_id AS uuid)
                """
            ),
            {**fields, "review_id": existing["id"]},
        )
    else:
        await session.execute(
            text(
                """
                INSERT INTO structural_variant_reviews (
                    family_id,
                    variant_id,
                    classification,
                    tags,
                    tag_metadata,
                    note,
                    updated_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    CAST(:family_id AS uuid),
                    :variant_id,
                    :classification,
                    CAST(:tags_json AS jsonb),
                    CAST(:tag_metadata_json AS jsonb),
                    :note,
                    :updated_by,
                    :created_at,
                    :updated_at
                )
                """
            ),
            {**fields, "family_id": context.family_uuid, "created_at": now},
        )
    await session.commit()
    refreshed = await _fetch_review_row(
        session,
        family_uuid=context.family_uuid,
        variant_id=normalized_variant_id,
    )
    if refreshed is None:
        raise HTTPException(status_code=500, detail="Review update failed")
    return _serialize_review(refreshed)


async def list_structural_variant_filter_presets(
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
            FROM structural_variant_filter_presets
            WHERE (scope = 'family' AND family_id = CAST(:family_id AS uuid) AND owner = :owner)
               OR (scope = 'global' AND owner = :owner)
            ORDER BY CASE WHEN scope = 'family' THEN 0 ELSE 1 END, lower(name)
            """
        ),
        {"family_id": family_uuid, "owner": user.username},
    )
    return [_serialize_preset(dict(row)) for row in result.mappings().all()]


async def save_structural_variant_filter_preset(
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
    family_match_sql = (
        "family_id = CAST(:family_id AS uuid)"
        if scoped_family_uuid is not None
        else "family_id IS NULL"
    )
    result = await session.execute(
        text(
            f"""
            SELECT id::text AS id
            FROM structural_variant_filter_presets
            WHERE scope = :scope
              AND owner = :owner
              AND name = :name
              AND {family_match_sql}
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
        "filters_json": _json_payload(payload.filters),
        "sample_filters_json": _json_payload(payload.sample_filters),
        "sample_templates_json": _json_payload(payload.sample_templates),
        "updated_at": now,
    }
    if existing_id is not None:
        await session.execute(
            text(
                """
                UPDATE structural_variant_filter_presets
                SET description = :description,
                    filters = CAST(:filters_json AS jsonb),
                    sample_filters = CAST(:sample_filters_json AS jsonb),
                    sample_templates = CAST(:sample_templates_json AS jsonb),
                    updated_at = :updated_at
                WHERE id = CAST(:preset_id AS uuid)
                """
            ),
            {**params, "preset_id": existing_id},
        )
    else:
        family_insert_sql = "CAST(:family_id AS uuid)" if scoped_family_uuid is not None else "NULL"
        await session.execute(
            text(
                f"""
                INSERT INTO structural_variant_filter_presets (
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
                    {family_insert_sql},
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
            {**params, "created_at": now},
        )
    await session.commit()
    refreshed = await session.execute(
        text(
            f"""
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
            FROM structural_variant_filter_presets
            WHERE scope = :scope
              AND owner = :owner
              AND name = :name
              AND {family_match_sql}
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


async def delete_structural_variant_filter_preset(
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
            FROM structural_variant_filter_presets
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
        text("DELETE FROM structural_variant_filter_presets WHERE id = CAST(:preset_id AS uuid)"),
        {"preset_id": preset_uuid},
    )
    await session.commit()
