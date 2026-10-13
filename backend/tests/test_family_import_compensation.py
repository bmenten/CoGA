"""Compensation/atomicity helpers for failed family-package imports (issue #334).

Covers the fail-clean plumbing without a live database:
  * `_delete_family_shell` also clears orphan ClickHouse interval-track rows.
  * `_flag_family_import_incomplete` stamps a failed update/overwrite as degraded, merged
    with the flag already there (a later failure keeps an earlier one), and removes the
    import's own `import_unfinished` entry in the same transaction.
  * `_clear_family_import_incomplete`, after a completed import, removes what of the flag
    it imported again (the flag stays until each failed dataset is), with the import's own
    entry and each earlier import's entry it completed; an entry it did not complete (an
    update, or an overwrite without that dataset) stays.
  * After a failed overwrite whose restore failed part-way, `_flag_family_restore_failed`
    keeps the import's entry naming every dataset of the family (SAFE-16), which each
    completed overwrite shrinks and the last removes; what imports imported is recorded
    in `imported_scopes`.
  * Each single-file upload records what it brought in `imported_scopes`, in its own
    transaction, so such an entry names it, and an upload with overwrite strikes it off
    (SAFE-19); each upload holds the family's write locks from its first read.
The SQL itself runs against Postgres in
integration/test_import_crash_bookkeeping_integration.py.
"""

import copy
import json
from types import SimpleNamespace

import pytest

from app.services import family_package_import as fpi
from app.services import family_package_registration as registration


class _FakeSession:
    def __init__(self) -> None:
        self.executed: list[tuple[str, dict | None]] = []

    async def execute(self, statement, params=None):
        self.executed.append((str(statement), params))

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(assembly_name="GRCh38", family_uuid="uuid-1", family_id="F1")


@pytest.mark.asyncio
async def test_delete_family_shell_also_clears_interval_tracks(monkeypatch) -> None:
    calls = {"small": 0, "structural": 0, "interval": 0}

    async def fake_small(assembly, family):
        calls["small"] += 1

    async def fake_structural(assembly, family):
        calls["structural"] += 1

    async def fake_interval(assembly, *, family_uuid):
        calls["interval"] += 1
        assert family_uuid == "uuid-1"

    # _delete_family_shell now lives in family_package_registration; patch the
    # deletion helpers where that function resolves them.
    monkeypatch.setattr(
        "app.services.family_package_registration.delete_family_small_variants", fake_small
    )
    monkeypatch.setattr(
        "app.services.family_package_registration.delete_family_structural_variants", fake_structural
    )
    monkeypatch.setattr(
        "app.services.family_package_registration.delete_interval_tracks", fake_interval
    )

    session = _FakeSession()
    await fpi._delete_family_shell(session, _ctx())

    # Coverage/segment/haplotype interval tracks are cleared alongside the variants.
    assert calls == {"small": 1, "structural": 1, "interval": 1}
    assert any("DELETE FROM families" in sql for sql, _ in session.executed)


class _FamilyRow(_FakeSession):
    """The family row's import state (``flag``: metadata.import_incomplete,
    ``unfinished``: metadata.import_unfinished, ``imported``: metadata.imported_scopes,
    None when absent). Answers the locked reads and applies each UPDATE as its statement
    documents, told apart by what it binds: the mark binds an ``entry`` for its
    ``import_key``; an import's failure and a completed import's clear bind the new
    ``flag`` and ``unfinished`` (JSON null: none) and ``imported_scopes`` (JSON null: left
    as it is); a single-file upload's record binds no flag, its ``imported_scopes``, and
    ``unfinished`` only when it struck something off a whole-family entry."""

    def __init__(self, flag=None, unfinished=None, imported=None) -> None:
        super().__init__()
        self.flag = flag
        self.unfinished = unfinished
        self.imported = imported

    async def execute(self, statement, params=None):
        await super().execute(statement, params)
        sql = " ".join(str(statement).split())
        params = params or {}
        if sql.startswith("SELECT"):
            row = {
                "family_uuid": "uuid-1",
                "flag": copy.deepcopy(self.flag),
                "unfinished": copy.deepcopy(self.unfinished),
                "imported_scopes": copy.deepcopy(self.imported),
            }
            return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: row))
        assert sql.startswith("UPDATE families"), sql
        if "entry" in params:
            self.unfinished = {**(self.unfinished or {}), params["import_key"]: json.loads(params["entry"])}
        elif "payload" in params:
            self.flag = json.loads(params["payload"])
            if isinstance(self.unfinished, dict):
                self.unfinished.pop(params.get("import_key"), None)
                self.unfinished = self.unfinished or None
        elif "flag" not in params:
            self.imported = json.loads(params["imported_scopes"])
            if "unfinished" in params:
                self.unfinished = json.loads(params["unfinished"])
        else:
            self.flag = json.loads(params["flag"])
            self.unfinished = json.loads(params["unfinished"])
            recorded = json.loads(params.get("imported_scopes", "null"))
            if recorded is not None:
                self.imported = recorded
        return SimpleNamespace(rowcount=1)


# What an overwrite of each dataset replaces (registration.dataset_scopes): the SNV
# callset's source, and a per-sample dataset's samples.
_SCOPES = {
    "snv": {"source": "auto", "samples": None},
    "sv_needlr": {"source": None, "samples": None},
    "coverage": {"source": None, "samples": ["S1", "S2"]},
    "haplotypes": {"source": None, "samples": None},
}


def _entry(job_id, datasets, finished=(), scopes=None):
    return {
        "job_id": job_id,
        "at": "2026-10-02T10:00:00+00:00",
        "datasets": list(datasets),
        "finished_datasets": list(finished),
        "scopes": {name: (scopes or _SCOPES)[name] for name in datasets if name in (scopes or _SCOPES)},
    }


def _flag(job_id, failed, imported=(), *, jobs=None):
    """A flag as an import wrote it: each failed dataset with its job and scope."""
    return {
        "at": "2026-10-01T09:00:00+00:00",
        "failed_datasets": sorted(failed),
        "imported_datasets": sorted(imported),
        "job_id": job_id,
        "failed_jobs": {name: (jobs or {}).get(name, job_id) for name in sorted(failed)},
        "scopes": {name: _SCOPES[name] for name in sorted(failed)},
    }


