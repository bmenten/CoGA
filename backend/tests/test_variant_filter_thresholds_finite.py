"""A variant filter's threshold is a finite number or absent (CLIN-50).

The family small-variant and structural-variant lists, the Variant Explorer and the NIPT
variant list took ``nan`` (and ``inf``) as a threshold, which the SQL and the Python filter
paths compare differently, so one path kept what the other dropped. Each such query parameter
is now a ``FiniteThreshold``, and such a value is refused with 422 before the route runs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app import dependencies
from backend.app.core.config import settings
from backend.app.core.postgres import get_postgres_session
from backend.app.dependencies import create_access_token
from backend.app.main import app
from backend.app.services.access_control import CurrentUser

ANALYST = "analyst@example.com"


class _FakeSession:
    async def rollback(self) -> None:
        return None


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch):
    original_overrides = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True
    monkeypatch.setattr(settings, "azure_client_id", None)
    monkeypatch.setattr(settings, "azure_tenant_id", None)
    # Long enough for HS256; the token is minted below.
    monkeypatch.setattr(settings, "secret_key", "x" * 40)
    user = CurrentUser(
        id="00000000-0000-4000-8000-000000000001",
        username="analyst",
        email=ANALYST,
        role="analyst",
        created_at=datetime.now(timezone.utc),
    )

    async def by_email(_session: Any, email: str) -> CurrentUser | None:
        return user if email == ANALYST else None

    async def session():
        yield _FakeSession()

    monkeypatch.setattr(dependencies, "get_current_user_by_email", by_email)
    app.dependency_overrides[get_postgres_session] = session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides = original_overrides


@pytest.mark.parametrize(
    ("path", "threshold"),
    [
        ("/api/families/FAM1/small-variants", "max_gnomad_af"),
        ("/api/families/FAM1/small-variants/export", "min_cadd"),
        ("/api/families/FAM1/structural-variants", "max_population_af"),
        ("/api/variant-explorer/small-variants", "min_revel"),
        ("/api/families/FAM1/nipt/variants", "min_confidence"),
    ],
)
@pytest.mark.parametrize("value", ["nan", "NaN", "inf", "-inf"])
def test_a_threshold_that_is_no_finite_number_is_refused(client: TestClient, path: str, threshold: str, value: str) -> None:
    response = client.get(
        f"{path}?{threshold}={value}", headers={"Authorization": f"Bearer {create_access_token({'sub': ANALYST})}"}
    )

    assert response.status_code == 422, response.text
    [error] = response.json()["detail"]
    assert error["loc"] == ["query", threshold]


def test_no_variant_route_takes_a_float_threshold_without_the_guard() -> None:
    # A float query parameter added to these routers takes the same type, or NaN is back.
    routers = Path(__file__).resolve().parents[1] / "app" / "routers"
    for name in ("families_small_variants.py", "families_structural_variants.py", "variant_explorer.py", "families_nipt.py"):
        text = (routers / name).read_text()
        assert ": float | None = None" not in text and ": Optional[float] = None" not in text, name
