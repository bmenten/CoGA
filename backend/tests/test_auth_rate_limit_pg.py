from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.services.auth_rate_limit_pg import (
    clear_login_failures,
    get_login_throttle_state,
    record_failed_login,
)


class _FakeResult:
    def __init__(self, mapping=None, scalar=None) -> None:
        self._mapping = mapping
        self._scalar = scalar

    def mappings(self):
        return self

    def first(self):
        return self._mapping

    def scalar_one_or_none(self):
        return self._scalar


class _FakeSession:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict[str, object]] = {}

    async def execute(self, query, params):
        sql = str(query)
        key = (params["scope_type"], params["scope_value"])
        if "SELECT locked_until" in sql:
            row = self.rows.get(key)
            return _FakeResult(scalar=row.get("locked_until") if row is not None else None)
        if "SELECT failure_count, last_failure_at" in sql:
            return _FakeResult(mapping=self.rows.get(key))
        if "INSERT INTO auth_login_attempts" in sql:
            self.rows[key] = {
                "failure_count": params["failure_count"],
                "last_failure_at": params["last_failure_at"],
                "locked_until": params["locked_until"],
            }
            return _FakeResult()
        if "DELETE FROM auth_login_attempts" in sql:
            self.rows.pop(key, None)
            return _FakeResult()
        raise AssertionError(f"Unexpected SQL: {sql}")


@pytest.mark.asyncio
async def test_record_failed_login_applies_backoff_after_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession()
    now = datetime(2026, 5, 12, 8, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(settings, "login_rate_limit_threshold", 3)
    monkeypatch.setattr(settings, "login_rate_limit_base_backoff_seconds", 10)
    monkeypatch.setattr(settings, "login_rate_limit_max_backoff_seconds", 60)
    monkeypatch.setattr(settings, "login_rate_limit_window_seconds", 900)

    assert await record_failed_login(session, email="user@example.com", remote_ip=None, now=now) is None
    assert await record_failed_login(session, email="user@example.com", remote_ip=None, now=now) is None

    state = await record_failed_login(session, email="user@example.com", remote_ip=None, now=now)

    assert state is not None
    assert state.retry_after_seconds == 10
    assert session.rows[("email", "user@example.com")]["failure_count"] == 3

    next_state = await record_failed_login(session, email="user@example.com", remote_ip=None, now=now)

    assert next_state is not None
    assert next_state.retry_after_seconds == 20


@pytest.mark.asyncio
async def test_get_login_throttle_state_returns_active_lockout() -> None:
    session = _FakeSession()
    now = datetime(2026, 5, 12, 8, 0, tzinfo=timezone.utc)
    session.rows[("email", "user@example.com")] = {
        "failure_count": 5,
        "last_failure_at": now,
        "locked_until": now + timedelta(seconds=45),
    }

    state = await get_login_throttle_state(
        session,
        email="user@example.com",
        remote_ip=None,
        now=now,
    )

    assert state is not None
    assert state.retry_after_seconds == 45


@pytest.mark.asyncio
async def test_clear_login_failures_removes_email_and_ip_scopes() -> None:
    session = _FakeSession()
    session.rows[("email", "user@example.com")] = {
        "failure_count": 2,
        "last_failure_at": None,
        "locked_until": None,
    }
    session.rows[("remote_ip", "127.0.0.1")] = {
        "failure_count": 2,
        "last_failure_at": None,
        "locked_until": None,
    }

    await clear_login_failures(
        session,
        email="user@example.com",
        remote_ip="127.0.0.1",
    )

    assert session.rows == {}
