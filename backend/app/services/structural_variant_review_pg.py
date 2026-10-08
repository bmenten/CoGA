from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Sequence

from fastapi import HTTPException
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.sql import require_uuid
from ..schemas import (
    CnvAcmgClassificationPayload,
    SmallVariantReviewOut,
    SmallVariantReviewSummaryOut,
    SmallVariantReviewUpdate,
    StructuralVariantFilterPresetCreate,
    StructuralVariantFilterPresetOut,
)
from . import cnv_acmg_points
from .clinical_audit_service import record_structural_review_changes
from .family_metadata_context import FamilyMetadataContext
from .access_control import CurrentUser
from .review_pg_utils import (
    _added_tags,
    _has_stored_record,
    _lock_review,
    _raise_on_stale_review,
    _json_payload,
    _log_unreadable_classification,
    _merge_tag_metadata,
    _normalize_tags,
)
from .small_variant_review_tags import list_small_variant_tag_definitions
from .clickhouse_variant_records import StructuralVariantRecord
from .structural_variant_evidence import (
    build_structural_evidence_snapshot,
    fetch_structural_variant_record,
    read_structural_evidence_versions,
)


def _normalize_cnv_acmg_payload(
    payload: CnvAcmgClassificationPayload | None,
) -> tuple[dict[str, Any] | None, float | None, str | None]:
    """Validate submitted CNV criteria and recompute class/points server-side.

    Returns ``(blob, point_total, class_key)`` — ``blob`` is the JSON to store, or
    ``None`` to clear. The client-supplied total is ignored.
    """

    if payload is None or not payload.criteria:
        return None, None, None
    kind = (payload.kind or "loss").strip().lower()
    if not cnv_acmg_points.is_valid_kind(kind):
        raise HTTPException(status_code=400, detail=f"Invalid CNV kind: {payload.kind}")
    normalized_criteria: list[dict[str, Any]] = []
    for criterion in payload.criteria:
        code = (criterion.code or "").strip()
        if not cnv_acmg_points.is_valid_code(kind, code):
            raise HTTPException(status_code=400, detail=f"Unknown CNV criterion: {criterion.code}")
        normalized_criteria.append(
            {
                "code": code,
                "points": cnv_acmg_points.clamp_points(kind, code, criterion.points),
                "accepted": bool(criterion.accepted),
                "evidence": (criterion.evidence or "").strip() or None,
                "auto_suggested": bool(criterion.auto_suggested),
            }
        )
    point_total, class_key, class_label = cnv_acmg_points.compute_classification(
        kind, normalized_criteria
    )
    blob = {
        "kind": kind,
        "criteria": normalized_criteria,
        "point_total": point_total,
        "classification": class_label,
    }
    return blob, point_total, class_key


def _cnv_json_or_none(value: Any) -> str | None:
    if not value:
        return None
    return _json_payload(value)


def _deserialize_cnv_acmg(value: Any) -> CnvAcmgClassificationPayload | None:
    """The stored classification, or None when there is none or it cannot be read.

    ``None`` alone cannot tell those apart, so an unreadable record is logged here and
    the review serializer marks it (``acmg_unreadable``) instead of dropping it (#514).
    """
    if not value:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError) as exc:
            _log_unreadable_classification("CNV classification", exc)
            return None
        if value is None:  # a stored JSON null is no record, not a broken one
            return None
    try:
        return CnvAcmgClassificationPayload.model_validate(value)
    except Exception as exc:  # noqa: BLE001 - served as unreadable, never raised
        _log_unreadable_classification("CNV classification", exc)
        return None


def _serialize_review(document: dict[str, Any]) -> SmallVariantReviewOut:
    cnv_acmg = _deserialize_cnv_acmg(document.get("cnv_acmg"))
    return SmallVariantReviewOut(
        variant_id=str(document.get("variant_id") or ""),
        classification=document.get("classification"),
        tags=_normalize_tags(document.get("tags", [])),
        tag_metadata=document.get("tag_metadata") or {},
        note=document.get("note"),
        updated_by=document.get("updated_by"),
        updated_at=document.get("updated_at"),
        cnv_acmg=cnv_acmg,
        acmg_unreadable=cnv_acmg is None and _has_stored_record(document.get("cnv_acmg")),
    )


