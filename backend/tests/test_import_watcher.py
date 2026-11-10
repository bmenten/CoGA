"""The import watcher's parts that need no database: what it watches, what it reads, its loop.

The watcher looks into the folders of FAMILY_IMPORT_WATCH_ROOTS that lie under FAMILY_IMPORT_ROOTS
(never a bucket), at each package folder there that holds a package file, coga-package.json. A
file that says its run is under way, or failed, is no delivery, and supersedes the open ones of its
folder. The watcher compares the files the import reads with the ones the package file lists, and
warns when the folder's PED says otherwise than the file's members; a check that fails is recorded
with a fixed sentence, never the error; a folder that changed while it was checked is left to the
next round. A round skips a watched folder only when it cannot be read, or lists nothing while it
has open deliveries (a share not mounted); one that lists folders without a package file is
mounted, and its missing deliveries count. An open delivery seen again is checked again only when
what CoGA holds has changed in what its check read (the project its package file names, the
family and samples of its package), never after a check that could not read them. Its loop
runs only while it holds the
leadership, logs a failed round without values, and returns at once when nothing is watched. The
rounds against a real database are in e2e/test_e2e_import_watcher.py.

All samples and values here are synthetic.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.core.config import settings
from backend.app.schemas import FamilyImportDatasetSummary, FamilyPackageValidationOut
from backend.app.services import import_watcher as watcher
from backend.app.services.coga_package import PACKAGE_FILE_NAME, CogaPackage


def _package(root: Path, name: str, *, package_file: bool = True) -> Path:
    folder = root / name
    (folder / "snv").mkdir(parents=True)
    (folder / "family.ped").write_text(f"{name}\tPROBAND\t0\t0\t1\t2\n", encoding="utf-8")
    (folder / "snv" / "family.vcf").write_text("##fileformat=VCFv4.2\n", encoding="utf-8")
    if package_file:
        (folder / PACKAGE_FILE_NAME).write_text("{}", encoding="utf-8")
    return folder


# --- what is watched -------------------------------------------------------------------------


def test_only_local_folders_under_the_import_roots_are_watched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    imports, outside = tmp_path / "imports", tmp_path / "outside"
    (imports / "share").mkdir(parents=True)
    outside.mkdir()
    monkeypatch.setattr(settings, "family_import_roots", [str(imports)])
    monkeypatch.setattr(
        settings,
        "family_import_watch_roots",
        [str(imports / "share"), str(imports / "share"), str(outside), "gs://bucket/share"],
    )
    caplog.set_level(logging.WARNING, logger=watcher.logger.name)

    assert watcher.watch_roots() == [(imports / "share").resolve()]
    assert "2 of FAMILY_IMPORT_WATCH_ROOTS is not watched" in caplog.text
    assert str(outside) not in caplog.text


def test_with_no_import_roots_in_development_any_local_folder_is_watched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [])
    monkeypatch.setattr(settings, "family_import_watch_roots", [str(tmp_path)])

    assert watcher.watch_roots() == [tmp_path.resolve()]


def test_the_marked_package_folders_of_a_watched_folder_are_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])
    marked = _package(tmp_path, "FAM2")
    _package(tmp_path, "FAM1", package_file=False)
    _package(tmp_path, ".hidden")
    (tmp_path / "loose.txt").write_text("a file, no folder", encoding="utf-8")
    (tmp_path / "LINK").symlink_to(marked, target_is_directory=True)

    # The link leads to a folder already found: it is found once.
    assert watcher.marked_folders(tmp_path) == watcher.WatchedFolderListing(folders=[marked.resolve()], empty=False)


def test_a_watched_folder_with_only_hidden_entries_is_empty_and_one_with_unmarked_folders_is_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])
    hidden_only, unmarked = tmp_path / "hidden_only", tmp_path / "unmarked"
    hidden_only.mkdir()
    (hidden_only / ".DS_Store").write_text("x", encoding="utf-8")
    _package(hidden_only, ".hidden")
    _package(unmarked, "FAM1", package_file=False)

    # A share that is not mounted reads as an empty folder; one that lists a folder is mounted.
    assert watcher.marked_folders(hidden_only) == watcher.WatchedFolderListing(folders=[], empty=True)
    assert watcher.marked_folders(unmarked) == watcher.WatchedFolderListing(folders=[], empty=False)


def test_a_watched_folder_that_cannot_be_read_gives_none(tmp_path: Path) -> None:
    assert watcher.marked_folders(tmp_path / "missing") is None
    (tmp_path / "file").write_text("x", encoding="utf-8")
    assert watcher.marked_folders(tmp_path / "file") is None


def test_a_watched_folder_whose_listing_fails_gives_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(_path: Any) -> Any:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(watcher.os, "scandir", refuse)

    assert watcher.marked_folders(tmp_path) is None


# --- samples and files -----------------------------------------------------------------------


def _members_package(*members: dict[str, Any]) -> CogaPackage:
    return CogaPackage.model_validate(
        {
            "schema": "coga-package/1",
            "status": "complete",
            "written_at": "2026-11-10T09:00:00Z",
            "pipeline": {"name": "p", "version": "1", "run_id": "RUN1"},
            "family": {"family_id": "FAM1"},
            "members": list(members),
        }
    )


def test_a_folder_ped_that_says_otherwise_than_the_members_is_a_warning(tmp_path: Path) -> None:
    folder = tmp_path / "FAM1"
    folder.mkdir()
    (folder / "FAM1.ped").write_text(
        "FAM1\tPROBAND\tFATHER1\tMOTHER\t1\t2\nFAM1\tFATHER1\t0\t0\t1\t1\nFAM1\tMOTHER\t0\t0\t2\t1\n"
        "FAM1\tSIB1\tFATHER1\tMOTHER\t0\t1\n",
        encoding="utf-8",
    )
    agreeing = _members_package(
        {"sample_id": "PROBAND", "sex": "male", "father": "FATHER1", "mother": "MOTHER"},
        {"sample_id": "FATHER1", "sex": "male"},
        {"sample_id": "MOTHER", "sex": "unknown"},
        # Unknown sex in the PED, and a member the PED lacks (an index): no difference.
        {"sample_id": "SIB1", "sex": "female", "father": "FATHER1", "mother": "MOTHER"},
        {"sample_id": "INDEX1", "sex": "female"},
    )
    assert watcher._folder_ped_differences(folder, agreeing) is None

    differing = _members_package(
        {"sample_id": "PROBAND", "sex": "female", "father": "FATHER1", "mother": "MOTHER"},
        {"sample_id": "FATHER1", "sex": "male"},
        {"sample_id": "MOTHER", "sex": "female", "father": "FATHER1"},
    )
    warning = watcher._folder_ped_differences(folder, differing)

    assert warning is not None and warning.code == "package_ped_differs"
    assert "(FAM1.ped)" in warning.message
    assert "of MOTHER, PROBAND, SIB1:" in warning.message


def test_without_members_or_a_readable_ped_nothing_is_compared(tmp_path: Path) -> None:
    folder = tmp_path / "FAM1"
    folder.mkdir()
    package = _members_package({"sample_id": "PROBAND", "sex": "male"})
    assert watcher._folder_ped_differences(folder, package) is None

    (folder / "FAM1.ped").write_bytes(b"\xff\xfe not text")
    assert watcher._folder_ped_differences(folder, package) is None
    (folder / "FAM1.ped").write_text("FAM1\tPROBAND\n", encoding="utf-8")
    assert watcher._folder_ped_differences(folder, package) is None

    no_members = package.model_copy(update={"members": None})
    assert watcher._folder_ped_differences(folder, no_members) is None


def test_the_files_the_import_reads_are_each_enabled_datasets(tmp_path: Path) -> None:
    # Not the folder's PED: the import reads none.
    folder = _package(tmp_path, "FAM1")
    (folder / "coverage").mkdir()
    (folder / "coverage" / "S1.bed").write_text("1\t0\t10\t3\n", encoding="utf-8")
    validation = FamilyPackageValidationOut(
        valid=True,
        datasets=[
            FamilyImportDatasetSummary(dataset_type="snv", files=["snv/family.vcf"]),
            FamilyImportDatasetSummary(dataset_type="coverage", files=["coverage"]),
            FamilyImportDatasetSummary(dataset_type="sv", enabled=False, files=["sv/calls.vcf"]),
        ],
    )

    assert watcher._files_the_import_reads(folder, validation) == ["coverage/S1.bed", "snv/family.vcf"]
    assert watcher._relative(folder, "/elsewhere/family.ped") is None
    assert watcher._relative(folder, None) is None


# --- checking a delivery ---------------------------------------------------------------------


class _Session:
    """Answers each statement with no rows, and keeps them."""

    def __init__(self, store: list[str]) -> None:
        self.store = store

    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def execute(self, statement: Any, _params: Any = None) -> Any:
        self.store.append(str(statement))
        return SimpleNamespace(first=lambda: None, mappings=lambda: SimpleNamespace(first=lambda: None, all=lambda: []), all=lambda: [])

    async def commit(self) -> None:
        return None


def _factory(store: list[str]) -> Any:
    return lambda: _Session(store)


@pytest.mark.asyncio
async def test_a_package_file_that_cannot_be_read_needs_attention(tmp_path: Path) -> None:
    folder = _package(tmp_path, "FAM1")
    (folder / PACKAGE_FILE_NAME).write_text("{not json", encoding="utf-8")

    assessment = await watcher.assess_delivery(_factory([]), folder)

    assert (assessment.state, [issue.code for issue in assessment.issues]) == ("needs_attention", ["package_not_json"])


def _complete_file(folder: Path, **fields: Any) -> None:
    """A complete package file for ``folder`` that lists its files as they are now."""
    files = [
        {"path": path.relative_to(folder).as_posix(), "size": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in sorted(folder.rglob("*"))
        if path.is_file() and path.name != PACKAGE_FILE_NAME
    ]
    body: dict[str, Any] = {
        "schema": "coga-package/1",
        "status": "complete",
        "written_at": "2026-11-10T09:00:00Z",
        "pipeline": {"name": "p", "version": "1", "run_id": "RUN1"},
        "project": "WGS",
        "assembly": "GRCh38",
        "analysis_type": "wgs",
        "family": {"family_id": folder.name},
        "files": files,
        **fields,
    }
    (folder / PACKAGE_FILE_NAME).write_text(json.dumps(body), encoding="utf-8")


@pytest.mark.asyncio
async def test_a_package_whose_copy_runs_still_is_incomplete(tmp_path: Path) -> None:
    folder = _package(tmp_path, "FAM1")
    _complete_file(folder)
    body = json.loads((folder / PACKAGE_FILE_NAME).read_text())
    body["files"].append({"path": "snv/family.vcf.tbi", "size": 10, "sha256": "0" * 64})
    (folder / PACKAGE_FILE_NAME).write_text(json.dumps(body), encoding="utf-8")

    assessment = await watcher.assess_delivery(_factory([]), folder)

    assert (assessment.state, [(issue.code, issue.path) for issue in assessment.issues]) == (
        "incomplete",
        [("file_missing", "snv/family.vcf.tbi")],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("qc", "issue", "warning"), [("fail", "package_qc_failed", None), ("warn", None, "package_qc_warned")])
async def test_the_pipelines_qc_and_the_files_it_does_not_list_are_told(
    tmp_path: Path, qc: str, issue: str | None, warning: str | None
) -> None:
    folder = _package(tmp_path, "FAM1")
    _complete_file(folder, qc={"status": qc}, family=None)
    for number in range(7):
        (folder / f"later{number}.txt").write_text("x", encoding="utf-8")

    assessment = await watcher.assess_delivery(_factory([]), folder)

    # A complete file without its family: nothing is read after the project (which the fake
    # session does not hold).
    assert [item.code for item in assessment.issues] == [
        "package_field_missing",
        *([issue] if issue else []),
        "package_project_unknown",
    ]
    assert [item.code for item in assessment.warnings] == ["file_not_in_package", *([warning] if warning else [])]
    assert assessment.warnings[0].message == (
        "The folder holds 7 file(s) coga-package.json does not list: "
        "later0.txt, later1.txt, later2.txt, later3.txt, later4.txt, and 2 more."
    )
    assert (assessment.state, assessment.manifest_text, assessment.family_id) == ("needs_attention", None, None)


@pytest.mark.asyncio
async def test_a_folder_discover_cannot_draft_for_needs_attention(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [])
    folder = _package(tmp_path, "FAM1")
    (folder / PACKAGE_FILE_NAME).write_text(
        '{"schema": "coga-package/1", "status": "complete", "written_at": "2026-11-10T09:00:00Z",'
        ' "family": {"family_id": "FAM1"}, "analysis_type": "wgs", "assembly": "GRCh38",'
        ' "pipeline": {"name": "p", "version": "1", "run_id": "r"}, "files": []}',
        encoding="utf-8",
    )

    def no_draft(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(errors=[SimpleNamespace(code="ped_missing", message="No PED was found.")], manifest_yaml="", warnings=[])

    monkeypatch.setattr(watcher, "discover_family_package_manifest", no_draft)

    assessment = await watcher.assess_delivery(_factory([]), folder)

    assert assessment.state == "needs_attention"
    # The package file gives no project and no files, nor its members and datasets; Discover
    # found no PED to propose them from.
    assert [issue.code for issue in assessment.issues] == [
        "package_field_missing",
        "package_field_missing",
        "discover_failed",
        "ped_missing",
    ]
    assert (assessment.run_id, assessment.family_id) == ("r", "FAM1")


@pytest.mark.asyncio
async def test_a_check_that_fails_is_recorded_with_a_fixed_sentence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    folder = _package(tmp_path, "FAM1")
    store: list[str] = []

    async def fingerprint(_folder: Path) -> str:
        return "a" * 64

    async def fail(*_args: Any) -> Any:
        raise RuntimeError("a value of FAM1")

    monkeypatch.setattr(watcher, "_fingerprint", fingerprint)
    monkeypatch.setattr(watcher, "assess_delivery", fail)
    caplog.set_level(logging.WARNING, logger=watcher.logger.name)

    # The fake session finds no earlier delivery and inserts none: the insert returns no row.
    assert await watcher.look_at_folder(_factory(store), tmp_path, folder) is None
    insert = next(statement for statement in store if "INSERT INTO import_deliveries" in statement)
    assert insert
    assert "FAM1" not in caplog.text and "could not check a delivery: RuntimeError" in caplog.text


@pytest.mark.asyncio
async def test_a_folder_that_changed_while_it_was_checked_is_left_to_the_next_round(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = _package(tmp_path, "FAM1")
    store: list[str] = []
    fingerprints = iter(["a" * 64, "b" * 64])

    async def fingerprint(_folder: Path) -> str:
        return next(fingerprints)

    async def assess(*_args: Any) -> Any:
        return watcher.DeliveryAssessment(state="ready")

    monkeypatch.setattr(watcher, "_fingerprint", fingerprint)
    monkeypatch.setattr(watcher, "assess_delivery", assess)

    assert await watcher.look_at_folder(_factory(store), tmp_path, folder) is None
    assert not any("INSERT INTO import_deliveries" in statement for statement in store)


@pytest.mark.asyncio
async def test_a_delivery_that_cannot_be_stored_is_kept_without_what_it_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    folder = _package(tmp_path, "FAM1")
    stored: list[Any] = []

    async def fingerprint(_folder: Path) -> str:
        return "a" * 64

    async def assess(*_args: Any) -> Any:
        return watcher.DeliveryAssessment(state="ready", family_id="FAM1")

    async def store(_factory: Any, _root: Path, _folder: Path, _fingerprint: str, assessment: Any) -> str:
        stored.append(assessment)
        if len(stored) == 1:
            raise RuntimeError("a value of FAM1")
        return assessment.state

    monkeypatch.setattr(watcher, "_fingerprint", fingerprint)
    monkeypatch.setattr(watcher, "assess_delivery", assess)
    monkeypatch.setattr(watcher, "_store_delivery", store)
    caplog.set_level(logging.WARNING, logger=watcher.logger.name)

    assert await watcher.look_at_folder(_factory([]), tmp_path, folder) == ("new", "needs_attention")
    assert [issue.code for issue in stored[1].issues] == ["watch_store_failed"]
    assert stored[1].family_id is None
    assert "could not store a delivery: RuntimeError" in caplog.text and "FAM1" not in caplog.text


@pytest.mark.asyncio
async def test_a_delivery_that_cannot_be_stored_at_all_stops_no_round(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = _package(tmp_path, "FAM1")

    async def fingerprint(_folder: Path) -> str:
        return "a" * 64

    async def assess(*_args: Any) -> Any:
        return watcher.DeliveryAssessment(state="ready")

    async def store(*_args: Any) -> str:
        raise RuntimeError("the database is gone")

    monkeypatch.setattr(watcher, "_fingerprint", fingerprint)
    monkeypatch.setattr(watcher, "assess_delivery", assess)
    monkeypatch.setattr(watcher, "_store_delivery", store)

    assert await watcher.look_at_folder(_factory([]), tmp_path, folder) is None


@pytest.mark.asyncio
async def test_a_folder_without_a_fingerprint_is_left_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store: list[str] = []

    async def none(_folder: Path) -> None:
        return None

    monkeypatch.setattr(watcher, "_fingerprint", none)

    assert await watcher.look_at_folder(_factory(store), tmp_path, tmp_path / "FAM1") is None
    assert store == []


@pytest.mark.asyncio
async def test_a_folder_that_cannot_be_fingerprinted_has_none(tmp_path: Path) -> None:
    assert await watcher._fingerprint(tmp_path / "missing") is None


class _OpenDeliveriesSession(_Session):
    """Answers the supersede statement with one open delivery of the folder."""

    async def execute(self, statement: Any, _params: Any = None) -> Any:
        self.store.append(str(statement))
        rows = [("d1", "f" * 64)] if "SET state = 'superseded'" in str(statement) else []
        return SimpleNamespace(all=lambda: rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["running", "failed"])
async def test_a_run_under_way_or_failed_is_no_delivery_and_supersedes_the_open_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    folder = _package(tmp_path, "FAM1")
    (folder / PACKAGE_FILE_NAME).write_text(
        json.dumps(
            {
                "schema": "coga-package/1",
                "status": status,
                "written_at": "2026-11-10T09:00:00Z",
                "pipeline": {"name": "p", "version": "1", "run_id": "RUN2"},
            }
        ),
        encoding="utf-8",
    )
    recorded: list[dict[str, Any]] = []

    async def record(_session: Any, **fields: Any) -> None:
        recorded.append(fields)

    async def no_fingerprint(_folder: Path) -> str:
        raise AssertionError("a run under way is no delivery: its folder is not fingerprinted")

    monkeypatch.setattr(watcher, "_record", record)
    monkeypatch.setattr(watcher, "_fingerprint", no_fingerprint)
    store: list[str] = []

    assert await watcher.look_at_folder(lambda: _OpenDeliveriesSession(store), tmp_path, folder) == ("superseded", "1")
    assert not any("INSERT INTO import_deliveries" in statement for statement in store)
    (event,) = recorded
    assert event["action"] == "import_delivery_superseded" and event["delivery_id"] == "d1"
    assert (event["after"]["package_status"], event["after"]["run_id"]) == (status, "RUN2")
    assert event["summary"] == f"Delivery FAM1: superseded, a pipeline run in its folder is {status}"

    # With no open delivery of the folder, nothing is superseded and nothing is reported.
    assert await watcher.look_at_folder(_factory([]), tmp_path, folder) is None


@pytest.mark.asyncio
async def test_a_round_counts_the_deliveries_superseded_by_a_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.services import import_delivery_review

    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])
    _package(tmp_path, "FAM1")
    _package(tmp_path, "FAM2")
    looks = iter([("superseded", "2"), ("new", "ready")])

    async def no_jobs(_factory: Any) -> int:
        return 0

    async def look(*_args: Any) -> tuple[str, str]:
        return next(looks)

    async def mark_missing(*_args: Any) -> int:
        return 0

    monkeypatch.setattr(import_delivery_review, "sync_delivery_jobs", no_jobs)
    monkeypatch.setattr(watcher, "look_at_folder", look)
    monkeypatch.setattr(watcher, "mark_missing", mark_missing)

    done = await watcher.run_watch_round(None, [tmp_path])  # type: ignore[arg-type]

    assert (done.superseded_for_run, done.deliveries, done.checked_again) == (2, {"ready": 1}, {})



# --- checking an open delivery again ----------------------------------------------------------


def _seen(**fields: Any) -> dict[str, Any]:
    """An open delivery's row as the watcher sees it again: one of a project CoGA lacked."""
    row: dict[str, Any] = {
        "id": "d" * 8,
        "state": "needs_attention",
        "issues": [{"code": "package_project_unknown", "message": "", "path": None}],
        "project_id": None,
        "marker": {"project": "LATER", "assembly": "GRCh38"},
        "validation": {"family_id": "FAM1", "sample_ids": ["PROBAND"]},
    }
    row.update(fields)
    return row


