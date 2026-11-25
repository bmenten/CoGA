"""The import watcher's parts that need no database: what it watches, what it reads, its loop.

The watcher looks into the folders of FAMILY_IMPORT_WATCH_ROOTS that lie under FAMILY_IMPORT_ROOTS
at each package folder there that holds a package file, coga-package.json. A
file that says its run is under way, or failed, is no delivery, and supersedes the open ones of its
folder. The watcher compares the files the import reads with the ones the package file lists, and
warns when the folder's PED says otherwise than the file's members; a check that fails is recorded
with a fixed sentence, never the error; a folder that changed while it was checked is left to the
next round. A round skips a watched folder only when it cannot be read, or lists nothing while it
has open deliveries (a share not mounted); one that lists folders without a package file is
mounted, and its missing deliveries count. An open delivery seen again is checked again only when
what CoGA holds has changed in what its check read (the project its package file names, the
family and samples of its package), never after a check that could not read them. A file the
import reads that the package file does not list is listed by CoGA itself, with its size and
SHA-256, and warned of. Its loop runs only while it holds the leadership, logs a failed round
without values, and runs its rounds also when nothing is watched (a folder added by hand is a
delivery too). The rounds against a real database are in e2e/test_e2e_import_watcher.py.

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
from backend.app.services import package_changed_after_import
from backend.app.services.family_package_fingerprint import PackageFingerprint, PackageListing
from backend.app.services.family_package_registration import FamilySamplesHeld
from backend.app.services.coga_package import PACKAGE_FILE_NAME, CogaPackage, package_document


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
        [str(imports / "share"), str(imports / "share"), str(outside)],
    )
    caplog.set_level(logging.WARNING, logger=watcher.logger.name)

    assert watcher.watch_roots() == [(imports / "share").resolve()]
    assert "1 of FAMILY_IMPORT_WATCH_ROOTS is not watched" in caplog.text
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


def test_the_package_folders_in_the_project_folders_of_a_watched_folder_are_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])
    in_wgs = _package(tmp_path / "WGS", "FAM1")
    in_pgt = _package(tmp_path / "PGT", "FAM2")
    _package(tmp_path / "PGT", "FAM3", package_file=False)
    loose = _package(tmp_path, "FAM4")

    listing = watcher.marked_folders(tmp_path)

    assert listing is not None
    assert sorted(listing.folders) == sorted([in_wgs.resolve(), in_pgt.resolve(), loose.resolve()])
    assert [watcher.project_folder_of(tmp_path, folder) for folder in (in_wgs, in_pgt, loose)] == ["WGS", "PGT", None]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("project_folder", "issue"),
    [("WGS", None), ("PGT", "package_project_folder_differs"), (None, "package_not_in_project_folder")],
)
async def test_a_delivery_lies_in_the_folder_of_its_project(
    tmp_path: Path, project_folder: str | None, issue: str | None
) -> None:
    folder = _package(tmp_path / project_folder if project_folder else tmp_path, "FAM1")
    _complete_file(folder)

    assessment = await watcher.assess_delivery(_factory([]), folder, watched_in=tmp_path)

    placed = [
        found.code
        for found in assessment.issues
        if found.code in {"package_not_in_project_folder", "package_project_folder_differs"}
    ]
    assert placed == ([issue] if issue else [])
    assert assessment.package_check["project_folder"] == {"name": project_folder}


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


def _patch_fingerprints(monkeypatch: pytest.MonkeyPatch, fingerprint: Any) -> None:
    """Take the folder's fingerprints from ``fingerprint``, an async function of the folder: the
    first one, which ``look_at_folder`` takes with the folder's listing (``_listing``), and each
    one after it (``_fingerprint``)."""

    async def listing(folder: Path) -> Any:
        sha256 = await fingerprint(folder)
        return None if sha256 is None else PackageListing(PackageFingerprint(sha256=sha256, files=0), {})

    monkeypatch.setattr(watcher, "_fingerprint", fingerprint)
    monkeypatch.setattr(watcher, "_listing", listing)


async def _follow_nothing(*_args: Any, **_kwargs: Any) -> Any:
    """A round's following of imported deliveries, with no family imported from a delivery."""
    return package_changed_after_import.FollowRound()


async def _nothing_failed(_factory: Any) -> Any:
    """A round's offer of the deliveries whose import failed, with none failed."""
    from backend.app.services.import_delivery_review import OfferedAgain

    return OfferedAgain()


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
        "analysis_type": "long_read_wgs",
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
async def test_files_listed_without_a_checksum_are_warned_of_and_their_sha256_kept(tmp_path: Path) -> None:
    # The owner's decision of 2026-11-11: a checksum per file is optional. The delivery warns that
    # the copy is unchecked, and keeps the SHA-256 CoGA computed, which the import records.
    folder = _package(tmp_path, "FAM1")
    _complete_file(folder)
    body = json.loads((folder / PACKAGE_FILE_NAME).read_text())
    for entry in body["files"]:
        entry.pop("sha256")
    (folder / PACKAGE_FILE_NAME).write_text(json.dumps(body), encoding="utf-8")

    assessment = await watcher.assess_delivery(_factory([]), folder)

    assert "package_files_without_checksum" in [item.code for item in assessment.warnings]
    warned = next(item for item in assessment.warnings if item.code == "package_files_without_checksum")
    assert warned.message == (
        "coga-package.json gives no checksum for 2 file(s): family.ped, snv/family.vcf. CoGA computed "
        "their SHA-256, but cannot check that the copy is what the pipeline made."
    )
    assert assessment.package_check is not None
    assert assessment.package_check["without_checksum"] == ["family.ped", "snv/family.vcf"]
    assert assessment.package_check["sha256"] == {
        "family.ped": hashlib.sha256((folder / "family.ped").read_bytes()).hexdigest(),
        "snv/family.vcf": hashlib.sha256((folder / "snv" / "family.vcf").read_bytes()).hexdigest(),
    }
    assert "file_checksum_differs" not in [item.code for item in assessment.issues]