# --- an import's failure ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flag_family_import_incomplete_records_datasets() -> None:
    row = _FamilyRow()
    await fpi._flag_family_import_incomplete(
        row,
        _ctx(),
        failed_datasets=["snv"],
        imported_datasets=["sv_needlr"],
        scopes=_SCOPES,
    )
    assert row.flag["failed_datasets"] == ["snv"]
    assert row.flag["imported_datasets"] == ["sv_needlr"]
    assert "at" in row.flag
    # What would import it again: the scope it failed for.
    assert row.flag["scopes"] == {"snv": _SCOPES["snv"]}
    select_sql, _ = row.executed[0]
    assert "FOR UPDATE" in select_sql  # merged under the row's lock


@pytest.mark.asyncio
async def test_flag_family_import_incomplete_records_the_import_job() -> None:
    # The flag names the job that holds each dataset's error; it never copies the error
    # texts, which can carry file paths.
    row = _FamilyRow()
    await fpi._flag_family_import_incomplete(
        row, _ctx(), failed_datasets=["snv"], imported_datasets=[], job_id="job-uuid"
    )
    assert row.flag["job_id"] == "job-uuid"
    assert row.flag["failed_jobs"] == {"snv": "job-uuid"}
    assert set(row.flag) == {"at", "failed_datasets", "imported_datasets", "job_id", "failed_jobs", "scopes"}

    # An import run outside a job records none.
    row = _FamilyRow()
    await fpi._flag_family_import_incomplete(
        row, _ctx(), failed_datasets=["snv"], imported_datasets=[]
    )
    assert row.flag["job_id"] is None


@pytest.mark.asyncio
async def test_a_failed_import_removes_its_own_unfinished_entry_with_the_flag() -> None:
    # One transaction: the family is never left with neither the flag nor the entry.
    row = _FamilyRow(unfinished={"job-1": _entry("job-1", ["snv"]), "job-0": _entry("job-0", ["coverage"])})
    await fpi._flag_family_import_incomplete(
        row, _ctx(), failed_datasets=["snv"], imported_datasets=[], job_id="job-1", import_key="job-1"
    )
    assert row.flag["failed_datasets"] == ["snv"]
    assert set(row.unfinished) == {"job-0"}
    update_sql = row.executed[-1][0]
    assert "import_incomplete" in update_sql and "import_unfinished" in update_sql


@pytest.mark.asyncio
async def test_a_later_failure_keeps_an_earlier_one_with_its_job() -> None:
    # Before: the later flag replaced the earlier one, and sv's failure was forgotten.
    row = _FamilyRow(flag=_flag("job-1", ["sv_needlr"], ["snv"]))
    await fpi._flag_family_import_incomplete(
        row, _ctx(), failed_datasets=["coverage"], imported_datasets=[], job_id="job-2", scopes=_SCOPES
    )
    assert row.flag["failed_datasets"] == ["coverage", "sv_needlr"]
    assert row.flag["failed_jobs"] == {"coverage": "job-2", "sv_needlr": "job-1"}
    assert row.flag["job_id"] == "job-2"
    assert row.flag["scopes"] == {"coverage": _SCOPES["coverage"], "sv_needlr": _SCOPES["sv_needlr"]}


@pytest.mark.asyncio
async def test_a_failure_drops_an_earlier_failure_it_imported_again() -> None:
    row = _FamilyRow(flag=_flag("job-1", ["sv_needlr"]))
    await fpi._flag_family_import_incomplete(
        row,
        _ctx(),
        failed_datasets=["coverage"],
        imported_datasets=["sv_needlr"],
        job_id="job-2",
        scopes=_SCOPES,
        imported_scopes={"sv_needlr": _SCOPES["sv_needlr"]},
    )
    assert row.flag["failed_datasets"] == ["coverage"]
    assert row.flag["failed_jobs"] == {"coverage": "job-2"}


# --- a completed import's clear -----------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_family_import_incomplete_drops_the_flag() -> None:
    row = _FamilyRow()
    left = await fpi._clear_family_import_incomplete(row, _ctx())
    assert left is not None and left.unfinished == {} and left.failures.failed == {}
    select_sql, _ = row.executed[0]
    assert "FOR UPDATE" in select_sql  # read under the row's lock
    assert row.flag is None and row.unfinished is None


@pytest.mark.asyncio
async def test_a_completed_import_that_did_not_import_the_failed_dataset_leaves_it_flagged() -> None:
    # Before: any import that completed cleared the flag, sv still missing.
    row = _FamilyRow(flag=_flag("job-1", ["sv_needlr"], ["snv"]))
    left = await fpi._clear_family_import_incomplete(row, _ctx(), imported={"snv": _SCOPES["snv"]})
    assert row.flag == _flag("job-1", ["sv_needlr"], ["snv"])
    assert left.failures.failed == {"sv_needlr": "job-1"}


@pytest.mark.asyncio
async def test_an_import_that_imports_the_failed_dataset_again_in_any_mode_clears_it() -> None:
    # An update counts: the failing loader's rows were rolled back or cleaned up, so the
    # update found none and imported the dataset whole (one whose rows stayed is skipped,
    # and a skipped dataset is not imported).
    row = _FamilyRow(flag=_flag("job-1", ["sv_needlr"]))
    left = await fpi._clear_family_import_incomplete(
        row, _ctx(), imported={"sv_needlr": _SCOPES["sv_needlr"]}
    )
    assert row.flag is None and left.failures.failed == {}


