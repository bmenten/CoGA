"""A batch of UI events the browser sends again is stored once (E2E, real Postgres).

The browser keeps a batch of UI events until the API acknowledges it (frontend
``lib/telemetry.ts``): when its page closes while the batch is in flight, it sends the batch
again, whether or not the first request arrived. Each event carries the id the browser gave it,
the endpoint stores the event under that id (``routers/ui_events.py``), and the writers' INSERT
does nothing for an id the table holds (SEC-17), so the batch is stored once. Before, the
endpoint gave every event it received a new id, and a batch sent again was stored twice.

The requests run over HTTP through an in-process ``httpx.ASGITransport`` client, as the seeded
admin, on one event loop (see test_e2e_api_contract.py for why): a batch sent twice, then a third
time with its ids in capitals and in braces, and, to compare, an event without an id sent twice.
An event of another user, written as the worker writes one, under an id the table holds stores
nothing. The rows are read back from Postgres. The values are synthetic. ``ui_events`` is
append-only, so the rows stay, under labels of the run's own.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_OTHER_USER = "analyst-b@example.org"


async def _exercise(run: str) -> dict:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.routers.ui_events import _payload_from_event
    from backend.app.schemas import UiEventIn
    from backend.app.services.event_pipeline import write_event_batch_with_retry
    from backend.app.services.ui_event_pg import _write_ui_event_batch
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        await _harness.ensure_e2e_project(session)

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
    payload = _payload_from_event(
        UiEventIn.model_validate(
            {"event_type": "click", "label": f"{prefix} other user", "event_id": ids["sign out"]}
        ),
        user=other,
        remote_ip=None,
        user_agent=None,
    )
    assert payload is not None
    await write_event_batch_with_retry(_write_ui_event_batch, [payload], name="ui_event")

    async with sessionmaker() as session:
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
def stored(request) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    mp = pytest.MonkeyPatch()
    # The production default, pinned: the async mode (each event is still written as its
    # request runs: no worker runs here).
    mp.setattr(settings, "audit_log_mode", "async")
    request.addfinalizer(mp.undo)
    run = uuid4().hex[:12]
    return _harness.run_async(lambda: _exercise(run))


def _rows(stored: dict, label: str) -> list[dict]:
    return [row for row in stored["rows"] if row["label"] == label]


def test_each_send_is_answered_as_the_first(stored) -> None:
    assert stored["answers"] == [(202, {"accepted": 2})] * 3 + [(202, {"accepted": 1})] * 2


def test_a_batch_sent_again_is_stored_once_under_the_ids_the_browser_gave(stored) -> None:
    # Before, three rows each, one per send, each under an id of the server's.
    for label, event_id in stored["ids"].items():
        assert [row["event_id"] for row in _rows(stored, label)] == [event_id], label


def test_an_event_sent_without_an_id_is_stored_at_each_send(stored) -> None:
    # To compare: the server gives an event received without an id a new one each time.
    rows = _rows(stored, "without an id")
    assert len(rows) == 2
    assert rows[0]["event_id"] != rows[1]["event_id"]


def test_another_users_event_under_an_id_the_table_holds_stores_nothing(stored) -> None:
    assert _rows(stored, "other user") == []
    (row,) = _rows(stored, "sign out")
    assert row["user_email"] != _OTHER_USER