@pytest.mark.asyncio
async def test_a_file_the_import_reads_that_the_package_file_does_not_list_is_listed_by_coga(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A CRAM put into the folder after the pipeline wrote its package file, which a dataset Discover
    # proposed reads: CoGA lists it with its size and SHA-256 and warns (the owner's decision of
    # 2026-11-23), instead of a delivery no correction can unblock. One it cannot read stays an issue.
    folder = _package(tmp_path, "FAM1")
    _complete_file(folder)
    (folder / "alignments").mkdir()
    (folder / "alignments" / "PROBAND.cram").write_bytes(b"a CRAM put into the folder later")
    (folder / "notes.txt").write_text("not read by the import", encoding="utf-8")
    read_by_the_import = ["alignments/PROBAND.cram", "alignments/PROBAND.cram.crai"]
    checks: list[dict[str, Any]] = []

    async def no_proposal(_folder: Path, package: CogaPackage) -> Any:
        return package, [], []

    async def content(_factory: Any, _folder: Path, assessment: Any, *, base: Any, effective: Any) -> Any:
        # The check of the content, as far as this needs it: a file the import reads that the
        # package lists is one whose SHA-256 reading the folder found.
        check = assessment.package_check
        checks.append({"check": json.loads(json.dumps(check)), "files": [f.path for f in effective.files], "same": base is effective})
        issues = [watcher.DeliveryIssue.model_validate(item) for item in check["issues"]]
        issues.extend(
            watcher._issue(watcher.FILE_NOT_LISTED, "not listed", path)
            for path in read_by_the_import
            if path not in check["sha256"] and not any(issue.path == path for issue in issues)
        )
        assessment.issues = issues
        assessment.warnings = [watcher.DeliveryIssue.model_validate(item) for item in check["warnings"]]
        assessment.state = "needs_attention" if issues else "ready"
        return assessment

    monkeypatch.setattr(watcher, "_proposal", no_proposal)
    monkeypatch.setattr(watcher, "assess_content", content)

    assessment = await watcher.assess_delivery(_factory([]), folder)

    # Checked twice: as delivered, then with the package that lists what CoGA could read.
    assert len(checks) == 2 and checks[1]["same"]
    cram = (folder / "alignments" / "PROBAND.cram").read_bytes()
    assert assessment.package is not None
    assert assessment.package["files"][-1] == {"path": "alignments/PROBAND.cram", "size": len(cram)}
    assert "alignments/PROBAND.cram" in checks[1]["files"]
    assert assessment.package_check is not None
    assert assessment.package_check["proposed_files"] == ["alignments/PROBAND.cram"]
    assert assessment.package_check["sha256"]["alignments/PROBAND.cram"] == hashlib.sha256(cram).hexdigest()
    # The index that is not there is no file CoGA can list: an issue that says so, once.
    assert [(issue.code, issue.path) for issue in assessment.issues] == [
        (watcher.FILE_NOT_LISTED, "alignments/PROBAND.cram.crai")
    ]
    assert "could not read it to list it" in assessment.issues[0].message
    warnings = {warning.code: warning.message for warning in assessment.warnings}
    # The folder's other unlisted file is still told, the listed CRAM no longer among them.
    assert warnings["file_not_in_package"] == "The folder holds 1 file(s) coga-package.json does not list: notes.txt."
    assert warnings["package_files_proposed"].startswith(
        "coga-package.json does not list 1 file(s) the import reads: alignments/PROBAND.cram. CoGA added them"
    )


@pytest.mark.asyncio
async def test_a_delivery_whose_import_reads_only_listed_files_is_checked_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _package(tmp_path, "FAM1")
    _complete_file(folder)
    checks: list[Any] = []

    async def no_proposal(_folder: Path, package: CogaPackage) -> Any:
        return package, [], []

    async def content(_factory: Any, _folder: Path, assessment: Any, **_kwargs: Any) -> Any:
        checks.append(assessment.package_check["proposed_files"])
        assessment.state = "ready"
        return assessment

    monkeypatch.setattr(watcher, "_proposal", no_proposal)
    monkeypatch.setattr(watcher, "assess_content", content)

    assessment = await watcher.assess_delivery(_factory([]), folder)

    assert (assessment.state, checks) == ("ready", [[]])
    assert "package_files_proposed" not in [warning.code for warning in assessment.warnings]


@pytest.mark.asyncio
async def test_a_folder_discover_cannot_draft_for_needs_attention(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [])
    folder = _package(tmp_path, "FAM1")
    (folder / PACKAGE_FILE_NAME).write_text(
        '{"schema": "coga-package/1", "status": "complete", "written_at": "2026-11-10T09:00:00Z",'
        ' "family": {"family_id": "FAM1"}, "analysis_type": "long_read_wgs", "assembly": "GRCh38",'
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

    async def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("a value of FAM1")

    _patch_fingerprints(monkeypatch, fingerprint)
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

    _patch_fingerprints(monkeypatch, fingerprint)
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

    _patch_fingerprints(monkeypatch, fingerprint)
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

    _patch_fingerprints(monkeypatch, fingerprint)
    monkeypatch.setattr(watcher, "assess_delivery", assess)
    monkeypatch.setattr(watcher, "_store_delivery", store)

    assert await watcher.look_at_folder(_factory([]), tmp_path, folder) is None


@pytest.mark.asyncio
async def test_a_folder_without_a_fingerprint_is_left_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store: list[str] = []

    async def none(_folder: Path) -> None:
        return None

    _patch_fingerprints(monkeypatch, none)

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
    _patch_fingerprints(monkeypatch, no_fingerprint)
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

    async def look(*_args: Any, **_kwargs: Any) -> tuple[str, str]:
        return next(looks)

    async def mark_missing(*_args: Any, **_kwargs: Any) -> int:
        return 0

    monkeypatch.setattr(import_delivery_review, "sync_delivery_jobs", no_jobs)
    monkeypatch.setattr(import_delivery_review, "offer_failed_deliveries_again", _nothing_failed)
    monkeypatch.setattr(watcher, "look_at_folder", look)
    monkeypatch.setattr(watcher, "mark_missing", mark_missing)
    monkeypatch.setattr(package_changed_after_import, "follow_imported_deliveries", _follow_nothing)

    done = await watcher.run_watch_round(None, [tmp_path])  # type: ignore[arg-type]

    assert (done.superseded_for_run, done.deliveries, done.checked_again) == (2, {"ready": 1}, {})



# --- the checks of new deliveries, beside the rounds -----------------------------------------


def _new(root: Path, name: str, size: int, fingerprint: str | None = None) -> watcher.NewDelivery:
    """A new delivery as a round found it: a folder whose files hold ``size`` bytes."""
    sha256 = fingerprint or hashlib.sha256(name.encode()).hexdigest()
    entries: dict[str, list[object]] = {"reads.cram": ["file", size, 0], "linked": ["link", "elsewhere"]}
    listing = PackageListing(PackageFingerprint(sha256=sha256, files=1), entries)
    return watcher.NewDelivery(root=root, folder=root / name, read=SimpleNamespace(), listing=listing)  # type: ignore[arg-type]


async def _settle() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


def test_a_new_delivery_is_as_large_as_the_files_its_listing_names() -> None:
    new = _new(Path("/x"), "FAM1", 1234)
    assert new.size == 1234
    new.listing = PackageListing(new.listing.fingerprint, None)
    assert new.size == float("inf")


@pytest.mark.asyncio
async def test_new_deliveries_are_checked_the_smallest_first_a_few_at_a_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The VM case: a genome's package, whose VCF a check reads whole for most of an hour, holds
    # neither the small packages behind it nor the round.
    news = {name: _new(tmp_path, name, size) for name, size in (("BIG", 300), ("MID", 200), ("SMALL", 100), ("TINY", 10))}
    release = {name: asyncio.Event() for name in news}
    started: list[str] = []

    async def fingerprint(folder: Path) -> str:
        return news[folder.name].fingerprint

    async def check(_factory: Any, new: watcher.NewDelivery) -> str:
        started.append(new.folder.name)
        await release[new.folder.name].wait()
        return "ready"

    monkeypatch.setattr(watcher, "_fingerprint", fingerprint)
    monkeypatch.setattr(watcher, "check_new_delivery", check)
    checks = watcher.DeliveryChecks(None, limit=2)  # type: ignore[arg-type]
    for name in news:
        checks.submit(news[name])
    await _settle()
    # The first two start at once, while checks are free; the others wait.
    assert started == ["BIG", "MID"]
    assert (checks.running, checks.waiting) == (2, 2)

    release["MID"].set()
    await _settle()
    assert started == ["BIG", "MID", "TINY"]
    release["TINY"].set()
    await _settle()
    assert started == ["BIG", "MID", "TINY", "SMALL"]

    release["BIG"].set()
    release["SMALL"].set()
    await checks.drain()
    assert checks.taken() == {"ready": 4}
    assert checks.taken() == {}
    assert (checks.running, checks.waiting) == (0, 0)


@pytest.mark.asyncio
async def test_a_folder_is_checked_once_at_a_time_and_as_it_is_now(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = {"fingerprint": "a" * 64}
    release = asyncio.Event()
    checked: list[str] = []

    async def fingerprint(_folder: Path) -> str:
        return now["fingerprint"]

    async def check(_factory: Any, new: watcher.NewDelivery) -> str | None:
        checked.append(new.fingerprint)
        await release.wait()
        # A folder that changed while it was checked is not stored (check_new_delivery).
        return "ready" if new.fingerprint == now["fingerprint"] else None

    monkeypatch.setattr(watcher, "_fingerprint", fingerprint)
    monkeypatch.setattr(watcher, "check_new_delivery", check)
    checks = watcher.DeliveryChecks(None, limit=2)  # type: ignore[arg-type]
    checks.submit(_new(tmp_path, "FAM1", 10, "a" * 64))
    await _settle()
    # The next round finds it again, as it is being checked: it is not checked twice.
    checks.submit(_new(tmp_path, "FAM1", 10, "a" * 64))
    await _settle()
    assert checked == ["a" * 64]
    assert (checks.running, checks.waiting) == (1, 0)

    # Changed since: it waits for the check of the folder as it was, though a check is free.
    now["fingerprint"] = "b" * 64
    checks.submit(_new(tmp_path, "FAM1", 10, "b" * 64))
    await _settle()
    assert checked == ["a" * 64]
    assert (checks.running, checks.waiting) == (1, 1)

    release.set()
    await checks.drain()
    assert checked == ["a" * 64, "b" * 64]
    assert checks.taken() == {"ready": 1}


@pytest.mark.asyncio
async def test_a_check_that_waited_leaves_a_folder_changed_or_gone_since_to_the_next_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now: dict[str, str | None] = {"CHANGED": "c" * 64, "GONE": None}
    checked: list[str] = []

    async def fingerprint(folder: Path) -> str | None:
        return now[folder.name]

    async def check(_factory: Any, new: watcher.NewDelivery) -> str:
        checked.append(new.folder.name)
        return "ready"

    monkeypatch.setattr(watcher, "_fingerprint", fingerprint)
    monkeypatch.setattr(watcher, "check_new_delivery", check)
    checks = watcher.DeliveryChecks(None)  # type: ignore[arg-type]
    checks.submit(_new(tmp_path, "CHANGED", 10, "a" * 64))
    checks.submit(_new(tmp_path, "GONE", 10, "a" * 64))
    await checks.drain()

    assert checked == []
    assert checks.taken() == {}


@pytest.mark.asyncio
async def test_a_check_that_fails_stops_no_other_and_is_logged_without_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    news = [_new(tmp_path, "BAD", 10), _new(tmp_path, "FAM1", 20)]

    async def fingerprint(folder: Path) -> str:
        return next(new.fingerprint for new in news if new.folder == folder)

    async def check(_factory: Any, new: watcher.NewDelivery) -> str:
        if new.folder.name == "BAD":
            raise RuntimeError(f"a value of {new.folder}")
        return "ready"

    monkeypatch.setattr(watcher, "_fingerprint", fingerprint)
    monkeypatch.setattr(watcher, "check_new_delivery", check)
    caplog.set_level(logging.WARNING, logger=watcher.logger.name)
    checks = watcher.DeliveryChecks(None, limit=1)  # type: ignore[arg-type]
    for new in news:
        checks.submit(new)
    await checks.drain()

    assert checks.taken() == {"ready": 1}
    assert "Import watcher could not check a delivery: RuntimeError" in caplog.text
    assert str(tmp_path) not in caplog.text


@pytest.mark.asyncio
async def test_stopping_the_checks_drops_the_waiting_and_cancels_the_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    news = [_new(tmp_path, "FAM1", 10), _new(tmp_path, "FAM2", 20)]
    started: list[str] = []
    cancelled: list[str] = []

    async def fingerprint(folder: Path) -> str:
        return next(new.fingerprint for new in news if new.folder == folder)

    async def check(_factory: Any, new: watcher.NewDelivery) -> str:
        started.append(new.folder.name)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(new.folder.name)
            raise
        return "ready"

    monkeypatch.setattr(watcher, "_fingerprint", fingerprint)
    monkeypatch.setattr(watcher, "check_new_delivery", check)
    checks = watcher.DeliveryChecks(None, limit=1)  # type: ignore[arg-type]
    for new in news:
        checks.submit(new)
    await _settle()
    await checks.stop()
    await _settle()

    assert started == cancelled == ["FAM1"]
    assert (checks.running, checks.waiting) == (0, 0)
    assert checks.taken() == {}


@pytest.mark.asyncio
async def test_a_round_hands_its_new_deliveries_to_the_watchers_checks_and_does_not_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.app.services import import_delivery_review

    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])
    _package(tmp_path, "FAM1")
    release = asyncio.Event()

    async def no_jobs(_factory: Any) -> int:
        return 0

    async def mark_missing(*_args: Any, **_kwargs: Any) -> int:
        return 0

    stored: list[str] = []

    async def look(_factory: Any, root: Path, folder: Path, _seen: Any = None, *, checks: Any = None) -> None:
        # A delivery stored is seen again, not new (look_at_folder).
        if folder.name not in stored:
            checks.submit(_new(root, folder.name, 10))

    async def fingerprint(folder: Path) -> str:
        return _new(tmp_path, folder.name, 10).fingerprint

    async def check(_factory: Any, new: watcher.NewDelivery) -> str:
        await release.wait()
        stored.append(new.folder.name)
        return "ready"

    monkeypatch.setattr(import_delivery_review, "sync_delivery_jobs", no_jobs)
    monkeypatch.setattr(import_delivery_review, "offer_failed_deliveries_again", _nothing_failed)
    monkeypatch.setattr(watcher, "look_at_folder", look)
    monkeypatch.setattr(watcher, "mark_missing", mark_missing)
    monkeypatch.setattr(watcher, "_fingerprint", fingerprint)
    monkeypatch.setattr(watcher, "check_new_delivery", check)
    monkeypatch.setattr(package_changed_after_import, "follow_imported_deliveries", _follow_nothing)
    checks = watcher.DeliveryChecks(None)  # type: ignore[arg-type]

    done = await watcher.run_watch_round(None, [tmp_path], checks=checks)  # type: ignore[arg-type]
    await _settle()
    assert (done.deliveries, done.checks_waiting, done.checks_running) == ({}, 0, 1)
    assert checks.running == 1

    # The next round counts it once its check is done.
    release.set()
    await _settle()
    done = await watcher.run_watch_round(None, [tmp_path], checks=checks)  # type: ignore[arg-type]
    await checks.drain()
    assert done.deliveries == {"ready": 1}

    # Without the watcher's checks, a round checks its new deliveries itself and waits for them.
    stored.clear()
    done = await watcher.run_watch_round(None, [tmp_path])  # type: ignore[arg-type]
    assert (done.deliveries, done.checks_waiting, done.checks_running) == ({"ready": 1}, 0, 0)


@pytest.mark.asyncio
async def test_a_round_hands_over_every_new_delivery_before_any_check_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The VM's first round: a large package that sorts first by name took a check at once, before
    # the round had listed the small ones behind it. The smallest of all a round finds goes first.
    from backend.app.services import import_delivery_review

    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])
    monkeypatch.setattr(settings, "family_import_watch_checks", 1)
    sizes = {"A_LARGE": 300, "B_MEDIUM": 200, "C_SMALL": 10}
    for name in sizes:
        _package(tmp_path, name)
    started: list[str] = []

    async def no_jobs(_factory: Any) -> int:
        return 0

    async def mark_missing(*_args: Any, **_kwargs: Any) -> int:
        return 0

    async def look(_factory: Any, root: Path, folder: Path, _seen: Any = None, *, checks: Any = None) -> None:
        checks.submit(_new(root, folder.name, sizes[folder.name]))
        # The round awaits between folders: a check could start meanwhile.
        await _settle()

    async def fingerprint(folder: Path) -> str:
        return _new(tmp_path, folder.name, sizes[folder.name]).fingerprint

    async def check(_factory: Any, new: watcher.NewDelivery) -> str:
        started.append(new.folder.name)
        return "ready"

    monkeypatch.setattr(import_delivery_review, "sync_delivery_jobs", no_jobs)
    monkeypatch.setattr(import_delivery_review, "offer_failed_deliveries_again", _nothing_failed)
    monkeypatch.setattr(watcher, "look_at_folder", look)
    monkeypatch.setattr(watcher, "mark_missing", mark_missing)
    monkeypatch.setattr(watcher, "_fingerprint", fingerprint)
    monkeypatch.setattr(watcher, "check_new_delivery", check)
    monkeypatch.setattr(package_changed_after_import, "follow_imported_deliveries", _follow_nothing)

    done = await watcher.run_watch_round(None, [tmp_path])  # type: ignore[arg-type]

    assert started == ["C_SMALL", "B_MEDIUM", "A_LARGE"]
    assert done.deliveries == {"ready": 3}


