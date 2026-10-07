import json

import pytest

from app.services.audit_log_pg import (
    AuditLogEventPayload,
    _audit_log_insert_params,
    _insert_audit_log_event,
)


class _FakeSession:
    def __init__(self) -> None:
        self.params = None

    async def execute(self, _query, params):
        self.params = params


@pytest.mark.asyncio
async def test_insert_audit_log_event_serializes_jsonb_fields() -> None:
    session = _FakeSession()
    payload = AuditLogEventPayload(
        method="PATCH",
        path="/families/F1/small-variant-tags/review",
        status_code=200,
        duration_ms=12,
        request_body={"label": "Review", "password": "***"},
        request_meta={"headers": {"content-type": "application/json"}},
        db_update={"dbEntity": "families", "updateType": "update"},
        user_email="admin@example.com",
    )

    await _insert_audit_log_event(session, payload)

    assert isinstance(session.params["request_body"], str)
    assert isinstance(session.params["request_meta"], str)
    assert isinstance(session.params["db_update"], str)


def test_insert_params_escape_what_postgres_refuses_and_flag_the_columns() -> None:
    # A %00 in the URL arrives decoded; the body is what json.loads made of the request.
    payload = AuditLogEventPayload(
        method="DELETE",
        path="/api/projects/P\x00X",
        status_code=400,
        duration_ms=3,
        query_string="k\x00ey",
        request_body={"note": "x\x00y", "half": "\ud800", "ratio": float("nan")},
        request_meta={"headers": {"content-type": "application/json"}},
        db_update={"dbEntity": "projects", "entityId": "P\x00X", "updateType": "delete"},
        error="ValueError: bad id P\x00X",
    )

    params = _audit_log_insert_params(payload)

    assert params["path"] == "/api/projects/P\\x00X"
    assert params["query_string"] == "k\\x00ey"
    assert params["error"] == "ValueError: bad id P\\x00X"
    assert json.loads(params["request_body"]) == {"note": "x\\x00y", "half": "\\ud800", "ratio": "NaN"}
    assert json.loads(params["db_update"])["entityId"] == "P\\x00X"
    # request_meta keeps what the middleware recorded and names every escaped column.
    assert json.loads(params["request_meta"]) == {
        "headers": {"content-type": "application/json"},
        "_escaped": ["db_update", "error", "path", "query_string", "request_body"],
    }
    # The payload itself is left as the middleware built it.
    assert payload.path == "/api/projects/P\x00X"


def test_insert_params_of_a_clean_request_are_unchanged() -> None:
    payload = AuditLogEventPayload(
        method="GET",
        path="/api/families/F1",
        status_code=200,
        duration_ms=1,
        user_id="00000000-0000-0000-0000-000000000001",
        request_meta={"headers": {"accept": "*/*"}},
    )

    params = _audit_log_insert_params(payload)

    assert params["path"] == "/api/families/F1"
    assert params["user_id"] == "00000000-0000-0000-0000-000000000001"
    assert params["request_body"] is None
    assert params["db_update"] is None
    assert json.loads(params["request_meta"]) == {"headers": {"accept": "*/*"}}
