from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request

from ..core.coga_logging import is_sensitive_key
from ..dependencies import get_current_user
from ..schemas import (
    UI_EVENT_BATCH_MAX_EVENTS,
    UiEventBatchIn,
    UiEventIn,
    UiEventIngestResult,
)
from ..services.access_control import CurrentUser, changes_only_own_data
from ..services.ui_event_pg import UiEventPayload, write_ui_event

router = APIRouter(prefix="/ui-events", tags=["ui-events"])

# Interactions the client is allowed to record. Anything else is dropped so a
# misbehaving or tampered client cannot store arbitrary event types.
_ALLOWED_EVENT_TYPES = {"click", "navigation", "submit", "view", "query"}
# Single source of truth lives on the schema, which now rejects oversize
# batches with 422; this slice stays as a defensive backstop.
_MAX_EVENTS_PER_BATCH = UI_EVENT_BATCH_MAX_EVENTS
_MAX_LABEL_LEN = 200
_MAX_SHORT_LEN = 128
_MAX_PATH_LEN = 512
_MAX_USER_AGENT_LEN = 256

# An event's ``occurred_at`` is the browser's clock. The server keeps it when it lies between a
# day before and five minutes after the moment the server received the event: the browser sends
# its events every few seconds, holding them only while a send fails and at most as long as its
# sign-in lasts (two hours by default), and its clock may be a few minutes ahead. Any other value
# is replaced by the receive time, with the client's value kept as text in the event's detail.
# Unbounded, a value such as 0001-01-01T00:00:00+05:00 (before the year 1 in UTC) made the
# database driver fail, and in the async mode it took every other event of its batch, any
# user's, with it.
OCCURRED_AT_MAX_AGE = timedelta(hours=24)
OCCURRED_AT_MAX_LEAD = timedelta(minutes=5)
# The key, in ``ui_events.detail``, holding the ``occurred_at`` the client sent when the server
# stored its receive time instead. Only this endpoint sets it: a client's own is dropped.
CLIENT_OCCURRED_AT_KEY = "_client_occurred_at"

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_NUMERIC_ID_RE = re.compile(r"^\d{3,}$")
_WHITESPACE_RE = re.compile(r"\s+")


def _clean_short(value: str | None, max_len: int = _MAX_SHORT_LEN) -> str | None:
    if not value:
        return None
    collapsed = _WHITESPACE_RE.sub(" ", str(value)).strip()
    if not collapsed:
        return None
    return collapsed[:max_len]


def _clean_label(value: str | None) -> str | None:
    return _clean_short(value, _MAX_LABEL_LEN)


def _mask_path(value: str | None) -> str | None:
    """Reduce a route/href to its structure: identifier segments become ``:id``
    and query strings are reduced to their (sorted) keys, so clinical
    identifiers are never persisted."""

    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None

    path_part, _, query_part = raw.partition("?")
    segments = path_part.split("/")
    masked_segments = [
        ":id" if (_UUID_RE.match(seg) or _NUMERIC_ID_RE.match(seg)) else seg
        for seg in segments
    ]
    masked = "/".join(masked_segments)

    if query_part:
        keys = sorted({pair.split("=", 1)[0] for pair in query_part.split("&") if pair})
        if keys:
            masked = f"{masked}?{'&'.join(keys)}"

    return masked[:_MAX_PATH_LEN]


def _sanitize_detail(detail: dict[str, Any] | None) -> dict[str, Any]:
    if not detail:
        return {}
    sanitized: dict[str, Any] = {}
    for key, value in detail.items():
        key_text = str(key)
        if is_sensitive_key(key_text):
            sanitized[key_text] = "***"
        elif isinstance(value, str):
            sanitized[key_text] = value[:_MAX_SHORT_LEN]
        elif isinstance(value, (int, float, bool)) or value is None:
            sanitized[key_text] = value
        else:
            # The detail blob is for small scalar context. Nested structures aren't
            # scalar and can hide secrets at depth that top-level key masking misses
            # (e.g. {"headers": {"authorization": "Bearer ..."}}); str()-ing them
            # persisted that token verbatim. Record only the shape, never the contents.
            sanitized[key_text] = f"[{type(value).__name__}]"
    return sanitized


def _bounded_occurred_at(
    value: datetime | None, received_at: datetime
) -> tuple[datetime | None, str | None]:
    """The ``occurred_at`` to store, and the client's value as text when that is not it.

    A value without an offset is read as UTC. Comparing aware datetimes cannot overflow, so
    every value the schema accepts is decided here, the years 1 and 9999 included, and what is
    returned lies within a day of the receive time, which the driver can convert to UTC."""
    if value is None:
        return None, None
    aware = value if value.utcoffset() is not None else value.replace(tzinfo=timezone.utc)
    if received_at - OCCURRED_AT_MAX_AGE <= aware <= received_at + OCCURRED_AT_MAX_LEAD:
        return aware, None
    return received_at, value.isoformat()


def _payload_from_event(
    event: UiEventIn,
    *,
    user: CurrentUser,
    remote_ip: str | None,
    user_agent: str | None,
    received_at: datetime | None = None,
) -> UiEventPayload | None:
    event_type = (event.event_type or "").strip().lower()
    if event_type not in _ALLOWED_EVENT_TYPES:
        return None
    occurred_at, client_occurred_at = _bounded_occurred_at(
        event.occurred_at,
        received_at if received_at is not None else datetime.now(timezone.utc),
    )
    detail = _sanitize_detail(event.detail)
    detail.pop(CLIENT_OCCURRED_AT_KEY, None)
    if client_occurred_at is not None:
        detail[CLIENT_OCCURRED_AT_KEY] = client_occurred_at
    return UiEventPayload(
        event_type=event_type,
        category=_clean_short(event.category, 64),
        label=_clean_label(event.label),
        target_id=_clean_short(event.target_id),
        path=_mask_path(event.path),
        to_path=_mask_path(event.to_path),
        href=_mask_path(event.href),
        component=_clean_short(event.component),
        session_id=_clean_short(event.session_id, 64),
        occurred_at=occurred_at,
        detail=detail,
        remote_ip=remote_ip,
        user_agent=_clean_short(user_agent, _MAX_USER_AGENT_LEN),
        user_id=str(user.id) if getattr(user, "id", None) else None,
        user_email=getattr(user, "email", None),
        user_role=getattr(user, "role", None),
    )


@router.post("", response_model=UiEventIngestResult, status_code=202)
# The events of the caller's own use of the interface (a print or an export, say): a reader's
# are recorded as anyone's are.
@changes_only_own_data
async def ingest_ui_events(
    payload: UiEventBatchIn,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
) -> UiEventIngestResult:
    """Accept a batch of client-side interaction events for the current user.

    Events are sanitized (identifiers masked, query strings reduced to keys)
    before being queued for storage. Available to any authenticated user; the
    actor is always taken from the auth token, never the request body.

    An event the database cannot take is recorded as lost (logged with its payload
    and counted, TF-13 S-5), as the queue's worker records one: the batch is still
    answered 202, so the client does not resend events already stored."""

    remote_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    # Every event of the batch is bounded against the same moment (OCCURRED_AT_MAX_AGE).
    received_at = datetime.now(timezone.utc)

    accepted = 0
    for event in payload.events[:_MAX_EVENTS_PER_BATCH]:
        record = _payload_from_event(
            event,
            user=user,
            remote_ip=remote_ip,
            user_agent=user_agent,
            received_at=received_at,
        )
        if record is None:
            continue
        await write_ui_event(record)
        accepted += 1

    return UiEventIngestResult(accepted=accepted)