@pytest.mark.asyncio
async def test_no_check_starts_while_new_deliveries_are_gathered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    news = {name: _new(tmp_path, name, size) for name, size in (("FIRST", 300), ("NEXT", 100), ("LAST", 10))}
    release = asyncio.Event()
    started: list[str] = []

    async def fingerprint(folder: Path) -> str:
        return news[folder.name].fingerprint

    async def check(_factory: Any, new: watcher.NewDelivery) -> str:
        started.append(new.folder.name)
        await release.wait()
        return "ready"

    monkeypatch.setattr(watcher, "_fingerprint", fingerprint)
    monkeypatch.setattr(watcher, "check_new_delivery", check)
    checks = watcher.DeliveryChecks(None, limit=1)  # type: ignore[arg-type]
    checks.submit(news["FIRST"])
    await _settle()
    with checks.gathering():
        checks.submit(news["NEXT"])
        # A check that ends while a round gathers starts no other: the round has not found them all.
        release.set()
        await _settle()
        assert (started, checks.running, checks.waiting) == (["FIRST"], 0, 1)
        checks.submit(news["LAST"])
    await checks.drain()

    assert started == ["FIRST", "LAST", "NEXT"]


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
async def test_a_package_files_assembly_is_checked_against_the_projects_by_its_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _coga(monkeypatch, project=("p1", "GRCh38"))
    for assembly in ("GRCh38", "GRCh38.p14", "hg38"):
        assert await watcher._project_issues(_Session([]), "WGS", assembly) == ("p1", [])

    project_id, issues = await watcher._project_issues(_Session([]), "WGS", "GRCh37")
    assert project_id == "p1"
    assert [(issue.code, issue.message) for issue in issues] == [
        ("package_assembly_differs", "coga-package.json gives the assembly GRCh37; the project's is GRCh38.")
    ]


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
async def test_a_sample_a_same_individual_link_names_added_to_the_family_since_the_check_is_a_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The conventional NIPT's delivery, checked before the monogenic NIPT's package added PLASMA1.
    _coga(monkeypatch, project=("p1", "GRCh38"), existing=("existing_family",))
    package = {
        "project": "NIPT",
        "assembly": "GRCh38",
        "members": [{"sample_id": "PLASMA2", "same_individual_as": ["PLASMA1"]}],
    }
    waiting = _seen(
        project_id="p1",
        issues=[{"code": "existing_family"}, {"code": "package_same_individual_outside"}],
        effective=package,
        validation={"family_id": "FAM1", "sample_ids": ["PLASMA2"]},
    )
    held: dict[str, FamilySamplesHeld] = {"now": FamilySamplesHeld("FAM1", frozenset({"FATHER"}))}

    async def samples_held(_session: Any, *, family_id: str | None) -> FamilySamplesHeld:
        return held["now"]

    monkeypatch.setattr(watcher, "family_samples_held", samples_held)
    assert await watcher._coga_side_changed(_Session([]), waiting) is False

    # The monogenic NIPT's package added PLASMA1: the issue no longer holds.
    held["now"] = FamilySamplesHeld("FAM1", frozenset({"FATHER", "PLASMA1", "PLASMA2"}), (("PLASMA1", "PLASMA2"),))
    assert await watcher._coga_side_changed(_Session([]), waiting) is True

    # Linked, and checked so: no change. The link taken out by hand: an update would not make it.
    linked = {**waiting, "issues": [{"code": "existing_family"}]}
    assert await watcher._coga_side_changed(_Session([]), linked) is False
    held["now"] = FamilySamplesHeld("FAM1", frozenset({"FATHER", "PLASMA1", "PLASMA2"}))
    assert await watcher._coga_side_changed(_Session([]), linked) is True
    warned = {**linked, "warnings": [{"code": "package_same_individual_not_made"}]}
    assert await watcher._coga_side_changed(_Session([]), warned) is False


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

    async def assess(_factory: Any, checked: Path, _read: Any = None, **_kwargs: Any) -> Any:
        assessed.append(checked)
        return watcher.DeliveryAssessment(state="ready", project_id="p1")

    async def update(
        _factory: Any, _folder: Path, delivery_id: str, _fingerprint: str, assessment: Any, _why: Any = None, **_kwargs: Any
    ) -> str:
        updated.append((delivery_id, assessment.state))
        return assessment.state

    _patch_fingerprints(monkeypatch, fingerprint)
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
    for state in ("refused", "queued", "imported", "incomplete"):
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

    async def update(_factory: Any, _folder: Path, _id: str, _fingerprint: str, assessment: Any, _why: Any = None, **_kwargs: Any) -> str:
        stored.append(assessment)
        if len(stored) == 1:
            raise RuntimeError("a value of FAM1")
        return assessment.state

    _patch_fingerprints(monkeypatch, fingerprint)
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

    async def update(*args: Any, **_kwargs: Any) -> str:
        updated.append(args)
        return "ready"

    _patch_fingerprints(monkeypatch, fingerprint)
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

    async def mark_missing(_factory: Any, root: Path, seen: set[str], *, unreadable: Any = ()) -> int:
        counted.append((root, seen))
        assert list(unreadable) == []
        return 0

    monkeypatch.setattr(import_delivery_review, "sync_delivery_jobs", no_jobs)
    monkeypatch.setattr(import_delivery_review, "offer_failed_deliveries_again", _nothing_failed)
    monkeypatch.setattr(watcher, "_open_deliveries", open_deliveries)
    monkeypatch.setattr(watcher, "mark_missing", mark_missing)
    followed: list[set[str]] = []

    async def follow(_factory: Any, *, skipped_roots: set[str], seen_fingerprints: dict[str, str]) -> Any:
        followed.append(set(skipped_roots))
        return package_changed_after_import.FollowRound()

    monkeypatch.setattr(package_changed_after_import, "follow_imported_deliveries", follow)

    done = await watcher.run_watch_round(None, [mounted, unmounted])  # type: ignore[arg-type]

    # The mounted folder is watched, so its missing delivery counts a round; the empty one is not.
    assert (done.roots_watched, done.roots_skipped) == (1, 1)
    assert counted == [(mounted, set())]
    # Nothing is decided on an imported delivery of the skipped folder either.
    assert followed == [{str(unmounted)}]


