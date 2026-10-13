"""The admin routes of the sequencing-QC limits (REQ-QC-003).

The limits are admin-managed. ``PUT /api/admin/qc-thresholds/{profile_key}`` refuses a
viewer before anything is read, refuses a limit on a metric outside the catalogue with the
service's 400, takes the failing side from the catalogue whatever the body says, and
records the signed-in admin as the editor of a limit it sets. ``GET
/api/admin/qc-thresholds`` serves the catalogue the software fixes, each metric with its
failing side, so the admin page offers no other metric and no side to choose.

The requests go through the real routes and ``set_qc_threshold``; the session records the
statements instead of running them, and the caller is set by overriding
``get_current_user``. The accounts are synthetic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi.testclient import TestClient
import pytest

from backend.app.core.postgres import get_postgres_session
from backend.app.dependencies import get_current_user
from backend.app.main import app
from backend.app.middleware import request_logging
from backend.app.routers import admin as admin_router
from backend.app.services.access_control import CurrentUser
from backend.app.services.qc_threshold_service import QC_METRICS

_ADMIN = CurrentUser(
    id="11111111-1111-1111-1111-111111111111",
    username="qa-admin@example.org",
    email="qa-admin@example.org",
    role="admin",
    created_at=datetime.now(timezone.utc),
)
_VIEWER = CurrentUser(
    id="22222222-2222-2222-2222-222222222222",
    username="viewer@example.org",
    email="viewer@example.org",
    role="viewer",
    created_at=datetime.now(timezone.utc),
)


class _RecordingSession:
    """Answers ``set_qc_threshold``'s profile lookup (a profile exists) and its read of the
    bounds in force (none), and records every statement."""

    def __init__(self) -> None:
        self.statements: list[tuple[str, dict]] = []
        self.commits = 0

    async def execute(self, statement: Any, params: dict | None = None) -> Any:
        self.statements.append((" ".join(str(statement).split()), dict(params or {})))

        class _Result:
            def scalar_one_or_none(self_inner) -> str:
                return "00000000-0000-0000-0000-000000000009"

            def mappings(self_inner) -> Any:
                class _Mappings:
                    def first(self_m) -> None:
                        return None

                return _Mappings()

        return _Result()

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None

    def written(self, verb_and_table: str) -> list[dict]:
        return [params for sql, params in self.statements if sql.startswith(verb_and_table + " ")]


@pytest.fixture()
def signed_in(monkeypatch: pytest.MonkeyPatch):
    """Returns ``(client, session, sign_in)``; ``sign_in(user)`` sets the caller."""
    original_overrides = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True
    session = _RecordingSession()
    caller: dict[str, CurrentUser] = {"user": _ADMIN}

    async def override_get_postgres_session():
        yield session

    async def override_get_current_user() -> CurrentUser:
        return caller["user"]

    async def no_audit_write(_payload: Any) -> None:
        return None

    monkeypatch.setattr(request_logging, "write_audit_log_event", no_audit_write)
    app.dependency_overrides[get_postgres_session] = override_get_postgres_session
    app.dependency_overrides[get_current_user] = override_get_current_user

    def sign_in(user: CurrentUser) -> None:
        caller["user"] = user

    with TestClient(app) as client:
        yield client, session, sign_in
    app.dependency_overrides = original_overrides


_BODY = {
    "metric_key": "depth.mean_depth",
    "warn_value": 20.0,
    "error_value": 10.0,
    "reason": "Per the validation report",
}


def test_a_viewer_may_not_read_or_change_the_limits(signed_in) -> None:
    client, session, sign_in = signed_in
    sign_in(_VIEWER)

    responses = [
        client.put("/api/admin/qc-thresholds/default", json=_BODY),
        client.post("/api/admin/qc-thresholds/profiles", json={"label": "PGT"}),
        client.get("/api/admin/qc-thresholds"),
    ]

    for response in responses:
        assert response.status_code == 403, response.text
        assert response.json() == {"detail": "Admin access required"}
    assert session.statements == []
    assert session.commits == 0


def test_a_limit_on_a_metric_outside_the_catalogue_is_refused_at_the_route(signed_in) -> None:
    client, session, _sign_in = signed_in

    response = client.put(
        "/api/admin/qc-thresholds/default", json={**_BODY, "metric_key": "depth.median_depth"}
    )

    assert response.status_code == 400, response.text
    assert response.json() == {"detail": "Unknown QC metric: depth.median_depth"}
    assert session.statements == []
    assert session.commits == 0


def test_a_limit_an_admin_sets_records_the_admin_and_the_catalogues_failing_side(signed_in) -> None:
    client, session, _sign_in = signed_in

    # A side sent with the limit is no field of the request and is not read.
    response = client.put(
        "/api/admin/qc-thresholds/default", json={**_BODY, "direction": "higher_is_worse"}
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "profile_key": "default",
        "metric_key": "depth.mean_depth",
        "warn_value": 20.0,
        "error_value": 10.0,
    }
    [upsert] = session.written("INSERT INTO qc_thresholds")
    assert upsert["direction"] == "lower_is_worse"
    assert upsert["user_id"] == _ADMIN.id
    [change] = session.written("INSERT INTO qc_threshold_changes")
    assert (change["user_id"], change["user_email"], change["reason"]) == (
        _ADMIN.id,
        _ADMIN.email,
        "Per the validation report",
    )
    assert session.commits == 1


def test_the_catalogue_served_is_the_softwares_with_each_metrics_failing_side(
    signed_in, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _session, _sign_in = signed_in

    async def nothing_stored(*_args: Any, **_kwargs: Any) -> list:
        return []

    monkeypatch.setattr(admin_router, "list_qc_threshold_profiles", nothing_stored)
    monkeypatch.setattr(admin_router, "list_qc_threshold_changes", nothing_stored)

    response = client.get("/api/admin/qc-thresholds")

    assert response.status_code == 200, response.text
    served = response.json()["metrics"]
    assert [(metric["key"], metric["direction"]) for metric in served] == [
        (metric.key, metric.direction) for metric in QC_METRICS
    ]
    assert [(metric["label"], metric["unit"]) for metric in served] == [
        (metric.label, metric.unit) for metric in QC_METRICS
    ]