@pytest.mark.asyncio
async def test_a_partial_reimport_shrinks_the_flag_to_what_is_still_missing() -> None:
    row = _FamilyRow(flag=_flag("job-2", ["coverage", "sv_needlr"], jobs={"sv_needlr": "job-1"}))
    left = await fpi._clear_family_import_incomplete(
        row, _ctx(), imported={"sv_needlr": _SCOPES["sv_needlr"]}
    )
    assert row.flag["failed_datasets"] == ["coverage"]
    assert row.flag["failed_jobs"] == {"coverage": "job-2"}
    assert row.flag["scopes"] == {"coverage": _SCOPES["coverage"]}
    # When and by which job the import failed stay as they were.
    assert (row.flag["at"], row.flag["job_id"]) == ("2026-10-01T09:00:00+00:00", "job-2")
    assert left.failures.failed == {"coverage": "job-2"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "imported"),
    [
        ("another SNV source", {"snv": {"source": "deepvariant", "samples": None}}),
        ("fewer samples", {"coverage": {"source": None, "samples": ["S1"]}}),
    ],
)
async def test_an_import_of_less_than_failed_leaves_it_flagged(case, imported) -> None:
    name = next(iter(imported))
    row = _FamilyRow(flag=_flag("job-1", [name]))
    left = await fpi._clear_family_import_incomplete(row, _ctx(), imported=imported)
    assert left.failures.failed == {name: "job-1"}, case


@pytest.mark.asyncio
async def test_a_flag_that_records_no_scope_is_cleared_by_its_dataset() -> None:
    # Written before flags recorded scopes: the dataset alone is compared.
    flag = {key: value for key, value in _flag("job-1", ["snv"]).items() if key not in {"scopes", "failed_jobs"}}
    row = _FamilyRow(flag=flag)
    await fpi._clear_family_import_incomplete(
        row, _ctx(), imported={"snv": {"source": "deepvariant", "samples": None}}
    )
    assert row.flag is None


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["garbage", True, {"failed_datasets": "snv"}])
async def test_a_flag_not_in_the_shape_an_import_writes_stays(flag) -> None:
    row = _FamilyRow(flag=flag)
    left = await fpi._clear_family_import_incomplete(row, _ctx(), imported={"snv": _SCOPES["snv"]})
    assert row.flag == flag
    assert left.failures.unreadable


@pytest.mark.asyncio
async def test_a_completed_import_removes_its_own_entry_and_those_it_completed() -> None:
    stored = {
        "job-own": _entry("job-own", ["snv", "coverage"]),
        # Stopped in coverage; this overwrite imported coverage again.
        "job-covered": _entry("job-covered", ["snv", "coverage"], finished=["snv"]),
        # Had finished everything it set out to do (stopped after its datasets).
        "job-done": _entry("job-done", ["snv"], finished=["snv"]),
        # Stopped in haplotypes, which this import did not import.
        "job-other": _entry("job-other", ["haplotypes"]),
        # Not in the shape an import writes: nothing shows it complete.
        "unreadable": "garbage",
    }
    row = _FamilyRow(unfinished=copy.deepcopy(stored))
    left = await fpi._clear_family_import_incomplete(
        row,
        _ctx(),
        import_key="job-own",
        rewritten={"snv": _SCOPES["snv"], "coverage": _SCOPES["coverage"]},
    )
    assert set(left.unfinished) == {"job-other", "unreadable"}
    assert row.unfinished == {"job-other": stored["job-other"], "unreadable": "garbage"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "replaced"),
    [
        # The SNV loader replaces only its own source's rows: the stopped import's
        # partial callset of another source stays.
        ("another SNV source", {"snv": {"source": "deepvariant", "samples": None}}),
        # A per-sample loader replaces only the samples it names.
        ("fewer samples", {"coverage": {"source": None, "samples": ["S1"]}}),
        # A family-level file is not the per-sample files it would have to cover.
        ("a family-level file", {"coverage": {"source": None, "samples": None}}),
    ],
)
async def test_an_overwrite_of_less_than_the_stopped_import_wrote_leaves_its_entry(case, replaced) -> None:
    pending = next(iter(replaced))
    row = _FamilyRow(unfinished={"job-stopped": _entry("job-stopped", [pending])})

    left = await fpi._clear_family_import_incomplete(row, _ctx(), rewritten=replaced)

    assert set(left.unfinished) == {"job-stopped"}, case


@pytest.mark.asyncio
async def test_an_overwrite_of_the_same_samples_or_more_completes_the_stopped_import() -> None:
    row = _FamilyRow(unfinished={"job-stopped": _entry("job-stopped", ["coverage"])})

    left = await fpi._clear_family_import_incomplete(
        row, _ctx(), rewritten={"coverage": {"source": None, "samples": ["S1", "S2", "S3"]}}
    )

    assert left.unfinished == {} and row.unfinished is None


@pytest.mark.asyncio
async def test_an_entry_without_the_scope_of_what_it_was_writing_is_never_completed() -> None:
    row = _FamilyRow(unfinished={"job-stopped": {**_entry("job-stopped", ["snv"]), "scopes": {}}})

    left = await fpi._clear_family_import_incomplete(row, _ctx(), rewritten={"snv": _SCOPES["snv"]})

    assert set(left.unfinished) == {"job-stopped"}


@pytest.mark.asyncio
async def test_an_update_completes_no_earlier_imports_partial_data() -> None:
    # An update skips a dataset that already holds data -- partly written data too -- so
    # it replaces nothing: an entry with anything pending stays.
    row = _FamilyRow(
        unfinished={
            "job-own": _entry("job-own", ["snv"]),
            "job-stopped": _entry("job-stopped", ["snv", "coverage"], finished=["coverage"]),
        }
    )
    left = await fpi._clear_family_import_incomplete(
        row, _ctx(), import_key="job-own", rewritten={}, imported={"snv": _SCOPES["snv"]}
    )
    assert set(left.unfinished) == {"job-stopped"}


@pytest.mark.asyncio
async def test_a_clear_that_fails_leaves_the_marks_and_says_so() -> None:
    class _Failing(_FakeSession):
        async def execute(self, statement, params=None):
            raise RuntimeError("database unavailable")

    assert await fpi._clear_family_import_incomplete(_Failing(), _ctx(), import_key="job-1") is None


# --- a restore that did not finish: the whole family (SAFE-16) ----------------------------