@pytest.mark.asyncio
async def test_with_nothing_watched_the_watcher_still_runs_its_rounds(monkeypatch: pytest.MonkeyPatch) -> None:
    # A folder added by hand is a delivery too: its import job's end, and its folder after the
    # import, are a round's, with no folder watched.
    monkeypatch.setattr(settings, "family_import_watch_roots", [])
    monkeypatch.setattr(settings, "family_import_watch_interval_seconds", 0)
    leadership = _Leadership([True])
    monkeypatch.setattr(watcher, "WatcherLeadership", lambda: leadership)
    monkeypatch.setattr(watcher, "get_postgres_sessionmaker", lambda: None)
    stop = asyncio.Event()
    rounds: list[list[Path]] = []

    async def round_(_factory: Any, roots: list[Path], **_kwargs: Any) -> watcher.WatchRound:
        rounds.append(roots)
        stop.set()
        return watcher.WatchRound(jobs_ended=1, offered_again={"ready": 1})

    monkeypatch.setattr(watcher, "run_watch_round", round_)

    await asyncio.wait_for(watcher.import_watcher(stop), timeout=5)

    assert rounds == [[]]
    assert leadership.released == 1


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
    rounds: list[Any] = []
    made: list[Any] = []

    class _Checks:
        def __init__(self, _factory: Any) -> None:
            self.stopped = 0
            made.append(self)

        async def stop(self) -> None:
            self.stopped += 1

    async def round_(_factory: Any, roots: list[Path], *, checks: Any) -> watcher.WatchRound:
        rounds.append(checks)
        if len(rounds) == 1:
            raise RuntimeError(f"a value of {roots[0]}")
        if len(rounds) == 3:
            stop.set()
        return watcher.WatchRound(roots_watched=1, deliveries={"ready": 1}, checks_waiting=2, checks_running=1)

    monkeypatch.setattr(watcher, "DeliveryChecks", _Checks)
    monkeypatch.setattr(watcher, "run_watch_round", round_)
    caplog.set_level(logging.INFO, logger=watcher.logger.name)

    await asyncio.wait_for(watcher.import_watcher(stop), timeout=5)

    assert len(rounds) == 3
    assert leadership.released == 1
    assert "Import watch round failed: RuntimeError" in caplog.text
    assert str(tmp_path) not in caplog.text
    assert "new deliveries {'ready': 1}" in caplog.text
    assert "2 new delivery(ies) waiting for their check, 1 being checked" in caplog.text
    # The checks are kept from round to round while the watcher leads; when it no longer leads,
    # they are dropped (another watcher finds their folders again), and so when it stops.
    assert rounds[0] is not rounds[1] and rounds[1] is rounds[2]
    assert [checks.stopped for checks in made] == [1, 1]


