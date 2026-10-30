"""Shared helpers for the small- and structural-variant review-PG services.

These were byte-for-byte duplicated across small_variant_review_pg and
structural_variant_review_pg (and re-implemented in several other service
modules); they live here as the single source of truth.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Sequence, TypeVar

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ValidationError
from sqlalchemy import text

from ..core.coga_logging import scrub_log

logger = logging.getLogger(__name__)

_ClassificationT = TypeVar("_ClassificationT", bound=BaseModel)


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


def _save_time(*reviews: dict[str, Any] | None) -> datetime:
    """The time a save stamps on the reviews it writes: now, never before one they hold.

    ``updated_at`` is the version a later save is checked against (#513). Read under the
    reviews' locks it is in the past, but a clock that stepped back would make a save older
    than the review it replaces; it is then stamped a microsecond after that review.
    """
    now = datetime.now(timezone.utc)
    held = [
        stamp
        for stamp in (_as_utc((review or {}).get("updated_at")) for review in reviews)
        if stamp is not None
    ]
    latest = max(held, default=None)
    if latest is not None and latest >= now:
        return latest + timedelta(microseconds=1)
    return now


def _raise_on_stale_review(
    payload: Any,
    existing: dict[str, Any] | None,
    serialize: Callable[[dict[str, Any]], Any],
    *,
    expected_field: str = "expected_updated_at",
    partner_variant_id: str | None = None,
) -> None:
    """Refuse a save made against a review that has changed since the client loaded it.

    Two reviewers saving the same variant used to be last-write-wins, without either
    being told (#513). The client sends the ``updated_at`` it loaded (null for "no
    review yet"); anything else now stored means someone saved in between, and the
    409 carries the current review so the client can show it instead of overwriting.

    A compound-het pair save writes its partner's review too, and checks it the same way
    from ``expected_field`` (``partner_expected_updated_at``); the 409 then names the
    partner (``variant_id``) and carries the partner's current review.
    """
    if expected_field not in payload.model_fields_set:
        return
    expected = _as_utc(getattr(payload, expected_field))
    current = _as_utc((existing or {}).get("updated_at")) if existing else None
    if existing is None and expected is None:
        return
    if existing is not None and expected is not None and current == expected:
        return
    current_review = serialize(existing) if existing is not None else None
    who = (existing or {}).get("updated_by") or "someone"
    subject = (
        f"The review of the compound-het partner {partner_variant_id}"
        if partner_variant_id
        else "This review"
    )
    detail: dict[str, Any] = {
        "code": "review_conflict",
        "message": (
            f"{subject} was changed by {who} after you opened it; your changes were not "
            "saved. The current review has been loaded."
            if existing is not None
            else f"{subject} was removed after you opened it; your changes were not saved."
        ),
        "current": jsonable_encoder(current_review, by_alias=True) if current_review else None,
    }
    if partner_variant_id:
        detail["variant_id"] = partner_variant_id
    raise HTTPException(status_code=409, detail=detail)


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


def _read_stored_classification(
    value: Any, model: type[_ClassificationT], kind: str
) -> _ClassificationT | None:
    """The stored classification, or None when there is none or it cannot be read.

    ``None`` alone cannot tell those apart, so an unreadable record is logged here and
    the review serializer marks it (``acmg_unreadable``) instead of dropping it (#514).
    ``kind`` names the record in that log line ("ACMG classification").
    """
    if not value:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError) as exc:
            _log_unreadable_classification(kind, exc)
            return None
        if value is None:  # a stored JSON null is no record, not a broken one
            return None
    try:
        return model.model_validate(value)
    except Exception as exc:  # noqa: BLE001 - served as unreadable, never raised
        _log_unreadable_classification(kind, exc)
        return None


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
