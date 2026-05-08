import pytest

from app.services.audit_log_pg import AuditLogEventPayload, _insert_audit_log_event


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
