from __future__ import annotations

from typing import Any, Sequence
from uuid import uuid4

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text
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
from .clinical_audit_service import (
    CompoundHetPairChange,
    compound_het_pair_state,
    record_compound_het_pair_changes,
    record_review_changes,
)
from .analysis_reviews import ScopedReview, effective_reviews_with_content, scope_review_clause
from .analysis_scope import AnalysisScope
from .family_metadata_context import FamilyMetadataContext
from .access_control import CurrentUser
from .review_pg_utils import (
    _added_tags,
    _has_stored_record,
    _lock_review,
    _raise_on_stale_review,
    _merge_tag_metadata,
    _normalize_tags,
    _note_authorship,
    _save_time,
    _stored_classification,
    review_class_filter,
    review_shows_class,
)
from .small_variant_review_acmg import (
    freeze_evidence_snapshot,
    _normalize_acmg_payload,
    _deserialize_acmg,
)
from .small_variant_review_repository import (
    _fetch_compound_het_group_rows,
    _fetch_review_rows_for_variants,
    _fetch_scoped_review,
    _write_own_review,
    _clear_compound_het_group,
    _compound_het_clear_payload,
    _document_has_compound_het_review,
    _review_document_has_any_content,
)
from .small_variant_review_tags import list_small_variant_tag_definitions


def _serialize_tag_metadata(
    *,
    document: dict[str, Any],
    tags_key: str,
    metadata_key: str,
) -> dict[str, dict[str, Any]]:
    """Who added each tag and when: every save records it for each tag it writes
    (``_merge_tag_metadata``)."""
    metadata = document[metadata_key]
    return {
        tag: {"updated_by": metadata[tag].get("updated_by"), "updated_at": metadata[tag].get("updated_at")}
        for tag in _normalize_tags(document.get(tags_key, []))
    }


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
        ),
        note=document.get("note"),
        note_updated_by=document["note_updated_by"],
        note_updated_at=document["note_updated_at"],
        updated_by=document.get("updated_by"),
        updated_at=document.get("updated_at"),
        compound_het=_serialize_compound_het(document),
        acmg=acmg,
        acmg_unreadable=acmg is None and _has_stored_record(document.get("acmg")),
    )


async def _effective_family_reviews(
    session: AsyncSession, *, family_uuid: str, scope: AnalysisScope
) -> list[dict[str, Any]]:
    """The family's reviews the scope reads, one per variant (``analysis_reviews``), the empty
    ones left out: the columns the review summary and the review filters read."""
    params: dict[str, Any] = {"family_id": family_uuid}
    belongs = scope_review_clause(scope, params)
    result = await session.execute(
        text(
            f"""
            SELECT
                variant_id,
                analysis,
                classification,
                tags,
                note,
                acmg,
                compound_het_group_id,
                compound_het_partner_variant_ids,
                compound_het_tags,
                compound_het_note,
                compound_het_classification,
                updated_at
            FROM small_variant_reviews
            WHERE family_id = CAST(:family_id AS uuid)
              AND {belongs}
            """
        ),
        params,
    )
    return list(
        effective_reviews_with_content(
            (dict(row) for row in result.mappings().all()), scope, _review_document_has_any_content
        ).values()
    )


