"""A request that fails leaves none of its values in the application log (E2E: real Postgres,
real uvicorn).

A failed statement quotes what it was given: SQLAlchemy's error text holds the SQL and its
parameters, and Postgres's own message can quote the value it could not read. The request
middleware used to log that text twice on the 500 line (its message and its traceback), and
uvicorn, to which Starlette raises the exception again once it has answered it, logged it a
third time as "Exception in ASGI application".

Here a route binds a value from the request body into a statement Postgres refuses. The request
goes over HTTP to a real uvicorn server on this test's event loop, through the request
middleware and with the filter the app installs on uvicorn's error log. Both log records must
name the error (its type, the driver's error and the SQLSTATE) and keep its frames, without the
value; the request's row in the access-controlled audit table keeps the error's full text, as it
keeps the body.

The app is the middleware and one route, not CoGA's own: which endpoints fail on such a value
changes as they are hardened. The value is synthetic.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import asyncio
import json
import logging
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_ROUTE = "/api/e2e-server-error/{probe}"


class _Capture(logging.Handler):
    def __init__(self, formatter: logging.Formatter) -> None:
        super().__init__()
        self.setFormatter(formatter)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


async def _exercise(run: str, value: str) -> dict:
    import httpx
    import uvicorn
    from fastapi import Body, FastAPI
    from sqlalchemy import text
    from uvicorn.logging import DefaultFormatter

    from backend.app.core.coga_logging import JsonLogFormatter, install_server_error_redaction
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.middleware import request_logging

    await init_postgres_schema()
    sessionmaker = get_postgres_sessionmaker()

    app = FastAPI()
    app.middleware("http")(request_logging.log_request_response)

    @app.post(_ROUTE)
    async def _store_note(probe: str, payload: dict = Body(...)) -> dict:
        async with sessionmaker() as session:
            # Bound as text, so that Postgres, not the driver, refuses it and quotes it.
            await session.execute(
                text("SELECT CAST(CAST(:note AS text) AS integer)"), {"note": payload["note"]}
            )
        return {"probe": probe}

    # As the app does when it is imported (main.py).
    install_server_error_redaction()
    middleware_log = _Capture(JsonLogFormatter())
    uvicorn_log = _Capture(DefaultFormatter("%(levelprefix)s %(message)s", use_colors=False))
    request_logger = request_logging.logger._logger
    error_logger = logging.getLogger("uvicorn.error")
    request_logger.addHandler(middleware_log)
    error_logger.addHandler(uvicorn_log)

    # log_config=None leaves the test run's logging configuration alone; the capture above
    # formats uvicorn's records as its default configuration does.
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", log_config=None, access_log=False)
    )
    serving = asyncio.create_task(server.serve())
    path = f"/api/e2e-server-error/{run}"
    try:
        while not server.started:
            if serving.done():
                serving.result()  # a server that could not start says why
            await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            response = await client.post(path, json={"note": value})
    finally:
        server.should_exit = True
        await serving
        request_logger.removeHandler(middleware_log)
        error_logger.removeHandler(uvicorn_log)

    async with sessionmaker() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT status_code, route_path, request_body, error "
                    "FROM audit_log_events WHERE path = :path"
                ),
                {"path": path},
            )
        ).mappings().all()
    return {
        "status": response.status_code,
        "middleware": [json.loads(line) for line in middleware_log.lines],
        "uvicorn": [line for line in uvicorn_log.lines if "Exception in ASGI application" in line],
        "audit": [dict(row) for row in rows],
    }


@pytest.fixture(scope="module")
def failed(request) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    mp = pytest.MonkeyPatch()
    # The production default, pinned: the audit is asynchronous (each row is still written as
    # its request ends: no worker runs here).
    mp.setattr(settings, "audit_log_mode", "async")
    request.addfinalizer(mp.undo)
    run = uuid4().hex[:12]
    value = f"Jane Doe {run}"
    return {"value": value, **_harness.run_async(lambda: _exercise(run, value))}


def test_the_request_fails_and_its_audit_row_keeps_the_error_text(failed) -> None:
    assert failed["status"] == 500
    assert len(failed["audit"]) == 1, failed["audit"]
    row = failed["audit"][0]
    assert row["status_code"] == 500
    assert row["route_path"] == _ROUTE
    assert row["request_body"] == {"note": failed["value"]}
    # The full text, in the table only an admin reads: the statement, Postgres's message and
    # the parameters, each quoting the value.
    assert "SELECT CAST(" in row["error"]
    assert row["error"].count(failed["value"]) >= 2, row["error"]


def test_the_500_line_names_the_error_and_holds_no_value(failed) -> None:
    errors = [line for line in failed["middleware"] if line["severity"] == "ERROR"]
    assert len(errors) == 1, failed["middleware"]
    line = errors[0]
    kind = "DBAPIError (InvalidTextRepresentationError, SQLSTATE 22P02)"
    assert line["message"] == f"Unhandled server error: {kind}"
    assert line["httpRequest"]["status"] == 500
    assert line["detail"]["route"] == _ROUTE
    assert ", in _store_note\n" in line["traceback"]
    assert line["traceback"].endswith(f"\n{kind}")
    assert failed["value"] not in json.dumps(line)
    assert "[parameters:" not in json.dumps(line)


def test_uvicorns_traceback_names_the_error_and_holds_no_value(failed) -> None:
    assert len(failed["uvicorn"]) == 1, failed["uvicorn"]
    line = failed["uvicorn"][0]
    assert line.startswith("ERROR:    Exception in ASGI application\nTraceback (most recent call last):\n")
    assert ", in _store_note\n" in line
    assert line.endswith("\nDBAPIError (InvalidTextRepresentationError, SQLSTATE 22P02)")
    assert failed["value"] not in line
    assert "[parameters:" not in line
