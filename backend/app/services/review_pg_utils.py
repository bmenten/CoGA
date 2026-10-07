"""Shared helpers for the small- and structural-variant review-PG services.

These were byte-for-byte duplicated across small_variant_review_pg and
structural_variant_review_pg (and re-implemented in several other service
modules); they live here as the single source of truth.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Sequence
from uuid import UUID

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from sqlalchemy import text

from ..core.coga_logging import scrub_log

logger = logging.getLogger(__name__)


def _require_uuid(value: str, detail: str) -> str:
    try:
        UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=detail) from exc
    return value


def _normalize_tags(tags: Iterable[str]) -> list[str]:
    return sorted({str(tag).strip() for tag in tags if str(tag).strip()})


def _added_tags(tags: Iterable[str], held: Iterable[str] | None) -> list[str]:
    """The tags a save adds to those the stored review already holds.

    A review save checks only these against the tags the family may use. A tag the review
    holds stays whatever became of its definition: the quick tag toggle and the review
    dialog send the stored tags back, so a deleted tag would otherwise refuse every later
    save of the review, the one removing it included.
    """
    kept = set(_normalize_tags(held or []))
    return [tag for tag in _normalize_tags(tags) if tag not in kept]


def _json_payload(value: Any) -> str:
    return json.dumps(jsonable_encoder(value if value is not None else {}))


async def _lock_review(session: Any, *, scope: str, family_uuid: str, variant_id: str) -> None:
    """Serialize concurrent saves of one variant's review until this transaction ends."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
        {"k": f"{scope}:{family_uuid}:{variant_id}"},
    )


def _as_utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _raise_on_stale_review(
    payload: Any,
    existing: dict[str, Any] | None,
    serialize: Callable[[dict[str, Any]], Any],
) -> None:
    """Refuse a save made against a review that has changed since the client loaded it.

    Two reviewers saving the same variant used to be last-write-wins, without either
    being told (#513). The client sends the ``updated_at`` it loaded (null for "no
    review yet"); anything else now stored means someone saved in between, and the
    409 carries the current review so the client can show it instead of overwriting.
    """
    if "expected_updated_at" not in payload.model_fields_set:
        return
    expected = _as_utc(payload.expected_updated_at)
    current = _as_utc((existing or {}).get("updated_at")) if existing else None
    if existing is None and expected is None:
        return
    if existing is not None and expected is not None and current == expected:
        return
    current_review = serialize(existing) if existing is not None else None
    who = (existing or {}).get("updated_by") or "someone"
    raise HTTPException(
        status_code=409,
        detail={
            "code": "review_conflict",
            "message": (
                f"This review was changed by {who} after you opened it; your changes were not "
                "saved. The current review has been loaded."
                if existing is not None
                else "This review was removed after you opened it; your changes were not saved."
            ),
            "current": jsonable_encoder(current_review, by_alias=True) if current_review else None,
        },
    )


def _has_stored_record(value: Any) -> bool:
    """Is a JSON record stored here at all? Empty values and JSON null are not."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return True  # something is stored, and it is not valid JSON
    return bool(value)


def _log_unreadable_classification(kind: str, exc: BaseException) -> None:
    """Record that a stored classification could not be read back (#514).

    The review is still served, with the classification marked unreadable rather than
    failing the page, but never silently: a stored classification dropping out of view
    is a clinical-data event. Only field paths and error types are logged; the stored
    values, free-text evidence included, stay out of the log.
    """
    if isinstance(exc, ValidationError):
        problems = [
            f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['type']}"
            for error in exc.errors(include_url=False, include_context=False, include_input=False)
        ]
        detail = "; ".join(problems[:10])
    else:
        detail = type(exc).__name__
    logger.error(
        "Stored %s could not be read and is served as unreadable (%s)", kind, scrub_log(detail)
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
            merged[tag] = {
                "updated_by": username,
                "updated_at": timestamp,
            }
    return merged
