import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.core.config import settings
from app.dependencies import get_current_user
from app.routers.ui_events import (
    CLIENT_OCCURRED_AT_KEY,
    OCCURRED_AT_MAX_AGE,
    OCCURRED_AT_MAX_LEAD,
    _clean_label,
    _mask_path,
    _payload_from_event,
    _sanitize_detail,
    router,
)
from app.schemas import UI_EVENT_BATCH_MAX_EVENTS, UiEventBatchIn, UiEventIn
from app.services import event_pipeline, ui_event_pg
from app.services.event_pipeline import dropped_event_count
from app.services.ui_event_pg import UiEventPayload, _ui_event_insert_params


def test_mask_path_reduces_identifiers_and_query_to_keys() -> None:
    masked = _mask_path(
        "/families/3fa85f64-5717-4562-b3fc-2c963f66afa6/small-variants?gene=BRCA1&clinvar=path"
    )
    assert masked == "/families/:id/small-variants?clinvar&gene"


def test_mask_path_masks_numeric_id_segments() -> None:
    assert _mask_path("/projects/12345/page/2") == "/projects/:id/page/2"


def test_clean_label_collapses_whitespace_and_truncates() -> None:
    assert _clean_label("  Apply\n  filters  ") == "Apply filters"
    assert len(_clean_label("x" * 500) or "") == 200


def test_sanitize_detail_masks_sensitive_keys() -> None:
    sanitized = _sanitize_detail({"token": "abc", "count": 3, "label": "ok"})
    assert sanitized["token"] == "***"
    assert sanitized["count"] == 3
    assert sanitized["label"] == "ok"


def test_sanitize_detail_drops_nested_structures_without_leaking() -> None:
    # Top-level key masking misses a secret nested under a non-sensitive key. The nested
    # value must be recorded as a shape placeholder, never serialized (which previously
    # persisted the token verbatim, truncated).
    sanitized = _sanitize_detail(
        {"headers": {"authorization": "Bearer super-secret-token"}, "tags": ["a", "b"]}
    )
    assert sanitized["headers"] == "[dict]"
    assert sanitized["tags"] == "[list]"
    assert "super-secret-token" not in str(sanitized)


def test_payload_caps_user_agent_length() -> None:
    user = SimpleNamespace(id="u-1", email="a@example.com", role="viewer")
    payload = _payload_from_event(
        UiEventIn(event_type="click"),
        user=user,
        remote_ip="127.0.0.1",
        user_agent="UA-" + "x" * 1000,
    )
    assert payload is not None
    assert payload.user_agent is not None
    assert len(payload.user_agent) <= 256


def test_payload_takes_actor_from_user_not_body_and_drops_unknown_type() -> None:
    user = SimpleNamespace(id="u-1", email="analyst@example.com", role="viewer")

    rejected = _payload_from_event(
        UiEventIn(event_type="exfiltrate"),
        user=user,
        remote_ip="127.0.0.1",
        user_agent="pytest",
    )
    assert rejected is None

    payload = _payload_from_event(
        UiEventIn(
            event_type="click",
            category="button",
            label="Save preset",
            to_path="/families/3fa85f64-5717-4562-b3fc-2c963f66afa6/small-variants",
        ),
        user=user,
        remote_ip="127.0.0.1",
        user_agent="pytest",
    )
    assert payload is not None
    assert payload.event_type == "click"
    assert payload.user_email == "analyst@example.com"
    assert payload.user_role == "viewer"
    assert payload.to_path == "/families/:id/small-variants"


def test_event_batch_accepts_up_to_the_cap() -> None:
    batch = UiEventBatchIn(
        events=[UiEventIn(event_type="click")] * UI_EVENT_BATCH_MAX_EVENTS
    )
    assert len(batch.events) == UI_EVENT_BATCH_MAX_EVENTS


def test_event_batch_rejects_oversize_batch() -> None:
    # An over-cap batch is now an explicit validation error (HTTP 422 at the
    # route) rather than a silently truncated 202.
    with pytest.raises(ValidationError):
        UiEventBatchIn(
            events=[UiEventIn(event_type="click")] * (UI_EVENT_BATCH_MAX_EVENTS + 1)
        )


def test_insert_params_serializes_detail_to_json_string() -> None:
    params = _ui_event_insert_params(
        UiEventPayload(event_type="navigation", detail={"from": "/a", "to": "/b"})
    )
    assert isinstance(params["detail"], str)
    assert params["detail"] == '{"from": "/a", "to": "/b"}'


