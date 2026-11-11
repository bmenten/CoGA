"""A package folder that changes, or goes, after its import: the parts that need no database.

The import watcher follows the folder of each delivery a family's record names, by its package
fingerprint and the listing kept with the delivery (the owner's decisions of 2026-11-11): what
changed (the files added, removed, changed in size or time, each list to 50 paths with its count),
what a round does with each delivery (raise a change, keep it, count a round its folder could not
be listed, end it when the folder is as imported again or its state was refused), the guard that
decides nothing while the folder a delivery lies under cannot be read or lists nothing (a share
not mounted), and the refusal of a changed folder's new delivery, which ends the change. The
rounds against a real database, with the family notice and the sign-out warning, are in
e2e/test_e2e_package_changed_after_import.py.

All values here are synthetic.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from backend.app.services import family_package_fingerprint as fingerprints
from backend.app.services import package_changed_after_import as followed
from backend.app.services.package_changed_after_import import FolderNow, decide, listing_difference

_A, _B, _C = "a" * 64, "b" * 64, "c" * 64


def _now(fingerprint: str | None, entries: dict[str, list[object]] | None = None) -> FolderNow:
    return FolderNow(
        listing=fingerprints.PackageListing(fingerprints.PackageFingerprint(sha256=fingerprint, files=0), entries)
        if fingerprint is not None
        else None
    )


# --- the listing and what changed ---------------------------------------------------------------


def test_the_listing_is_taken_over_the_walk_its_fingerprint_hashes(tmp_path: Path) -> None:
    (tmp_path / "snv").mkdir()
    (tmp_path / "snv" / "family.vcf").write_text("##fileformat=VCFv4.2\n", encoding="utf-8")
    (tmp_path / "family.ped").write_text("FAM1\tPROBAND\t0\t0\t1\t2\n", encoding="utf-8")
    (tmp_path / ".DS_Store").write_text("", encoding="utf-8")

    listing = fingerprints.package_listing(tmp_path)

    assert listing.fingerprint == fingerprints.package_fingerprint(tmp_path, kept_manifest_sha256=None)
    assert listing.entries is not None and set(listing.entries) == {"snv/family.vcf", "family.ped"}
    vcf = os.stat(tmp_path / "snv" / "family.vcf")
    assert listing.entries["snv/family.vcf"] == ["file", vcf.st_size, vcf.st_mtime_ns]


def test_a_folder_of_more_entries_than_a_listing_keeps_has_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("a", "b", "c"):
        (tmp_path / name).write_text(name, encoding="utf-8")
    monkeypatch.setattr(fingerprints, "PACKAGE_LISTING_MAX_ENTRIES", 2)

    listing = fingerprints.package_listing(tmp_path)

    assert listing.entries is None
    assert listing.fingerprint == fingerprints.package_fingerprint(tmp_path, kept_manifest_sha256=None)


def test_a_name_that_is_no_utf8_is_kept_with_its_bytes_escaped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A name the file system gives as bytes that are no UTF-8 (which some file systems refuse to
    # create) reads with a lone surrogate, which the database cannot store.
    def records(_folder: str) -> Any:
        yield [os.fsdecode(b"bad\xffname.txt"), "file", 1, 1]

    monkeypatch.setattr(fingerprints, "_records", records)

    listing = fingerprints.package_listing(tmp_path)

    assert listing.entries is not None
    (name,) = listing.entries
    name.encode("utf-8")  # storable: no lone surrogate
    assert name.startswith("bad\\udcff")


def test_what_changed_names_each_path_added_removed_and_changed() -> None:
    before = {"a.vcf": ["file", 10, 1], "b.bam": ["file", 20, 2], "c.txt": ["file", 30, 3]}
    after = {"a.vcf": ["file", 10, 1], "b.bam": ["file", 21, 2], "d.txt": ["file", 5, 4]}

    assert listing_difference(before, after) == {
        "listed": True,
        "added": ["d.txt"],
        "removed": ["c.txt"],
        "changed": ["b.bam"],
        "added_count": 1,
        "removed_count": 1,
        "changed_count": 1,
    }


def test_what_changed_names_fifty_paths_of_each_kind_and_counts_them_all() -> None:
    after = {f"new/{index:03d}.txt": ["file", 1, 1] for index in range(60)}

    difference = listing_difference({}, after)

    assert len(difference["added"]) == followed.NAMED_PATHS == 50
    assert difference["added"][0] == "new/000.txt" and difference["added_count"] == 60


def test_without_a_listing_no_file_is_named() -> None:
    assert listing_difference(None, {"a": ["file", 1, 1]}) == {
        "listed": False,
        "added": [],
        "removed": [],
        "changed": [],
        "added_count": None,
        "removed_count": None,
        "changed_count": None,
    }


# --- what a round does with a followed delivery -------------------------------------------------


def test_a_folder_as_it_was_imported_is_kept() -> None:
    step = decide(state="imported", imported_fingerprint=_A, change=None, missing_rounds=0, now=_now(_A), refused=False)
    assert (step.action, step.missing_rounds) == ("keep", 0)


def test_a_folder_that_changed_raises_its_change_with_what_changed() -> None:
    difference = listing_difference({"a": ["file", 1, 1]}, {"a": ["file", 2, 1]})

    step = decide(
        state="imported",
        imported_fingerprint=_A,
        change=None,
        missing_rounds=0,
        now=_now(_B),
        refused=False,
        difference=difference,
        detected_at="2026-11-11T10:00:00+00:00",
    )

    assert step.action == "raise"
    assert step.change == {
        "kind": "changed",
        "fingerprint": _B,
        **difference,
        "detected_at": "2026-11-11T10:00:00+00:00",
    }


def test_a_change_raised_for_the_folder_as_it_is_is_kept_and_one_changed_again_raised_again() -> None:
    raised = {"kind": "changed", "fingerprint": _B}

    kept = decide(state="changed_after_import", imported_fingerprint=_A, change=raised, missing_rounds=0, now=_now(_B), refused=False)
    again = decide(state="changed_after_import", imported_fingerprint=_A, change=raised, missing_rounds=0, now=_now(_C), refused=False)

    assert kept.action == "keep"
    assert again.action == "raise" and again.change is not None and again.change["fingerprint"] == _C


def test_a_folder_as_it_was_imported_again_ends_its_change_by_itself() -> None:
    step = decide(
        state="changed_after_import",
        imported_fingerprint=_A,
        change={"kind": "changed", "fingerprint": _B},
        missing_rounds=0,
        now=_now(_A),
        refused=False,
    )
    assert (step.action, step.resolution) == ("resolve", "as_imported")


def test_a_folder_state_that_was_refused_is_never_raised_and_ends_a_change_raised() -> None:
    never = decide(state="imported", imported_fingerprint=_A, change=None, missing_rounds=0, now=_now(_B), refused=True)
    ended = decide(
        state="changed_after_import",
        imported_fingerprint=_A,
        change={"kind": "changed", "fingerprint": _B},
        missing_rounds=0,
        now=_now(_B),
        refused=True,
    )

    assert never.action == "keep"
    assert (ended.action, ended.resolution) == ("resolve", "refused")


def test_a_folder_gone_is_raised_only_after_the_rounds_a_share_hiccup_may_take() -> None:
    gone = FolderNow(gone=True)

    first = decide(state="imported", imported_fingerprint=_A, change=None, missing_rounds=0, now=gone, refused=False)
    second = decide(state="imported", imported_fingerprint=_A, change=None, missing_rounds=first.missing_rounds, now=gone, refused=False)
    later = decide(
        state="changed_after_import",
        imported_fingerprint=_A,
        change=second.change,
        missing_rounds=second.missing_rounds,
        now=gone,
        refused=False,
    )

    assert (first.action, first.missing_rounds) == ("count", 1)
    assert second.action == "raise" and second.missing_rounds == followed.GONE_AFTER_ROUNDS
    assert second.change is not None and (second.change["kind"], second.change["fingerprint"]) == ("gone", None)
    assert later.action == "count"


def test_a_changed_folder_that_then_goes_is_raised_as_gone_and_one_back_as_imported_ends() -> None:
    gone = decide(
        state="changed_after_import",
        imported_fingerprint=_A,
        change={"kind": "changed", "fingerprint": _B},
        missing_rounds=followed.GONE_AFTER_ROUNDS - 1,
        now=FolderNow(gone=True),
        refused=False,
    )
    back = decide(
        state="changed_after_import",
        imported_fingerprint=_A,
        change=gone.change,
        missing_rounds=gone.missing_rounds,
        now=_now(_A),
        refused=False,
    )

    assert gone.change is not None and gone.change["kind"] == "gone"
    assert (back.action, back.resolution, back.missing_rounds) == ("resolve", "as_imported", 0)


def test_a_folder_too_large_to_fingerprint_changed_and_is_raised_once() -> None:
    too_large = FolderNow()

    raised = decide(state="imported", imported_fingerprint=_A, change=None, missing_rounds=0, now=too_large, refused=False)
    again = decide(
        state="changed_after_import", imported_fingerprint=_A, change=raised.change, missing_rounds=0, now=too_large, refused=False
    )

    assert raised.change is not None and (raised.change["fingerprint"], raised.change["listed"]) == (None, False)
    assert again.action == "keep"


def test_a_folder_that_cannot_be_listed_or_is_too_large_reads_as_such(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert followed._folder_now(str(tmp_path / "missing")).gone is True
    (tmp_path / "a").write_text("a", encoding="utf-8")
    assert followed._folder_now(str(tmp_path)).fingerprint == fingerprints.package_fingerprint(tmp_path, kept_manifest_sha256=None).sha256

    def too_large(_folder: str) -> Any:
        raise fingerprints.PackageFolderTooLarge(1)

    monkeypatch.setattr(followed, "package_listing", too_large)
    assert followed._folder_now(str(tmp_path)) == FolderNow()


# --- the guard: nothing is decided on a share that is not mounted -------------------------------


class _Rows:
    """A session that answers the followed deliveries' read with ``rows``."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def __aenter__(self) -> _Rows:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    async def execute(self, _statement: Any, _params: Any = None) -> Any:
        rows = self.rows
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))


