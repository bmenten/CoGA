"""A queued import from a bucket keeps its gs:// or s3:// URI.

``str(Path("gs://bucket/x"))`` is ``gs:/bucket/x``: no longer a remote URI, so the worker
ran a queued package import on a local path that does not exist.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from backend.app.core.object_storage import is_remote_uri
from backend.app.services import family_package_jobs
from backend.app.services.family_package_source import package_folder_path


@pytest.mark.parametrize(
    "uri",
    ["gs://coga-phi/imports/F1", "s3://bucket/families/F1", "  gs://coga-phi/imports/F1  "],
)
def test_a_remote_package_folder_keeps_its_uri(uri: str) -> None:
    stored = package_folder_path(uri)
    assert stored == uri.strip()
    assert is_remote_uri(stored)


def test_a_local_package_folder_expands_the_home_directory() -> None:
    assert package_folder_path("~/families/F1") == str(Path("~/families/F1").expanduser())
    assert package_folder_path("/data/families/F1") == "/data/families/F1"


class _Result:
    def mappings(self) -> "_Result":
        return self

    def one(self) -> dict:
        return {}


class _Session:
    def __init__(self) -> None:
        self.params: dict = {}

    async def execute(self, statement, params):
        self.params = params
        return _Result()

    async def commit(self) -> None:
        return None


def test_a_queued_bucket_import_stores_a_uri_the_worker_can_run(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _Session()
    monkeypatch.setattr(family_package_jobs, "_serialize_job", lambda row: row)

    asyncio.run(
        family_package_jobs.queue_family_import_job(
            session,
            folder_path="gs://coga-phi/imports/F1",
            project_id=None,
            dry_run=True,
            requested_by="admin@example.org",
        )
    )

    assert session.params["submitted_path"] == "gs://coga-phi/imports/F1"
    assert is_remote_uri(session.params["submitted_path"])
