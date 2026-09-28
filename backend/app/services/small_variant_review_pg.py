from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import (
    SmallVariantCompoundHetReviewOut,
    SmallVariantCompoundHetReviewUpdate,
    SmallVariantReviewOut,
    SmallVariantReviewSummaryOut,
    SmallVariantReviewUpdate,
)
from .clickhouse_small_variants import (
    get_small_variant_family_record,
    has_affected_het_call,
    variants_share_gene,
)
from .clinical_audit_service import record_review_changes
from .family_metadata_context import FamilyMetadataContext
from .metadata_service import CurrentUser
# Re-exported here so existing `from ...small_variant_review_pg import _json_payload`
# imports (and a test) keep working.
from .review_pg_utils import (
    _has_stored_record,
    _lock_review,
    _raise_on_stale_review,
    _json_payload,  # noqa: F401  (re-exported for import-path compatibility)
    _merge_tag_metadata,
    _normalize_tags,
)


# Re-exported so existing import paths keep resolving from this module.
from .small_variant_review_acmg import build_evidence_snapshot, _normalize_acmg_payload, _deserialize_acmg, _acmg_json_or_none, _json_or_none  # noqa: F401
from .small_variant_review_repository import _postgres_bigint_or_none, _fetch_review_row, _fetch_review_rows, _fetch_compound_het_group_rows, _insert_review_row, _update_review_row, _delete_review_row, _clear_compound_het_group, _compound_het_clear_payload, _compound_het_field_names, _preserve_existing_compound_het, _document_has_individual_review, _document_has_compound_het_review, _review_document_has_any_content  # noqa: F401
from .small_variant_review_tags import list_small_variant_tag_definitions, create_small_variant_tag_definition, update_small_variant_tag_definition, delete_small_variant_tag_definition, DEFAULT_SMALL_VARIANT_TAGS, DEFAULT_SMALL_VARIANT_TAG_KEYS  # noqa: F401
from .small_variant_review_presets import list_small_variant_filter_presets, list_small_variant_filter_presets_for_owner, list_small_variant_filter_presets_for_admin, save_small_variant_filter_preset, delete_small_variant_filter_preset, delete_small_variant_filter_preset_for_owner  # noqa: F401


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
    acmg = _deserialize_acmg(document.get("acmg"))
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
        acmg=acmg,
        acmg_unreadable=acmg is None and _has_stored_record(document.get("acmg")),
    )


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
    if variant is None:
        raise HTTPException(status_code=404, detail="Variant not found")

    # Resolve the allowed-tag set lazily (a GROUP BY/ARRAY_AGG join) and only when
    # the regular or compound-het payload actually carries tags — the common no-tag
    # save skips the query; it runs at most once when tags are present.
    allowed_tags: set[str] | None = None

    async def _allowed_tags() -> set[str]:
        nonlocal allowed_tags
        if allowed_tags is None:
            allowed_tags = {
                definition.key
                for definition in await list_small_variant_tag_definitions(
                    session,
                    family_uuid=context.family_uuid,
                    project_ids=context.project_ids,
                )
            }
        return allowed_tags

    normalized_tags = _normalize_tags(payload.tags)
    if normalized_tags:
        unknown_tags = [tag for tag in normalized_tags if tag not in await _allowed_tags()]
        if unknown_tags:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown small-variant tag(s): {', '.join(sorted(unknown_tags))}",
            )

    normalized_note = (payload.note or "").strip() or None
    normalized_classification = (payload.classification or "").strip() or None
    now = datetime.now(timezone.utc)
    # One save of this variant's review at a time; the version check below then sees
    # every earlier save, so two reviewers can no longer overwrite each other (#513).
    await _lock_review(session, scope="svr", family_uuid=context.family_uuid, variant_id=variant_id)
    existing = await _fetch_review_row(
        session,
        family_uuid=context.family_uuid,
        variant_id=variant_id,
    )
    _raise_on_stale_review(payload, existing, _serialize_review)
    compound_het_data: dict[str, Any] | None = None
    compound_het_requested = "compound_het" in payload.model_fields_set
    compound_het_payload: SmallVariantCompoundHetReviewUpdate | None = (
        payload.compound_het if compound_het_requested else None
    )

    # ACMG classification: recompute the class/points server-side from the
    # submitted criteria. When not part of this request, preserve any existing blob.
    acmg_requested = "acmg" in payload.model_fields_set
    if acmg_requested:
        normalized_acmg, acmg_point_total, acmg_class = _normalize_acmg_payload(payload.acmg)
        # Freeze the evidence the classification was based on (None if it was cleared).
        acmg_evidence_snapshot = (
            build_evidence_snapshot(variant, now) if normalized_acmg else None
        )
    else:
        normalized_acmg = (existing or {}).get("acmg")
        acmg_point_total = (existing or {}).get("acmg_point_total")
        acmg_class = (existing or {}).get("acmg_class")
        acmg_evidence_snapshot = (existing or {}).get("acmg_evidence_snapshot")

    if compound_het_requested and compound_het_payload is not None:
        normalized_compound_het_classification = (
            (compound_het_payload.classification or "").strip() or None
        )
        normalized_compound_het_tags = _normalize_tags(compound_het_payload.tags)
        normalized_compound_het_note = (compound_het_payload.note or "").strip() or None
        compound_het_partner_id = (compound_het_payload.partner_variant_id or "").strip() or None
        unknown_compound_het_tags = (
            [tag for tag in normalized_compound_het_tags if tag not in await _allowed_tags()]
            if normalized_compound_het_tags
            else []
        )
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
                "compound_het_partner_variant_ids": [compound_het_partner_id],
            }
            partner_compound_het_data = {
                **shared_compound_het_data,
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
        and normalized_acmg is None
    ):
        if existing is not None and _document_has_compound_het_review(existing):
            data = {
                "variant_key": variant.variant_key if variant is not None else None,
                "variant_id": variant_id,
                "classification": None,
                "tags": [],
                "tag_metadata": {},
                "note": None,
                "acmg": None,
                "acmg_point_total": None,
                "acmg_class": None,
                "acmg_evidence_snapshot": None,
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
        "acmg": normalized_acmg,
        "acmg_point_total": acmg_point_total,
        "acmg_class": acmg_class,
        "acmg_evidence_snapshot": acmg_evidence_snapshot,
        "updated_by": user.username,
        "updated_at": now,
    }
    if compound_het_data is not None:
        data.update(compound_het_data)
    elif not compound_het_requested and existing is not None and _document_has_compound_het_review(existing):
        data.update(_preserve_existing_compound_het(existing))

    # The before -> after state for the immutable clinical audit trail (Phase 2).
    new_state = {
        "acmg_class": acmg_class,
        "acmg": normalized_acmg,
        "tags": normalized_tags,
        "note": normalized_note,
    }

    async def _audit() -> None:
        await record_review_changes(
            session,
            family_uuid=context.family_uuid,
            family_identifier=context.family_id,
            variant_id=variant_id,
            user=user,
            existing=existing,
            new_state=new_state,
        )

    if existing is not None:
        merged = {**existing, **data}
        if _review_document_has_any_content(merged):
            await _update_review_row(
                session,
                review_id=existing["id"],
                fields=merged,
            )
            await _audit()
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
        await _audit()
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
        await _audit()
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