def test_insert_params_escape_what_postgres_refuses_and_flag_it_in_detail() -> None:
    params = _ui_event_insert_params(
        UiEventPayload(
            event_type="click",
            label="Save\x00x",
            to_path="/families/F\x00/small-variants",
            detail={"note": "a\x00b", "ratio": float("nan"), "count": 2},
        )
    )
    assert params["label"] == "Save\\x00x"
    assert params["to_path"] == "/families/F\\x00/small-variants"
    assert json.loads(params["detail"]) == {
        "note": "a\\x00b",
        "ratio": "NaN",
        "count": 2,
        "_escaped": ["detail", "label", "to_path"],
    }


def test_insert_params_drop_a_client_flag_in_detail() -> None:
    # Only the writer sets _escaped: a client cannot claim an escape that did not happen.
    params = _ui_event_insert_params(
        UiEventPayload(event_type="click", detail={"_escaped": "label", "count": 1})
    )
    assert json.loads(params["detail"]) == {"count": 1}


_VIEWER = SimpleNamespace(id="u-1", email="analyst@example.com", role="viewer")


def _ui_event_app(user: SimpleNamespace = _VIEWER) -> FastAPI:
    # The router alone, for one signed-in user: no lifespan, so no worker runs.
    api = FastAPI()
    api.include_router(router, prefix="/api")
    api.dependency_overrides[get_current_user] = lambda: user
    return api


def _ui_event_client() -> TestClient:
    # A server error is answered as such (500), not raised into the test.
    return TestClient(_ui_event_app(), raise_server_exceptions=False)


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_an_event_that_cannot_be_stored_is_recorded_and_the_batch_still_answered(
    mode, monkeypatch, caplog
) -> None:
    # In sync mode, and in async mode while no worker runs, each event is written as the
    # request runs. A failed write answered 500 after the events before it were stored, so
    # the client resent the batch and stored those twice, and the loss was not counted. The
    # batch is now answered as the worker's queue answers it, and the lost event recorded as
    # the worker records one: counted, and logged with its payload (TF-13 S-5).
    stored: list[str | None] = []

    async def _write(payloads):
        if payloads[0].label == "Lost":
            raise RuntimeError("INSERT failed [parameters: ('Lost',)]")
        stored.extend(payload.label for payload in payloads)

    monkeypatch.setattr(settings, "audit_log_mode", mode)
    monkeypatch.setattr(ui_event_pg, "_ui_event_queue", None)
    monkeypatch.setattr(ui_event_pg, "_write_ui_event_batch", _write)
    monkeypatch.setattr(event_pipeline, "_dropped_counts", {})
    caplog.set_level(logging.ERROR, logger="app.services.event_pipeline")

    response = _ui_event_client().post(
        "/api/ui-events",
        json={
            "events": [
                {"event_type": "click", "label": "Kept"},
                {"event_type": "click", "label": "Lost"},
                {"event_type": "navigation", "label": "Also kept"},
            ]
        },
    )

    assert response.status_code == 202
    assert response.json() == {"accepted": 3}
    assert stored == ["Kept", "Also kept"]
    assert dropped_event_count("ui_event") == 1
    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 1, messages
    assert messages[0].startswith(
        "Audit pipeline ui_event: event not persisted (synchronous write failed: RuntimeError); "
        "dropped_total=1 payload=UiEventPayload(event_type='click', category=None, label='Lost',"
    )
    assert "[parameters:" not in messages[0]


# occurred_at is the browser's clock. The endpoint keeps it from a day before to five minutes
# after it received the event, and otherwise stores the receive time, keeping the client's
# value in the detail. A value the database driver could not convert to UTC used to fail the
# event's write, and in the async mode every other event of the worker's batch with it.

# The moment the server received the events of the bound tests.
_RECEIVED = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


def _stored_occurred_at(sent, detail=None):
    """The occurred_at and detail stored for a click sent with ``sent`` as its occurred_at,
    read as the endpoint reads a request body, and received at ``_RECEIVED``."""
    event = UiEventIn.model_validate({"event_type": "click", "occurred_at": sent, "detail": detail})
    payload = _payload_from_event(
        event, user=_VIEWER, remote_ip=None, user_agent=None, received_at=_RECEIVED
    )
    assert payload is not None
    return payload.occurred_at, payload.detail


