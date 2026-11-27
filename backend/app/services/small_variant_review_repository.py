from __future__ import annotations

from datetime import datetime
from typing import Any, Collection, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.sql import rows_matched
from .analysis_reviews import ScopedReview, effective_reviews, keeps_own_review, scope_review_clause, scoped_review
from .analysis_scope import AnalysisScope
from .review_pg_utils import _json_payload, _normalize_tags
from .small_variant_review_acmg import _acmg_json_or_none, _json_or_none


POSTGRES_BIGINT_MIN = -(2**63)


POSTGRES_BIGINT_MAX = (2**63) - 1


def _postgres_bigint_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        int_value = int(value)
    except (TypeError, ValueError, OverflowError):  # an infinity is no bigint either
        return None
    if POSTGRES_BIGINT_MIN <= int_value <= POSTGRES_BIGINT_MAX:
        return int_value
    return None


def _compound_het_clear_payload() -> dict[str, Any]:
    return {
        "compound_het_group_id": None,
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


def _document_has_individual_review(document: dict[str, Any]) -> bool:
    return bool(
        str(document.get("classification") or "").strip()
        or _normalize_tags(document.get("tags", []))
        or str(document.get("note") or "").strip()
        or document.get("acmg")
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


# Shared column list for small_variant_reviews fetches; the single-row and
# compound-het-group queries differ only in their WHERE clause.
_SMALL_VARIANT_REVIEW_SELECT = """
            SELECT
                id::text AS id,
                family_id::text AS family_id,
                analysis,
                variant_key,
                variant_id,
                classification,
                tags,
                tag_metadata,
                note,
                note_updated_by,
                note_updated_at,
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
                acmg,
                acmg_point_total,
                acmg_class,
                acmg_evidence_snapshot,
                updated_by,
                created_at,
                updated_at
            FROM small_variant_reviews
"""


async def _fetch_review_rows(
    session: AsyncSession,
    *,
    where_clause: str,
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    # where_clause is a hardcoded predicate string (never user input); the
    # values are bound via params.
    statement = text(f"{_SMALL_VARIANT_REVIEW_SELECT}            WHERE {where_clause}")
    result = await session.execute(statement, params)
    return [dict(row) for row in result.mappings().all()]


async def _fetch_review_rows_for_variants(
    session: AsyncSession,
    *,
    family_uuid: str,
    variant_ids: Sequence[str],
    scope: AnalysisScope,
) -> list[dict[str, Any]]:
    """The reviews of these variants the scope may read (each analysis's own, and the shared
    ones: ``analysis_reviews.scope_review_clause``), with the same columns as a single-review
    fetch; the caller picks each variant's effective review.

    Every list of variants carries its reviews through this, so a list serves each review
    as a read of that one review would, its ACMG record included.

    The ids go as one text[] parameter, not one parameter per id (an expanding IN): a CSV
    export reads up to 50,001 rows, and asyncpg refuses a statement with more than 32,767
    parameters.
    """
    params: dict[str, Any] = {"family_id": family_uuid, "variant_ids": list(variant_ids)}
    belongs = scope_review_clause(scope, params)
    return await _fetch_review_rows(
        session,
        where_clause=(
            "family_id = CAST(:family_id AS uuid) AND variant_id = ANY(CAST(:variant_ids AS text[])) "
            f"AND {belongs}"
        ),
        params=params,
    )


async def _fetch_scoped_review(
    session: AsyncSession,
    *,
    family_uuid: str,
    variant_id: str,
    scope: AnalysisScope,
) -> ScopedReview:
    """The scope's own review of the variant (the one a save in it writes) and the shared one
    it reads when it has none (``analysis_reviews.scoped_review``)."""
    params: dict[str, Any] = {"family_id": family_uuid, "variant_id": variant_id}
    belongs = scope_review_clause(scope, params)
    rows = await _fetch_review_rows(
        session,
        where_clause=f"family_id = CAST(:family_id AS uuid) AND variant_id = :variant_id AND {belongs}",
        params=params,
    )
    return scoped_review(rows, scope)


async def _fetch_compound_het_group_rows(
    session: AsyncSession,
    *,
    family_uuid: str,
    group_id: str,
    scope: AnalysisScope,
) -> list[dict[str, Any]]:
    """The reviews of a compound-het pair in the scope: of each variant whose effective review
    in it holds the pair (``compound_het_group_id``), that review. A pair is one analysis's, or
    a shared one each analysis without reviews of its own of the variants reads."""
    params: dict[str, Any] = {"family_id": family_uuid, "group_id": group_id}
    belongs = scope_review_clause(scope, params)
    rows = await _fetch_review_rows(
        session,
        where_clause=(
            f"family_id = CAST(:family_id AS uuid) AND {belongs} AND variant_id IN ("
            "SELECT member.variant_id FROM small_variant_reviews member "
            "WHERE member.family_id = CAST(:family_id AS uuid) AND member.compound_het_group_id = :group_id "
            f"AND {scope_review_clause(scope, params, column='member.analysis')})"
        ),
        params=params,
    )
    return [
        review
        for review in effective_reviews(rows, scope).values()
        if review.get("compound_het_group_id") == group_id
    ]


async def _delete_review_row(session: AsyncSession, review_id: str) -> None:
    await session.execute(
        text("DELETE FROM small_variant_reviews WHERE id = CAST(:review_id AS uuid)"),
        {"review_id": review_id},
    )


def _review_row_params(fields: dict[str, Any]) -> dict[str, Any]:
    """The bind parameters a review row's INSERT and UPDATE share: the fields as given, the
    key bounded to a Postgres bigint, and the JSONB columns serialised."""
    return {
        **fields,
        "variant_key": _postgres_bigint_or_none(fields.get("variant_key")),
        "tags_json": _json_payload(fields.get("tags", [])),
        "tag_metadata_json": _json_payload(fields.get("tag_metadata", {})),
        "compound_het_partner_variant_ids_json": _json_payload(
            fields.get("compound_het_partner_variant_ids", [])
        ),
        "compound_het_tags_json": _json_payload(fields.get("compound_het_tags", [])),
        "compound_het_tag_metadata_json": _json_payload(
            fields.get("compound_het_tag_metadata", {})
        ),
        "acmg_json": _acmg_json_or_none(fields.get("acmg")),
        "acmg_point_total": fields.get("acmg_point_total"),
        "acmg_class": fields.get("acmg_class"),
        "acmg_evidence_snapshot_json": _json_or_none(fields.get("acmg_evidence_snapshot")),
    }


class ReviewRowNotStored(RuntimeError):
    """An update of a review row the store does not hold: it would store nothing."""


async def _update_review_row(
    session: AsyncSession,
    *,
    review_id: str,
    fields: dict[str, Any],
) -> None:
    # An update that matches no row stores nothing: raised, so the save does not go on to
    # record its change on the clinical audit trail and commit it as if it had been stored.
    result = await session.execute(
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
                note_updated_by = :note_updated_by,
                note_updated_at = :note_updated_at,
                compound_het_group_id = :compound_het_group_id,
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
                acmg = CAST(:acmg_json AS jsonb),
                acmg_point_total = :acmg_point_total,
                acmg_class = :acmg_class,
                acmg_evidence_snapshot = CAST(:acmg_evidence_snapshot_json AS jsonb),
                updated_by = :updated_by,
                updated_at = :updated_at
            WHERE id = CAST(:review_id AS uuid)
            """
        ),
        {**_review_row_params(fields), "review_id": review_id},
    )
    if rows_matched(result) != 1:
        raise ReviewRowNotStored("The review row to update is not stored")


async def _insert_review_row(
    session: AsyncSession,
    *,
    family_uuid: str,
    analysis: str,
    fields: dict[str, Any],
    created_at: datetime,
) -> None:
    """Store a new review of the family's analysis ``analysis`` (``NO_ANALYSIS`` for a family
    that holds none)."""
    await session.execute(
        text(
            """
            INSERT INTO small_variant_reviews (
                family_id,
                analysis,
                variant_key,
                variant_id,
                classification,
                tags,
                tag_metadata,
                note,
                note_updated_by,
                note_updated_at,
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
                acmg,
                acmg_point_total,
                acmg_class,
                acmg_evidence_snapshot,
                updated_by,
                created_at,
                updated_at
            )
            VALUES (
                CAST(:family_id AS uuid),
                :analysis,
                :variant_key,
                :variant_id,
                :classification,
                CAST(:tags_json AS jsonb),
                CAST(:tag_metadata_json AS jsonb),
                :note,
                :note_updated_by,
                :note_updated_at,
                :compound_het_group_id,
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
                CAST(:acmg_json AS jsonb),
                :acmg_point_total,
                :acmg_class,
                CAST(:acmg_evidence_snapshot_json AS jsonb),
                :updated_by,
                :created_at,
                :updated_at
            )
            """
        ),
        {
            **_review_row_params(fields),
            "family_id": family_uuid,
            "analysis": analysis,
            "created_at": created_at,
        },
    )


async def _write_own_review(
    session: AsyncSession,
    *,
    family_uuid: str,
    scope: AnalysisScope,
    reviews: ScopedReview,
    fields: dict[str, Any],
    created_at: datetime,
) -> None:
    """Write ``fields`` as the scope's own review of a variant (``reviews``: its own and the
    shared one): its own is updated, or stored when it has none; the shared review stays as it
    is. A review that holds nothing is deleted, or kept empty when it hides a shared review that
    holds something (``analysis_reviews.keeps_own_review``)."""
    keep = keeps_own_review(reviews, _review_document_has_any_content, fields)
    if reviews.own is not None:
        if keep:
            await _update_review_row(session, review_id=reviews.own["id"], fields=fields)
        else:
            await _delete_review_row(session, reviews.own["id"])
    elif keep:
        await _insert_review_row(
            session,
            family_uuid=family_uuid,
            analysis=scope.review_analysis,
            fields={**_compound_het_clear_payload(), **fields},
            created_at=created_at,
        )


async def _clear_compound_het_group(
    session: AsyncSession,
    *,
    family_uuid: str,
    group_id: str,
    scope: AnalysisScope,
    now: datetime,
    written_by_save: Collection[str] = (),
) -> None:
    """Break up a compound-het pair in the scope: each review of it loses the pair, and one that
    held nothing else is deleted. A shared review of it stays as it is: the scope gets its own
    review of the variant, without the pair, stored at ``now`` (``_write_own_review``).

    The reviews of ``written_by_save`` are left as they are: the save that breaks the pair up
    writes each of them next, its compound-het fields included. Deleted here, the review that
    held only the pair was gone when the save wrote it, and the save's tags and note were
    lost while the clinical audit trail recorded them.
    """
    if not group_id:
        return
    documents = await _fetch_compound_het_group_rows(
        session,
        family_uuid=family_uuid,
        group_id=group_id,
        scope=scope,
    )
    clear_payload = _compound_het_clear_payload()
    for document in documents:
        variant_id = str(document.get("variant_id"))
        if variant_id in written_by_save:
            continue
        await _write_own_review(
            session,
            family_uuid=family_uuid,
            scope=scope,
            reviews=await _fetch_scoped_review(
                session, family_uuid=family_uuid, variant_id=variant_id, scope=scope
            ),
            fields={**document, **clear_payload},
            created_at=now,
        )
