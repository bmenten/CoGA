"""The app as the family-folder lookup tests ask it: a viewer who may read every family.

The alignment, signal-track and QC-report lookups (``test_alignment_lookup_family_folders.py``,
``test_signal_track_lookup_family_folders.py``, ``test_qc_report_lookup_family_folders.py``)
and the URLs they hand out (``test_encoded_id_urls.py``) are asked through the app, ``httpx``
on the ASGI app, which decodes a path once as uvicorn does. What they check is which file a
request reaches, so the family check lets the viewer read every family the test lists, the
database holds no rows (no sample records where its import put a file, so the lookups read the
family's folders), and nothing is written to the audit table.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import ModuleType, SimpleNamespace
from typing import Any, Iterable, Mapping

from fastapi import HTTPException
import httpx
import pytest

from backend.app.core.postgres import get_postgres_session
from backend.app.dependencies import get_current_user
from backend.app.main import app
from backend.app.middleware import request_logging
from backend.app.services.access_control import CurrentUser

ORIGIN = "http://coga.test"


def a_viewer() -> CurrentUser:
    return CurrentUser(
        id="user-viewer",
        username="viewer@example.com",
        email="viewer@example.com",
        role="viewer",
        created_at=datetime.now(timezone.utc),
    )


def sign_in_a_viewer_who_reads(
    monkeypatch: pytest.MonkeyPatch,
    families: Mapping[str, Iterable[str]],
    *routers: ModuleType,
) -> None:
    """Sign a viewer in for the routers' family lookups: each family of ``families`` (its ID to
    its members' sample IDs, read as each request asks) is theirs, any other is not found."""

    async def family_record(session: Any, family_id: str, user: Any) -> Any:
        if family_id not in families:
            raise HTTPException(status_code=404, detail="Family not found")
        return SimpleNamespace(members=[SimpleNamespace(sample_id=sample_id) for sample_id in families[family_id]])

    async def no_session():
        yield _NoRows()

    async def signed_in() -> CurrentUser:
        return a_viewer()

    async def keep_audit_row(payload: Any) -> None:
        return None

    for router in routers:
        monkeypatch.setattr(router, "get_family_record", family_record)
    monkeypatch.setattr(request_logging, "write_audit_log_event", keep_audit_row)
    monkeypatch.setitem(app.dependency_overrides, get_postgres_session, no_session)
    monkeypatch.setitem(app.dependency_overrides, get_current_user, signed_in)


class _NoRows:
    """A database session that holds no rows: every query answers nothing."""

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(all=lambda: [], one_or_none=lambda: None, scalar_one_or_none=lambda: None)


def asgi_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN)