def _encode_like_the_driver(payloads) -> None:
    """Convert each occurred_at to UTC, as asyncpg's timestamptz encoder does on a server whose
    local time is UTC, before any row of the statement reaches Postgres: a value it cannot
    convert fails the whole statement (OverflowError)."""
    for payload in payloads:
        value = payload.occurred_at
        if value is not None and value.utcoffset() is not None:
            value.astimezone(timezone.utc)


def test_the_bounds_are_a_day_before_and_five_minutes_after_the_receive_time() -> None:
    # As docs/security-posture.md §2 and docs/database.md give them.
    assert OCCURRED_AT_MAX_AGE == timedelta(hours=24)
    assert OCCURRED_AT_MAX_LEAD == timedelta(minutes=5)


@pytest.mark.parametrize(
    ("sent", "stored"),
    [
        # At either bound.
        ("2026-10-08T12:00:00Z", datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)),
        ("2026-10-09T12:05:00Z", datetime(2026, 10, 9, 12, 5, tzinfo=timezone.utc)),
        # The same instants, with another offset.
        ("2026-10-08T17:30:00+05:30", datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)),
        ("2026-10-09T02:05:00-10:00", datetime(2026, 10, 9, 12, 5, tzinfo=timezone.utc)),
        # Without an offset, read as UTC.
        ("2026-10-08T12:00:00", datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)),
        ("2026-10-09T12:05:00", datetime(2026, 10, 9, 12, 5, tzinfo=timezone.utc)),
        # A Unix time, which the schema also accepts.
        (1791547200, datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)),
        (None, None),
    ],
)
def test_an_occurred_at_within_its_bounds_is_stored_as_sent(sent, stored) -> None:
    occurred_at, detail = _stored_occurred_at(sent)
    assert occurred_at == stored
    assert occurred_at is None or occurred_at.utcoffset() is not None
    assert detail == {}


@pytest.mark.parametrize(
    ("sent", "kept_as"),
    [
        # A microsecond outside either bound.
        ("2026-10-08T11:59:59.999999Z", "2026-10-08T11:59:59.999999+00:00"),
        ("2026-10-09T12:05:00.000001Z", "2026-10-09T12:05:00.000001+00:00"),
        ("2026-10-08T11:59:59.999999", "2026-10-08T11:59:59.999999"),
        ("2026-10-09T12:05:00.000001", "2026-10-09T12:05:00.000001"),
        # The receive time as a clock ten hours behind UTC reads it: ten hours ahead.
        ("2026-10-09T12:00:00-10:00", "2026-10-09T12:00:00-10:00"),
        # Before the year 1 and after the year 9999 in UTC: the driver could not convert them.
        ("0001-01-01T00:00:00+05:00", "0001-01-01T00:00:00+05:00"),
        ("9999-12-31T23:59:59-05:00", "9999-12-31T23:59:59-05:00"),
        # The extremes without an offset, which the driver wrote as -infinity and infinity.
        ("0001-01-01T00:00:00", "0001-01-01T00:00:00"),
        ("9999-12-31T23:59:59.999999", "9999-12-31T23:59:59.999999"),
        # The Unix epoch.
        (0, "1970-01-01T00:00:00+00:00"),
    ],
)
def test_an_occurred_at_outside_its_bounds_is_the_receive_time_and_kept_in_the_detail(
    sent, kept_as
) -> None:
    occurred_at, detail = _stored_occurred_at(sent)
    assert occurred_at == _RECEIVED
    assert detail == {CLIENT_OCCURRED_AT_KEY: kept_as}