def _one(sample=None, source=None):
    """One scope as imported_scopes and a whole-family entry list it: one sample at a time."""
    return {"source": source, "samples": [sample] if sample else None}


def _mark(job_id="job-2", datasets=("snv", "coverage")):
    return registration.ImportMark.begin(
        job_id=job_id, datasets=datasets, scopes={name: _SCOPES[name] for name in datasets}
    )


@pytest.mark.asyncio
async def test_a_restore_that_failed_part_way_keeps_its_entry_naming_every_dataset_of_the_family() -> None:
    # The family holds SV calls and S3's coverage from earlier imports, and another import
    # stopped in haplotypes. This overwrite (snv, and coverage of S1 and S2) failed, and so
    # did its restore, which deletes every table's rows of the family, whichever import
    # wrote them, before it puts the backup's back.
    mark = _mark()
    stopped = _entry("job-stopped", ["haplotypes"])
    row = _FamilyRow(
        unfinished={mark.key: mark.entry(), "job-stopped": copy.deepcopy(stopped)},
        imported={"sv_needlr": [_one()], "coverage": [_one("S1"), _one("S3")]},
    )

    listed = await fpi._flag_family_restore_failed(
        row,
        _ctx(),
        failed_datasets=["coverage", "snv"],
        job_id="job-2",
        mark=mark,
        scopes=mark.scopes,
        family_samples=["S1", "S2"],
    )

    # The flag, as since SAFE-13: every dataset of the import failed, none imported.
    assert (row.flag["failed_datasets"], row.flag["imported_datasets"]) == (["coverage", "snv"], [])
    assert row.flag["failed_jobs"] == {"coverage": "job-2", "snv": "job-2"}
    # Before: the entry made way for the flag, so an import of snv and coverage again
    # opened the gate with the SV calls gone. Now it stays, naming every dataset the family
    # holds as pending, each with what an overwrite of it must replace; S3 is no longer the
    # family's, so no import could name it.
    entry = row.unfinished[mark.key]
    assert listed == entry["datasets"] == ["coverage", "haplotypes", "snv", "sv_needlr"]
    assert (entry["job_id"], entry["at"], entry["finished_datasets"]) == ("job-2", mark.at, [])
    assert entry["whole_family"] == [
        {"dataset": "coverage", "scope": _one("S1")},
        {"dataset": "coverage", "scope": _one("S2")},
        {"dataset": "haplotypes", "scope": _SCOPES["haplotypes"]},
        {"dataset": "snv", "scope": _SCOPES["snv"]},
        {"dataset": "sv_needlr", "scope": _one()},
    ]
    assert row.unfinished["job-stopped"] == stopped
    assert "FOR UPDATE" in row.executed[0][0]  # composed under the row's lock


@pytest.mark.asyncio
async def test_before_its_restore_an_import_marks_every_dataset_of_the_family_pending() -> None:
    # So a process that stops in the restore leaves the family marked as a whole too.
    mark = _mark()
    row = _FamilyRow(unfinished={mark.key: mark.entry(["snv"])}, imported={"sv_needlr": [_one()]})

    await fpi._mark_family_import_restoring(row, _ctx(), mark, family_samples=["S1", "S2"])

    entry = row.unfinished[mark.key]
    assert entry["datasets"] == ["coverage", "snv", "sv_needlr"]
    assert entry["finished_datasets"] == []
    assert "FOR UPDATE" in row.executed[0][0]

    # If that cannot be written, every dataset of the import is pending instead, as before.
    class _ReadFails(_FamilyRow):
        async def execute(self, statement, params=None):
            if " ".join(str(statement).split()).startswith("SELECT"):
                raise RuntimeError("database unavailable")
            return await super().execute(statement, params)

    row = _ReadFails(unfinished={mark.key: mark.entry(["snv"])})
    await fpi._mark_family_import_restoring(row, _ctx(), mark)
    assert row.unfinished[mark.key] == mark.entry()


@pytest.mark.asyncio
async def test_the_whole_family_entry_goes_once_each_dataset_is_imported_again_with_overwrite() -> None:
    mark = _mark()
    entry = registration.whole_family_entry(
        mark, imported={"sv_needlr": [_one()]}, unfinished={}, family_samples=["S1", "S2"]
    )
    row = _FamilyRow(unfinished={mark.key: copy.deepcopy(entry)})

    # An update replaces nothing: it skips a dataset whose rows are there.
    await fpi._clear_family_import_incomplete(
        row, _ctx(), rewritten={}, imported={"snv": _SCOPES["snv"], "sv_needlr": _one()}
    )
    assert row.unfinished == {mark.key: entry}

    # An overwrite of S1's coverage alone: S2's is still to import.
    await fpi._clear_family_import_incomplete(row, _ctx(), rewritten={"coverage": _one("S1")})
    assert [item["scope"] for item in row.unfinished[mark.key]["whole_family"] if item["dataset"] == "coverage"] == [
        _one("S2")
    ]

    # The datasets that failed, imported again in one overwrite: the gate holds, for the
    # SV calls the import did not carry.
    left = await fpi._clear_family_import_incomplete(
        row, _ctx(), rewritten={"snv": _SCOPES["snv"], "coverage": _SCOPES["coverage"]}
    )
    assert set(left.unfinished) == {mark.key}
    assert row.unfinished[mark.key]["datasets"] == ["sv_needlr"]
    assert row.unfinished[mark.key]["finished_datasets"] == []
    assert row.unfinished[mark.key]["whole_family"] == [{"dataset": "sv_needlr", "scope": _one()}]

    # The last of them, imported again with overwrite: the family is complete.
    left = await fpi._clear_family_import_incomplete(row, _ctx(), rewritten={"sv_needlr": _one()})
    assert left.unfinished == {} and row.unfinished is None


