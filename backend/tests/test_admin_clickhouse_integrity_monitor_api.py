"""The scheduled ClickHouse integrity check's last result, served to admins.

The monitor (clickhouse_integrity_monitor.py) checks every variant assembly after startup
and then on an interval, and kept its last result in memory where no endpoint served it.
`GET /api/admin/clickhouse/variants/integrity-monitor` serves it: whether the check is on,
how often it runs, when the last sweep ran, and per assembly when it was checked and the
report, or that the check could not run. Admins only, like the rest of `/api/admin`.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import settings
from backend.app.core.postgres import get_postgres_session
from backend.app.dependencies import get_current_user
from backend.app.main import app
from backend.app.services import clickhouse_integrity_monitor as mon
from backend.app.services.access_control import CurrentUser

PATH = "/api/admin/clickhouse/variants/integrity-monitor"

_CORRUPT_REPORT = {
    "assembly_name": "GRCh38",
    "status": "corrupt",
    "table_checks": [
        {
            "name": "GRCh38/SNV_INDEL/entries",
            "exists": True,
            "passed": False,
            "failed_parts": 1,
            "messages": ["CHECKSUM_DOESNT_MATCH"],
        }
    ],
    "detached_broken_parts": [],
    "gene_index_consistency": {
        "checked": True,
        "gene_index_keys": 10,
        "annotation_index_gene_keys": 10,
        "consistent": True,
        "drift": 0,
    },
    "notes": ["One or more active parts failed CHECK TABLE — rebuild or restore affected tables."],
}


class _FakeSession:
    async def rollback(self) -> None:
        return None


def _client_as(role: str):
    user = CurrentUser(
        id=f"{role}-1",
        username=f"{role}@example.com",
        email=f"{role}@example.com",
        role=role,
        created_at=datetime.now(timezone.utc),
    )

    async def override_session():
        yield _FakeSession()

    async def override_user():
        return user

    app.dependency_overrides[get_postgres_session] = override_session
    # The real admin check runs on top of this user, as in production.
    app.dependency_overrides[get_current_user] = override_user
    return TestClient(app)


@pytest.fixture()
def restore_app():
    original = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True
    mon._last_results.clear()
    mon._last_sweep.clear()
    yield
    app.dependency_overrides = original
    mon._last_results.clear()
    mon._last_sweep.clear()


def _sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_list():
        return ["GRCh38", "T2T-CHM13"]

    async def fake_check(name):
        if name == "T2T-CHM13":
            raise RuntimeError("Code: 210. Connection refused")
        return _CORRUPT_REPORT

    monkeypatch.setattr(mon, "list_clickhouse_variant_assemblies", fake_list)
    monkeypatch.setattr(mon, "check_clickhouse_variant_integrity", fake_check)
    asyncio.run(mon.run_integrity_sweep())


def test_an_admin_sees_the_last_scheduled_result(restore_app, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "clickhouse_integrity_monitor_enabled", True)
    monkeypatch.setattr(settings, "clickhouse_integrity_interval_seconds", 21_600)
    _sweep(monkeypatch)

    with _client_as("admin") as client:
        response = client.get(PATH)

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["interval_seconds"] == 21_600
    assert body["last_sweep_at"] is not None
    assert body["last_sweep_error"] is None
    grch38, t2t = body["results"]
    assert grch38["assembly_name"] == "GRCh38"
    assert grch38["checked_at"] is not None
    assert grch38["report"] == _CORRUPT_REPORT
    assert grch38["error"] is None
    # A check that could not run says so; the exception text stays in the log.
    assert t2t["assembly_name"] == "T2T-CHM13"
    assert t2t["report"] is None
    assert t2t["error"] == mon.CHECK_FAILED_MESSAGE
    assert "Connection refused" not in response.text


def test_before_the_first_sweep_there_is_no_result(restore_app, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "clickhouse_integrity_monitor_enabled", False)

    with _client_as("admin") as client:
        response = client.get(PATH)

    assert response.status_code == 200
    assert response.json() == {
        "enabled": False,
        "interval_seconds": settings.clickhouse_integrity_interval_seconds,
        "last_sweep_at": None,
        "last_sweep_error": None,
        "results": [],
    }


def test_a_non_admin_is_refused(restore_app) -> None:
    with _client_as("viewer") as client:
        response = client.get(PATH)

    assert response.status_code == 403


def test_a_request_without_a_token_is_refused(restore_app) -> None:
    with TestClient(app) as client:
        response = client.get(PATH)

    assert response.status_code == 401
