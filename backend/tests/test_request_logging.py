import asyncio
import json
import logging

import pytest
from sqlalchemy.exc import DBAPIError
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.coga_logging import JsonLogFormatter
from app.core.config import settings
from app.middleware import request_logging as rl
from app.middleware.request_logging import (
    _derive_db_update,
    _parse_request_body,
    _query_string_for_logging,
    _request_url_for_logging,
    _sanitize_for_logging,
)
from app.services import audit_log_pg, event_pipeline
from app.services.audit_log_pg import AUDIT_PIPELINE_NAME, log_model_update
from app.services.event_pipeline import dropped_event_count


def _build_request(method: str, path: str, *, path_params: dict | None = None) -> Request:
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "scheme": "http",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
        "http_version": "1.1",
        "path_params": path_params or {},
    }
    return Request(scope)


def test_log_model_update_sorts_fields() -> None:
    update = log_model_update(
        entity="projects",
        entity_id="a1b2",
        update_type="update",
        update_fields=["description", "name"],
    )
    assert update == {
        "dbEntity": "projects",
        "entityId": "a1b2",
        "updateType": "update",
        "updateFields": ["description", "name"],
    }


def test_sanitize_for_logging_masks_sensitive_keys_recursively() -> None:
    payload = {
        "email": "a@example.com",
        "password": "abc",
        "profile": {"api_key": "secret", "tokenValue": "123", "first_name": "A"},
        "nested": [{"authorizationHeader": "x"}, {"ok": "yes"}],
    }
    sanitized = _sanitize_for_logging(payload)
    assert sanitized["password"] == "***"
    assert sanitized["profile"]["api_key"] == "***"
    assert sanitized["profile"]["tokenValue"] == "***"
    assert sanitized["profile"]["first_name"] == "A"
    assert sanitized["nested"][0]["authorizationHeader"] == "***"


def _request_with_content_type(content_type: str) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/auth/token",
        "scheme": "http",
        "query_string": b"",
        "headers": [(b"content-type", content_type.encode())],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
        "http_version": "1.1",
        "path_params": {},
    }
    return Request(scope)


def test_parse_request_body_masks_form_urlencoded_password() -> None:
    # Regression: the OAuth2 password flow posts application/x-www-form-urlencoded,
    # which never parses as JSON. The parsed body must mask the password and must
    # not contain the plaintext anywhere before it is persisted to the audit DB.
    request = _request_with_content_type("application/x-www-form-urlencoded")
    body = b"grant_type=password&username=viewer%40example.com&password=hunter2"

    parsed = _parse_request_body(request, body)

    assert parsed == {
        "grant_type": "password",
        "username": "viewer@example.com",
        "password": "***",
    }
    assert "hunter2" not in json.dumps(parsed)


def test_parse_request_body_drops_unparseable_body() -> None:
    # A non-JSON, non-form body must not be persisted verbatim (it could carry
    # secrets we cannot key-mask); it degrades to a safe placeholder.
    request = _request_with_content_type("text/plain")
    body = b"password=hunter2 leaked as raw text"

    parsed = _parse_request_body(request, body)

    assert parsed == {
        "_captured": False,
        "_reason": "unparsed_body",
        "_content_type": "text/plain",
        "_bytes": len(body),
    }
    assert "hunter2" not in json.dumps(parsed)


def test_parse_request_body_still_masks_json_bodies() -> None:
    request = _request_with_content_type("application/json")
    body = json.dumps({"email": "a@example.com", "password": "hunter2"}).encode()

    parsed = _parse_request_body(request, body)

    assert parsed == {"email": "a@example.com", "password": "***"}


def test_derive_db_update_for_patch_request() -> None:
    request = _build_request(
        "PATCH",
        "/families/demo_family/small-variant-tags/review",
        path_params={"family_id": "demo_family", "tag_key": "review"},
    )
    update = _derive_db_update(request, {"label": "Needs review", "color": "#112233"})
    assert update is not None
    assert update["dbEntity"] == "families"
    assert update["entityId"] == "demo_family"
    assert update["updateType"] == "update"
    assert update["updateFields"] == ["color", "label"]


