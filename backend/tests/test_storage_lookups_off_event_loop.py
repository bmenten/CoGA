"""File lookups of a request run in a worker thread, not on the event loop.

The backend runs one uvicorn worker, so a blocking call on the event loop stalls every request,
and a package folder may lie on a network share or a mounted bucket, which answer slowly. The
QC-report link and report look the report up in the file system, and the admin's raw-file list
looks up each row's file. These pin that each lookup runs on another thread than the request's
coroutine. IDs and paths are synthetic.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import threading
from typing import Any
import uuid

import pytest
from fastapi.responses import FileResponse

from backend.app.core.config import settings
from backend.app.routers import family_qc_reports
from backend.app.services import raw_import_files_pg

_REPORT = "qc/S1.html"


@pytest.fixture()
def recorded_report(monkeypatch: pytest.MonkeyPatch) -> set[int]:
    """The report path the sample records, read on the request's own thread, which it notes."""
    loop_threads: set[int] = set()

    async def recorded_path(_session: Any, *, family_id: str, sample_id: str, user: Any) -> Any:
        loop_threads.add(threading.get_ident())
        return family_qc_reports._RecordedReport(_REPORT)

    async def recorded(_session: Any, *, family_id: str, sample_id: str) -> Any:
        loop_threads.add(threading.get_ident())
        return family_qc_reports._RecordedReport(_REPORT)

    monkeypatch.setattr(family_qc_reports, "_recorded_report_path", recorded_path)
    monkeypatch.setattr(family_qc_reports, "_recorded_report", recorded)
    monkeypatch.setattr(settings, "secret_key", "k" * 64)
    return loop_threads


async def _link_and_report() -> tuple[Any, Any]:
    link = await family_qc_reports.get_family_qc_report_link("F1", "S1", session=None, user=None)  # type: ignore[arg-type]
    token = family_qc_reports._qc_report_token("F1", "S1")
    report = await family_qc_reports.get_family_qc_report("F1", "S1", token=token, session=None)  # type: ignore[arg-type]
    return link, report


def test_the_qc_report_routes_read_the_file_system_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch, recorded_report: set[int], tmp_path: Path
) -> None:
    report_file = tmp_path / "S1.html"
    report_file.write_text("<html></html>")
    lookups: list[int] = []

    def resolve(family_id: str, relative_path: str, package_folder: object = None) -> Path:
        lookups.append(threading.get_ident())
        return report_file

    monkeypatch.setattr(family_qc_reports, "_resolve_report_file", resolve)

    _link, report = asyncio.run(_link_and_report())

    assert isinstance(report, FileResponse) and Path(report.path) == report_file
    assert len(lookups) == 2
    assert recorded_report.isdisjoint(lookups), "the file system was read on the event loop"


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> "_Rows":
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class _Session:
    """Answers each query with the rows it holds, noting the thread that queried."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.threads: set[int] = set()

    async def execute(self, *_args: Any, **_kwargs: Any) -> _Rows:
        self.threads.add(threading.get_ident())
        return _Rows(self.rows)


def test_the_raw_file_list_looks_up_each_file_off_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {"id": str(uuid.uuid4()), "storage_path": f"/data/families/F1/{name}", "created_at": None}
        for name in ("F1.ped", "S1.vcf.gz")
    ]
    lookups: list[int] = []

    def folders(_package_folder: object = None) -> list[str]:
        lookups.append(threading.get_ident())
        return []

    def as_served(row: dict[str, Any], _folders: Any = None) -> dict[str, Any]:
        lookups.append(threading.get_ident())
        return dict(row)

    monkeypatch.setattr(raw_import_files_pg, "readable_folders", folders)
    monkeypatch.setattr(raw_import_files_pg, "_row_to_dict", as_served)
    session = _Session(rows)

    listed = asyncio.run(raw_import_files_pg.list_raw_import_files(session, str(uuid.uuid4())))  # type: ignore[arg-type]
    one = asyncio.run(raw_import_files_pg.get_raw_import_file(session, rows[0]["id"]))  # type: ignore[arg-type]

    assert [row["id"] for row in listed] == [row["id"] for row in rows]
    assert one is not None and one["id"] == rows[0]["id"]
    assert len(lookups) == 4  # the folders and two rows for the list, one row for the file
    assert session.threads.isdisjoint(lookups), "a raw file was looked up on the event loop"