def _coga(monkeypatch: pytest.MonkeyPatch, *, project: tuple[str, str | None] | None, existing: tuple[str, ...] = ()) -> None:
    """What CoGA holds now: the project the package file names, and the codes of a family or samples
    that exist."""

    async def find_project(_session: Any, _name: str) -> tuple[str, str | None] | None:
        return project

    async def find_existing(_session: Any, **_kwargs: Any) -> list[Any]:
        return [SimpleNamespace(code=code, message="", path=None) for code in existing]

    monkeypatch.setattr(watcher, "_project", find_project)
    monkeypatch.setattr(watcher, "_existing_package_entity_warnings", find_existing)


@pytest.mark.asyncio
async def test_a_project_added_since_the_check_is_a_change(monkeypatch: pytest.MonkeyPatch) -> None:
    _coga(monkeypatch, project=("p1", "GRCh38"))
    assert await watcher._coga_side_changed(_Session([]), _seen()) is True

    _coga(monkeypatch, project=None)
    assert await watcher._coga_side_changed(_Session([]), _seen()) is False


@pytest.mark.asyncio
async def test_a_project_deleted_or_of_another_assembly_since_the_check_is_a_change(monkeypatch: pytest.MonkeyPatch) -> None:
    ready = _seen(state="ready", issues=[], project_id="p1")
    _coga(monkeypatch, project=("p1", "GRCh38"))
    assert await watcher._coga_side_changed(_Session([]), ready) is False

    # Deleted: the row's project_id was set to NULL with it.
    _coga(monkeypatch, project=None)
    assert await watcher._coga_side_changed(_Session([]), {**ready, "project_id": None}) is True

    _coga(monkeypatch, project=("p1", "GRCh37"))
    assert await watcher._coga_side_changed(_Session([]), ready) is True