async def get_small_variant_review_summary(
    session: AsyncSession,
    *,
    family_uuid: str,
    scope: AnalysisScope,
) -> SmallVariantReviewSummaryOut:
    """The counts of the family's reviews in the analysis ``scope`` reads them for: its own
    and the shared ones (``analysis_reviews``)."""
    # Every column _review_document_has_any_content reads: a review that holds only an ACMG
    # record, or only a compound-het pairing, is a reviewed variant too.
    reviewed_variant_ids: set[str] = set()
    noted_variant_ids: set[str] = set()
    tag_variant_ids: dict[str, set[str]] = {}
    for document in await _effective_family_reviews(session, family_uuid=family_uuid, scope=scope):
        variant_key = str(document["variant_id"])
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
    scope: AnalysisScope,
    classifications: Sequence[str] | None = None,
    tags: Sequence[str] | None = None,
    has_notes: bool = False,
) -> set[str]:
    """The variants of the family whose review, or compound-het pair review, the review filters
    keep: a classification, a review tag, a saved note (each set filter must hold).

    A classification keeps a review by the class it shows (``review_shows_class``, the family
    page's rule): a bare class word in its text, else its most severe class tag, else
    its text, whatever the case. So *Likely pathogenic - class 4* keeps a review
    classified by its class tag alone, and not one whose text says another class beside that
    tag. The pair review is read alike, from its own text and tags. The Variant Explorer's filter
    is the same rule in SQL (``_variant_ids_matching_reviews``). Each variant's review is the
    one the analysis ``scope`` reads (``analysis_reviews``).
    """
    requested_classes = set(review_class_filter(classifications))
    normalized_tags = {
        value.strip() for value in (tags or []) if str(value).strip()
    }
    if not requested_classes and not normalized_tags and not has_notes:
        return set()

    matching_ids: set[str] = set()
    for document in await _effective_family_reviews(session, family_uuid=family_uuid, scope=scope):
        variant_id = str(document.get("variant_id") or "").strip()
        if not variant_id:
            continue
        matches_classification = not requested_classes or (
            review_shows_class(document.get("classification"), document.get("tags"), requested_classes)
            or review_shows_class(
                document.get("compound_het_classification"),
                document.get("compound_het_tags"),
                requested_classes,
            )
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
    scope: AnalysisScope,
) -> dict[str, SmallVariantReviewOut]:
    """The review of each of these variants the analysis ``scope`` reads: its own, else the
    shared one (``analysis_reviews``); a variant without one, or whose review is empty, has
    none."""
    normalized_variant_ids = [
        str(variant_id).strip() for variant_id in variant_ids if str(variant_id).strip()
    ]
    if not normalized_variant_ids:
        return {}
    # The same columns as a single-review read, the ACMG record included: every list of
    # small variants (the tables, the report, NIPT, mtDNA) carries its reviews from here,
    # and the ACMG dialog opened from a list is seeded with what it carries.
    rows = await _fetch_review_rows_for_variants(
        session, family_uuid=family_uuid, variant_ids=normalized_variant_ids, scope=scope
    )
    return {
        variant_id: _serialize_review(document)
        for variant_id, document in effective_reviews_with_content(
            rows, scope, _review_document_has_any_content
        ).items()
    }


def _review_read(reviews: ScopedReview | None) -> dict[str, Any] | None:
    """The review a save in a scope reads of a variant (its effective review: the scope's own,
    else the shared one), or None when it has none or that review is empty."""
    review = reviews.effective if reviews is not None else None
    return review if review is not None and _review_document_has_any_content(review) else None


async def _reviews_a_pair_save_writes(
    session: AsyncSession,
    *,
    family_uuid: str,
    variant_ids: Sequence[str],
    scope: AnalysisScope,
) -> tuple[set[str], dict[str, ScopedReview]]:
    """The variants whose reviews a compound-het pair save in the scope may write, and the
    reviews of ``variant_ids`` (the saved variant and its partner) in the scope, as stored.

    Those are the two variants, and every variant of a pair either is in, in the scope: the
    save breaks such a pair up, and ``_clear_compound_het_group`` writes each of its reviews.
    """
    writes = set(variant_ids)
    reviews: dict[str, ScopedReview] = {}
    for variant_id in variant_ids:
        review = await _fetch_scoped_review(
            session, family_uuid=family_uuid, variant_id=variant_id, scope=scope
        )
        reviews[variant_id] = review
        group_id = (_review_read(review) or {}).get("compound_het_group_id")
        if group_id:
            members = await _fetch_compound_het_group_rows(
                session, family_uuid=family_uuid, group_id=str(group_id), scope=scope
            )
            writes.update(str(member["variant_id"]) for member in members)
    return writes, reviews