def _row(root: Path, folder: Path, **fields: Any) -> dict[str, Any]:
    return {
        "id": "00000000-0000-4000-8000-0000000000d1",
        "watch_root": str(root),
        "folder_path": str(folder),
        "folder_name": folder.name,
        "package_fingerprint": _A,
        "state": "imported",
        "source_change": None,
        "missing_rounds": 0,
        "family_id": "FAM1",
        **fields,
    }


@pytest.fixture
def follow_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, FolderNow]]:
    calls: list[tuple[str, FolderNow]] = []

    async def nothing_ended(_factory: Any) -> int:
        return 0

    async def follow_one(_factory: Any, row: dict[str, Any], now: FolderNow) -> Any:
        calls.append((row["folder_path"], now))
        return followed.Step("count", 1)

    monkeypatch.setattr(followed, "end_changes_no_family_names", nothing_ended)
    monkeypatch.setattr(followed, "_follow_one", follow_one)
    return calls


@pytest.mark.asyncio
async def test_nothing_is_decided_on_a_delivery_whose_folder_lies_under_one_that_is_skipped_or_lists_nothing(
    tmp_path: Path, follow_calls: list[tuple[str, FolderNow]]
) -> None:
    skipped, empty, missing, mounted = (tmp_path / name for name in ("skipped", "empty", "missing", "mounted"))
    for root in (skipped, empty, mounted):
        root.mkdir()
    (skipped / "other").mkdir()
    (mounted / "other").mkdir()
    rows = [
        _row(skipped, skipped / "WGS" / "FAM1"),
        _row(empty, empty / "WGS" / "FAM2"),
        _row(missing, missing / "WGS" / "FAM3"),
        _row(mounted, mounted / "WGS" / "FAM4"),
    ]

    done = await followed.follow_imported_deliveries(lambda: _Rows(rows), skipped_roots={str(skipped)})  # type: ignore[arg-type,return-value]

    # Only the delivery under the folder that lists something is followed: its folder is gone.
    assert [(folder, now.gone) for folder, now in follow_calls] == [(str(mounted / "WGS" / "FAM4"), True)]
    assert done.followed == 1