@pytest.mark.asyncio
async def test_a_family_imported_or_deleted_since_the_check_is_a_change(monkeypatch: pytest.MonkeyPatch) -> None:
    ready = _seen(state="ready", issues=[], project_id="p1")
    _coga(monkeypatch, project=("p1", "GRCh38"), existing=("existing_family", "existing_samples"))
    assert await watcher._coga_side_changed(_Session([]), ready) is True

    existing = _seen(
        project_id="p1",
        issues=[{"code": "existing_family"}, {"code": "existing_samples"}],
    )
    assert await watcher._coga_side_changed(_Session([]), existing) is False

    _coga(monkeypatch, project=("p1", "GRCh38"))
    assert await watcher._coga_side_changed(_Session([]), existing) is True


@pytest.mark.asyncio
async def test_what_a_check_did_not_read_is_never_a_change(monkeypatch: pytest.MonkeyPatch) -> None:
    _coga(monkeypatch, project=("p1", "GRCh38"), existing=("existing_family",))
    # A package file that could not be read: the check read neither the project nor the family.
    unread = _seen(issues=[{"code": "package_field_invalid"}], validation=None)
    assert await watcher._coga_side_changed(_Session([]), unread) is False
    # A check that failed, or could not be stored: nothing it read is kept.
    failed = _seen(issues=[{"code": "watch_check_failed"}], marker=None, validation=None)
    assert await watcher._coga_side_changed(_Session([]), failed) is False
    # The rows' JSON as text reads the same.
    assert await watcher._coga_side_changed(_Session([]), {**unread, "marker": '{"project": "LATER"}'}) is False


