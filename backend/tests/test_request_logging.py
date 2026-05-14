from starlette.requests import Request

from app.core.config import settings
from app.middleware.request_logging import (
    _derive_db_update,
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