@pytest.mark.asyncio
async def test_a_folder_the_round_saw_as_it_was_imported_is_not_listed_again(
    tmp_path: Path, follow_calls: list[tuple[str, FolderNow]]
) -> None:
    folder = tmp_path / "WGS" / "FAM1"
    folder.mkdir(parents=True)
    rows = [_row(tmp_path, folder), _row(tmp_path, folder, state="changed_after_import", source_change={"kind": "gone"})]

    await followed.follow_imported_deliveries(
        lambda: _Rows(rows), seen_fingerprints={str(folder): _A}  # type: ignore[arg-type,return-value]
    )

    # The changed one is followed (its folder may be as imported again); the imported one is not.
    assert [folder for folder, _now in follow_calls] == [str(folder)]


@pytest.mark.asyncio
async def test_one_folder_that_fails_never_stops_the_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def nothing_ended(_factory: Any) -> int:
        return 0

    async def fails(*_args: Any) -> Any:
        raise RuntimeError("SELECT ... FAM1-secret")

    monkeypatch.setattr(followed, "end_changes_no_family_names", nothing_ended)
    monkeypatch.setattr(followed, "_follow_one", fails)
    rows = [_row(tmp_path, tmp_path / "FAM1"), _row(tmp_path, tmp_path / "FAM2")]
    (tmp_path / "FAM1").mkdir()

    done = await followed.follow_imported_deliveries(lambda: _Rows(rows))  # type: ignore[arg-type,return-value]

    assert done.followed == 2 and (done.changed, done.gone, done.resolved) == (0, 0, {})
    assert "FAM1-secret" not in caplog.text


# --- a refusal of the changed folder's new delivery ends its change -----------------------------


class _Refusal:
    def __init__(self, ended: list[tuple[str, str, str]]) -> None:
        self.ended = ended
        self.statements: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, params: Any = None) -> Any:
        self.statements.append((str(statement), dict(params or {})))
        ended = self.ended
        return SimpleNamespace(all=lambda: ended)


@pytest.mark.asyncio
async def test_refusing_the_new_delivery_of_a_changed_folder_ends_the_change_with_its_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[dict[str, Any]] = []

    async def record(_session: Any, **fields: Any) -> None:
        recorded.append(fields)

    monkeypatch.setattr(followed, "record_clinical_event", record)
    session = _Refusal([("00000000-0000-4000-8000-0000000000d1", "FAM1", _A)])
    user = SimpleNamespace(id="00000000-0000-4000-8000-0000000000aa", username="superuser", email="superuser@example.org")
    refused = {"id": "00000000-0000-4000-8000-0000000000d2", "folder_path": "/imports/WGS/FAM1", "folder_name": "FAM1", "package_fingerprint": _B}

    ended = await followed.resolve_refused(session, refused=refused, user=user, reason="QC report only.")  # type: ignore[arg-type]

    assert ended == 1
    statement, params = session.statements[0]
    assert "source_change ->> 'fingerprint' = :fingerprint" in statement
    # Matched by the folder the change was found in, which may be another than the reference's.
    assert "source_change ->> 'folder_path' = :folder" in statement
    assert (params["folder"], params["fingerprint"]) == ("/imports/WGS/FAM1", _B)
    (event,) = recorded
    assert event["family_identifier"] == "system:import-inbox" and event["action"] == "import_delivery_change_resolved"
    assert event["actor_id"] == user.id
    assert event["after"] == {
        "folder": "FAM1",
        "package_fingerprint": _A,
        "state": "imported",
        "resolution": "refused",
        "folder_fingerprint": _B,
        "refused_delivery_id": refused["id"],
        "reason": "QC report only.",
    }


# --- what a family page and its sign-out read ---------------------------------------------------


def test_a_change_is_read_in_one_shape_with_its_lists_sorted() -> None:
    raw = {
        "delivery_id": "00000000-0000-4000-8000-0000000000d1",
        "folder": "FAM1",
        "imported_fingerprint": _A,
        "kind": "changed",
        "fingerprint": _B,
        "listed": True,
        "added": ["z.txt", "a.txt"],
        "removed": [],
        "changed": ["snv/family.vcf"],
        "added_count": 2,
        "removed_count": 0,
        "changed_count": True,  # not a count
        "detected_at": "2026-11-11T10:00:00+00:00",
    }

    out = followed.source_change_out(raw)

    assert out is not None
    assert (out["added"], out["changed_count"], out["removed_count"]) == (["a.txt", "z.txt"], None, 0)
    assert followed.source_change_out('{"kind": "gone"}') == {
        "delivery_id": "",
        "folder": "",
        "kind": "gone",
        "imported_fingerprint": None,
        "fingerprint": None,
        "listed": False,
        "added": [],
        "removed": [],
        "changed": [],
        "added_count": None,
        "removed_count": None,
        "changed_count": None,
        "detected_at": None,
        "found_at_sign_out": False,
        "archived": None,
    }
    assert followed.source_change_out(None) is None