class _SeenSession(_Session):
    """Finds the delivery ``row`` when the watcher sees its folder again."""

    def __init__(self, store: list[str], row: dict[str, Any]) -> None:
        super().__init__(store)
        self.row = row

    async def execute(self, statement: Any, _params: Any = None) -> Any:
        self.store.append(str(statement))
        row = self.row if "SET last_seen_at" in str(statement) else None
        return SimpleNamespace(first=lambda: row, mappings=lambda: SimpleNamespace(first=lambda: row, all=lambda: []), all=lambda: [])


@pytest.mark.asyncio
async def test_an_open_delivery_seen_again_is_checked_again_only_when_coga_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _package(tmp_path, "FAM1")
    assessed: list[Path] = []
    updated: list[tuple[str, str]] = []
    changed = {"value": False}

    async def fingerprint(_folder: Path) -> str:
        return "a" * 64

    async def coga_side_changed(_session: Any, _row: Any) -> bool:
        return changed["value"]

    async def assess(_factory: Any, checked: Path, _read: Any = None) -> Any:
        assessed.append(checked)
        return watcher.DeliveryAssessment(state="ready", project_id="p1")

    async def update(_factory: Any, _folder: Path, delivery_id: str, _fingerprint: str, assessment: Any) -> str:
        updated.append((delivery_id, assessment.state))
        return assessment.state

    monkeypatch.setattr(watcher, "_fingerprint", fingerprint)
    monkeypatch.setattr(watcher, "_coga_side_changed", coga_side_changed)
    monkeypatch.setattr(watcher, "assess_delivery", assess)
    monkeypatch.setattr(watcher, "_update_checked_again", update)
    store: list[str] = []

    def factory(row: dict[str, Any]) -> Any:
        return lambda: _SeenSession(store, row)

    assert await watcher.look_at_folder(factory(_seen()), tmp_path, folder) is None
    assert assessed == [] and updated == []

    changed["value"] = True
    assert await watcher.look_at_folder(factory(_seen()), tmp_path, folder) == ("checked_again", "ready")
    assert assessed == [folder] and updated == [("d" * 8, "ready")]
    assert not any("INSERT INTO import_deliveries" in statement for statement in store)

    # A delivery decided (or still copying) is never checked again.
    for state in ("refused", "queued", "imported", "incomplete", "superseded"):
        assert await watcher.look_at_folder(factory(_seen(state=state)), tmp_path, folder) is None
    assert len(assessed) == 1