@pytest.mark.asyncio
async def test_one_overwrite_of_the_whole_family_removes_the_entry() -> None:
    mark = _mark()
    entry = registration.whole_family_entry(mark, imported={"sv_needlr": [_one()]}, unfinished={})
    row = _FamilyRow(unfinished={mark.key: entry, "job-own": _entry("job-own", ["snv"])})

    left = await fpi._clear_family_import_incomplete(
        row,
        _ctx(),
        import_key="job-own",
        rewritten={"snv": _SCOPES["snv"], "coverage": _SCOPES["coverage"], "sv_needlr": _one()},
    )

    assert left.unfinished == {} and row.unfinished is None


@pytest.mark.asyncio
async def test_a_whole_family_list_not_in_the_shape_written_is_never_completed() -> None:
    stored = {**_entry("job-2", ["snv"]), "whole_family": "garbage"}
    row = _FamilyRow(unfinished={"job-2": copy.deepcopy(stored)})

    left = await fpi._clear_family_import_incomplete(row, _ctx(), rewritten={"snv": _SCOPES["snv"]})

    assert left.unfinished == {"job-2": stored}


@pytest.mark.asyncio
async def test_what_the_family_s_imports_imported_is_recorded_for_it() -> None:
    # A failure records what did import, one sample at a time.
    row = _FamilyRow()
    await fpi._flag_family_import_incomplete(
        row,
        _ctx(),
        failed_datasets=["snv"],
        imported_datasets=["coverage"],
        job_id="job-1",
        scopes=_SCOPES,
        imported_scopes={"coverage": _SCOPES["coverage"]},
    )
    assert row.imported == {"coverage": [_one("S1"), _one("S2")]}

    # A completed import records what it imported, and what a stopped import whose entry
    # it removed had finished; each scope once.
    row.unfinished = {"job-stopped": _entry("job-stopped", ["snv", "haplotypes"], finished=["haplotypes"])}
    await fpi._clear_family_import_incomplete(
        row,
        _ctx(),
        rewritten={"snv": _SCOPES["snv"]},
        imported={"snv": _SCOPES["snv"], "coverage": _one("S1")},
    )
    assert row.unfinished is None
    assert row.imported == {
        "coverage": [_one("S1"), _one("S2")],
        "haplotypes": [_SCOPES["haplotypes"]],
        "snv": [_SCOPES["snv"]],
    }
    update_sql = row.executed[-1][0]
    assert "imported_scopes" in update_sql

    # An import that imported nothing records nothing.
    row = _FamilyRow()
    await fpi._clear_family_import_incomplete(row, _ctx())
    assert row.imported is None


def test_a_second_restore_that_fails_keeps_what_the_first_left_to_import() -> None:
    first = registration.whole_family_entry(
        _mark("job-1", ("snv",)), imported={"sv_needlr": [_one()]}, unfinished={}
    )
    later = _mark("job-3", ("coverage",))

    entry = registration.whole_family_entry(later, imported=None, unfinished={"job-1": first})

    assert entry["datasets"] == ["coverage", "snv", "sv_needlr"]
    assert registration.is_whole_family_entry(entry) and not registration.is_whole_family_entry(
        _entry("job-0", ["snv"])
    )


def test_pending_datasets_are_those_an_import_had_not_finished() -> None:
    assert registration.pending_datasets(_entry("j", ["snv", "sv"], ["snv"])) == {"sv"}
    assert registration.pending_datasets(_entry("j", ["snv"], ["snv"])) == set()
    # Not in the shape an import writes: unknown, never "nothing pending".
    assert registration.pending_datasets("garbage") is None
    assert registration.pending_datasets({"datasets": ["snv"]}) is None
    assert registration.pending_datasets({"datasets": "snv", "finished_datasets": []}) is None


def test_an_import_mark_is_keyed_by_its_job_or_its_own_run() -> None:
    scopes = {"snv": _SCOPES["snv"], "sv": {"source": None, "samples": None}}
    mark = registration.ImportMark.begin(job_id="job-1", datasets=["sv", "snv", "sv"], scopes=scopes)
    assert mark.key == "job-1"
    assert mark.entry() == {
        "job_id": "job-1",
        "at": mark.at,
        "datasets": ["snv", "sv"],
        "finished_datasets": [],
        "scopes": scopes,
    }
    assert mark.entry(["sv"])["finished_datasets"] == ["sv"]
    outside = registration.ImportMark.begin(job_id=None, datasets=[])
    assert outside.key.startswith("run-") and outside.job_id is None


def test_a_dataset_scope_is_what_its_loader_replaces(tmp_path) -> None:
    from types import SimpleNamespace

    def dataset(**fields):
        extra = fields.pop("extra", {})
        return SimpleNamespace(
            family_vcf=fields.get("family_vcf"),
            per_sample=fields.get("per_sample") or {},
            model_extra=extra,
        )

    bundle = SimpleNamespace(
        manifest=SimpleNamespace(
            datasets={
                "snv": dataset(family_vcf="snv/family.vcf", extra={"source_format": "deepvariant"}),
                "haplotypes": dataset(family_vcf="GLIMPSE2/FAM.vcf.gz"),
                "coverage": dataset(per_sample={"S2": {"bed": "b"}, "S1": {"bed": "a"}, "bad": "x"}),
                # A family VCF wins over per-sample files, as the TRGT loader reads it.
                "repeats_trgt": dataset(family_vcf="repeats/fam.vcf", per_sample={"S1": {"file": "r"}}),
            }
        )
    )

    scopes = registration.dataset_scopes(
        bundle, ["snv", "haplotypes", "coverage", "repeats_trgt", "phenotypes"]
    )

    assert scopes == {
        "snv": {"source": "deepvariant", "samples": None},
        "haplotypes": {"source": None, "samples": None},
        "coverage": {"source": None, "samples": ["S1", "S2"]},
        "repeats_trgt": {"source": None, "samples": None},
        "phenotypes": {"source": None, "samples": None},
    }
    # An SNV callset without a declared source is read as "auto".
    bundle.manifest.datasets["snv"] = dataset(family_vcf="snv/family.vcf")
    assert registration.dataset_scopes(bundle, ["snv"])["snv"]["source"] == "auto"


# --- single-file uploads: recorded in what the family holds (SAFE-19) ----------------------
#
# Each upload's route is driven against the family row (``_UploadFamily``), its stores
# patched out: what it writes of the family's metadata, and when, is what is tested.

