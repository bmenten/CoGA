"""Compensation/atomicity helpers for failed family-package imports (issue #334).

Covers the fail-clean plumbing without a live database:
  * `_delete_family_shell` also clears orphan ClickHouse interval-track rows.
  * `_flag_family_import_incomplete` stamps a failed update/overwrite as degraded, and
    removes the import's own `import_unfinished` entry in the same statement.
  * `_clear_family_import_incomplete` drops the flag after a clean re-import, with the
    import's own entry and each earlier import's entry it completed; an entry it did not
    complete (an update, or an overwrite without that dataset) stays.
The SQL itself runs against Postgres in
integration/test_import_crash_bookkeeping_integration.py.
"""

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


@pytest.mark.asyncio
async def test_flag_family_import_incomplete_records_datasets() -> None:
    session = _FakeSession()
    await fpi._flag_family_import_incomplete(
        session,
        _ctx(),
        failed_datasets=["snv"],
        imported_datasets=["sv_needlr"],
    )
    sql, params = session.executed[0]
    assert "import_incomplete" in sql
    payload = json.loads(params["payload"])
    assert payload["failed_datasets"] == ["snv"]
    assert payload["imported_datasets"] == ["sv_needlr"]
    assert "at" in payload


@pytest.mark.asyncio
async def test_flag_family_import_incomplete_records_the_import_job() -> None:
    # The flag names the job that holds each dataset's error; it never copies the error
    # texts, which can carry file paths.
    session = _FakeSession()
    await fpi._flag_family_import_incomplete(
        session, _ctx(), failed_datasets=["snv"], imported_datasets=[], job_id="job-uuid"
    )
    payload = json.loads(session.executed[0][1]["payload"])
    assert payload["job_id"] == "job-uuid"
    assert set(payload) == {"at", "failed_datasets", "imported_datasets", "job_id"}

    # An import run outside a job records none.
    session = _FakeSession()
    await fpi._flag_family_import_incomplete(
        session, _ctx(), failed_datasets=["snv"], imported_datasets=[]
    )
    assert json.loads(session.executed[0][1]["payload"])["job_id"] is None


@pytest.mark.asyncio
async def test_a_failed_import_removes_its_own_unfinished_entry_with_the_flag() -> None:
    # One statement: the family is never left with neither the flag nor the entry.
    session = _FakeSession()
    await fpi._flag_family_import_incomplete(
        session, _ctx(), failed_datasets=["snv"], imported_datasets=[], job_id="job-1", import_key="job-1"
    )
    assert len(session.executed) == 1
    sql, params = session.executed[0]
    assert "import_incomplete" in sql and "import_unfinished" in sql
    assert params["import_key"] == "job-1"


class _UnfinishedSession(_FakeSession):
    """Answers the locked read of `import_unfinished` with ``stored``."""

    def __init__(self, stored) -> None:
        super().__init__()
        self.stored = stored

    async def execute(self, statement, params=None):
        await super().execute(statement, params)
        stored = self.stored
        return SimpleNamespace(scalar_one_or_none=lambda: stored)

    def written(self) -> dict:
        updates = [params for sql, params in self.executed if "UPDATE families" in sql]
        assert len(updates) == 1
        return json.loads(updates[0]["remaining"])


def _entry(job_id, datasets, finished=()):
    return {
        "job_id": job_id,
        "at": "2026-10-02T10:00:00+00:00",
        "datasets": list(datasets),
        "finished_datasets": list(finished),
    }


@pytest.mark.asyncio
async def test_clear_family_import_incomplete_drops_the_flag() -> None:
    session = _UnfinishedSession(None)
    assert await fpi._clear_family_import_incomplete(session, _ctx()) == {}
    select_sql, _ = session.executed[0]
    assert "FOR UPDATE" in select_sql  # read under the row's lock
    update_sql, params = session.executed[1]
    assert "- 'import_incomplete'" in update_sql
    assert json.loads(params["remaining"]) == {}


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
    session = _UnfinishedSession(stored)
    remaining = await fpi._clear_family_import_incomplete(
        session, _ctx(), import_key="job-own", rewritten=["snv", "coverage"]
    )
    assert set(remaining) == {"job-other", "unreadable"}
    assert session.written() == {"job-other": stored["job-other"], "unreadable": "garbage"}


@pytest.mark.asyncio
async def test_an_update_completes_no_earlier_imports_partial_data() -> None:
    # An update skips a dataset that already holds data -- partly written data too -- so
    # it replaces nothing: an entry with anything pending stays.
    stored = {
        "job-own": _entry("job-own", ["snv"]),
        "job-stopped": _entry("job-stopped", ["snv", "coverage"], finished=["coverage"]),
    }
    session = _UnfinishedSession(stored)
    remaining = await fpi._clear_family_import_incomplete(
        session, _ctx(), import_key="job-own", rewritten=[]
    )
    assert set(remaining) == {"job-stopped"}


@pytest.mark.asyncio
async def test_a_clear_that_fails_leaves_the_marks_and_says_so() -> None:
    class _Failing(_FakeSession):
        async def execute(self, statement, params=None):
            raise RuntimeError("database unavailable")

    assert await fpi._clear_family_import_incomplete(_Failing(), _ctx(), import_key="job-1") is None


def test_pending_datasets_are_those_an_import_had_not_finished() -> None:
    assert registration.pending_datasets(_entry("j", ["snv", "sv"], ["snv"])) == {"sv"}
    assert registration.pending_datasets(_entry("j", ["snv"], ["snv"])) == set()
    # Not in the shape an import writes: unknown, never "nothing pending".
    assert registration.pending_datasets("garbage") is None
    assert registration.pending_datasets({"datasets": ["snv"]}) is None
    assert registration.pending_datasets({"datasets": "snv", "finished_datasets": []}) is None


def test_an_import_mark_is_keyed_by_its_job_or_its_own_run() -> None:
    mark = registration.ImportMark.begin(job_id="job-1", datasets=["sv", "snv", "sv"])
    assert mark.key == "job-1"
    assert mark.entry() == {
        "job_id": "job-1",
        "at": mark.at,
        "datasets": ["snv", "sv"],
        "finished_datasets": [],
    }
    assert mark.entry(["sv"])["finished_datasets"] == ["sv"]
    outside = registration.ImportMark.begin(job_id=None, datasets=[])
    assert outside.key.startswith("run-") and outside.job_id is None
