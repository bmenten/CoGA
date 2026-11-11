"""An import whose package changed while it was read is undone, and its folder offered again.

What the import read of a package that changed while it was read may be partly the package as it
was and partly as it is now. The owner decided (2026-11-05) that such an import is undone and the
package offered again: every dataset it wrote, or had still to run, counts as failed; a family it
created is removed, an existing family's data and package record are put back; the job ends
failed with ``package_changed_during_import``; and the package list says so for the folder
(``changed_during_import``), which the import page keeps listed. The paths through the real
stores are in e2e/test_e2e_import_undone_when_package_changes.py.

All samples and values here are synthetic.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.schemas import FamilyImportDatasetSummary
from backend.app.services import family_package_import, family_package_registration, imported_package_folders


def _dataset(dataset_type: str, status: str, message: str | None = None) -> FamilyImportDatasetSummary:
    return FamilyImportDatasetSummary(dataset_type=dataset_type, enabled=True, status=status, message=message)


def test_every_dataset_the_import_wrote_or_had_still_to_run_counts_as_failed() -> None:
    datasets = [
        _dataset("snv", "imported"),
        _dataset("sv", "warning"),
        _dataset("coverage", "registered"),
        _dataset("repeats", "valid"),
        _dataset("mito", "skipped"),
        _dataset("qc", "failed", "the QC report could not be read"),
        _dataset("haplotypes", "skipped"),
    ]
    ran = [summary for summary in datasets if summary.dataset_type != "haplotypes"]

    family_package_import._fail_datasets_of_changed_package(datasets, ran=ran)

    undone = family_package_import._UNDONE_DATASET_MESSAGE
    assert [(dataset.dataset_type, dataset.status, dataset.message) for dataset in datasets] == [
        ("snv", "failed", undone),
        ("sv", "failed", undone),
        ("coverage", "failed", undone),
        ("repeats", "failed", undone),
        # An update's skip wrote nothing; a failure keeps its own reason; a dataset the import
        # did not set out to import is left as it is.
        ("mito", "skipped", None),
        ("qc", "failed", "the QC report could not be read"),
        ("haplotypes", "skipped", None),
    ]


class _Session:
    """Answers each statement with ``rows`` and keeps what it was asked."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.commits = 0

    async def execute(self, statement: Any, params: Any = None) -> Any:
        self.statements.append((str(statement), dict(params or {})))
        rows = self.rows
        return SimpleNamespace(
            mappings=lambda: SimpleNamespace(all=lambda: rows, first=lambda: rows[0] if rows else None)
        )

    async def commit(self) -> None:
        self.commits += 1


@pytest.mark.asyncio
async def test_a_folder_whose_latest_import_was_undone_is_offered_again() -> None:
    session = _Session(
        [
            {"folder": "/data/families/FAM1", "changed": True},
            {"folder": "/data/families/FAM2", "changed": False},
            {"folder": "/data/share/FAM3", "changed": True},
        ]
    )

    changed = await imported_package_folders.folders_changed_during_import(
        session,  # type: ignore[arg-type]
        ["/data/families/FAM1", "/data/families/FAM2", "/data/share/FAM3/"],
    )

    assert changed == {"/data/families/FAM1", "/data/share/FAM3/"}
    [(sql, params)] = session.statements
    assert "NOT dry_run" in sql and "DISTINCT ON" in sql
    assert params["folders"] == ["/data/families/FAM1", "/data/families/FAM2", "/data/share/FAM3"]
    assert json.loads(params["changed"]) == [{"code": "package_changed_during_import"}]


@pytest.mark.asyncio
async def test_no_folder_asks_nothing() -> None:
    session = _Session([])

    assert await imported_package_folders.folders_changed_during_import(session, []) == set()  # type: ignore[arg-type]
    assert session.statements == []


@pytest.mark.asyncio
async def test_the_package_record_is_put_back_as_it_was() -> None:
    session = _Session([])

    await family_package_registration.put_back_family_package_record(
        session,  # type: ignore[arg-type]
        "00000000-0000-4000-8000-000000000001",
    )

    [(sql, params)] = session.statements
    assert "UPDATE families" in sql
    assert "metadata -> 'package_import' -> 'previous_import'" in sql and "- 'package_import'" in sql
    assert params == {"family_uuid": "00000000-0000-4000-8000-000000000001"}
    assert session.commits == 1


def test_the_registration_keeps_the_record_the_family_had_before() -> None:
    import inspect

    source = inspect.getsource(family_package_registration)
    assert "jsonb_build_object('previous_import', (metadata -> 'package_import') - 'previous_import')" in source
    assert "WHEN jsonb_typeof(metadata -> 'package_import') = 'object' THEN jsonb_build_object(" in source