@pytest.mark.asyncio
async def test_stopping_the_watcher_ends_its_task() -> None:
    task = asyncio.create_task(asyncio.sleep(60))
    stop = asyncio.Event()

    await watcher.stop_import_watcher(task, stop)

    assert stop.is_set() and task.cancelled()


def test_a_draft_whose_phased_vcf_names_a_sample_the_ped_lacks_still_proposes_its_members_and_datasets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The PGT index is in the phased family VCF, not in the PED: the draft does not validate, but
    # the package still gets the PED's members and the datasets, so the person adds the index in
    # Deliveries; the delivery's check reports it.
    from backend.app.core.config import settings
    from backend.tests.e2e._pgt_package import write_pgt_package

    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])
    folder = tmp_path / "FAM1"
    names = write_pgt_package(folder, "FAM1")
    (folder / "manifest.yaml").unlink()
    ped = folder / "family.ped"
    ped.write_text("".join(f"{line}\n" for line in ped.read_text().splitlines() if names["INDEX"] not in line))
    package = CogaPackage.model_validate(
        {
            "schema": "coga-package/1",
            "status": "complete",
            "written_at": "2026-11-10T12:00:00Z",
            "pipeline": {"name": "p", "version": "1", "run_id": "r"},
            "family": {"family_id": "FAM1"},
        }
    )

    completed, warnings, issues = asyncio.run(watcher._proposal(folder, package))

    assert issues == []
    assert sorted(member.sample_id for member in completed.members or []) == sorted(
        names[role] for role in ("FATHER", "MOTHER", "E_RISK", "E_CLEAR")
    )
    assert (completed.datasets or {})["haplotypes"]["family_vcf"] == "haplotypes/family.glimpse2.vcf"
    assert {"package_members_proposed", "package_datasets_proposed"} <= {warning.code for warning in warnings}


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
    async def validated(*_a: Any, **_k: Any) -> Any:
        return SimpleNamespace(errors=[]), bundle

    monkeypatch.setattr(watcher, "validated_package", validated)
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