async def _lock_pair_save_reviews(
    session: AsyncSession,
    *,
    family_uuid: str,
    variant_id: str,
    partner_variant_id: str | None,
    scope: AnalysisScope,
) -> tuple[ScopedReview, ScopedReview | None]:
    """Lock every review a compound-het pair save writes; return the variant's and the
    partner's reviews in the scope, read under their locks.

    A pair save writes the partner's review as well as the variant's, and breaking up a pair
    rewrites the review of each of its variants. Each such review is locked as a save of it
    locks it, so no save of it runs between the read here and this save's write: the partner
    review someone saved meanwhile is read, never overwritten by a copy read before it. The
    reviews are looked up first and locked in one order, so two pair saves never each hold a
    lock the other waits for. Read again under the locks, a pair save that now writes a
    review it did not lock (a pair changed in between) is refused like a stale save.
    """
    variant_ids = [variant_id] + ([partner_variant_id] if partner_variant_id else [])
    planned, _ = await _reviews_a_pair_save_writes(
        session, family_uuid=family_uuid, variant_ids=variant_ids, scope=scope
    )
    for locked_variant_id in sorted(planned):
        await _lock_review(session, scope="svr", family_uuid=family_uuid, variant_id=locked_variant_id)
    writes, reviews = await _reviews_a_pair_save_writes(
        session, family_uuid=family_uuid, variant_ids=variant_ids, scope=scope
    )
    existing = _review_read(reviews[variant_id])
    if not writes <= planned:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "review_conflict",
                "message": (
                    "A compound-het pair of this variant or its partner was changed while this "
                    "review was being saved; your changes were not saved. The current review "
                    "has been loaded."
                ),
                "current": (
                    jsonable_encoder(_serialize_review(existing), by_alias=True)
                    if existing is not None
                    else None
                ),
            },
        )
    return reviews[variant_id], reviews.get(partner_variant_id) if partner_variant_id else None


async def _commit_stored_review(
    session: AsyncSession,
    *,
    family_uuid: str,
    variant_id: str,
    scope: AnalysisScope,
) -> SmallVariantReviewOut:
    """Commit a save, and return the variant's review in the scope as stored: the scope's own.

    The review is read back in the save's transaction, before the commit: a save that did not
    store it is refused and commits nothing, the clinical audit events it wrote included.
    """
    stored = (
        await _fetch_scoped_review(session, family_uuid=family_uuid, variant_id=variant_id, scope=scope)
    ).own
    if stored is None or not _review_document_has_any_content(stored):
        raise HTTPException(status_code=500, detail="Review update failed")
    await session.commit()
    return _serialize_review(stored)


def _pair_removed(variant_id: str, holder: dict[str, Any]) -> CompoundHetPairChange:
    """The removal of the compound-het pair review a variant's review row holds."""
    return CompoundHetPairChange(
        variant_id=variant_id,
        before=compound_het_pair_state(holder),
        after=None,
        group_id=str(holder.get("compound_het_group_id") or "") or None,
    )