# A structural-variant preset is its owner's, for the family it is saved in (scope 'family')
# or, reusable, for every family they can open (scope 'global', no family): one per family,
# scope, owner and name.
_PRESET_COLUMNS = """
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
"""


def _serialize_preset(row: dict[str, Any]) -> StructuralVariantFilterPresetOut:
    return StructuralVariantFilterPresetOut(
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
                cnv_acmg,
                cnv_point_total,
                cnv_class,
                cnv_evidence_snapshot,
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
                cnv_acmg,
                cnv_point_total,
                cnv_class,
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
    normalized_tags = _normalize_tags(payload.tags)
    normalized_note = (payload.note or "").strip() or None
    normalized_classification = (payload.classification or "").strip() or None
    # The CNV (ClinGen) scoring, as a small-variant save treats ``acmg``: a save that
    # leaves ``cnv_acmg`` out (the tag toggle, the review dialog) keeps the stored scoring;
    # one that sends it replaces the scoring, and sending null or no criteria clears it.
    cnv_requested = "cnv_acmg" in payload.model_fields_set
    cnv_blob, cnv_point_total, cnv_class = _normalize_cnv_acmg_payload(payload.cnv_acmg)
    # A scoring that is saved freezes the evidence it rests on, as an ACMG save does for a
    # small variant: the SV as the family's data holds it, and the annotation versions
    # behind it, read before the lock. Without SV storage (no assembly) there is nothing
    # to read, and the scoring is saved without it; sign-out counts that as unverified.
    evidence_source: tuple[StructuralVariantRecord, dict[str, str]] | None = None
    if cnv_requested and cnv_blob is not None and getattr(context, "assembly_name", None):
        sv_record = await fetch_structural_variant_record(context, normalized_variant_id)
        if sv_record is None:
            raise HTTPException(status_code=404, detail="Structural variant not found")
        evidence_source = (
            sv_record,
            await read_structural_evidence_versions(session, context=context, user=user),
        )
    # One save of this variant's review at a time, checked against the version the
    # client loaded (#513).
    await _lock_review(
        session, scope="strv", family_uuid=context.family_uuid, variant_id=normalized_variant_id
    )
    existing = await _fetch_review_row(
        session,
        family_uuid=context.family_uuid,
        variant_id=normalized_variant_id,
    )
    _raise_on_stale_review(payload, existing, _serialize_review)
    # Only a tag the save adds must be one the family may use, and only then is the
    # allowed-tag set (a GROUP BY/ARRAY_AGG join) resolved; one the review holds is
    # kept, a deleted tag included (see _added_tags).
    added_tags = _added_tags(normalized_tags, (existing or {}).get("tags"))
    if added_tags:
        allowed_tags = {
            definition.key
            for definition in await list_small_variant_tag_definitions(
                session,
                family_uuid=context.family_uuid,
                project_ids=context.project_ids,
            )
        }
        unknown_tags = [tag for tag in added_tags if tag not in allowed_tags]
        if unknown_tags:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown structural-variant tag(s): {', '.join(sorted(unknown_tags))}",
            )
    if not cnv_requested and existing is not None:
        # Kept exactly as stored, an unreadable record included (#514).
        cnv_blob = existing.get("cnv_acmg") or None
        cnv_point_total = existing.get("cnv_point_total")
        cnv_class = existing.get("cnv_class")
    now = datetime.now(timezone.utc)

    async def _audit(new_state: dict[str, Any] | None) -> None:
        # The before -> after of this save in the immutable clinical audit trail, on the
        # family's hash chain and in this save's transaction, as for small variants.
        await record_structural_review_changes(
            session,
            family_uuid=context.family_uuid,
            family_identifier=context.family_id,
            variant_id=normalized_variant_id,
            user=user,
            existing=existing,
            new_state=new_state,
        )

    if (
        normalized_note is None
        and normalized_classification is None
        and not normalized_tags
        and cnv_blob is None
    ):
        if existing is not None:
            await session.execute(
                text("DELETE FROM structural_variant_reviews WHERE id = CAST(:review_id AS uuid)"),
                {"review_id": existing["id"]},
            )
            await _audit(None)
            await session.commit()
        return SmallVariantReviewOut(variant_id=normalized_variant_id, tags=[])

    # Kept with a scoring the save leaves out; replaced (or cleared) with one it sends.
    if evidence_source is not None:
        cnv_evidence_snapshot = build_structural_evidence_snapshot(*evidence_source, captured_at=now)
    elif cnv_requested:
        cnv_evidence_snapshot = None
    else:
        cnv_evidence_snapshot = (existing or {}).get("cnv_evidence_snapshot")
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
        "cnv_acmg_json": _cnv_json_or_none(cnv_blob),
        "cnv_point_total": cnv_point_total,
        "cnv_class": cnv_class,
        "cnv_evidence_snapshot_json": _cnv_json_or_none(cnv_evidence_snapshot),
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
                    cnv_acmg = CAST(:cnv_acmg_json AS jsonb),
                    cnv_point_total = :cnv_point_total,
                    cnv_class = :cnv_class,
                    cnv_evidence_snapshot = CAST(:cnv_evidence_snapshot_json AS jsonb),
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
                    cnv_acmg,
                    cnv_point_total,
                    cnv_class,
                    cnv_evidence_snapshot,
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
                    CAST(:cnv_acmg_json AS jsonb),
                    :cnv_point_total,
                    :cnv_class,
                    CAST(:cnv_evidence_snapshot_json AS jsonb),
                    :updated_by,
                    :created_at,
                    :updated_at
                )
                """
            ),
            {**fields, "family_id": context.family_uuid, "created_at": now},
        )
    await _audit(
        {
            "classification": normalized_classification,
            "tags": normalized_tags,
            "note": normalized_note,
            "cnv_acmg": cnv_blob,
            "cnv_point_total": cnv_point_total,
            "cnv_class": cnv_class,
        }
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
) -> list[StructuralVariantFilterPresetOut]:
    result = await session.execute(
        text(
            f"""
            SELECT {_PRESET_COLUMNS}
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
    payload: StructuralVariantFilterPresetCreate,
    user: CurrentUser,
) -> StructuralVariantFilterPresetOut:
    """Create the owner's preset of that name and scope, for this family or reusable, or
    replace its content if it exists."""
    normalized_name = payload.name.strip()
    if not normalized_name:
        raise HTTPException(status_code=400, detail="Preset name cannot be blank")
    now = datetime.now(timezone.utc)
    params = {
        "family_id": family_uuid if payload.scope == "family" else None,
        "scope": payload.scope,
        "owner": user.username,
        "name": normalized_name,
        "description": (payload.description or "").strip() or None,
        "filters_json": _json_payload(payload.filters),
        "sample_filters_json": _json_payload(payload.sample_filters),
        "sample_templates_json": _json_payload(payload.sample_templates),
        "now": now,
    }
    # The conflict target is the table's unique index (idx_structural_variant_filter_presets_unique).
    result = await session.execute(
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
                CAST(:family_id AS uuid),
                :scope,
                :owner,
                :name,
                :description,
                CAST(:filters_json AS jsonb),
                CAST(:sample_filters_json AS jsonb),
                CAST(:sample_templates_json AS jsonb),
                :now,
                :now
            )
            ON CONFLICT (
                COALESCE(family_id, '00000000-0000-0000-0000-000000000000'::uuid),
                scope,
                owner,
                name
            )
            DO UPDATE SET
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


async def delete_structural_variant_filter_preset(
    session: AsyncSession,
    *,
    family_uuid: str,
    preset_id: str,
    user: CurrentUser,
) -> None:
    preset_uuid = require_uuid(preset_id, "Preset not found", status_code=404)
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
