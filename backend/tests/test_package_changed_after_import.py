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

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

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
        {"id": "d1", "folder_path": "/imports/WGS/FAM1", "folder_name": "FAM1", "package_fingerprint": _A},
        {"id": "d3", "folder_path": "/imports/WGS/FAM3", "folder_name": "FAM3", "package_fingerprint": _C},
    ]
    session = _Unnamed(rows, {"/imports/WGS/FAM1": ("d2", "FAM1")})

    ended = await followed.end_changes_no_family_names(lambda: session)  # type: ignore[arg-type,return-value]

    assert ended == 2
    assert [(event["after"]["resolution"], event["after"]["imported_delivery_id"]) for event in recorded] == [
        ("imported", "d2"),
        ("not_named", None),
    ]
    assert recorded[0]["summary"] == "Delivery FAM1: the change of its folder ended, its new delivery was imported"


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
    assert done.resolved == {"imported_or_not_named": 2, "refused": 1}


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

    assert calls == [{"family_uuid": "family-uuid", "changed": "changed_after_import"}]
    assert change is not None and (change["kind"], change["folder"], change["listed"]) == ("gone", "FAM1", False)
    assert followed._describe({"kind": "gone"}) == "its folder is gone or cannot be read"
    assert followed._describe({"kind": "changed", "listed": False}) == "its folder changed"
    assert followed._json_object("not json") is None
