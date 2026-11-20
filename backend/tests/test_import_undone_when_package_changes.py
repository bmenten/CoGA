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


_FAMILY_UUID = "00000000-0000-4000-8000-000000000001"
_MNIPT_MANIFEST = "a" * 64
_CNIPT_MANIFEST = "b" * 64


class _PutBack:
    """A family holding a monogenic NIPT, into which a conventional NIPT of the same plasma was
    imported and is undone: its records, its samples and its raw-file rows as the put-back reads
    them, and what it writes."""

    def __init__(self) -> None:
        self.records = {
            "monogenic_nipt": {"folder_path": "/imports/MNIPT/F1", "manifest_sha256": _MNIPT_MANIFEST},
            "conventional_nipt": {
                "folder_path": "/imports/CNIPT/F1",
                "manifest_sha256": _CNIPT_MANIFEST,
                # What the conventional NIPT's registration changed of the plasma, as it was.
                "previous_samples": {
                    "CFDNA1": {
                        "package_sample_metadata": {"assay": "nipt_cfdna", "assay_panel": "PANEL1"},
                        "assays": ["nipt_cfdna"],
                        "assay_panels": {"monogenic_nipt": "PANEL1"},
                        "package_imports": {"monogenic_nipt": {"datasets": {}}},
                    }
                },
            },
        }
        self.samples = [
            {
                "sample_uuid": "uuid-cfdna1",
                "sample_id": "CFDNA1",
                "metadata": {
                    "package_sample_metadata": {"assay": "cnipt_cfdna"},
                    "assays": ["cnipt_cfdna", "nipt_cfdna"],
                    "assay_panels": {"conventional_nipt": "LOWPASS", "monogenic_nipt": "PANEL1"},
                    "package_imports": {"conventional_nipt": {"datasets": {}}, "monogenic_nipt": {"datasets": {}}},
                    # A key the registration does not write stays as it is.
                    "sequencing_qc": {"monogenic_nipt": {"depth": 1}},
                },
            },
            {"sample_uuid": "uuid-father1", "sample_id": "FATHER1", "metadata": {"assays": []}},
        ]
        self.raw_files = [
            {"id": "f-cnipt", "storage_path": "/imports/CNIPT/F1/ccnv/CFDNA1.bins.bed", "managed": False, "dataset": "ccnv", "sha256": None},
            {"id": "f-mnipt", "storage_path": "/imports/MNIPT/F1/nipt.vcf", "managed": False, "dataset": "snv", "sha256": None},
            {"id": "m-cnipt", "storage_path": "/data/raw_imports/F1/0123456789ab__manifest.yaml", "managed": True, "dataset": "manifest", "sha256": _CNIPT_MANIFEST},
            {"id": "m-mnipt", "storage_path": "/data/raw_imports/F1/ba9876543210__manifest.yaml", "managed": True, "dataset": "manifest", "sha256": _MNIPT_MANIFEST},
        ]
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.samples_written: dict[str, dict[str, Any]] = {}
        self.deleted: list[str] = []
        self.commits = 0

    async def execute(self, statement: Any, params: Any = None) -> Any:
        sql = " ".join(str(statement).split())
        params = dict(params or {})
        self.statements.append((sql, params))
        if sql.startswith("SELECT metadata -> 'package_imports' AS records"):
            row = {"records": json.loads(json.dumps(self.records))}
            return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: row))
        if sql.startswith("SELECT id::text AS sample_uuid"):
            rows = json.loads(json.dumps(self.samples))
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))
        if sql.startswith("SELECT") and "FROM raw_import_files" in sql:
            files = self.raw_files
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: files))
        if sql.startswith("UPDATE samples"):
            self.samples_written[params["sample_uuid"]] = json.loads(params["metadata"])
        elif sql.startswith("DELETE FROM raw_import_files"):
            self.deleted = list(params["ids"])
        return SimpleNamespace()

    async def commit(self) -> None:
        self.commits += 1


@pytest.mark.asyncio
async def test_the_package_record_is_put_back_as_it_was(monkeypatch: pytest.MonkeyPatch) -> None:
    unlinked: list[Any] = []

    async def unlink(files: Any) -> int:
        unlinked.extend(files)
        return len(unlinked)

    monkeypatch.setattr(family_package_registration, "unlink_managed_files", unlink)
    session = _PutBack()

    await family_package_registration.put_back_family_package_record(
        session,  # type: ignore[arg-type]
        _FAMILY_UUID,
        "conventional_nipt",
    )

    [(sql, params)] = [(sql, params) for sql, params in session.statements if sql.startswith("UPDATE families")]
    # The record of the import's analysis, and the family's analysis types, as they were before it;
    # the records of the family's other analyses as they are.
    assert "metadata -> 'package_imports' -> CAST(:key AS text) -> 'previous_import'" in sql
    assert "metadata -> 'package_imports' -> CAST(:key AS text) -> 'previous_analysis_types'" in sql
    assert "((metadata -> 'package_imports') - CAST(:key AS text))" in sql
    assert params == {"family_uuid": _FAMILY_UUID, "key": "conventional_nipt"}
    # The plasma's assays, panels, manifest entry and package records as they were: it is the
    # monogenic NIPT's alone again. The father, whom the import did not change, is not written.
    assert session.samples_written == {
        "uuid-cfdna1": {
            "package_sample_metadata": {"assay": "nipt_cfdna", "assay_panel": "PANEL1"},
            "assays": ["nipt_cfdna"],
            "assay_panels": {"monogenic_nipt": "PANEL1"},
            "package_imports": {"monogenic_nipt": {"datasets": {}}},
            "sequencing_qc": {"monogenic_nipt": {"depth": 1}},
        }
    }
    # The raw files of the conventional NIPT's folder, which no record names any more, go, with the
    # copy of the manifest kept for its job; the monogenic NIPT's stay.
    assert sorted(session.deleted) == ["f-cnipt", "m-cnipt"]
    assert unlinked == [("/data/raw_imports/F1/0123456789ab__manifest.yaml", "m-cnipt")]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_a_record_put_back_to_its_own_folder_keeps_the_folders_raw_files(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unlink(_files: Any) -> int:
        return 0

    monkeypatch.setattr(family_package_registration, "unlink_managed_files", unlink)
    session = _PutBack()
    # A second import of the conventional NIPT from the same folder, undone: its record goes back
    # to the first import's, which names that folder and manifest.
    session.records["conventional_nipt"]["previous_import"] = {
        "folder_path": "/imports/CNIPT/F1",
        "manifest_sha256": _CNIPT_MANIFEST,
    }

    await family_package_registration.put_back_family_package_record(
        session,  # type: ignore[arg-type]
        _FAMILY_UUID,
        "conventional_nipt",
    )

    assert session.deleted == []


@pytest.mark.asyncio
async def test_a_family_without_the_record_is_left_as_it_is() -> None:
    session = _PutBack()
    session.records = {"monogenic_nipt": session.records["monogenic_nipt"]}

    await family_package_registration.put_back_family_package_record(
        session,  # type: ignore[arg-type]
        _FAMILY_UUID,
        "conventional_nipt",
    )

    assert [sql.split(" ")[0] for sql, _params in session.statements] == ["SELECT"]
    assert session.commits == 1


def test_the_registration_keeps_the_record_the_family_had_before() -> None:
    import inspect

    source = inspect.getsource(family_package_registration)
    assert "(metadata -> 'package_imports' -> CAST(:record_key AS text)) - 'previous_import'" in source
    assert "jsonb_build_object('previous_analysis_types', metadata -> 'analysis_types')" in source