_FAMILY_SAMPLES = [
    {"sample_uuid": "uuid-S1", "sample_id": "S1", "sex": "female", "role": "proband"},
    {"sample_uuid": "uuid-S2", "sample_id": "S2", "sex": "male", "role": "sibling"},
]

# What each upload of S1's data records (dataset, scope).
_UPLOADS = {
    "small_variants": ("snv", {"source": "clair3", "samples": None}),
    "structural_variants": ("sv_manual", _one("S1")),
    "repeats": ("repeats_trgt", _one("S1")),
    "track": ("bed_coverage", _one("S1")),
}


class _UploadFamily(_FamilyRow):
    """``_FamilyRow`` as an upload's route meets it: the upload's other statements are
    answered too (the sample's metadata the structural-variant upload reads and writes
    back; ``repeats``, the count of the sample's TRGT calls the TRGT upload reads), and
    each commit is recorded among the statements, so a test sees what came before it."""

    def __init__(self, *, repeats: int = 0, **row) -> None:
        super().__init__(**row)
        self.repeats = repeats

    async def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        if sql.startswith("UPDATE families") or " FROM families " in sql:
            return await super().execute(statement, params)
        self.executed.append((sql, params))
        if "COUNT(*)" in sql:
            return SimpleNamespace(scalar_one=lambda: self.repeats)
        return SimpleNamespace(scalar_one_or_none=lambda: None)

    async def commit(self) -> None:
        self.executed.append(("COMMIT", None))

    def index(self, prefix: str) -> int:
        return next(i for i, (sql, _) in enumerate(self.executed) if sql.lstrip().startswith(prefix))


def _upload_file(text: str, name: str):
    from io import BytesIO

    from fastapi import UploadFile

    return UploadFile(file=BytesIO(text.encode()), filename=name)