@pytest.mark.asyncio
async def test_a_check_again_that_cannot_be_stored_is_kept_without_what_it_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    folder = _package(tmp_path, "FAM1")
    stored: list[Any] = []

    async def fingerprint(_folder: Path) -> str:
        return "a" * 64

    async def changed(_session: Any, _row: Any) -> bool:
        return True

    async def assess(*_args: Any) -> Any:
        return watcher.DeliveryAssessment(state="ready", family_id="FAM1", project_id="p1")

    async def update(_factory: Any, _folder: Path, _id: str, _fingerprint: str, assessment: Any) -> str:
        stored.append(assessment)
        if len(stored) == 1:
            raise RuntimeError("a value of FAM1")
        return assessment.state

    monkeypatch.setattr(watcher, "_fingerprint", fingerprint)
    monkeypatch.setattr(watcher, "_coga_side_changed", changed)
    monkeypatch.setattr(watcher, "assess_delivery", assess)
    monkeypatch.setattr(watcher, "_update_checked_again", update)
    caplog.set_level(logging.WARNING, logger=watcher.logger.name)

    looked = await watcher.look_at_folder(lambda: _SeenSession([], _seen()), tmp_path, folder)
    assert looked == ("checked_again", "needs_attention")
    # Without the project or the validation, it is not checked again in the next round.
    assert [issue.code for issue in stored[1].issues] == ["watch_store_failed"]
    assert (stored[1].project_id, stored[1].validation) == (None, None)
    assert "could not store a delivery: RuntimeError" in caplog.text and "FAM1" not in caplog.text


