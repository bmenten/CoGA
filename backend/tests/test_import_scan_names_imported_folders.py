"""The package list names the families imported from each folder it lists.

A package folder stays on the share after its import (CoGA serves the alignments and the QC
report from it), so the import page's list kept every folder ever imported, reading like one that
waits. The list now gives each folder the families whose package record names it
(``families.metadata.package_import.folder_path``), when their last import registered them, and
whether that import did not complete or still runs; the page hides a folder whose families all
imported completely. A bucket folder's URI is matched with or without the ``/`` it may end with.

All samples and values here are synthetic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.services import imported_package_folders as imported
from backend.tests._family_import_fakes import signed_in_client


class _Session:
    """Answers the look-up with ``rows`` and keeps the folders it was asked for."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.asked: list[list[str]] = []

    async def execute(self, statement: Any, params: Any = None) -> Any:
        assert "FROM families" in str(statement)
        self.asked.append(list(params["folders"]))
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: self.rows))


def _row(family_id: str, folder: str, **metadata: Any) -> dict[str, Any]:
    return {
        "family_id": family_id,
        "folder_path": folder,
        "registered_at": metadata.get("registered_at"),
        "import_incomplete": metadata.get("import_incomplete"),
        "import_unfinished": metadata.get("import_unfinished"),
    }


@pytest.mark.asyncio
async def test_each_folder_gets_the_families_imported_from_it() -> None:
    session = _Session(
        [
            _row("FAM1", "/data/families/FAM1", registered_at="2026-11-05T10:00:00+00:00"),
            _row("FAM1B", "/data/families/FAM1"),
        ]
    )

    found = await imported.imported_package_folders(
        session,  # type: ignore[arg-type]
        ["/data/families/FAM1", "/data/families/FAM2"],
    )

    assert session.asked == [["/data/families/FAM1", "/data/families/FAM2"]]
    assert [family.model_dump() for family in found["/data/families/FAM1"]] == [
        {
            "family_id": "FAM1",
            "registered_at": datetime(2026, 11, 5, 10, tzinfo=timezone.utc),
            "import_incomplete": False,
        },
        {"family_id": "FAM1B", "registered_at": None, "import_incomplete": False},
    ]
    assert "/data/families/FAM2" not in found


@pytest.mark.asyncio
async def test_a_bucket_folder_is_matched_with_or_without_its_closing_slash() -> None:
    session = _Session([_row("FAM1", "gs://bucket/packages/FAM1/")])

    found = await imported.imported_package_folders(
        session,  # type: ignore[arg-type]
        ["gs://bucket/packages/FAM1", "gs://bucket/packages/FAM1/"],
    )

    assert session.asked == [["gs://bucket/packages/FAM1"]]
    assert [family.family_id for family in found["gs://bucket/packages/FAM1"]] == ["FAM1"]
    assert [family.family_id for family in found["gs://bucket/packages/FAM1/"]] == ["FAM1"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("flag", "unfinished", "incomplete"),
    [
        (None, None, False),
        (False, {}, False),
        ({"failed_datasets": ["snv"]}, None, True),
        (True, None, True),
        (None, {"00000000-0000-4000-8000-000000000001": {"datasets": ["snv"]}}, True),
    ],
)
async def test_a_family_whose_last_import_did_not_complete_or_runs_is_told(flag: Any, unfinished: Any, incomplete: bool) -> None:
    session = _Session([_row("FAM1", "/data/families/FAM1", import_incomplete=flag, import_unfinished=unfinished)])

    found = await imported.imported_package_folders(session, ["/data/families/FAM1"])  # type: ignore[arg-type]

    assert found["/data/families/FAM1"][0].import_incomplete is incomplete


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["yesterday", 20261105, None])
async def test_a_registration_time_that_is_no_time_is_left_out(value: Any) -> None:
    session = _Session([_row("FAM1", "/data/families/FAM1", registered_at=value)])

    found = await imported.imported_package_folders(session, ["/data/families/FAM1"])  # type: ignore[arg-type]

    assert found["/data/families/FAM1"][0].registered_at is None


@pytest.mark.asyncio
async def test_an_empty_list_asks_nothing() -> None:
    session = _Session([])

    assert await imported.imported_package_folders(session, []) == {}  # type: ignore[arg-type]
    assert session.asked == []


def test_the_package_list_gives_each_folder_its_imported_families(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.routers import family_imports
    from backend.app.schemas import ImportedPackageFamily

    def scan() -> list[dict[str, Any]]:
        return [
            {"folder_path": folder, "name": name, "family_id": name, "has_ped": True}
            for name, folder in (("FAM1", "/data/families/FAM1"), ("FAM2", "/data/families/FAM2"))
        ]

    async def lookup(_session: Any, folders: list[str]) -> dict[str, list[ImportedPackageFamily]]:
        assert folders == ["/data/families/FAM1", "/data/families/FAM2"]
        return {"/data/families/FAM1": [ImportedPackageFamily(family_id="FAM1")]}

    monkeypatch.setattr(family_imports, "scan_family_import_packages", scan)
    monkeypatch.setattr(family_imports, "imported_package_folders", lookup)

    with signed_in_client("superuser") as client:
        listed = client.get("/api/family-imports/packages")

    assert listed.status_code == 200, listed.text
    assert [(item["name"], item["imported_families"]) for item in listed.json()] == [
        ("FAM1", [{"family_id": "FAM1", "registered_at": None, "import_incomplete": False}]),
        ("FAM2", []),
    ]
