"""An administrator's client of the app whose BED routes read a fake interval store.

The routes find the sample ``SAMPLE`` (PROBAND, a GRCh38 family's) and go through the real
``bed_service``, with the interval store's reads replaced by the test's. The session answers no
statement, so a request that reaches a database read fails; the request audit rows are kept,
not written.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.core.postgres import get_postgres_session
from backend.app.dependencies import get_current_user
from backend.app.main import app
from backend.app.middleware import request_logging
from backend.app.routers import bed as bed_router
from backend.app.services import bed_service
from backend.app.services.access_control import CurrentUser
from backend.app.services.family_metadata_context import SampleMetadataContext

SAMPLE = SampleMetadataContext(
    sample_uuid="uuid-proband",
    sample_id="PROBAND",
    family_uuid="family-uuid",
    family_id="FAM_BED",
    sex="female",
    project_ids=["project-uuid"],
    assembly_id="assembly-uuid",
    assembly_name="GRCh38",
)


class _FakeSession:
    async def rollback(self) -> None:
        return None


@contextmanager
def bed_route_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    rows: Callable[..., Any],
    downsampled: Callable[..., Any] | None = None,
) -> Iterator[TestClient]:
    """The client, with ``rows`` as the interval store's row read (``fetch_interval_track_rows``)
    and, when given, ``downsampled`` as its windowed APCAD read."""
    original_overrides = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True
    admin = CurrentUser(
        id="a1",
        username="admin@example.org",
        email="admin@example.org",
        role="admin",
        created_at=datetime.now(timezone.utc),
    )

    async def override_get_postgres_session():
        yield _FakeSession()

    async def override_get_current_user():
        return admin

    async def sample_context(_session: Any, *, sample_identifier: str, user: CurrentUser) -> SampleMetadataContext:
        return SAMPLE

    async def sources(_assembly: str, **_kwargs: Any) -> dict[str, list[str]]:
        return {"PROBAND": ["web"]}

    async def keep_audit_row(_payload: Any) -> None:
        return None

    monkeypatch.setattr(bed_router, "build_sample_metadata_context", sample_context)
    monkeypatch.setattr(bed_service, "get_interval_track_sources_by_sample", sources)
    monkeypatch.setattr(bed_service, "fetch_interval_track_rows", rows)
    if downsampled is not None:
        monkeypatch.setattr(bed_service, "fetch_apcad_downsampled", downsampled)
    monkeypatch.setattr(request_logging, "write_audit_log_event", keep_audit_row)
    app.dependency_overrides[get_postgres_session] = override_get_postgres_session
    app.dependency_overrides[get_current_user] = override_get_current_user
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides = original_overrides
        app.state.skip_startup_tasks = False