def test_derive_db_update_strips_api_mount_prefix() -> None:
    # Deployed routes are mounted under ``/api`` — the derived entity must be the real
    # collection ("families"), not the "api" mount segment.
    request = _build_request(
        "POST",
        "/api/families/demo_family/notes",
        path_params={"family_id": "demo_family"},
    )
    update = _derive_db_update(request, {"text": "hi"})
    assert update is not None
    assert update["dbEntity"] == "families"
    assert update["entityId"] == "demo_family"
    assert update["updateType"] == "create"


def test_derive_db_update_ignores_bare_api_prefix() -> None:
    # A request to the mount root alone has no entity once the prefix is stripped.
    request = _build_request("DELETE", "/api")
    assert _derive_db_update(request, None) is None


def test_query_string_for_logging_omits_values_by_default(monkeypatch) -> None:
    monkeypatch.setattr(settings, "audit_log_query_string_mode", "none")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/families/demo_family",
            "scheme": "http",
            "query_string": b"family_id=F1&start=1&end=2",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
            "http_version": "1.1",
        }
    )

    assert _query_string_for_logging(request) is None
    assert _request_url_for_logging(request) == "/families/demo_family"


def test_query_string_for_logging_can_keep_keys_only(monkeypatch) -> None:
    monkeypatch.setattr(settings, "audit_log_query_string_mode", "keys")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/families/demo_family",
            "scheme": "http",
            "query_string": b"family_id=F1&start=1&end=2",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
            "http_version": "1.1",
        }
    )

    assert _query_string_for_logging(request) == "end&family_id&start"


def test_query_string_for_logging_sanitizes_sensitive_values(monkeypatch) -> None:
    monkeypatch.setattr(settings, "audit_log_query_string_mode", "sanitized")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/families/demo_family",
            "scheme": "http",
            "query_string": b"family_id=F1&sample=S1&start=1",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
            "http_version": "1.1",
        }
    )

    assert _query_string_for_logging(request) == "family_id=%2A%2A%2A&sample=%2A%2A%2A&start=1"


def test_request_body_excluded_from_stdout_log_but_kept_in_audit(monkeypatch) -> None:
    # The clinical request body must NOT reach the stdout application log, but
    # must still be persisted to the access-controlled audit DB.
    captured_audit: list = []

    async def _fake_write(payload):
        captured_audit.append(payload)

    monkeypatch.setattr(rl, "write_audit_log_event", _fake_write)

    class _Capture(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.lines: list[dict] = []
            self.setFormatter(JsonLogFormatter())

        def emit(self, record: logging.LogRecord) -> None:
            self.lines.append(json.loads(self.format(record)))

    handler = _Capture()
    rl.logger._logger.addHandler(handler)
    previous_level = rl.logger._logger.level
    rl.logger._logger.setLevel(logging.INFO)
    try:
        body = json.dumps({"patient_name": "Jane Doe", "hpo": ["HP:0001250"]}).encode()
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/families/F1/notes",
            "scheme": "http",
            "query_string": b"",
            "http_version": "1.1",
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
            "path_params": {"family_id": "F1"},
        }
        sent = {"done": False}

        async def receive():
            if not sent["done"]:
                sent["done"] = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        async def call_next(_request):
            return JSONResponse({"ok": True})

        asyncio.run(rl.log_request_response(Request(scope, receive), call_next))
    finally:
        rl.logger._logger.removeHandler(handler)
        rl.logger._logger.setLevel(previous_level)

    assert handler.lines, "expected a stdout log line"
    line = handler.lines[-1]
    # The clinical body and its values must be absent from the stdout log payload.
    assert "requestBody" not in line
    assert "Jane Doe" not in json.dumps(line)
    # Non-PHI request metadata is still logged.
    assert "httpRequest" in line

    # The audit DB still receives the full request body.
    assert captured_audit, "expected an audit-log payload"
    assert captured_audit[-1].request_body == {
        "patient_name": "Jane Doe",
        "hpo": ["HP:0001250"],
    }