# --- one followed delivery, in its transaction --------------------------------------------------


class _Locked:
    """A session for ``_follow_one``: it answers the locked read with ``locked``, the look-up of a
    refused delivery with ``refused``, and keeps every statement and each commit."""

    def __init__(self, locked: dict[str, Any] | None, refused: str | None = None) -> None:
        self.locked = locked
        self.refused = refused
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.commits = 0

    async def __aenter__(self) -> _Locked:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    async def execute(self, statement: Any, params: Any = None) -> Any:
        sql = str(statement)
        self.statements.append((sql, dict(params or {})))
        locked, refused = self.locked, self.refused
        if "FOR UPDATE" in sql:
            return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: locked))
        if "state = 'refused'" in sql:
            return SimpleNamespace(first=lambda: (refused,) if refused else None)
        return SimpleNamespace()

    async def commit(self) -> None:
        self.commits += 1


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    async def record(_session: Any, **fields: Any) -> None:
        events.append(fields)

    monkeypatch.setattr(followed, "record_clinical_event", record)
    return events


def _locked(**fields: Any) -> dict[str, Any]:
    return {
        "state": "imported",
        "source_change": None,
        "missing_rounds": 0,
        "file_listing": {"a.txt": ["file", 1, 1]},
        **fields,
    }


@pytest.mark.asyncio
async def test_a_followed_folder_that_changed_is_recorded_with_what_changed(
    tmp_path: Path, recorded: list[dict[str, Any]]
) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    session = _Locked(_locked())

    step = await followed._follow_one(
        lambda: session, _row(tmp_path, tmp_path), followed._folder_now(str(tmp_path))  # type: ignore[arg-type,return-value]
    )

    assert step is not None and step.action == "raise"
    update = next(params for sql, params in session.statements if "SET state = :changed" in sql)
    change = followed._json_object(update["change"])
    assert change is not None and (change["added"], change["changed"]) == (["b.txt"], ["a.txt"])
    (event,) = recorded
    assert (event["actor"], event["action"]) == ("system:import-watcher", "import_delivery_changed_after_import")
    assert event["after"]["change"]["added"] == ["b.txt"] and event["after"]["previous_change"] is None
    assert event["summary"] == (
        f"Delivery {tmp_path.name}: changed after its import, its folder changed: 1 file(s) added, 0 removed, 1 changed"
    )
    assert session.commits == 1


@pytest.mark.asyncio
async def test_a_followed_folder_back_as_imported_or_refused_ends_its_change(
    tmp_path: Path, recorded: list[dict[str, Any]]
) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    now = followed._folder_now(str(tmp_path))
    raised = {"kind": "changed", "fingerprint": now.fingerprint}

    back = _Locked(_locked(state="changed_after_import", source_change={"kind": "gone", "fingerprint": None}))
    as_imported = await followed._follow_one(
        lambda: back,  # type: ignore[arg-type,return-value]
        _row(tmp_path, tmp_path, state="changed_after_import", package_fingerprint=now.fingerprint),
        now,
    )
    refused = _Locked(_locked(state="changed_after_import", source_change=raised), refused="d2")
    ended = await followed._follow_one(
        lambda: refused, _row(tmp_path, tmp_path, state="changed_after_import"), now  # type: ignore[arg-type,return-value]
    )

    assert as_imported is not None and as_imported.resolution == "as_imported"
    assert ended is not None and ended.resolution == "refused"
    assert [event["after"]["resolution"] for event in recorded] == ["as_imported", "refused"]
    assert recorded[1]["after"]["refused_delivery_id"] == "d2"
    assert recorded[0]["summary"].endswith("the folder is as it was imported")
    assert recorded[1]["summary"].endswith("the folder's state was refused")
    for session in (back, refused):
        assert any("SET state = 'imported', source_change = NULL" in sql for sql, _params in session.statements)


@pytest.mark.asyncio
async def test_a_round_its_folder_is_missing_is_counted_and_a_delivery_decided_meanwhile_left(
    tmp_path: Path, recorded: list[dict[str, Any]]
) -> None:
    missing = _Locked(_locked())
    counted = await followed._follow_one(
        lambda: missing, _row(tmp_path, tmp_path), FolderNow(gone=True)  # type: ignore[arg-type,return-value]
    )
    decided = _Locked(_locked(state="changed_after_import"))
    left = await followed._follow_one(
        lambda: decided, _row(tmp_path, tmp_path), FolderNow(gone=True)  # type: ignore[arg-type,return-value]
    )

    assert counted is not None and (counted.action, counted.missing_rounds) == ("count", 1)
    assert missing.statements[-1][1] == {"missing": 1, "id": "00000000-0000-4000-8000-0000000000d1"}
    assert missing.commits == 1
    assert left is None and decided.commits == 0
    assert recorded == []


class _Unnamed:
    """A session for ``end_changes_no_family_names``: the changed deliveries no family names, and
    for each the delivery of its folder a family names now (``named``)."""

    def __init__(self, rows: list[dict[str, Any]], named: dict[str, tuple[str, str]]) -> None:
        self.rows, self.named = rows, named

    async def __aenter__(self) -> _Unnamed:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    async def execute(self, statement: Any, params: Any = None) -> Any:
        sql = str(statement)
        if "NOT EXISTS" in sql:
            rows = self.rows
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))
        if "JOIN families" in sql:
            named = self.named.get(str((params or {}).get("folder")))
            return SimpleNamespace(first=lambda: named)
        return SimpleNamespace()

    async def commit(self) -> None:
        return None


