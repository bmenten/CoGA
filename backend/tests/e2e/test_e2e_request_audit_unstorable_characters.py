"""A request is audited whatever characters it carries (E2E, real Postgres).

Postgres refuses some values a request can carry: a NUL in a TEXT column (a ``%00`` in the
path or the query string arrives decoded), ``\\u0000`` or half of a UTF-16 surrogate pair in a
JSONB document, and the JSON numbers ``NaN`` and ``Infinity``, which Python's JSON parser
accepts. The request audit used to fail its INSERT on them and log only a warning, so a
signed-in user could put such a value in a JSON field the endpoint ignores and have the
action carried out with no row in ``audit_log_events``. In the default async mode the failed
INSERT also took every other row of its batch with it.

Each request runs over HTTP through an in-process ``httpx.ASGITransport`` client, as the seeded
admin, on one event loop (see test_e2e_api_contract.py for why). Without the lifespan no
worker runs, so each audit row is written as its request ends; the worker's batch write is
called directly. The rows are read back from Postgres: each request has its row, with every
value Postgres refuses written as an escape and the escaped columns named under ``_escaped``.
The UI-event log is checked the same way. The values are synthetic, and the project the
requests change is the test's own, dropped at the end.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_PROJECTS = "/api/projects"


async def _audit_rows(session, *, method: str, path: str) -> list[dict]:
    from sqlalchemy import text

    result = await session.execute(
        text(
            """
            SELECT method, path, query_string, status_code, request_body, request_meta, db_update
            FROM audit_log_events
            WHERE method = :method AND path = :path
            ORDER BY created_at
            """
        ),
        {"method": method, "path": path},
    )
    return [dict(row) for row in result.mappings().all()]


async def _description(session, project_id: str) -> str:
    from sqlalchemy import text

    return (
        await session.execute(
            text("SELECT description FROM projects WHERE id = CAST(:id AS uuid)"), {"id": project_id}
        )
    ).scalar_one()


def _row(rows: dict, prefix: str) -> dict:
    found = [row for key, row in rows.items() if key and key.startswith(prefix)]
    assert len(found) == 1, f"expected one audit row for the {prefix!r} request, found {len(found)}"
    return found[0]


async def _exercise(run: str) -> dict:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services.audit_log_pg import AuditLogEventPayload, _write_audit_log_batch
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        _admin, shared_project_id, _assembly_id = await _harness.ensure_e2e_project(session)
        project_id = (
            await session.execute(
                text(
                    """
                    INSERT INTO projects (name, description, species_id, assembly_id, metadata)
                    SELECT :name, 'before', species_id, assembly_id, '{}'::jsonb
                    FROM projects WHERE id = CAST(:shared AS uuid)
                    RETURNING id::text
                    """
                ),
                {"name": f"audit-unstorable-{run}", "shared": shared_project_id},
            )
        ).scalar_one()
        await session.commit()

    project_path = f"{_PROJECTS}/{project_id}"
    out: dict = {"project_path": project_path, "responses": {}}
    responses = out["responses"]
    json_headers = {"Content-Type": "application/json"}
    try:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
            ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"

            # A NUL, as the JSON escape a client sends, in a field ProjectUpdate ignores.
            nul_body = '{"description": "nul-%s", "note": "x\\u0000y"}' % run
            resp = await ac.put(project_path, content=nul_body.encode(), headers=json_headers)
            responses["nul_field"] = resp.status_code
            async with sessionmaker() as session:
                out["description_after_nul"] = await _description(session, project_id)

            # The other values Postgres refuses: half a surrogate pair, NaN and -Infinity.
            other_body = (
                '{"description": "other-%s", "half": "\\ud800", "ratio": NaN, "low": -Infinity}' % run
            )
            resp = await ac.put(project_path, content=other_body.encode(), headers=json_headers)
            responses["other_fields"] = resp.status_code

            # A %00 in the path and in a query key: the path reaches the app decoded.
            resp = await ac.delete(f"{_PROJECTS}/{run}%00x?k%00ey=1")
            responses["nul_path"] = resp.status_code

            # The UI-event log, the same values in a label and in the detail.
            events_body = (
                '{"events": [{"event_type": "click", "category": "button", '
                '"label": "audit-%s Save\\u0000x", "detail": {"note": "a\\u0000b", "ratio": NaN}}]}'
            ) % run
            resp = await ac.post("/api/ui-events", content=events_body.encode(), headers=json_headers)
            responses["ui_event"] = {"status": resp.status_code, "text": resp.text}

        # The async worker writes a batch in one INSERT: a row Postgres refuses must not take
        # the others with it. Rows like the middleware's, the second with a NUL in its path.
        batch_path = f"{_PROJECTS}/batch-{run}"
        try:
            await _write_audit_log_batch(
                [
                    AuditLogEventPayload(method="GET", path=batch_path, status_code=200, duration_ms=1),
                    AuditLogEventPayload(
                        method="GET", path=f"{batch_path}\x00", status_code=404, duration_ms=1
                    ),
                ]
            )
            out["batch_error"] = None
        except Exception as exc:  # noqa: BLE001 - reported to the assertion
            out["batch_error"] = type(exc).__name__

        async with sessionmaker() as session:
            out["description"] = await _description(session, project_id)
            out["put_rows"] = {
                (row["request_body"] or {}).get("description"): row
                for row in await _audit_rows(session, method="PUT", path=project_path)
            }
            out["nul_path_rows"] = await _audit_rows(
                session, method="DELETE", path=f"{_PROJECTS}/{run}\\x00x"
            )
            ui_rows = await _audit_rows(session, method="POST", path="/api/ui-events")
            out["ui_event_audit_rows"] = [
                row for row in ui_rows if f"audit-{run} " in json.dumps(row["request_body"])
            ]
            out["ui_events"] = [
                dict(row)
                for row in (
                    await session.execute(
                        text("SELECT label, detail FROM ui_events WHERE label LIKE :label"),
                        {"label": f"audit-{run} %"},
                    )
                ).mappings().all()
            ]
            out["batch_rows"] = [
                *await _audit_rows(session, method="GET", path=batch_path),
                *await _audit_rows(session, method="GET", path=f"{batch_path}\\x00"),
            ]
    finally:
        async with sessionmaker() as session:
            await session.execute(text("DELETE FROM ui_events WHERE label LIKE :label"), {"label": f"audit-{run} %"})
            await session.execute(text("DELETE FROM projects WHERE id = CAST(:id AS uuid)"), {"id": project_id})
            await session.commit()
    return out


@pytest.fixture(scope="module")
def audited(request) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    mp = pytest.MonkeyPatch()
    # The production defaults, pinned: query strings are reduced to their keys, and the audit
    # is asynchronous (each row is still written as its request ends: no worker runs here).
    mp.setattr(settings, "audit_log_query_string_mode", "keys")
    mp.setattr(settings, "audit_log_mode", "async")
    request.addfinalizer(mp.undo)
    run = uuid4().hex[:12]
    return _harness.run_async(lambda: _exercise(run))


def test_a_nul_in_an_ignored_json_field_is_audited(audited) -> None:
    # The action ran: the request changed the project.
    assert audited["responses"]["nul_field"] == 200
    assert audited["description_after_nul"].startswith("nul-")
    row = _row(audited["put_rows"], "nul-")
    assert row["status_code"] == 200
    assert row["request_body"]["note"] == "x\\x00y"
    assert row["request_meta"]["_escaped"] == ["request_body"]
    # The content type is still recorded beside the flag.
    assert row["request_meta"]["headers"]["content-type"] == "application/json"
    assert row["db_update"]["updateFields"] == ["description", "note"]


def test_half_a_surrogate_pair_and_nan_in_ignored_fields_are_audited(audited) -> None:
    assert audited["responses"]["other_fields"] == 200
    assert audited["description"].startswith("other-")
    row = _row(audited["put_rows"], "other-")
    assert row["request_body"]["half"] == "\\ud800"
    assert row["request_body"]["ratio"] == "NaN"
    assert row["request_body"]["low"] == "-Infinity"
    assert row["request_meta"]["_escaped"] == ["request_body"]


def test_a_nul_in_the_path_and_query_is_audited(audited) -> None:
    # The project id is refused before any lookup; the attempt is still on record.
    assert audited["responses"]["nul_path"] == 400
    assert len(audited["nul_path_rows"]) == 1, audited["nul_path_rows"]
    row = audited["nul_path_rows"][0]
    assert row["status_code"] == 400
    assert row["query_string"] == "k\\x00ey"
    assert row["db_update"]["entityId"].endswith("\\x00x")
    assert row["request_meta"]["_escaped"] == ["db_update", "path", "query_string"]


def test_a_row_with_nothing_escaped_carries_no_flag(audited) -> None:
    clean = [row for row in audited["batch_rows"] if row["status_code"] == 200]
    assert len(clean) == 1, audited["batch_rows"]
    assert clean[0]["request_meta"] == {}


def test_a_ui_event_with_a_nul_is_stored_and_its_request_audited(audited) -> None:
    assert audited["responses"]["ui_event"]["status"] == 202, audited["responses"]["ui_event"]
    assert json.loads(audited["responses"]["ui_event"]["text"]) == {"accepted": 1}
    assert len(audited["ui_events"]) == 1, audited["ui_events"]
    event = audited["ui_events"][0]
    assert event["label"].endswith(" Save\\x00x")
    assert event["detail"]["note"] == "a\\x00b"
    assert event["detail"]["ratio"] == "NaN"
    assert event["detail"]["_escaped"] == ["detail", "label"]
    assert len(audited["ui_event_audit_rows"]) == 1, audited["ui_event_audit_rows"]
    row = audited["ui_event_audit_rows"][0]
    assert row["status_code"] == 202
    assert row["request_meta"]["_escaped"] == ["request_body"]


def test_a_batch_holding_a_row_with_a_nul_is_written_whole(audited) -> None:
    assert audited["batch_error"] is None
    assert sorted(row["status_code"] for row in audited["batch_rows"]) == [200, 404]
    nul_row = next(row for row in audited["batch_rows"] if row["status_code"] == 404)
    assert nul_row["path"].endswith("\\x00")
    assert nul_row["request_meta"] == {"_escaped": ["path"]}
