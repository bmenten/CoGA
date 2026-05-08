from starlette.requests import Request

from app.middleware.request_logging import _derive_db_update, _sanitize_for_logging
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