@pytest.mark.asyncio
async def test_a_change_no_family_names_any_more_ends_naming_the_delivery_imported_since(
    recorded: list[dict[str, Any]],
) -> None:
    rows = [
        {"id": "d1", "folder_path": "/imports/WGS/FAM1", "folder_name": "FAM1", "package_fingerprint": _A, "family_id": "FAM1"},
        {"id": "d3", "folder_path": "/imports/WGS/FAM3", "folder_name": "FAM3", "package_fingerprint": _C, "family_id": "FAM3"},
    ]
    session = _Unnamed(rows, {"/imports/WGS/FAM1": ("d2", "FAM1")})

    ended = await followed.end_changes_no_family_names(lambda: session)  # type: ignore[arg-type,return-value]

    assert ended == 2
    assert [(event["after"]["resolution"], event["after"]["imported_delivery_id"]) for event in recorded] == [
        ("overwritten", "d2"),
        ("not_named", None),
    ]
    assert recorded[0]["summary"] == "Delivery FAM1: the change of its folder ended, the family was imported again with overwrite"


@pytest.mark.asyncio
async def test_a_round_counts_what_it_raised_and_ended(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    steps = iter(
        [
            followed.Step("raise", 0, change={"kind": "changed"}),
            followed.Step("raise", 2, change={"kind": "gone"}),
            followed.Step("resolve", 0, resolution="refused"),
            None,
        ]
    )

    async def two_ended(_factory: Any) -> int:
        return 2

    async def follow_one(*_args: Any) -> Any:
        return next(steps)

    monkeypatch.setattr(followed, "end_changes_no_family_names", two_ended)
    monkeypatch.setattr(followed, "_follow_one", follow_one)
    (tmp_path / "other").mkdir()
    rows = [_row(tmp_path, tmp_path / f"FAM{index}") for index in range(4)]

    done = await followed.follow_imported_deliveries(lambda: _Rows(rows))  # type: ignore[arg-type,return-value]

    assert (done.followed, done.changed, done.gone) == (4, 1, 1)
    assert done.resolved == {"overwritten_or_not_named": 2, "refused": 1}


@pytest.mark.asyncio
async def test_a_family_reads_the_change_of_the_delivery_its_record_names() -> None:
    raw = {
        "delivery_id": "d1",
        "folder": "FAM1",
        "imported_fingerprint": _A,
        "kind": "gone",
        "detected_at": "2026-11-11T10:00:00+00:00",
    }
    calls: list[dict[str, Any]] = []

    class _Scalar:
        async def execute(self, _statement: Any, params: Any = None) -> Any:
            calls.append(dict(params or {}))
            return SimpleNamespace(scalar_one_or_none=lambda: raw)

    change = await followed.family_package_source_change(_Scalar(), "family-uuid")  # type: ignore[arg-type]

    assert calls == [{"family_uuid": "family-uuid", "raised": ["changed_after_import", "archived"]}]
    assert change is not None and (change["kind"], change["folder"], change["listed"]) == ("gone", "FAM1", False)
    assert followed._describe({"kind": "gone"}) == "its folder is gone or cannot be read"
    assert followed._describe({"kind": "changed", "listed": False}) == "its folder changed"
    assert followed._json_object("not json") is None


# --- the reference: the delivery the family's data was last imported from whole ----------------


def test_a_new_family_or_an_overwrite_makes_its_delivery_the_reference_and_an_update_keeps_it() -> None:
    first = {"id": "d1", "package_fingerprint": _A}
    second = {"id": "d2", "package_fingerprint": _B}

    assert followed.reference_delivery(import_mode="new_family", delivery=first, previous_reference=None) == first
    # An update skips the datasets the family holds: the reference stays the one the record had.
    assert followed.reference_delivery(import_mode="update", delivery=second, previous_reference=first) == first
    assert followed.reference_delivery(import_mode="overwrite", delivery=second, previous_reference=first) == second
    # A family's first package import is its reference, whatever its mode.
    assert followed.reference_delivery(import_mode="update", delivery=second, previous_reference=None) == second


class _Registration:
    """A session for ``_register_package_provenance``: it answers the read of the record's reference
    with ``previous`` and keeps the metadata the family's update writes."""

    def __init__(self, previous: Any) -> None:
        self.previous = previous
        self.written: dict[str, Any] | None = None

    async def execute(self, statement: Any, params: Any = None) -> Any:
        sql = str(statement)
        previous = self.previous
        if "-> 'reference_delivery' FROM families" in sql:
            return SimpleNamespace(scalar_one_or_none=lambda: previous)
        if sql.lstrip().startswith("UPDATE families") and params and "metadata" in params:
            self.written = json.loads(params["metadata"])
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: []))

    async def commit(self) -> None:
        return None


