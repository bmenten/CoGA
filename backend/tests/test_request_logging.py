import asyncio
import json
import logging
import types

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
from app.services.audit_log_pg import log_model_update


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


def test_a_failed_audit_write_is_logged_by_kind_not_by_its_text(monkeypatch, caplog) -> None:
    # The error of a failed INSERT quotes the row it could not write (SQLAlchemy appends the
    # parameters, the request body among them); the warning used to log that text whole.
    class _AsyncpgError(Exception):
        pass

    class _AdaptedError(Exception):
        sqlstate = "22P05"

    orig = _AdaptedError("unsupported Unicode escape sequence")
    orig.__cause__ = _AsyncpgError("unsupported Unicode escape sequence")
    failure = DBAPIError(
        "INSERT INTO audit_log_events ...", {"request_body": '{"patient_name": "Jane Doe"}'}, orig
    )

    async def _failing_write(_payload):
        raise failure

    async def call_next(_request):
        return JSONResponse({"ok": True})

    monkeypatch.setattr(rl, "write_audit_log_event", _failing_write)
    caplog.set_level(logging.WARNING, logger=rl.logger._logger.name)
    body = json.dumps({"patient_name": "Jane Doe"}).encode()

    response = asyncio.run(rl.log_request_response(_json_post("/api/families/F1/notes", body), call_next))

    assert response.status_code == 200  # a lost audit row never fails the request
    warnings = [r for r in caplog.records if r.getMessage().startswith("Failed to persist audit log")]
    assert len(warnings) == 1
    assert warnings[0].getMessage() == (
        "Failed to persist audit log: DBAPIError (_AsyncpgError, SQLSTATE 22P05)"
    )
    assert "Jane Doe" not in JsonLogFormatter().format(warnings[0])


def test_an_unhandled_error_is_logged_by_kind_and_frames_not_by_its_text(monkeypatch, caplog) -> None:
    # A failed statement's text quotes its SQL and parameters, here a value from the request
    # body. The 500 line used to carry that text twice (message and traceback); it now names
    # the error, its route and its frames, and only the audit row keeps the text.
    class _AsyncpgError(Exception):
        pass

    class _AdaptedError(Exception):
        sqlstate = "22021"

    orig = _AdaptedError('invalid byte sequence for encoding "UTF8": 0x00')
    orig.__cause__ = _AsyncpgError('invalid byte sequence for encoding "UTF8": 0x00')
    failure = DBAPIError("UPDATE notes SET text = $1 WHERE family_id = $2", ("Jane Doe", "F1"), orig)
    captured_audit: list = []

    async def _fake_write(payload):
        captured_audit.append(payload)

    async def call_next(request):
        request.scope["route"] = types.SimpleNamespace(path="/api/families/{family_id}/notes")
        raise failure

    monkeypatch.setattr(rl, "write_audit_log_event", _fake_write)
    caplog.set_level(logging.INFO, logger=rl.logger._logger.name)
    body = json.dumps({"text": "Jane Doe"}).encode()

    with pytest.raises(DBAPIError):
        asyncio.run(rl.log_request_response(_json_post("/api/families/F1/notes", body), call_next))

    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert len(errors) == 1
    line = json.loads(JsonLogFormatter().format(errors[0]))
    assert line["message"] == "Unhandled server error: DBAPIError (_AsyncpgError, SQLSTATE 22021)"
    assert line["httpRequest"]["status"] == 500
    assert line["detail"]["route"] == "/api/families/{family_id}/notes"
    assert line["traceback"].startswith("Traceback (most recent call last):\n")
    assert ", in call_next\n" in line["traceback"]
    assert line["traceback"].endswith("\nDBAPIError (_AsyncpgError, SQLSTATE 22021)")
    assert "Jane Doe" not in json.dumps(line)
    # The access-controlled audit row keeps the error's full text, the body beside it.
    assert captured_audit[-1].status_code == 500
    assert captured_audit[-1].error == str(failure)
    assert "Jane Doe" in captured_audit[-1].error


def test_a_server_error_response_is_logged_with_its_route(monkeypatch, caplog) -> None:
    # A route that answers 5xx itself raised nothing: the line names the route, no traceback.
    async def _fake_write(_payload):
        return None

    async def call_next(request):
        request.scope["route"] = types.SimpleNamespace(path="/api/monarch/semsim")
        return JSONResponse({"detail": "Monarch is unavailable"}, status_code=503)

    monkeypatch.setattr(rl, "write_audit_log_event", _fake_write)
    caplog.set_level(logging.INFO, logger=rl.logger._logger.name)

    response = asyncio.run(rl.log_request_response(_build_request("GET", "/api/monarch/semsim"), call_next))

    assert response.status_code == 503
    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert len(errors) == 1
    line = json.loads(JsonLogFormatter().format(errors[0]))
    assert line["message"] == "Unhandled server error"
    assert line["detail"]["route"] == "/api/monarch/semsim"
    assert "traceback" not in line