@pytest.mark.asyncio
async def test_a_folder_that_changed_while_it_was_checked_again_is_left_to_the_next_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _package(tmp_path, "FAM1")
    fingerprints = iter(["a" * 64, "b" * 64])
    updated: list[Any] = []

    async def fingerprint(_folder: Path) -> str:
        return next(fingerprints)

    async def changed(_session: Any, _row: Any) -> bool:
        return True

    async def assess(*_args: Any) -> Any:
        return watcher.DeliveryAssessment(state="ready")

    async def update(*args: Any) -> str:
        updated.append(args)
        return "ready"

    monkeypatch.setattr(watcher, "_fingerprint", fingerprint)
    monkeypatch.setattr(watcher, "_coga_side_changed", changed)
    monkeypatch.setattr(watcher, "assess_delivery", assess)
    monkeypatch.setattr(watcher, "_update_checked_again", update)

    assert await watcher.look_at_folder(lambda: _SeenSession([], _seen()), tmp_path, folder) is None
    assert updated == []


# --- the loop --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_round_skips_only_a_watched_folder_that_lists_nothing_while_deliveries_are_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.app.services import import_delivery_review

    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])
    # Its delivery's folder was moved away; the folder still lists a folder without a package file.
    mounted = tmp_path / "mounted"
    _package(mounted, "FAM1", package_file=False)
    unmounted = tmp_path / "unmounted"
    unmounted.mkdir()
    counted: list[tuple[Path, set[str]]] = []

    async def no_jobs(_factory: Any) -> int:
        return 0

    async def open_deliveries(_factory: Any, _root: Path) -> int:
        return 1

    async def mark_missing(_factory: Any, root: Path, seen: set[str]) -> int:
        counted.append((root, seen))
        return 0

    monkeypatch.setattr(import_delivery_review, "sync_delivery_jobs", no_jobs)
    monkeypatch.setattr(watcher, "_open_deliveries", open_deliveries)
    monkeypatch.setattr(watcher, "mark_missing", mark_missing)

    done = await watcher.run_watch_round(None, [mounted, unmounted])  # type: ignore[arg-type]

    # The mounted folder is watched, so its missing delivery counts a round; the empty one is not.
    assert (done.roots_watched, done.roots_skipped) == (1, 1)
    assert counted == [(mounted, set())]