@pytest.mark.parametrize(
    ("import_mode", "previous", "reference"),
    [
        ("new_family", None, "d2"),
        ("overwrite", {"id": "d1", "package_fingerprint": _A}, "d2"),
        ("update", {"id": "d1", "package_fingerprint": _A}, "d1"),
        ("update", None, "d2"),
    ],
)
@pytest.mark.asyncio
async def test_an_imports_record_keeps_its_mode_and_the_reference_delivery(
    monkeypatch: pytest.MonkeyPatch, import_mode: str, previous: Any, reference: str
) -> None:
    from backend.app.services import family_package_registration as registration

    async def nothing(*_args: Any, **_kwargs: Any) -> Any:
        return None

    for name in ("lock_family_pedigree", "read_structure_snapshot", "record_structure_change", "_record_package_raw_files"):
        monkeypatch.setattr(registration, name, nothing)
    for name, value in (
        ("_manifest_pgt_metadata", {}),
        ("_normalize_manifest_samples", {}),
        ("_sample_provenance", {}),
        ("_manifest_carrier_types", {}),
        ("_dataset_provenance", []),
    ):
        monkeypatch.setattr(registration, name, lambda *_args, value=value: value)
    monkeypatch.setattr(registration, "_manifest_member_status_overrides", lambda *_args: {})
    delivery = {"id": "d2", "package_fingerprint": _B}
    bundle = SimpleNamespace(
        manifest=SimpleNamespace(schema_version=1, metadata={}, analysis_type=None, samples={}),
        ped=SimpleNamespace(members=[], text=""),
        root=Path("/imports/WGS/FAM1"),
        manifest_sha256=None,
        package_fingerprint=None,
        delivery=delivery,
    )
    session = _Registration(previous)

    await registration._register_package_provenance(
        session,  # type: ignore[arg-type]
        bundle=bundle,  # type: ignore[arg-type]
        validation=SimpleNamespace(family_id="FAM1"),  # type: ignore[arg-type]
        family_uuid="family-uuid",
        family_identifier="FAM1",
        user=SimpleNamespace(id="u1", username="superuser"),  # type: ignore[arg-type]
        import_mode=import_mode,
    )

    assert session.written is not None
    record = session.written["package_import"]
    assert (record["import_mode"], record["delivery"]["id"], record["reference_delivery"]["id"]) == (
        import_mode,
        "d2",
        reference,
    )


# --- an archived folder ---------------------------------------------------------------------------

_ARCHIVED = {"kind": "gone", "fingerprint": None, "archived": {"archived_at": "t", "archived_by": "superuser", "reason": "r"}}


def test_an_archived_folder_still_gone_is_kept_and_one_back_as_imported_ends() -> None:
    kept = decide(state="archived", imported_fingerprint=_A, change=_ARCHIVED, missing_rounds=2, now=FolderNow(gone=True), refused=False)
    back = decide(state="archived", imported_fingerprint=_A, change=_ARCHIVED, missing_rounds=2, now=_now(_A), refused=False)

    assert (kept.action, kept.missing_rounds) == ("keep", 2)
    assert (back.action, back.resolution, back.missing_rounds) == ("resolve", "as_imported", 0)


def test_an_archived_folder_back_otherwise_is_a_change_again_unless_its_state_was_refused() -> None:
    again = decide(
        state="archived",
        imported_fingerprint=_A,
        change=_ARCHIVED,
        missing_rounds=2,
        now=_now(_B),
        refused=False,
        difference=listing_difference({}, {"a": ["file", 1, 1]}),
    )
    refused = decide(state="archived", imported_fingerprint=_A, change=_ARCHIVED, missing_rounds=2, now=_now(_B), refused=True)

    assert again.action == "raise" and again.change is not None
    assert (again.change["kind"], again.change["fingerprint"], again.change["added"]) == ("changed", _B, ["a"])
    assert (refused.action, refused.resolution) == ("resolve", "refused")


class _Archive:
    """A session for ``archive_gone_delivery``: it answers the locked read with ``row``, keeps every
    statement and commits, and answers the read of the delivery after it with ``out``."""

    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.commits = 0

    async def execute(self, statement: Any, params: Any = None) -> Any:
        sql = str(statement)
        self.statements.append((sql, dict(params or {})))
        row = self.row
        return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: row))

    async def commit(self) -> None:
        self.commits += 1


_SUPERUSER = SimpleNamespace(id="00000000-0000-4000-8000-0000000000aa", username="superuser", email="superuser@example.org")
_DELIVERY = "00000000-0000-4000-8000-0000000000d1"


def _gone_row(**fields: Any) -> dict[str, Any]:
    return {
        "id": _DELIVERY,
        "folder_name": "FAM1",
        "package_fingerprint": _A,
        "state": "changed_after_import",
        "source_change": {"kind": "gone", "fingerprint": None, "listed": False, "detected_at": "t0", "folder": "FAM1"},
        "family_id": "FAM1",
        **fields,
    }


@pytest.mark.asyncio
async def test_a_gone_folder_is_confirmed_as_archived_with_who_when_and_why(
    monkeypatch: pytest.MonkeyPatch, recorded: list[dict[str, Any]]
) -> None:
    out = SimpleNamespace(id=_DELIVERY, state="archived")

    async def delivery(_session: Any, delivery_id: str) -> Any:
        assert delivery_id == _DELIVERY
        return out

    monkeypatch.setattr(followed, "get_import_delivery", delivery)
    session = _Archive(_gone_row())

    archived = await followed.archive_gone_delivery(
        session, delivery_id=_DELIVERY.upper(), user=_SUPERUSER, reason="The run was archived to tape."  # type: ignore[arg-type]
    )

    assert archived is out and session.commits == 1
    update = next(params for sql, params in session.statements if "SET state = :archived" in sql)
    assert update["archived"] == "archived" and update["id"] == _DELIVERY
    kept = json.loads(update["change"])
    assert kept["kind"] == "gone" and kept["detected_at"] == "t0"
    assert (kept["archived"]["archived_by"], kept["archived"]["reason"]) == ("superuser", "The run was archived to tape.")
    assert kept["archived"]["archived_at"]
    (event,) = recorded
    assert (event["family_identifier"], event["action"], event["actor"], event["actor_id"]) == (
        "system:import-inbox",
        "import_delivery_archived",
        "superuser",
        _SUPERUSER.id,
    )
    assert event["after"]["reason"] == "The run was archived to tape."
    assert (event["after"]["state"], event["after"]["previous_state"]) == ("archived", "changed_after_import")
    assert event["metadata"] == {"delivery_id": _DELIVERY}