async def upsert_small_variant_review(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    variant_id: str,
    payload: SmallVariantReviewUpdate,
    user: CurrentUser,
) -> SmallVariantReviewOut:
    """Save the variant's review in the analysis the family is read for (``context.scope``):
    the analysis's own review, stored under it (``analysis_reviews``). A family that holds
    several analyses with small variants of their own names the analysis (``AnalysisNotNamed``,
    422 otherwise). A save in an analysis that has no review of its own of the variant writes
    one, from the shared review it read and the save's values; the shared review stays as it
    is."""
    scope = context.scope.require_named()
    # The analysis's own callset: the other analyses' are left out of the variant read, so the
    # evidence a classification freezes is the analysis's own.
    left_out_sources = scope.left_out_sources("small_variants")
    assembly_name = context.assembly_name
    variant = None
    if assembly_name:
        variant = await get_small_variant_family_record(
            assembly_name=assembly_name,
            family_guid=context.family_uuid,
            variant_id=variant_id,
            left_out_sources=left_out_sources,
        )
    if variant is None or not assembly_name:
        raise HTTPException(status_code=404, detail="Variant not found")

    # Resolve the allowed-tag set lazily (a GROUP BY/ARRAY_AGG join) and only when
    # the regular or compound-het payload adds a tag the stored review does not hold —
    # the common save skips the query; it runs at most once.
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

    async def _refuse_unknown_added_tags(tags: list[str], held: Any) -> None:
        # Only a tag the save adds must be one the family may use; one the review holds
        # is kept, a deleted tag included (see _added_tags).
        added = _added_tags(tags, held)
        unknown_tags = [tag for tag in added if tag not in await _allowed_tags()] if added else []
        if unknown_tags:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown small-variant tag(s): {', '.join(sorted(unknown_tags))}",
            )

    normalized_tags = _normalize_tags(payload.tags)
    normalized_note = (payload.note or "").strip() or None
    # A class label in another case is stored in the label's own case.
    normalized_classification = _stored_classification(payload.classification)
    compound_het_requested = "compound_het" in payload.model_fields_set
    compound_het_payload: SmallVariantCompoundHetReviewUpdate | None = (
        payload.compound_het if compound_het_requested else None
    )
    compound_het_partner_id = (
        (compound_het_payload.partner_variant_id or "").strip() or None
        if compound_het_payload is not None
        else None
    )
    partner_reviews: ScopedReview | None = None
    # One save of this variant's review at a time, in every analysis; the version check below
    # then sees every earlier save, so two reviewers never overwrite each other.
    if compound_het_requested:
        # A pair save writes the partner's review, and the reviews of any pair it breaks
        # up, as well: each is locked too, and the partner's checked like the variant's.
        reviews, partner_reviews = await _lock_pair_save_reviews(
            session,
            family_uuid=context.family_uuid,
            variant_id=variant_id,
            partner_variant_id=(
                compound_het_partner_id if compound_het_partner_id != variant_id else None
            ),
            scope=scope,
        )
    else:
        await _lock_review(session, scope="svr", family_uuid=context.family_uuid, variant_id=variant_id)
        reviews = await _fetch_scoped_review(
            session,
            family_uuid=context.family_uuid,
            variant_id=variant_id,
            scope=scope,
        )
    # The reviews the client was shown, which the save is checked against and builds on: the
    # analysis's own, else the shared one.
    existing = _review_read(reviews)
    partner_existing = _review_read(partner_reviews)
    _raise_on_stale_review(payload, existing, _serialize_review)
    if compound_het_payload is not None and compound_het_partner_id not in (None, variant_id):
        _raise_on_stale_review(
            compound_het_payload,
            partner_existing,
            _serialize_review,
            expected_field="partner_expected_updated_at",
            partner_variant_id=compound_het_partner_id,
        )
    # Taken under the locks, after the reads: a review this save writes is never stamped
    # earlier than the version it replaces.
    now = _save_time(
        existing, partner_existing, reviews.own, partner_reviews.own if partner_reviews is not None else None
    )
    await _refuse_unknown_added_tags(normalized_tags, (existing or {}).get("tags"))
    compound_het_data: dict[str, Any] | None = None
    # The compound-het pair reviews this save records, removes or changes, each as the
    # review row of one of its variants held it: audited with the save.
    pair_changes: list[CompoundHetPairChange] = []

    # ACMG classification: recompute the class/points server-side from the
    # submitted criteria. When not part of this request, preserve any existing blob.
    acmg_requested = "acmg" in payload.model_fields_set
    if acmg_requested:
        normalized_acmg, acmg_point_total, acmg_class = _normalize_acmg_payload(payload.acmg)
        # Freeze the evidence the classification was based on (None if it was cleared).
        acmg_evidence_snapshot = (
            await freeze_evidence_snapshot(session, variant, now, assembly_id=context.assembly_id)
            if normalized_acmg
            else None
        )
    else:
        normalized_acmg = (existing or {}).get("acmg")
        acmg_point_total = (existing or {}).get("acmg_point_total")
        acmg_class = (existing or {}).get("acmg_class")
        acmg_evidence_snapshot = (existing or {}).get("acmg_evidence_snapshot")

    if compound_het_requested and compound_het_payload is not None:
        normalized_compound_het_classification = _stored_classification(compound_het_payload.classification)
        normalized_compound_het_tags = _normalize_tags(compound_het_payload.tags)
        normalized_compound_het_note = (compound_het_payload.note or "").strip() or None
        await _refuse_unknown_added_tags(
            normalized_compound_het_tags, (existing or {}).get("compound_het_tags")
        )
        if compound_het_partner_id:
            if compound_het_partner_id == variant_id:
                raise HTTPException(status_code=400, detail="Compound-het partner must be a different variant")
            partner_variant = await get_small_variant_family_record(
                assembly_name=assembly_name,
                family_guid=context.family_uuid,
                variant_id=compound_het_partner_id,
                left_out_sources=left_out_sources,
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
            # partner_existing was read under the partner's lock (_lock_pair_save_reviews):
            # the partner's own review below is written back as it is stored now.
            existing_group_id = existing.get("compound_het_group_id") if existing else None
            partner_group_id = partner_existing.get("compound_het_group_id") if partner_existing else None
            target_group_id = None
            if (
                existing_group_id
                and existing is not None
                and partner_existing is not None
                and existing_group_id == partner_group_id
                and compound_het_partner_id in (existing.get("compound_het_partner_variant_ids") or [])
                and variant_id in (partner_existing.get("compound_het_partner_variant_ids") or [])
            ):
                target_group_id = existing_group_id
            # A pair either variant was in with another variant is broken up, each group
            # once: its removal is audited under the variant whose review held it. The two
            # reviews this save writes below are left to that write.
            cleared_group_ids: list[str] = []
            for group_id, holder_id, holder in (
                (existing_group_id, variant_id, existing),
                (partner_group_id, compound_het_partner_id, partner_existing),
            ):
                if group_id is None or group_id == target_group_id or group_id in cleared_group_ids:
                    continue
                cleared_group_ids.append(group_id)
                pair_changes.append(
                    CompoundHetPairChange(
                        variant_id=holder_id,
                        before=compound_het_pair_state(holder),
                        after=None,
                        group_id=str(group_id),
                    )
                )
                await _clear_compound_het_group(
                    session,
                    family_uuid=context.family_uuid,
                    group_id=str(group_id),
                    scope=scope,
                    now=now,
                    written_by_save=(variant_id, compound_het_partner_id),
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
            pair_changes.append(
                CompoundHetPairChange(
                    variant_id=variant_id,
                    before=(
                        compound_het_pair_state(existing)
                        if existing is not None and existing.get("compound_het_group_id") == target_group_id
                        else None
                    ),
                    after=compound_het_pair_state(compound_het_data),
                    group_id=target_group_id,
                )
            )
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
                **_note_authorship(
                    existing=partner_existing,
                    note=(partner_existing or {}).get("note"),
                    username=user.username,
                    timestamp=now,
                ),
                "updated_by": user.username,
                "updated_at": now,
            }
            # The partner's own review in the analysis: its review as read, with the pair.
            await _write_own_review(
                session,
                family_uuid=context.family_uuid,
                scope=scope,
                reviews=partner_reviews or ScopedReview(),
                fields={
                    **_compound_het_clear_payload(),
                    **(partner_existing or {}),
                    **partner_individual_data,
                    **partner_compound_het_data,
                },
                created_at=now,
            )
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
                pair_changes.append(_pair_removed(variant_id, existing))
                # The variant's own review is left to the write below, which keeps it for
                # the tags and note this save gives it, or deletes it.
                await _clear_compound_het_group(
                    session,
                    family_uuid=context.family_uuid,
                    group_id=existing["compound_het_group_id"],
                    scope=scope,
                    now=now,
                    written_by_save=(variant_id,),
                )
            compound_het_data = _compound_het_clear_payload()
    elif compound_het_requested:
        if existing and existing.get("compound_het_group_id"):
            pair_changes.append(_pair_removed(variant_id, existing))
            await _clear_compound_het_group(
                session,
                family_uuid=context.family_uuid,
                group_id=existing["compound_het_group_id"],
                scope=scope,
                now=now,
                written_by_save=(variant_id,),
            )
        compound_het_data = _compound_het_clear_payload()

    async def _audit(new_state: dict[str, Any]) -> None:
        # The before -> after of this save in the immutable clinical audit trail (Phase 2),
        # in the save's transaction: the variant's own review, then the pair reviews, each
        # naming the analysis whose review it is.
        await record_review_changes(
            session,
            family_uuid=context.family_uuid,
            family_identifier=context.family_id,
            variant_id=variant_id,
            user=user,
            existing=existing,
            new_state=new_state,
            analysis=scope.review_analysis,
        )
        await record_compound_het_pair_changes(
            session,
            family_uuid=context.family_uuid,
            family_identifier=context.family_id,
            user=user,
            changes=pair_changes,
            analysis=scope.review_analysis,
        )

    async def _write(fields: dict[str, Any]) -> None:
        # The analysis's own review of the variant (``_write_own_review``).
        await _write_own_review(
            session,
            family_uuid=context.family_uuid,
            scope=scope,
            reviews=reviews,
            fields=fields,
            created_at=now,
        )

    if (
        normalized_note is None
        and normalized_classification is None
        and not normalized_tags
        and not compound_het_requested
        and normalized_acmg is None
    ):
        # Clearing a review removes its classification, tags and note: audited like any
        # other change, whether the row is deleted or kept for its compound-het pairing.
        cleared_state: dict[str, Any] = {
            "acmg_class": None,
            "acmg": None,
            "classification": None,
            "tags": [],
            "note": None,
        }
        if existing is None:
            return SmallVariantReviewOut(variant_id=variant_id, tags=[])
        await _write(
            {
                **_compound_het_clear_payload(),
                **existing,
                "variant_key": variant.variant_key,
                "variant_id": variant_id,
                "classification": None,
                "tags": [],
                "tag_metadata": {},
                "note": None,
                "note_updated_by": None,
                "note_updated_at": None,
                "acmg": None,
                "acmg_point_total": None,
                "acmg_class": None,
                "acmg_evidence_snapshot": None,
                "updated_by": user.username,
                "updated_at": now,
            }
        )
        await _audit(cleared_state)
        if _document_has_compound_het_review(existing):
            return await _commit_stored_review(
                session, family_uuid=context.family_uuid, variant_id=variant_id, scope=scope
            )
        await session.commit()
        return SmallVariantReviewOut(variant_id=variant_id, tags=[])

    data = {
        "variant_key": variant.variant_key,
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
        **_note_authorship(existing=existing, note=normalized_note, username=user.username, timestamp=now),
        "acmg": normalized_acmg,
        "acmg_point_total": acmg_point_total,
        "acmg_class": acmg_class,
        "acmg_evidence_snapshot": acmg_evidence_snapshot,
        "updated_by": user.username,
        "updated_at": now,
    }
    if compound_het_data is not None:
        data.update(compound_het_data)

    # The before -> after state for the immutable clinical audit trail (Phase 2): the
    # classification text as stored, so a change sent through the API alone is audited too.
    new_state = {
        "acmg_class": acmg_class,
        "acmg": normalized_acmg,
        "classification": normalized_classification,
        "tags": normalized_tags,
        "note": normalized_note,
    }

    # The review as read, with the save's values: a pair the save leaves out is kept.
    fields = {**_compound_het_clear_payload(), **(existing or {}), **data}
    await _write(fields)
    await _audit(new_state)
    if _review_document_has_any_content(fields):
        return await _commit_stored_review(
            session, family_uuid=context.family_uuid, variant_id=variant_id, scope=scope
        )
    await session.commit()
    return SmallVariantReviewOut(variant_id=variant_id, tags=[])
