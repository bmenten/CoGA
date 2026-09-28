from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, Request

from ..dependencies import get_current_user
from ..schemas import (
    UI_EVENT_BATCH_MAX_EVENTS,
    UiEventBatchIn,
    UiEventIn,
    UiEventIngestResult,
)
from ..services.access_control import CurrentUser
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

_SENSITIVE_PREFIXES = (
    "password",
    "secret",
    "token",
    "authorization",
    "api_key",
    "access_key",
)

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
        lowered = key_text.lower()
        if any(lowered.startswith(prefix) for prefix in _SENSITIVE_PREFIXES):
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


def _payload_from_event(
    event: UiEventIn,
    *,
    user: CurrentUser,
    remote_ip: str | None,
    user_agent: str | None,
) -> UiEventPayload | None:
    event_type = (event.event_type or "").strip().lower()
    if event_type not in _ALLOWED_EVENT_TYPES:
        return None
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
        occurred_at=event.occurred_at,
        detail=_sanitize_detail(event.detail),
        remote_ip=remote_ip,
        user_agent=_clean_short(user_agent, _MAX_USER_AGENT_LEN),
        user_id=str(user.id) if getattr(user, "id", None) else None,
        user_email=getattr(user, "email", None),
        user_role=getattr(user, "role", None),
    )


@router.post("", response_model=UiEventIngestResult, status_code=202)
async def ingest_ui_events(
    payload: UiEventBatchIn,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
) -> UiEventIngestResult:
    """Accept a batch of client-side interaction events for the current user.

    Events are sanitized (identifiers masked, query strings reduced to keys)
    before being queued for storage. Available to any authenticated user; the
    actor is always taken from the auth token, never the request body."""

    remote_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    accepted = 0
    for event in payload.events[:_MAX_EVENTS_PER_BATCH]:
        record = _payload_from_event(
            event,
            user=user,
            remote_ip=remote_ip,
            user_agent=user_agent,
        )
        if record is None:
            continue
        await write_ui_event(record)
        accepted += 1

    return UiEventIngestResult(accepted=accepted)
