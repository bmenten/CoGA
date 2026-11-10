"""Shared stand-ins of the package-import tests (synthetic IDs throughout).

* ``admin_user()``: the administrator who runs an import or an edit.
* ``two_sample_context()``: the family FAM001 an import writes into, as
  ``build_family_metadata_context`` returns it: S1 (affected proband) and S2 (mother).
* ``authorize_import_root(monkeypatch, tmp_path)``: ``tmp_path/imports`` as the only
  authorised import root, with ``tmp_path/outside`` beside it under none.
* ``signed_in_client(role)``: the app as a user of that role sees it, with no database
  behind it; for requests that look no family up.
* ``no_variant_copy``: in place of the import's copy of an existing family's callsets to
  the projects that hold none of them (``copy_variants_to_linked_projects``, which reads
  Postgres and ClickHouse): nothing to copy.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.core.config import settings
from backend.app.services.access_control import CurrentUser
from backend.app.services.family_metadata_context import FamilyMetadataContext

FAMILY = "FAM001"
ADMIN_ID = "00000000-0000-0000-0000-000000000001"


def admin_user() -> CurrentUser:
    return CurrentUser(
        id=ADMIN_ID,
        username="admin@example.com",
        email="admin@example.com",
        role="admin",
        created_at=datetime.now(timezone.utc),
    )


async def no_variant_copy(*_args: Any, **_kwargs: Any) -> bool:
    """Nothing copied: every project of the family holds each of its callsets
    (``family_project_variants.copy_variants_to_linked_projects``)."""
    return False


def _default_sample_uuid(sample_id: str) -> str:
    return f"uuid-{sample_id}"


def two_sample_context(
    *,
    family_uuid: str = "family-uuid",
    family_id: str = FAMILY,
    sample_uuid: Callable[[str], str] = _default_sample_uuid,
) -> FamilyMetadataContext:
    s1, s2 = sample_uuid("S1"), sample_uuid("S2")
    return FamilyMetadataContext(
        family_uuid=family_uuid,
        family_id=family_id,
        project_ids=["project-uuid"],
        sample_rows=[
            {"sample_uuid": s1, "sample_id": "S1", "sex": "male", "role": "proband", "affected": True},
            {"sample_uuid": s2, "sample_id": "S2", "sex": "female", "role": "mother", "affected": False},
        ],
        sample_uuid_to_name={s1: "S1", s2: "S2"},
        sample_name_to_uuid={"S1": s1, "S2": s2},
        affected_sample_names=["S1"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


def authorize_import_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """The authorised import root; ``outside/`` beside it is not under it."""
    root = tmp_path / "imports"
    root.mkdir()
    (tmp_path / "outside").mkdir()
    monkeypatch.setattr(settings, "family_import_roots", [str(root)])
    return root


@contextmanager
def signed_in_client(role: str = "admin", *, raise_server_exceptions: bool = True) -> Iterator[Any]:
    """The app as a signed-in user of ``role`` sees it, with no database behind it. With
    ``raise_server_exceptions=False`` a request the app fails on is answered 500, as a
    server answers it."""
    from fastapi.testclient import TestClient

    from backend.app.core.postgres import get_postgres_session
    from backend.app.dependencies import get_current_user
    from backend.app.main import app

    original_overrides = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True
    user = CurrentUser(
        id=ADMIN_ID,
        username=f"{role}@example.com",
        email=f"{role}@example.com",
        role=role,
        created_at=datetime.now(timezone.utc),
    )

    class _Session:
        async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
            # No database: a look-up finds nothing (the package list's families imported
            # from each folder).
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: []))

        async def rollback(self) -> None:
            return None

    async def override_get_postgres_session() -> Any:
        yield _Session()

    async def override_get_current_user() -> CurrentUser:
        return user

    app.dependency_overrides[get_postgres_session] = override_get_postgres_session
    app.dependency_overrides[get_current_user] = override_get_current_user
    try:
        with TestClient(app, raise_server_exceptions=raise_server_exceptions) as client:
            yield client
    finally:
        app.dependency_overrides = original_overrides
