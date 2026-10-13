"""A UI event's impossible timestamp loses no event (E2E, real Postgres).

The browser sends each UI event with ``occurred_at``, its own clock. ``0001-01-01T00:00:00+05:00``
is a valid date-time, but it lies before the year 1 in UTC: the Postgres driver (asyncpg) could
not convert it, and the INSERT failed. In the async mode the worker writes up to
``AUDIT_LOG_BATCH_SIZE`` queued events, any user's, in one statement, and that statement failed
on every attempt: every event of the batch reached only the error log and the counter. The
naive ``9999-12-31T23:59:59.999999`` was written as ``infinity``. The endpoint now keeps
``occurred_at`` from a day before to five minutes after it received the event, and otherwise
stores the receive time, keeping the client's value in ``detail._client_occurred_at``.

The request runs over HTTP through an in-process ``httpx.ASGITransport`` client, as the seeded
admin, on one event loop (see test_e2e_api_contract.py for why). Without the lifespan no worker
runs, so each event is written as its request runs; the worker's batch write is called with
events the endpoint built for two users. The rows are read back from Postgres. The values are
synthetic, and the rows are deleted at the end.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_YEAR_1 = "0001-01-01T00:00:00+05:00"
_YEAR_9999 = "9999-12-31T23:59:59.999999"


async def _exercise(run: str) -> dict:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.routers.ui_events import _payload_from_event
    from backend.app.schemas import UiEventIn
    from backend.app.services.event_pipeline import (
        dropped_event_count,
        write_event_batch_with_retry,
    )
    from backend.app.services.ui_event_pg import _write_ui_event_batch
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        await _harness.ensure_e2e_project(session)

    prefix = f"ts-bounds-{run}"
    out: dict = {"sent": datetime.now(timezone.utc) - timedelta(seconds=2)}
    try:
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
            _payload_from_event(
                UiEventIn.model_validate(
                    {"event_type": "click", "label": f"{prefix} batch kept"}
                ),
                user=analyst_a,
                remote_ip=None,
                user_agent=None,
            ),
            _payload_from_event(
                UiEventIn.model_validate(
                    {
                        "event_type": "click",
                        "label": f"{prefix} batch year 1",
                        "occurred_at": _YEAR_1,
                    }
                ),
                user=analyst_b,
                remote_ip=None,
                user_agent=None,
            ),
        ]
        out["batch_after"] = datetime.now(timezone.utc)
        await write_event_batch_with_retry(_write_ui_event_batch, batch, name="ui_event")
        out["batch_dropped"] = dropped_event_count("ui_event") - dropped_before

        async with sessionmaker() as session:
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
    finally:
        async with sessionmaker() as session:
            await session.execute(
                text("DELETE FROM ui_events WHERE label LIKE :label"), {"label": f"{prefix} %"}
            )
            await session.commit()
    return out


@pytest.fixture(scope="module")
def stored(request) -> dict:
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
    return _harness.run_async(lambda: _exercise(run))


def _at_receive_time(row: dict, before: datetime, after: datetime) -> bool:
    occurred_at = row["occurred_at"]
    return occurred_at.utcoffset() is not None and before <= occurred_at <= after


def test_a_request_with_impossible_times_stores_every_event(stored) -> None:
    assert stored["response"]["status"] == 202, stored["response"]
    assert json.loads(stored["response"]["text"]) == {"accepted": 3}
    rows = stored["rows"]
    assert {"request kept", "request year 1", "request year 9999"} <= set(rows), sorted(rows)
    kept = rows["request kept"]
    assert kept["occurred_at"] == stored["sent"]
    assert kept["detail"] == {}
    for name, sent in (("request year 1", _YEAR_1), ("request year 9999", _YEAR_9999)):
        row = rows[name]
        assert _at_receive_time(row, stored["request_before"], stored["request_after"]), row
        assert row["detail"] == {"_client_occurred_at": sent}


def test_the_workers_batch_holding_an_impossible_time_is_written_whole(stored) -> None:
    assert stored["batch_dropped"] == 0
    rows = stored["rows"]
    assert {"batch kept", "batch year 1"} <= set(rows), sorted(rows)
    assert rows["batch kept"]["user_email"] == "analyst-a@example.org"
    assert rows["batch kept"]["occurred_at"] is None
    assert rows["batch kept"]["detail"] == {}
    year_1 = rows["batch year 1"]
    assert year_1["user_email"] == "analyst-b@example.org"
    assert _at_receive_time(year_1, stored["batch_before"], stored["batch_after"]), year_1
    assert year_1["detail"] == {"_client_occurred_at": _YEAR_1}