@pytest.mark.asyncio
async def test_with_nothing_watched_the_watcher_returns_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "family_import_watch_roots", [])

    await asyncio.wait_for(watcher.import_watcher(asyncio.Event()), timeout=1)


class _Leadership:
    def __init__(self, holds: list[bool]) -> None:
        self.holds = holds
        self.released = 0

    async def hold(self) -> bool:
        return self.holds.pop(0) if self.holds else False

    async def release(self) -> None:
        self.released += 1


@pytest.mark.asyncio
async def test_the_watcher_looks_only_while_it_leads_and_logs_a_failed_round_without_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [])
    monkeypatch.setattr(settings, "family_import_watch_roots", [str(tmp_path)])
    monkeypatch.setattr(settings, "family_import_watch_interval_seconds", 0)
    leadership = _Leadership([True, False, True, True])
    monkeypatch.setattr(watcher, "WatcherLeadership", lambda: leadership)
    monkeypatch.setattr(watcher, "get_postgres_sessionmaker", lambda: None)
    stop = asyncio.Event()
    rounds: list[str] = []

    async def round_(_factory: Any, roots: list[Path]) -> watcher.WatchRound:
        rounds.append("round")
        if len(rounds) == 1:
            raise RuntimeError(f"a value of {roots[0]}")
        if len(rounds) == 3:
            stop.set()
        return watcher.WatchRound(roots_watched=1, deliveries={"ready": 1})

    monkeypatch.setattr(watcher, "run_watch_round", round_)
    caplog.set_level(logging.INFO, logger=watcher.logger.name)

    await asyncio.wait_for(watcher.import_watcher(stop), timeout=5)

    assert rounds == ["round", "round", "round"]
    assert leadership.released == 1
    assert "Import watch round failed: RuntimeError" in caplog.text
    assert str(tmp_path) not in caplog.text
    assert "new deliveries {'ready': 1}" in caplog.text