class _CorrectedMeanwhileSession:
    """A delivery row still open, and corrected to revision 2 since the check again read it."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    async def __aenter__(self) -> "_CorrectedMeanwhileSession":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def execute(self, statement: Any, _params: Any = None) -> Any:
        sql = str(statement)
        self.statements.append(sql)
        before = {"state": "needs_attention", "issues": [], "marker_sha256": None, "run_id": None, "family_id": "FAM1"}
        return SimpleNamespace(
            mappings=lambda: SimpleNamespace(first=lambda: before),
            scalar_one=lambda: 2,
        )

    async def commit(self) -> None:
        return None


@pytest.mark.asyncio
async def test_a_check_again_overtaken_by_a_correction_is_not_stored(tmp_path: Path) -> None:
    # The correction checked the delivery with the package the import will read: a check again of
    # the package before it must not put back that package's manifest and issues.
    session = _CorrectedMeanwhileSession()

    stored = await watcher._update_checked_again(
        lambda: session, tmp_path, "d" * 8, "a" * 64, watcher.DeliveryAssessment(state="ready"), revision=1
    )

    assert stored is None
    assert not any("UPDATE import_deliveries" in sql for sql in session.statements)


def _listed_row(**fields: Any) -> dict[str, Any]:
    """A delivery's row as the list reads it."""
    row: dict[str, Any] = {
        "id": "00000000-0000-4000-8000-000000000001",
        "watch_root": "/data/share",
        "folder_path": "/data/share/NIPT/FAM1",
        "folder_name": "FAM1",
        "state": "needs_attention",
        "package_fingerprint": "a" * 64,
        "marker_sha256": "b" * 64,
        "marker": {
            "project": "NIPT",
            "analysis_type": "monogenic_nipt",
            "pregnancy_id": "ZW1",
            "pipeline": {"name": "p", "version": "1", "run_id": "RUN1"},
        },
        "run_id": "RUN1",
        "family_id": "FAM1",
        "project_id": None,
        "issues": [],
        "warnings": [],
        "manifest_sha256": None,
        "job_id": None,
        "first_seen_at": "2026-11-23T10:00:00Z",
        "last_seen_at": "2026-11-23T10:00:00Z",
        "state_changed_at": "2026-11-23T10:00:00Z",
        "origin": "watched",
        "package": None,
        "source_change": None,
        "job_status": None,
        "job_dry_run": None,
        "revision": 0,
        "overwrite_approvable": False,
        "revised_package": None,
    }
    row.update(fields)
    return row


