"""The import watcher's parts that need no database: what it watches, what it reads, its loop.

The watcher looks into the folders of FAMILY_IMPORT_WATCH_ROOTS that lie under FAMILY_IMPORT_ROOTS
(never a bucket), at each package folder there that holds a ready marker. It compares the
marker's samples with the package's members and the files the import reads with the ones the
marker lists; a check that fails is recorded with a fixed sentence, never the error; a folder that
changed while it was checked is left to the next round. A round skips a watched folder only when it
cannot be read, or lists nothing while it has open deliveries (a share not mounted); one that lists
folders without a marker is mounted, and its missing deliveries count. An open delivery seen again is checked
again only when what CoGA holds has changed in what its check read (the project its marker names,
the family and samples of its package), never after a check that could not read them. Its loop
runs only while it holds the
leadership, logs a failed round without values, and returns at once when nothing is watched. The
rounds against a real database are in e2e/test_e2e_import_watcher.py.

All samples and values here are synthetic.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.core.config import settings
from backend.app.schemas import FamilyImportDatasetSummary, FamilyPackageValidationOut
from backend.app.services import import_watcher as watcher
from backend.app.services.import_ready_marker import MARKER_FILE_NAME, ReadyMarker


def _package(root: Path, name: str, *, marker: bool = True) -> Path:
    folder = root / name
    (folder / "snv").mkdir(parents=True)
    (folder / "family.ped").write_text(f"{name}\tPROBAND\t0\t0\t1\t2\n", encoding="utf-8")
    (folder / "snv" / "family.vcf").write_text("##fileformat=VCFv4.2\n", encoding="utf-8")
    if marker:
        (folder / MARKER_FILE_NAME).write_text("{}", encoding="utf-8")
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
    _package(tmp_path, "FAM1", marker=False)
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
    _package(unmarked, "FAM1", marker=False)

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


def test_the_markers_samples_must_be_members_of_the_sex_the_ped_gives() -> None:
    marker = ReadyMarker.model_validate(
        {
            "schema": "coga-ready/1",
            "samples": [
                {"sample_id": "PROBAND", "sex": "male"},
                {"sample_id": "MOTHER", "sex": "female"},
                {"sample_id": "FATHER1", "sex": "unknown"},
                {"sample_id": "EMB1"},
            ],
        }
    )

    issues, warnings = watcher._sample_issues(
        marker, {"PROBAND": "female", "MOTHER": "female", "FATHER1": "male", "FETUS": "unknown"}
    )

    assert [(issue.code, issue.message) for issue in issues] == [
        ("marker_sample_sex_differs", "coga-ready.json gives sample 'PROBAND' another sex than the PED."),
        ("marker_sample_not_in_package", "coga-ready.json names sample 'EMB1', which the package does not hold."),
    ]
    assert [issue.message for issue in warnings] == ["coga-ready.json does not name the package's member(s) FETUS."]


def test_a_marker_without_samples_is_not_compared() -> None:
    marker = ReadyMarker.model_validate({"schema": "coga-ready/1"})

    assert watcher._sample_issues(marker, {"PROBAND": "male"}) == ([], [])


def test_the_files_the_import_reads_are_the_ped_and_each_enabled_datasets(tmp_path: Path) -> None:
    folder = _package(tmp_path, "FAM1")
    (folder / "coverage").mkdir()
    (folder / "coverage" / "S1.bed").write_text("1\t0\t10\t3\n", encoding="utf-8")
    validation = FamilyPackageValidationOut(
        valid=True,
        ped_path=str(folder / "family.ped"),
        datasets=[
            FamilyImportDatasetSummary(dataset_type="snv", files=["snv/family.vcf"]),
            FamilyImportDatasetSummary(dataset_type="coverage", files=["coverage"]),
            FamilyImportDatasetSummary(dataset_type="sv", enabled=False, files=["sv/calls.vcf"]),
        ],
    )

    assert watcher._files_the_import_reads(folder, validation) == ["coverage/S1.bed", "family.ped", "snv/family.vcf"]
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
async def test_a_marker_that_cannot_be_read_needs_attention(tmp_path: Path) -> None:
    folder = _package(tmp_path, "FAM1")
    (folder / MARKER_FILE_NAME).write_text("{not json", encoding="utf-8")

    assessment = await watcher.assess_delivery(_factory([]), folder)

    assert (assessment.state, [issue.code for issue in assessment.issues]) == ("needs_attention", ["marker_not_json"])


@pytest.mark.asyncio
async def test_a_folder_discover_cannot_draft_for_needs_attention(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [])
    folder = _package(tmp_path, "FAM1")
    (folder / MARKER_FILE_NAME).write_text(
        '{"schema": "coga-ready/1", "written_at": "x", "family_id": "FAM1", "analysis_type": "wgs", "assembly": "GRCh38",'
        ' "pipeline": {"name": "p", "version": "1"}, "run": {"id": "r"}, "files": []}',
        encoding="utf-8",
    )

    def no_draft(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(errors=[SimpleNamespace(code="ped_missing", message="No PED was found.")], manifest_yaml="", warnings=[])

    monkeypatch.setattr(watcher, "discover_family_package_manifest", no_draft)

    assessment = await watcher.assess_delivery(_factory([]), folder)

    assert assessment.state == "needs_attention"
    # The marker gives no project and no files; Discover found no PED.
    assert [issue.code for issue in assessment.issues] == [
        "marker_field_missing",
        "marker_field_missing",
        "discover_failed",
        "ped_missing",
    ]


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



# --- checking an open delivery again ----------------------------------------------------------


def _seen(**fields: Any) -> dict[str, Any]:
    """An open delivery's row as the watcher sees it again: one of a project CoGA lacked."""
    row: dict[str, Any] = {
        "id": "d" * 8,
        "state": "needs_attention",
        "issues": [{"code": "marker_project_unknown", "message": "", "path": None}],
        "project_id": None,
        "marker": {"project": "LATER", "assembly": "GRCh38"},
        "validation": {"family_id": "FAM1", "sample_ids": ["PROBAND"]},
    }
    row.update(fields)
    return row


def _coga(monkeypatch: pytest.MonkeyPatch, *, project: tuple[str, str | None] | None, existing: tuple[str, ...] = ()) -> None:
    """What CoGA holds now: the project the marker names, and the codes of a family or samples
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
    # A marker that could not be read: the check read neither the project nor the family.
    unread = _seen(issues=[{"code": "marker_invalid"}], validation=None)
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

    async def assess(_factory: Any, checked: Path) -> Any:
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
    # Its delivery's folder was moved away; the folder still lists a folder without a marker.
    mounted = tmp_path / "mounted"
    _package(mounted, "FAM1", marker=False)
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