@pytest.mark.asyncio
async def test_stopping_the_watcher_ends_its_task() -> None:
    task = asyncio.create_task(asyncio.sleep(60))
    stop = asyncio.Event()

    await watcher.stop_import_watcher(task, stop)

    assert stop.is_set() and task.cancelled()


def test_the_proposal_keeps_no_warning_validation_checks_again(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # A warning validation gives at every check of the delivery (a NIPT file's column, a linked
    # folder) is not kept from Discover: kept, it stayed after a correction that resolved it.
    from backend.app.schemas import FamilyImportValidationIssue, FamilyPackageManifestBuildOut

    def issue(code: str) -> FamilyImportValidationIssue:
        return FamilyImportValidationIssue(code=code, message=f"{code} message")

    draft = FamilyPackageManifestBuildOut(
        valid=True,
        family_id="FAM1",
        manifest_yaml="schema_version: 1\nfamily_id: FAM1\n",
        warnings=[issue(code) for code in ("nipt_vcf_column_named_otherwise", "package_folder_link", "dataset_not_detected", "nipt_pair_detected")],
    )
    bundle = SimpleNamespace(ped=None, manifest=None)
    monkeypatch.setattr(watcher, "discover_family_package_manifest", lambda _request: draft)
    monkeypatch.setattr(watcher, "load_validated_family_package", lambda *_a, **_k: (SimpleNamespace(errors=[]), bundle))
    monkeypatch.setattr(watcher, "_ped_members_for_import", lambda *_a, **_k: [])
    monkeypatch.setattr(watcher, "_manifest_carrier_types", lambda _manifest: {})
    monkeypatch.setattr(watcher, "_manifest_member_overrides", lambda _manifest: {})
    monkeypatch.setattr(watcher, "proposed_package", lambda package, _draft, _members: package)
    package = CogaPackage.model_validate(
        {
            "schema": "coga-package/1",
            "status": "complete",
            "written_at": "2026-11-10T12:00:00Z",
            "pipeline": {"name": "p", "version": "1", "run_id": "r"},
            "family": {"family_id": "FAM1"},
        }
    )

    _completed, warnings, issues = asyncio.run(watcher._proposal(tmp_path, package))

    assert issues == []
    assert [warning.code for warning in warnings] == [
        "nipt_pair_detected",
        "package_members_proposed",
        "package_datasets_proposed",
    ]