def test_the_list_says_where_the_package_belongs_as_last_corrected() -> None:
    # The project, the analysis and the pregnancy are the laboratory's to correct: the list, and
    # the approval that names them, say them as the import reads them; the run stays the pipeline's.
    delivered = watcher._delivery_out(_listed_row())
    assert (delivered.project, delivered.analysis_type, delivered.pregnancy_id) == ("NIPT", "monogenic_nipt", "ZW1")

    corrected = watcher._delivery_out(
        _listed_row(
            revision=1,
            revised_package={"project": "NIPT2", "analysis_type": "monogenic_nipt", "pregnancy_id": "ZW2"},
        )
    )
    assert (corrected.project, corrected.pregnancy_id, corrected.pipeline_name) == ("NIPT2", "ZW2", "p")


@pytest.mark.asyncio
async def test_the_list_gives_the_deliveries_that_wait_first() -> None:
    # A slow copy supersedes a delivery each round: the deliveries that wait must not drop off a
    # list of the latest changes behind them.
    store: list[tuple[str, dict[str, Any]]] = []

    class _ListSession:
        async def execute(self, statement: Any, params: Any = None) -> Any:
            store.append((str(statement), dict(params or {})))
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: []))

    assert await watcher.list_import_deliveries(_ListSession(), states=None, limit=500) == []  # type: ignore[arg-type]

    ((sql, params),) = store
    order = sql[sql.index("ORDER BY") :]
    assert order.index("d.state = ANY(CAST(:first AS text[]))") < order.index("d.state_changed_at DESC")
    assert set(params["first"]) == {"incomplete", "ready", "needs_attention", "queued", "changed_after_import", "failed"}
    assert params["limit"] == watcher.DELIVERY_LIST_LIMIT


@pytest.mark.asyncio
async def test_a_proposal_that_failed_stands_until_a_correction_gives_what_it_could_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A folder added by hand whose members Discover could not propose: its package, edited as
    # JSON, gives them; the check then reads the package instead of standing on the old failure.
    folder = _package(tmp_path, "FAM1")
    document = {
        "schema": "coga-package/1",
        "status": "complete",
        "written_at": "2026-11-10T09:00:00Z",
        "pipeline": {"name": "CoGA, added by hand", "version": "1", "run_id": "manual-1"},
        "project": "WGS",
        "assembly": "GRCh38",
        "analysis_type": "long_read_wgs",
        "family": {"family_id": "FAM1"},
        "extra": {"origin": "manual"},
    }
    base = CogaPackage.model_validate(document)
    corrected = CogaPackage.model_validate(
        {
            **document,
            "members": [{"sample_id": "PROBAND", "sex": "male", "role": "proband"}],
            "datasets": {"snv": {"family_vcf": "snv/family.vcf"}},
        }
    )
    failed = [{"code": "discover_failed", "message": "Discover could not propose.", "path": None}]

    def assessment() -> Any:
        return watcher.DeliveryAssessment(
            state="needs_attention",
            package=package_document(base),
            package_check={"origin": "manual", "issues": [], "warnings": [], "sha256": {}, "proposal_issues": failed},
        )

    validated: list[str] = []

    async def validate(_folder: Path, *, manifest_text: str) -> Any:
        validated.append(manifest_text)
        raise RuntimeError("read on")

    monkeypatch.setattr(watcher, "validated_package", validate)

    stands = await watcher.assess_content(_factory([]), folder, assessment(), base=base, effective=base)
    assert (stands.state, [issue.code for issue in stands.issues][-1:], validated) == (
        "needs_attention",
        ["discover_failed"],
        [],
    )
    with pytest.raises(RuntimeError, match="read on"):
        await watcher.assess_content(_factory([]), folder, assessment(), base=base, effective=corrected)
    assert len(validated) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["superseded", "gone"])