@pytest.mark.parametrize(
    "row",
    [
        _gone_row(state="imported", source_change=None),
        _gone_row(source_change={"kind": "changed", "fingerprint": _B}),
        _gone_row(state="archived", source_change=_ARCHIVED),
        _gone_row(state="needs_attention", source_change=None),
    ],
    ids=["imported", "changed-not-gone", "archived-already", "open"],
)
@pytest.mark.asyncio
async def test_only_a_delivery_changed_after_import_with_its_folder_gone_can_be_archived(
    row: dict[str, Any], recorded: list[dict[str, Any]]
) -> None:
    session = _Archive(row)

    with pytest.raises(HTTPException) as refused:
        await followed.archive_gone_delivery(session, delivery_id=_DELIVERY, user=_SUPERUSER, reason="r")  # type: ignore[arg-type]

    assert refused.value.status_code == 409
    assert not any("SET state" in sql for sql, _params in session.statements)
    assert recorded == [] and session.commits == 0


@pytest.mark.parametrize("delivery_id", [_DELIVERY, "not-a-uuid"])
@pytest.mark.asyncio
async def test_archiving_no_such_delivery_is_not_found(delivery_id: str) -> None:
    session = _Archive(None)

    with pytest.raises(HTTPException) as missing:
        await followed.archive_gone_delivery(session, delivery_id=delivery_id, user=_SUPERUSER, reason="r")  # type: ignore[arg-type]

    assert missing.value.status_code == 404


def test_the_archive_route_is_a_superusers_with_a_reason_of_at_most_2000_characters() -> None:
    from backend.app import dependencies
    from backend.app.schemas import ImportDeliveryArchive
    from backend.tests._app_routes import dependency_calls, route_operations

    route = route_operations()[("POST", "/api/family-imports/deliveries/{delivery_id}/archive")]
    assert dependencies.get_current_superuser in dependency_calls(route.dependant)
    ImportDeliveryArchive(reason="x" * 2000)
    for reason in ("", "x" * 2001):
        with pytest.raises(ValueError):
            ImportDeliveryArchive(reason=reason)


def test_an_archived_change_is_read_with_its_archiving_and_does_not_gate_a_sign_out() -> None:
    out = followed.source_change_out({**_ARCHIVED, "delivery_id": "d1", "folder": "FAM1"})
    raised = followed.source_change_out({"kind": "gone", "delivery_id": "d1"})

    assert out is not None and out["archived"] == {"archived_at": "t", "archived_by": "superuser", "reason": "r"}
    assert followed.gates_a_sign_out(out) is False
    assert raised is not None and raised["archived"] is None and followed.gates_a_sign_out(raised) is True
    assert followed.gates_a_sign_out(None) is False


# --- a sign-out's own check of the folder ---------------------------------------------------------


class _Record:
    """A session for ``check_family_package_folder``: it answers the read of the family's record."""

    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row

    async def execute(self, _statement: Any, _params: Any = None) -> Any:
        row = self.row
        return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: row))


def _record(folder: Path | str | None, fingerprint: str, listing: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "reference": {"id": "d1", "package_fingerprint": fingerprint},
        "folder_path": str(folder) if folder is not None else None,
        "file_listing": listing,
    }


@pytest.fixture
def served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A family's package folder below the import root CoGA serves files from."""
    from backend.app.core.config import settings

    root = tmp_path / "imports"
    folder = root / "WGS" / "FAM1"
    folder.mkdir(parents=True)
    monkeypatch.setattr(settings, "family_import_roots", [str(root)])
    return folder


@pytest.mark.asyncio
async def test_a_sign_out_finds_a_folder_as_imported_and_a_family_without_a_delivery_has_nothing(served: Path) -> None:
    (served / "a.txt").write_text("a", encoding="utf-8")
    imported = fingerprints.package_fingerprint(served, kept_manifest_sha256=None).sha256

    assert await followed.check_family_package_folder(_Record(_record(served, imported)), "family-uuid") is None  # type: ignore[arg-type]
    unimported = {"reference": None, "folder_path": str(served), "file_listing": None}
    assert await followed.check_family_package_folder(_Record(unimported), "family-uuid") is None  # type: ignore[arg-type]
    assert await followed.check_family_package_folder(_Record(None), "family-uuid") is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_sign_out_finds_a_folder_changed_since_its_reference_with_what_changed(served: Path) -> None:
    (served / "a.txt").write_text("a", encoding="utf-8")
    kept = fingerprints.package_listing(served)
    (served / "b.txt").write_text("b", encoding="utf-8")

    change = await followed.check_family_package_folder(
        _Record(_record(served, kept.fingerprint.sha256, kept.entries)), "family-uuid"  # type: ignore[arg-type]
    )

    assert change is not None
    assert (change["kind"], change["found_at_sign_out"], change["detected_at"]) == ("changed", True, None)
    assert (change["delivery_id"], change["folder"], change["imported_fingerprint"]) == (
        "d1",
        served.name,
        kept.fingerprint.sha256,
    )
    assert change["fingerprint"] == fingerprints.package_fingerprint(served, kept_manifest_sha256=None).sha256
    assert (change["listed"], change["added"], change["changed"]) == (True, ["b.txt"], [])
    assert followed.gates_a_sign_out(change) is True