def test_a_client_cannot_set_the_client_occurred_at_key() -> None:
    # Only the endpoint says that the stored occurred_at is not the client's.
    _, detail = _stored_occurred_at(
        "2026-10-09T11:59:00Z", {CLIENT_OCCURRED_AT_KEY: "2026-10-09T11:00:00Z", "count": 1}
    )
    assert detail == {"count": 1}
    _, detail = _stored_occurred_at("2000-01-01T00:00:00Z", {CLIENT_OCCURRED_AT_KEY: "forged"})
    assert detail == {CLIENT_OCCURRED_AT_KEY: "2000-01-01T00:00:00+00:00"}


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_an_event_with_an_impossible_occurred_at_is_stored_with_the_rest_of_its_batch(
    mode, monkeypatch
) -> None:
    # Sync mode, and async mode while no worker runs, write each event as its request runs:
    # the year 1 with a positive offset failed its own write, and was counted as lost. The
    # naive year 9999 was written as infinity. Both are now stored at the receive time.
    stored: list[UiEventPayload] = []

    async def _write(payloads):
        _encode_like_the_driver(payloads)
        stored.extend(payloads)

    monkeypatch.setattr(settings, "audit_log_mode", mode)
    monkeypatch.setattr(ui_event_pg, "_ui_event_queue", None)
    monkeypatch.setattr(ui_event_pg, "_write_ui_event_batch", _write)
    monkeypatch.setattr(event_pipeline, "_dropped_counts", {})

    sent = datetime.now(timezone.utc) - timedelta(seconds=5)
    before = datetime.now(timezone.utc)
    response = _ui_event_client().post(
        "/api/ui-events",
        json={
            "events": [
                {"event_type": "click", "label": "Kept", "occurred_at": sent.isoformat()},
                {"event_type": "click", "label": "Year 1", "occurred_at": "0001-01-01T00:00:00+05:00"},
                {
                    "event_type": "navigation",
                    "label": "Year 9999",
                    "occurred_at": "9999-12-31T23:59:59.999999",
                },
                {"event_type": "click", "label": "No time"},
            ]
        },
    )
    after = datetime.now(timezone.utc)

    assert response.status_code == 202
    assert response.json() == {"accepted": 4}
    assert [payload.label for payload in stored] == ["Kept", "Year 1", "Year 9999", "No time"]
    assert dropped_event_count("ui_event") == 0
    kept, year_1, year_9999, no_time = stored
    assert (kept.occurred_at, kept.detail) == (sent, {})
    # Both impossible times are replaced by the one moment the batch was received.
    assert before <= year_1.occurred_at <= after
    assert year_9999.occurred_at == year_1.occurred_at
    assert year_1.detail == {CLIENT_OCCURRED_AT_KEY: "0001-01-01T00:00:00+05:00"}
    assert year_9999.detail == {CLIENT_OCCURRED_AT_KEY: "9999-12-31T23:59:59.999999"}
    assert (no_time.occurred_at, no_time.detail) == (None, {})


@pytest.mark.asyncio
async def test_the_workers_batch_keeps_every_users_events(monkeypatch) -> None:
    # In the async mode the worker writes up to AUDIT_LOG_BATCH_SIZE queued events, any
    # user's, in one statement. The year 1 with a positive offset failed that statement on
    # every attempt, and every event of the batch reached only the error log and the counter.
    batches: list[list[tuple[str, str]]] = []

    async def _write(payloads):
        _encode_like_the_driver(payloads)
        batches.append([(payload.user_email, payload.label) for payload in payloads])

    monkeypatch.setattr(settings, "audit_log_mode", "async")
    monkeypatch.setattr(settings, "audit_log_max_write_attempts", 2)
    monkeypatch.setattr(settings, "audit_log_flush_interval_seconds", 0.01)
    monkeypatch.setattr(ui_event_pg, "_write_ui_event_batch", _write)
    monkeypatch.setattr(ui_event_pg, "_ui_event_queue", asyncio.Queue())
    monkeypatch.setattr(ui_event_pg, "_ui_event_shutdown_event", asyncio.Event())
    monkeypatch.setattr(event_pipeline, "_dropped_counts", {})
    other = SimpleNamespace(id="u-2", email="other@example.com", role="viewer")

    for user, events in (
        (
            _VIEWER,
            [
                {"event_type": "click", "label": "Save preset"},
                {"event_type": "navigation", "label": "Open family"},
            ],
        ),
        (
            other,
            [{"event_type": "click", "label": "Year 1", "occurred_at": "0001-01-01T00:00:00+05:00"}],
        ),
    ):
        transport = ASGITransport(app=_ui_event_app(user))
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/ui-events", json={"events": events})
        assert response.status_code == 202

    # The worker's round at shutdown: it drains the queue and writes it in batches.
    ui_event_pg._ui_event_shutdown_event.set()
    await ui_event_pg._ui_event_worker()

    assert batches == [
        [
            ("analyst@example.com", "Save preset"),
            ("analyst@example.com", "Open family"),
            ("other@example.com", "Year 1"),
        ]
    ]
    assert dropped_event_count("ui_event") == 0