async def test_a_delivery_set_aside_whose_folder_is_as_it_was_checked_again_is_reopened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    # A lock file of a document opened on the share made the folder a new delivery, which
    # superseded this one; the file is gone again, so the folder is as this one was checked. Its
    # row is the folder's (one per fingerprint): it is checked again and reopened, never left
    # superseded behind an open delivery no approval can take.
    folder = _package(tmp_path, "FAM1")
    stored: list[tuple[str, Any]] = []

    async def fingerprint(_folder: Path) -> str:
        return "a" * 64

    async def assess(_factory: Any, checked: Path, _read: Any = None, **kwargs: Any) -> Any:
        assert kwargs.get("watched_in") == tmp_path
        return watcher.DeliveryAssessment(state="ready", project_id="p1")

    async def update(
        _factory: Any, _folder: Path, delivery_id: str, _fingerprint: str, assessment: Any, why: Any, **_kwargs: Any
    ) -> str:
        stored.append((delivery_id, why))
        return assessment.state

    _patch_fingerprints(monkeypatch, fingerprint)
    monkeypatch.setattr(watcher, "assess_delivery", assess)
    monkeypatch.setattr(watcher, "_update_checked_again", update)

    assert await watcher.look_at_folder(lambda: _SeenSession([], _seen(state=state)), tmp_path, folder) == (
        "reopened",
        "ready",
    )
    ((delivery_id, why),) = stored
    assert why is watcher.FOLDER_AS_BEFORE
    assert (why.action, why.states, why.supersede_others) == ("import_delivery_reopened", ("superseded", "gone"), True)


def test_a_project_folder_that_cannot_be_listed_is_named_and_its_deliveries_are_not_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])
    readable = _package(tmp_path / "WGS", "FAM1")
    (tmp_path / "PGT").mkdir()
    real_names_in = watcher._names_in

    def names_in(folder: Path) -> list[str] | None:
        return None if folder.name == "PGT" else real_names_in(folder)

    monkeypatch.setattr(watcher, "_names_in", names_in)

    listing = watcher.marked_folders(tmp_path)

    assert listing is not None
    assert (listing.folders, listing.unreadable) == ([readable.resolve()], [(tmp_path / "PGT").resolve()])


@pytest.mark.asyncio
async def test_a_round_does_not_count_a_delivery_in_an_unreadable_project_folder_as_missing(tmp_path: Path) -> None:
    store: list[tuple[str, dict[str, Any]]] = []

    class _MissingSession:
        async def __aenter__(self) -> "_MissingSession":
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def execute(self, statement: Any, params: Any = None) -> Any:
            store.append((str(statement), dict(params or {})))
            return SimpleNamespace(all=lambda: [])

        async def commit(self) -> None:
            return None

    unreadable = tmp_path / "PGT"
    assert await watcher.mark_missing(lambda: _MissingSession(), tmp_path, set(), unreadable=[unreadable]) == 0

    ((sql, params),) = store
    assert "starts_with(folder_path, u.prefix || '/')" in sql
    assert params["unreadable"] == [str(unreadable)]


@pytest.mark.asyncio
async def test_what_a_check_read_of_the_family_changing_is_a_change(monkeypatch: pytest.MonkeyPatch) -> None:
    # A member's status corrected in the members editor, or the region set on the family page, as
    # an issue of the delivery asks: the delivery is checked again, not left needing attention.
    _coga(monkeypatch, project=("p1", "GRCh38"))
    states = iter(["b" * 64, "c" * 64])

    async def family_state(_session: Any, family_id: str | None) -> str | None:
        assert family_id == "FAM1"
        return next(states)

    monkeypatch.setattr(watcher, "family_state", family_state)
    seen = _seen(
        issues=[],
        project_id="p1",
        validation={"family_id": "FAM1", "sample_ids": ["PROBAND"], "family_state": "b" * 64},
    )

    assert await watcher._coga_side_changed(_Session([]), seen) is False
    assert await watcher._coga_side_changed(_Session([]), seen) is True


@pytest.mark.asyncio
async def test_the_family_state_is_a_digest_of_what_coga_holds_and_none_without_a_family() -> None:
    class _StateSession:
        def __init__(self, state: str | None) -> None:
            self.state = state

        async def execute(self, statement: Any, params: Any = None) -> Any:
            row = {"state": self.state} if self.state is not None else None
            return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: row))

    held = '{"family": [], "links": [], "members": [], "samples": []}'
    assert await watcher.family_state(_StateSession(held), "FAM1") == hashlib.sha256(held.encode()).hexdigest()
    assert await watcher.family_state(_StateSession(None), "FAM1") is None
    assert await watcher.family_state(_StateSession(held), None) is None


def test_the_warnings_digest_follows_each_warning_and_their_order() -> None:
    one = {"code": "package_qc_warned", "message": "The pipeline's QC warned.", "path": None}
    two = {"code": "package_files_proposed", "message": "CoGA listed a file.", "path": None}

    assert watcher.warnings_sha256([one, two]) == watcher.warnings_sha256(json.dumps([one, two]))
    assert watcher.warnings_sha256([one, two]) != watcher.warnings_sha256([two, one])
    assert watcher.warnings_sha256([one]) != watcher.warnings_sha256([{**one, "message": "Another."}])
    assert watcher._delivery_out(_listed_row(warnings=[one])).warnings_sha256 == watcher.warnings_sha256([one])