@pytest.fixture
def uploads(monkeypatch):
    """``await uploads(kind, row, ...)`` runs that upload's route (``_UPLOADS``) for S1 (or
    ``sample_id``) of family F1 against ``row``; ``rows`` is how many rows it finds of what
    it would replace. Returns the events of its stores and locks, in order."""
    from app.routers import bed as bed_router
    from app.routers import families_small_variants as snv_router
    from app.routers import repeat_expansions as trgt_router
    from app.routers import structural_variants as sv_router
    from app.services import annotation_manifest_service, bed_service, variant_upload_service
    from app.services.family_metadata_context import SampleMetadataContext

    family = SimpleNamespace(
        family_uuid="uuid-1",
        family_id="F1",
        project_ids=["project-1"],
        assembly_id="assembly-1",
        assembly_name="GRCh38",
        sample_rows=_FAMILY_SAMPLES,
        relationship_rows=[],
        affected_sample_names=[],
    )
    events: list[tuple] = []
    existing = {"rows": 0}

    def recorder(name: str, result=None):
        async def record(*args, **kwargs):
            events.append((name, args, kwargs))
            return result() if callable(result) else result

        return record

    async def lock(session, family_uuid, variant_types, *, samples=None):
        events.append(("lock", tuple(variant_types), sorted(samples or []), len(session.executed)))

    async def nothing(*_args, **_kwargs):
        return None

    async def family_context(*_args, **_kwargs):
        return family

    async def ingest_trgt_text(session, *, sample_context, text_value, metadata, declared=None):
        session.executed.append(("INSERT INTO repeat_expansions", {"sample": sample_context.sample_id}))
        await session.commit()
        return {"processed": 1, "inserted": 1, "source_format": "trgt"}

    for module in (snv_router, sv_router, trgt_router, bed_router):
        monkeypatch.setattr(module, "record_upload_file_obj", nothing)
    monkeypatch.setattr(snv_router, "build_family_metadata_context", family_context)
    monkeypatch.setattr(sv_router, "build_family_metadata_context", family_context)
    monkeypatch.setattr(annotation_manifest_service, "merge_vcf_header_provenance", nothing)
    # The small-variant and structural-variant stores.
    monkeypatch.setattr(variant_upload_service, "lock_family_variant_writes", lock)
    monkeypatch.setattr(
        variant_upload_service, "count_family_small_variants", recorder("count", lambda: existing["rows"])
    )
    monkeypatch.setattr(variant_upload_service, "delete_family_small_variants", recorder("delete"))
    monkeypatch.setattr(variant_upload_service, "insert_small_variant_records", recorder("insert"))
    monkeypatch.setattr(variant_upload_service, "refresh_family_small_variant_summaries", nothing)
    monkeypatch.setattr(
        variant_upload_service, "get_track_presence_by_sample", recorder("haplotypes", lambda: {})
    )
    monkeypatch.setattr(
        variant_upload_service, "_fetch_chromosome_sizes", recorder("sizes", lambda: {"1": 10_000})
    )
    monkeypatch.setattr(
        variant_upload_service, "_delete_family_haplotype_blocks", recorder("delete haplotypes")
    )
    monkeypatch.setattr(variant_upload_service, "_insert_haplotype_rows", nothing)
    monkeypatch.setattr(variant_upload_service, "record_haplotype_phase_corrections", nothing)
    monkeypatch.setattr(
        variant_upload_service, "fetch_family_structural_variant_rows", recorder("fetch", lambda: [])
    )
    monkeypatch.setattr(variant_upload_service, "_fetch_genes_for_chroms", recorder("genes", lambda: {}))
    monkeypatch.setattr(variant_upload_service, "rewrite_family_structural_variants", recorder("rewrite"))
    # The TRGT upload's route reads and clears the sample's calls itself.
    monkeypatch.setattr(trgt_router, "lock_family_variant_writes", lock, raising=False)
    monkeypatch.setattr(trgt_router, "clear_sample_repeat_expansions", recorder("clear"))
    monkeypatch.setattr(trgt_router, "ingest_trgt_text", ingest_trgt_text)
    # The interval-track store; the BED upload's route takes the family's write locks.
    monkeypatch.setattr(bed_router, "lock_family_variant_writes", lock, raising=False)
    monkeypatch.setattr(
        bed_service, "count_interval_track_source_rows", recorder("count", lambda: existing["rows"])
    )
    monkeypatch.setattr(bed_service, "delete_interval_tracks", recorder("delete"))
    monkeypatch.setattr(bed_service, "delete_interval_track_sources", recorder("delete sources"))
    monkeypatch.setattr(bed_service, "insert_interval_track_rows", recorder("insert"))
    monkeypatch.setattr(bed_service, "upsert_interval_track_source", recorder("upsert"))

    async def run(kind, row, *, sample_id="S1", overwrite=False, callset="clair3", rows=0):
        events.clear()
        existing["rows"] = rows
        row.repeats = rows
        user = SimpleNamespace(id="admin", username="admin")
        target = SampleMetadataContext(
            sample_uuid=f"uuid-{sample_id}",
            sample_id=sample_id,
            family_uuid="uuid-1",
            family_id="F1",
            sex="female",
            project_ids=["project-1"],
            assembly_id="assembly-1",
            assembly_name="GRCh38",
        )

        async def sample_context(*_args, **_kwargs):
            return target

        for module in (sv_router, trgt_router, bed_router):
            monkeypatch.setattr(module, "build_sample_metadata_context", sample_context)
        if kind == "small_variants":
            glimpse2 = callset == "glimpse2"
            await snv_router.upload_family_small_variants(
                family_id="F1",
                file=_upload_file(
                    "##fileformat=VCFv4.2\n"
                    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\tS2\n"
                    + (
                        "1\t100\t.\tA\tG\t60\tPASS\t.\tGT:GP\t0|1:0.1,0.8,0.1\t0|0:0.9,0.05,0.05\n"
                        if glimpse2
                        else "1\t100\t.\tA\tG\t60\tPASS\t.\tGT\t0/1\t0/0\n"
                    ),
                    "family.vcf",
                ),
                overwrite=overwrite,
                source_format=callset,
                session=row,
                user=user,
            )
        elif kind == "structural_variants":
            await sv_router.upload_structural_variants(
                sample_id=sample_id,
                file=_upload_file("SV1\t1\t10000\t20000\tN\t<DEL>\tDEL\t0/1\n", "calls.tsv"),
                overwrite=overwrite,
                source_format="manual",
                session=row,
                user=user,
            )
        elif kind == "repeats":
            await trgt_router.upload_repeat_expansions(
                sample_id=sample_id,
                file=_upload_file("##fileformat=VCFv4.2\n", "repeats.vcf"),
                overwrite=overwrite,
                session=row,
                user=user,
            )
        else:
            await bed_router.upload_bed(
                sample_id=sample_id,
                bed_type="coverage",
                file=_upload_file("1\t0\t1000\tbin1\t31.0\n", "coverage.bed"),
                overwrite=overwrite,
                session=row,
                user=user,
            )
        return list(events)

    run.events = events  # what the last upload did, also when it raised
    return run


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", sorted(_UPLOADS))
async def test_a_single_file_upload_records_what_it_brought_with_its_commit(uploads, kind) -> None:
    # The family holds a package import's SNV callset. Before (SAFE-19): an upload left
    # imported_scopes as it was, so nothing recorded that the family held its data.
    row = _UploadFamily(imported={"snv": [_SCOPES["snv"]]})

    await uploads(kind, row)

    dataset, scope = _UPLOADS[kind]
    expected = {"snv": [_SCOPES["snv"]], dataset: [scope]}
    if dataset == "snv":  # the upload's callset beside the package's, each scope once
        expected = {"snv": [_SCOPES["snv"], scope]}
    assert row.imported == expected
    # Read under the family row's lock, and written in the transaction that commits what
    # the upload wrote: the record stands or falls with the upload.
    locked = row.index("SELECT metadata -> 'import_unfinished'")
    assert row.executed[locked][0].rstrip().endswith("FOR NO KEY UPDATE")
    assert locked < row.index("UPDATE families") < row.index("COMMIT")
    assert [sql for sql, _ in row.executed].count("COMMIT") == 1
    # An upload's record leaves the flag and the entries as they are.
    assert row.flag is None and row.unfinished is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", sorted(_UPLOADS))