def _json_post(path: str, body: bytes) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "scheme": "http",
        "query_string": b"",
        "http_version": "1.1",
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
        "path_params": {},
    }
    sent = {"done": False}

    async def receive():
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    return Request(scope, receive)


def _insert_error_quoting_the_row() -> DBAPIError:
    # The error of a failed INSERT quotes its statement and the row it could not write
    # (SQLAlchemy appends the parameters, the request body among them).
    class _AsyncpgError(Exception):
        pass

    class _AdaptedError(Exception):
        sqlstate = "22P05"

    orig = _AdaptedError("unsupported Unicode escape sequence")
    orig.__cause__ = _AsyncpgError("unsupported Unicode escape sequence")
    failure = DBAPIError(
        "INSERT INTO audit_log_events ...", {"request_body": '{"patient_name": "Jane Doe"}'}, orig
    )
    assert "INSERT INTO" in str(failure) and "[parameters:" in str(failure)
    return failure


def _post_a_note(monkeypatch, caplog) -> tuple[int, list[str]]:
    async def call_next(_request):
        return JSONResponse({"ok": True})

    monkeypatch.setattr(event_pipeline, "_dropped_counts", {})
    caplog.set_level(logging.WARNING)
    body = json.dumps({"patient_name": "Jane Doe"}).encode()
    response = asyncio.run(rl.log_request_response(_json_post("/api/families/F1/notes", body), call_next))
    return response.status_code, [record.getMessage() for record in caplog.records]


def _assert_lost_row_recorded(messages: list[str], reason: str) -> None:
    # Counted for the alert on coga_audit_events_not_persisted_total, and logged once with its
    # payload, from which the row can be restored (TF-13 S-5). The error is named by its kind,
    # never by its text, which quotes the statement and the row.
    assert dropped_event_count(AUDIT_PIPELINE_NAME) == 1
    lost = [message for message in messages if "event not persisted" in message]
    assert len(lost) == 1, messages
    assert lost[0].startswith(
        f"Audit pipeline audit_log: event not persisted ({reason}: "
        "DBAPIError (_AsyncpgError, SQLSTATE 22P05)); dropped_total=1 "
        "payload=AuditLogEventPayload(method='POST', path='/api/families/F1/notes', status_code=200,"
    )
    assert "request_body={'patient_name': 'Jane Doe'}" in lost[0]
    assert not [m for m in messages if "INSERT INTO" in m or "[parameters:" in m]


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_a_failed_synchronous_audit_write_is_counted_and_logged_with_its_row(
    mode, monkeypatch, caplog
) -> None:
    # AUDIT_LOG_MODE=sync writes the row as its request ends, and so does async while no
    # worker runs (before the lifespan starts it, after shutdown, under an in-process
    # client). A failed write there left only a warning: not counted, so the alert stayed
    # quiet, and the row lost. It is recorded now as one the worker could not store.
    failure = _insert_error_quoting_the_row()

    async def _failing_batch(_payloads):
        raise failure

    monkeypatch.setattr(settings, "audit_log_mode", mode)
    monkeypatch.setattr(audit_log_pg, "_audit_log_queue", None)
    monkeypatch.setattr(audit_log_pg, "_write_audit_log_batch", _failing_batch)

    status, messages = _post_a_note(monkeypatch, caplog)

    assert status == 200  # a lost audit row never fails the request
    _assert_lost_row_recorded(messages, "synchronous write failed")


def test_an_audit_write_that_raises_is_recorded_and_never_fails_the_request(
    monkeypatch, caplog
) -> None:
    # write_audit_log_event records a failed write itself. Should it raise all the same, the
    # middleware's guard records the lost row the same way.
    failure = _insert_error_quoting_the_row()

    async def _raising_write(_payload):
        raise failure

    monkeypatch.setattr(rl, "write_audit_log_event", _raising_write)

    status, messages = _post_a_note(monkeypatch, caplog)

    assert status == 200
    _assert_lost_row_recorded(messages, "audit write raised")
