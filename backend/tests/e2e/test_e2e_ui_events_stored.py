"""Every UI event the browser sends is stored, once and whole (E2E, real Postgres).

**A batch sent again is stored once.** The browser keeps a batch of UI events until the API
acknowledges it (frontend ``lib/telemetry.ts``): when its page closes while the batch is in
flight, it sends the batch again, whether or not the first request arrived. Each event
carries the id the browser gave it, the endpoint stores the event under that id
(``routers/ui_events.py``), and the writers' INSERT does nothing for an id the table holds
(SEC-17), so the batch is stored once.
Here a batch is sent twice, then a third time with its ids in capitals and in braces, and, to
compare, an event without an id is sent twice. An event of another user, written as the
worker writes one, under an id the table holds stores nothing.

**An impossible timestamp loses no event.** The browser sends each UI event with
``occurred_at``, its own clock. ``0001-01-01T00:00:00+05:00`` is a valid date-time, but it lies
before the year 1 in UTC: the Postgres driver (asyncpg) cannot convert it, and an INSERT of it
fails. In the async mode the worker writes up to ``AUDIT_LOG_BATCH_SIZE`` queued events, any
user's, in one statement, which such a value would fail on every attempt, for every event of
the batch. The naive ``9999-12-31T23:59:59.999999`` would be written as ``infinity``
(AUDIT-2). The endpoint keeps ``occurred_at`` from a day before to five minutes after it
received the event, and otherwise stores the receive time, keeping the client's value in
``detail._client_occurred_at``. Here a request holds both, and the worker's
batch write is called with events the endpoint built for two users, one with the year 1.

The requests run over HTTP through an in-process ``httpx.ASGITransport`` client, as the seeded
admin, on one event loop (see test_e2e_api_contract.py for why). Without the lifespan no worker
runs, so each event is written as its request runs. The rows are read back from Postgres. The
values are synthetic. ``ui_events`` is append-only, so the rows stay, under labels of each
run's own.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.tests.e2e._harness import ensure_postgres_schema

pytestmark = pytest.mark.integration

_OTHER_USER = "analyst-b@example.org"
_YEAR_1 = "0001-01-01T00:00:00+05:00"
_YEAR_9999 = "9999-12-31T23:59:59.999999"


async def _prepare_stack() -> None:
    """Apply the schema and set up the seeded admin and the e2e project."""
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.tests.e2e import _harness

    await ensure_postgres_schema()
    async with get_postgres_sessionmaker()() as session:
        await _harness.ensure_e2e_project(session)


def _built_by_the_endpoint(user: SimpleNamespace, event: dict) -> dict:
    """The event as the endpoint builds it for ``user`` and the queue receives it."""
    from backend.app.routers.ui_events import _payload_from_event
    from backend.app.schemas import UiEventIn

    payload = _payload_from_event(UiEventIn.model_validate(event), user=user, remote_ip=None, user_agent=None)
    assert payload is not None
    return payload


# --- A batch sent again ------------------------------------------------------------------


async def _send_again(run: str) -> dict:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.app.services.event_pipeline import write_event_batch_with_retry
    from backend.app.services.ui_event_pg import _write_ui_event_batch
    from backend.tests.e2e import _harness

    await _prepare_stack()
    prefix = f"sent-again-{run}"
    ids = {"sign out": str(uuid4()), "close the tab": str(uuid4())}
    batch = [
        {"event_type": "click", "label": f"{prefix} sign out", "event_id": ids["sign out"]},
        {
            "event_type": "navigation",
            "label": f"{prefix} close the tab",
            "event_id": ids["close the tab"],
        },
    ]
    spelled_otherwise = [
        {**batch[0], "event_id": ids["sign out"].upper()},
        {**batch[1], "event_id": "{" + ids["close the tab"] + "}"},
    ]
    without_an_id = [{"event_type": "click", "label": f"{prefix} without an id"}]

    out: dict = {"prefix": prefix, "ids": ids, "answers": []}
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"
        for events in (batch, batch, spelled_otherwise, without_an_id, without_an_id):
            resp = await ac.post("/api/ui-events", json={"events": events})
            out["answers"].append((resp.status_code, json.loads(resp.text)))

    # Another user's event under an id the table holds, built by the endpoint and written as
    # the worker writes a batch.
    other = SimpleNamespace(id=None, email=_OTHER_USER, role="viewer")
    payload = _built_by_the_endpoint(
        other, {"event_type": "click", "label": f"{prefix} other user", "event_id": ids["sign out"]}
    )
    await write_event_batch_with_retry(_write_ui_event_batch, [payload], name="ui_event")

    async with get_postgres_sessionmaker()() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT label, user_email, event_id::text AS event_id FROM ui_events "
                    "WHERE label LIKE :label ORDER BY created_at"
                ),
                {"label": f"{prefix} %"},
            )
        ).mappings().all()
        out["rows"] = [
            {**dict(row), "label": row["label"].removeprefix(f"{prefix} ")} for row in rows
        ]
    return out


@pytest.fixture(scope="module")
def sent_again(request) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    mp = pytest.MonkeyPatch()
    # The production default, pinned: the async mode (each event is still written as its
    # request runs: no worker runs here).
    mp.setattr(settings, "audit_log_mode", "async")
    request.addfinalizer(mp.undo)
    run = uuid4().hex[:12]
    return _harness.run_async(lambda: _send_again(run))


def _rows(stored: dict, label: str) -> list[dict]:
    return [row for row in stored["rows"] if row["label"] == label]


def test_each_send_is_answered_as_the_first(sent_again) -> None:
    assert sent_again["answers"] == [(202, {"accepted": 2})] * 3 + [(202, {"accepted": 1})] * 2


def test_a_batch_sent_again_is_stored_once_under_the_ids_the_browser_gave(sent_again) -> None:
    # One row each, under the id the browser gave it, however often it was sent.
    for label, event_id in sent_again["ids"].items():
        assert [row["event_id"] for row in _rows(sent_again, label)] == [event_id], label


def test_an_event_sent_without_an_id_is_stored_at_each_send(sent_again) -> None:
    # To compare: the server gives an event received without an id a new one each time.
    rows = _rows(sent_again, "without an id")
    assert len(rows) == 2
    assert rows[0]["event_id"] != rows[1]["event_id"]


def test_another_users_event_under_an_id_the_table_holds_stores_nothing(sent_again) -> None:
    assert _rows(sent_again, "other user") == []
    (row,) = _rows(sent_again, "sign out")
    assert row["user_email"] != _OTHER_USER


# --- An impossible timestamp ---------------------------------------------------------------


async def _send_impossible_times(run: str) -> dict:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.app.services.event_pipeline import (
        dropped_event_count,
        write_event_batch_with_retry,
    )
    from backend.app.services.ui_event_pg import _write_ui_event_batch
    from backend.tests.e2e import _harness

    await _prepare_stack()
    prefix = f"ts-bounds-{run}"
    out: dict = {"sent": datetime.now(timezone.utc) - timedelta(seconds=2)}
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"
        out["request_before"] = datetime.now(timezone.utc)
        resp = await ac.post(
            "/api/ui-events",
            json={
                "events": [
                    {
                        "event_type": "click",
                        "label": f"{prefix} request kept",
                        "occurred_at": out["sent"].isoformat(),
                    },
                    {
                        "event_type": "click",
                        "label": f"{prefix} request year 1",
                        "occurred_at": _YEAR_1,
                    },
                    {
                        "event_type": "navigation",
                        "label": f"{prefix} request year 9999",
                        "occurred_at": _YEAR_9999,
                    },
                ]
            },
        )
        out["request_after"] = datetime.now(timezone.utc)
        out["response"] = {"status": resp.status_code, "text": resp.text}

    # The worker's batch: one statement for the queued events of two users, one of them
    # with the year 1. Built by the endpoint, as the queue receives them.
    analyst_a = SimpleNamespace(id=None, email="analyst-a@example.org", role="viewer")
    analyst_b = SimpleNamespace(id=None, email="analyst-b@example.org", role="viewer")
    dropped_before = dropped_event_count("ui_event")
    out["batch_before"] = datetime.now(timezone.utc)
    batch = [
        _built_by_the_endpoint(analyst_a, {"event_type": "click", "label": f"{prefix} batch kept"}),
        _built_by_the_endpoint(
            analyst_b, {"event_type": "click", "label": f"{prefix} batch year 1", "occurred_at": _YEAR_1}
        ),
    ]
    out["batch_after"] = datetime.now(timezone.utc)
    await write_event_batch_with_retry(_write_ui_event_batch, batch, name="ui_event")
    out["batch_dropped"] = dropped_event_count("ui_event") - dropped_before

    async with get_postgres_sessionmaker()() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT label, user_email, occurred_at, detail FROM ui_events "
                    "WHERE label LIKE :label"
                ),
                {"label": f"{prefix} %"},
            )
        ).mappings().all()
        out["rows"] = {row["label"].removeprefix(f"{prefix} "): dict(row) for row in rows}
    return out


@pytest.fixture(scope="module")
def impossible_times(request) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    mp = pytest.MonkeyPatch()
    # The production default, pinned: the async mode (each event is still written as its
    # request runs: no worker runs here). A failing batch is retried once, without a wait.
    mp.setattr(settings, "audit_log_mode", "async")
    mp.setattr(settings, "audit_log_max_write_attempts", 2)
    mp.setattr(settings, "audit_log_flush_interval_seconds", 0.01)
    request.addfinalizer(mp.undo)
    run = uuid4().hex[:12]
    return _harness.run_async(lambda: _send_impossible_times(run))


def _at_receive_time(row: dict, before: datetime, after: datetime) -> bool:
    occurred_at = row["occurred_at"]
    return occurred_at.utcoffset() is not None and before <= occurred_at <= after


def test_a_request_with_impossible_times_stores_every_event(impossible_times) -> None:
    assert impossible_times["response"]["status"] == 202, impossible_times["response"]
    assert json.loads(impossible_times["response"]["text"]) == {"accepted": 3}
    rows = impossible_times["rows"]
    assert {"request kept", "request year 1", "request year 9999"} <= set(rows), sorted(rows)
    kept = rows["request kept"]
    assert kept["occurred_at"] == impossible_times["sent"]
    assert kept["detail"] == {}
    for name, sent in (("request year 1", _YEAR_1), ("request year 9999", _YEAR_9999)):
        row = rows[name]
        assert _at_receive_time(row, impossible_times["request_before"], impossible_times["request_after"]), row
        assert row["detail"] == {"_client_occurred_at": sent}


def test_the_workers_batch_holding_an_impossible_time_is_written_whole(impossible_times) -> None:
    assert impossible_times["batch_dropped"] == 0
    rows = impossible_times["rows"]
    assert {"batch kept", "batch year 1"} <= set(rows), sorted(rows)
    assert rows["batch kept"]["user_email"] == "analyst-a@example.org"
    assert rows["batch kept"]["occurred_at"] is None
    assert rows["batch kept"]["detail"] == {}
    year_1 = rows["batch year 1"]
    assert year_1["user_email"] == "analyst-b@example.org"
    assert _at_receive_time(year_1, impossible_times["batch_before"], impossible_times["batch_after"]), year_1
    assert year_1["detail"] == {"_client_occurred_at": _YEAR_1}