@pytest.mark.asyncio
async def test_a_sign_out_never_passes_a_folder_gone_unreadable_slow_unrecorded_or_unserved(
    served: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gone = await followed.check_family_package_folder(_Record(_record(served / "missing", _A)), "family-uuid")  # type: ignore[arg-type]
    unrecorded = await followed.check_family_package_folder(_Record(_record(None, _A)), "family-uuid")  # type: ignore[arg-type]
    # A folder CoGA serves no file from (outside every import root) is not listed.
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    listed: list[str] = []

    def listing(folder: str) -> Any:
        listed.append(str(folder))
        return fingerprints.PackageListing(fingerprints.PackageFingerprint(sha256=_A, files=0), {})

    monkeypatch.setattr(followed, "package_listing", listing)
    unserved = await followed.check_family_package_folder(_Record(_record(outside, _A)), "family-uuid")  # type: ignore[arg-type]
    assert listed == []

    def fails(_folder: str) -> Any:
        raise ValueError("embedded null byte")

    monkeypatch.setattr(followed, "package_listing", fails)
    unreadable = await followed.check_family_package_folder(_Record(_record(served, _A)), "family-uuid")  # type: ignore[arg-type]

    def slow(_folder: str) -> Any:
        import time

        time.sleep(0.5)
        return fingerprints.PackageListing(fingerprints.PackageFingerprint(sha256=_A, files=0), {})

    monkeypatch.setattr(followed, "package_listing", slow)
    monkeypatch.setattr(followed, "SIGN_OUT_FOLDER_CHECK_SECONDS", 0.05)
    timed_out = await followed.check_family_package_folder(_Record(_record(served, _A)), "family-uuid")  # type: ignore[arg-type]

    for change in (gone, unrecorded, unserved, unreadable, timed_out):
        assert change is not None and change["kind"] == "gone" and change["found_at_sign_out"] is True
        assert followed.gates_a_sign_out(change) is True


@pytest.mark.asyncio
async def test_a_sign_out_takes_a_folder_too_large_to_fingerprint_as_changed(
    served: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def too_large(_folder: str) -> Any:
        raise fingerprints.PackageFolderTooLarge(1)

    monkeypatch.setattr(followed, "package_listing", too_large)

    change = await followed.check_family_package_folder(_Record(_record(served, _A)), "family-uuid")  # type: ignore[arg-type]

    assert change is not None and (change["kind"], change["fingerprint"], change["listed"]) == ("changed", None, False)


def _out(**fields: Any) -> dict[str, Any]:
    out = followed.source_change_out({"delivery_id": "d1", "folder": "FAM1", **fields})
    assert out is not None
    return out


def test_a_sign_out_keeps_the_watchers_finding_unless_its_own_check_finds_something_else() -> None:
    watcher = _out(kind="changed", fingerprint=_B, detected_at="t0")
    archived = _out(**_ARCHIVED)
    own_same = _out(kind="changed", fingerprint=_B, found_at_sign_out=True)
    own_other = _out(kind="changed", fingerprint=_C, found_at_sign_out=True)
    own_gone = _out(kind="gone", found_at_sign_out=True)

    combine = followed.sign_out_source_change
    # The watcher's finding alone, or as the own check finds it too.
    assert combine(watcher, None) == watcher and combine(watcher, own_same) == watcher
    assert combine(None, None) is None
    # A change the watcher has not seen yet.
    assert combine(None, own_other) == own_other and combine(watcher, own_other) == own_other
    assert combine(watcher, own_gone) == own_gone
    # An archived folder still gone stays archived; one back otherwise is a change.
    assert combine(archived, own_gone) == archived and combine(archived, None) == archived
    assert combine(archived, own_other) == own_other
    assert followed.gates_a_sign_out(combine(archived, own_gone)) is False


# --- an update keeps the change: the reference is followed in the folder the record names now ---


@pytest.mark.asyncio
async def test_the_reference_is_compared_with_the_folder_its_familys_record_names_now(
    tmp_path: Path, recorded: list[dict[str, Any]]
) -> None:
    # The family's last import, an update, read another folder: CoGA serves the source files from
    # it, so it is compared with the reference's fingerprint, and the change raised on the
    # reference names the folder it was found in.
    served = tmp_path / "WGS" / "FAM1-rerun"
    served.mkdir(parents=True)
    (served / "a.txt").write_text("changed", encoding="utf-8")
    session = _Locked(_locked())
    row = {**_row(tmp_path, served), "folder_name": "FAM1", "followed_name": served.name}

    step = await followed._follow_one(lambda: session, row, followed._folder_now(str(served)))  # type: ignore[arg-type,return-value]

    assert step is not None and step.action == "raise"
    update = next(params for sql, params in session.statements if "SET state = :changed" in sql)
    change = json.loads(update["change"])
    assert (change["folder"], change["folder_path"]) == (served.name, str(served))
    assert change["changed"] == ["a.txt"]
    (event,) = recorded
    assert event["after"]["folder"] == "FAM1" and event["after"]["package_fingerprint"] == _A
    # The look-up of a refused state is of the folder followed.
    refused_lookup = next(params for sql, params in session.statements if "state = 'refused'" in sql)
    assert refused_lookup["folder"] == str(served)


def test_the_followed_deliveries_are_the_references_and_a_change_waits_for_the_new_references_import() -> None:
    import inspect

    follow = inspect.getsource(followed.follow_imported_deliveries)
    ended = inspect.getsource(followed.end_changes_no_family_names)
    # Followed: each family's reference, in the folder of the delivery its record names.
    assert "_REFERENCE_OF_A_FAMILY" in follow and "_NAMED_BY_THE_RECORD" in follow
    assert "'reference_delivery'" in followed._REFERENCE_OF_A_FAMILY
    # A change no family's record names as its reference ends, unless the family's new reference
    # is still being imported (a failed import that puts the family back puts its record back).
    assert "q.state = 'queued'" in ended
