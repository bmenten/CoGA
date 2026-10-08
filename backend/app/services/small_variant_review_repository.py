from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from .review_pg_utils import _json_payload, _normalize_tags
from .small_variant_review_acmg import _acmg_json_or_none, _json_or_none


POSTGRES_BIGINT_MIN = -(2**63)


POSTGRES_BIGINT_MAX = (2**63) - 1


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


def _compound_het_field_names() -> list[str]:
    return list(_compound_het_clear_payload().keys())


def _preserve_existing_compound_het(document: dict[str, Any]) -> dict[str, Any]:
    return {key: document.get(key) for key in _compound_het_field_names()}


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
                variant_key,
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
    expanding: Sequence[str] = (),
) -> list[dict[str, Any]]:
    # where_clause is a hardcoded predicate string (never user input); the
    # values are bound via params. `expanding` names the params bound as a list (IN).
    statement = text(f"{_SMALL_VARIANT_REVIEW_SELECT}            WHERE {where_clause}")
    if expanding:
        statement = statement.bindparams(*(bindparam(name, expanding=True) for name in expanding))
    result = await session.execute(statement, params)
    return [dict(row) for row in result.mappings().all()]


async def _fetch_review_rows_for_variants(
    session: AsyncSession,
    *,
    family_uuid: str,
    variant_ids: Sequence[str],
) -> list[dict[str, Any]]:
    """The review rows of these variants, with the same columns as a single-review fetch.

    Every list of variants carries its reviews through this, so a list serves each review
    as a read of that one review would. A list that selected its own columns once left out
    the ACMG record, and every classified variant then came back without its criteria.
    """
    return await _fetch_review_rows(
        session,
        where_clause="family_id = CAST(:family_id AS uuid) AND variant_id IN :variant_ids",
        params={"family_id": family_uuid, "variant_ids": list(variant_ids)},
        expanding=("variant_ids",),
    )


async def _fetch_review_row(
    session: AsyncSession,
    *,
    family_uuid: str,
    variant_id: str,
) -> dict[str, Any] | None:
    rows = await _fetch_review_rows(
        session,
        where_clause="family_id = CAST(:family_id AS uuid) AND variant_id = :variant_id",
        params={"family_id": family_uuid, "variant_id": variant_id},
    )
    return rows[0] if rows else None


async def _fetch_compound_het_group_rows(
    session: AsyncSession,
    *,
    family_uuid: str,
    group_id: str,
) -> list[dict[str, Any]]:
    return await _fetch_review_rows(
        session,
        where_clause="family_id = CAST(:family_id AS uuid) AND compound_het_group_id = :group_id",
        params={"family_id": family_uuid, "group_id": group_id},
    )


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
                :variant_key,
                :variant_id,
                :classification,
                CAST(:tags_json AS jsonb),
                CAST(:tag_metadata_json AS jsonb),
                :note,
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
        {**_review_row_params(fields), "family_id": family_uuid, "created_at": created_at},
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