async def test_a_restore_that_fails_part_way_names_what_an_upload_brought(uploads, kind) -> None:
    # The family's data came from an upload; an overwrite of another dataset (coverage)
    # failed, and its restore, which rewrites every table's rows of the family whichever
    # import or upload wrote them, failed part-way.
    row = _UploadFamily()
    await uploads(kind, row)
    mark = _mark(datasets=("coverage",))
    row.unfinished = {mark.key: mark.entry()}

    listed = await fpi._flag_family_restore_failed(
        row,
        _ctx(),
        failed_datasets=["coverage"],
        job_id="job-2",
        mark=mark,
        scopes=mark.scopes,
        family_samples=["S1", "S2"],
    )

    # Before: the entry named the import's coverage alone, and an import of that again
    # opened the gate with the uploaded data gone.
    dataset, scope = _UPLOADS[kind]
    assert listed == sorted({"coverage", dataset})
    assert {"dataset": dataset, "scope": scope} in row.unfinished[mark.key]["whole_family"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", sorted(_UPLOADS))
async def test_an_overwrite_upload_strikes_what_it_brought_again_off_the_whole_family_entry(
    uploads, kind
) -> None:
    dataset, scope = _UPLOADS[kind]
    mark = _mark(datasets=("coverage",))
    entry = registration.whole_family_entry(
        mark, imported={dataset: [scope]}, unfinished={}, family_samples=["S1", "S2"]
    )
    row = _UploadFamily(unfinished={mark.key: copy.deepcopy(entry)}, imported={dataset: [scope]})

    # Without overwrite (nothing was left to replace) it stays listed, as after an update.
    await uploads(kind, row)
    assert row.unfinished == {mark.key: entry}

    # With overwrite it is struck off (before: it stayed listed until a package import
    # brought it again, which none can). The import's own coverage is still to import.
    await uploads(kind, row, overwrite=True, rows=3)
    left = row.unfinished[mark.key]
    assert (left["datasets"], left["finished_datasets"]) == (["coverage"], [])
    assert [item["dataset"] for item in left["whole_family"]] == ["coverage", "coverage"]

    # The coverage imported again with overwrite: the family has been imported again in
    # full, and the entry goes.
    cleared = await fpi._clear_family_import_incomplete(
        row, _ctx(), rewritten={"coverage": _SCOPES["coverage"]}
    )
    assert cleared is not None and cleared.unfinished == {} and row.unfinished is None


@pytest.mark.asyncio
async def test_an_upload_strikes_off_only_the_samples_and_the_callset_it_brought(uploads) -> None:
    mark = _mark(datasets=("coverage",))
    entry = registration.whole_family_entry(
        mark,
        imported={
            "snv": [_SCOPES["snv"], {"source": "clair3", "samples": None}],
            "sv_manual": [_one("S1"), _one("S2")],
            "repeats_trgt": [_one("S1")],
        },
        unfinished={},
        family_samples=["S1", "S2"],
    )
    row = _UploadFamily(unfinished={mark.key: entry})

    await uploads("structural_variants", row, sample_id="S2", overwrite=True)
    await uploads("repeats", row, sample_id="S2", overwrite=True)
    await uploads("small_variants", row, overwrite=True)

    # S2's manual SV calls and the uploaded clair3 callset are struck off. S1's calls, the
    # TRGT calls of S1 and the package's callset (source_format auto: whichever callset it
    # resolved to) are still to bring again.
    assert [item for item in row.unfinished[mark.key]["whole_family"] if item["dataset"] != "coverage"] == [
        {"dataset": "repeats_trgt", "scope": _one("S1")},
        {"dataset": "snv", "scope": _SCOPES["snv"]},
        {"dataset": "sv_manual", "scope": _one("S1")},
    ]
    # Each recorded what it brought.
    assert row.imported["repeats_trgt"] == [_one("S2")]
    assert row.imported["sv_manual"] == [_one("S2")]


@pytest.mark.asyncio
async def test_imputed_genotypes_are_recorded_as_the_haplotypes_a_package_brings(uploads) -> None:
    # A package's haplotypes dataset loads the imputed family VCF through the same loader:
    # an overwrite upload of one brings again what the other brought.
    haplotypes = _SCOPES["haplotypes"]
    mark = _mark(datasets=("coverage",))
    entry = registration.whole_family_entry(
        mark, imported={"haplotypes": [haplotypes]}, unfinished={}, family_samples=["S1", "S2"]
    )
    row = _UploadFamily(unfinished={mark.key: entry})

    events = await uploads("small_variants", row, callset="glimpse2", overwrite=True, rows=3)

    assert "delete haplotypes" in [event[0] for event in events]
    assert row.imported == {"haplotypes": [haplotypes]}
    assert row.unfinished[mark.key]["datasets"] == ["coverage"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", sorted(_UPLOADS))
async def test_each_upload_holds_the_family_write_locks_from_its_first_read(uploads, kind) -> None:
    # A package import holds both of the family's variant-write locks from its snapshot to
    # its restore. An upload that held neither could fall in between: its rows undone by the
    # restore, or struck off a whole-family entry and then removed by it. Before, the TRGT
    # and BED uploads took no lock.
    row = _UploadFamily()

    events = await uploads(kind, row, overwrite=True, rows=3)

    locks = [event for event in events if event[0] == "lock"]
    assert len(locks) == 1, events
    _, types, samples, statements_before = locks[0]
    assert events.index(locks[0]) == 0 and statements_before == 0, "taken before the first read"
    expected = {
        "small_variants": ("small_variants",),
        "structural_variants": ("structural_variants",),
        "repeats": ("small_variants", "structural_variants"),
        "track": ("small_variants", "structural_variants"),
    }
    assert types == expected[kind]
    assert samples == (["uuid-S1", "uuid-S2"] if kind == "small_variants" else ["uuid-S1"])


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", sorted(_UPLOADS))
async def test_an_upload_whose_record_cannot_be_written_fails_and_keeps_none_of_its_rows(
    uploads, kind
) -> None:
    class _RecordFails(_UploadFamily):
        async def execute(self, statement, params=None):
            if "FOR NO KEY UPDATE" in str(statement):
                raise RuntimeError("database unavailable")
            return await super().execute(statement, params)

    row = _RecordFails()

    with pytest.raises(RuntimeError, match="database unavailable"):
        await uploads(kind, row, overwrite=True, rows=3)

    # Nothing committed, and no rows the family does not record: the small-variant upload
    # records once its rows are written and its clean-up removes them again; the others
    # record before their first write.
    assert ("COMMIT", None) not in row.executed
    assert row.imported is None
    writes = [
        (event[0], event[2])
        for event in uploads.events
        if event[0] in {"delete", "insert", "rewrite", "delete sources", "upsert"}
    ]
    if kind == "small_variants":
        assert [name for name, _ in writes] == ["delete", "insert", "delete"]
        assert writes[-1][1] == {"source": "clair3"}
    else:
        assert writes == []
    assert ("INSERT INTO repeat_expansions", {"sample": "S1"}) not in row.executed


def test_each_upload_has_the_dataset_of_the_package_import_that_brings_the_same() -> None:
    assert registration.small_variant_upload_scope("clair3") == ("snv", {"source": "clair3", "samples": None})
    assert registration.small_variant_upload_scope("mito") == ("snv", {"source": "mito", "samples": None})
    assert registration.small_variant_upload_scope("glimpse2") == ("haplotypes", _SCOPES["haplotypes"])
    assert registration.repeat_upload_scope("S1") == ("repeats_trgt", _one("S1"))
    # No package dataset brings these.
    assert registration.structural_variant_upload_scope("sniffles", "S1") == ("sv_sniffles", _one("S1"))
    assert registration.interval_track_upload_scope("apcad_pcf", "S1") == ("bed_apcad_pcf", _one("S1"))
