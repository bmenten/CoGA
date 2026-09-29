"""Compensation/atomicity helpers for failed family-package imports (issue #334).

Covers the fail-clean plumbing without a live database:
  * `_delete_family_shell` also clears orphan ClickHouse interval-track rows.
  * `_flag_family_import_incomplete` stamps a failed update/overwrite as degraded.
  * `_clear_family_import_incomplete` drops the flag after a clean re-import.
"""

import json
from types import SimpleNamespace

import pytest

from app.services import family_package_import as fpi


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
async def test_clear_family_import_incomplete_drops_the_flag() -> None:
    session = _FakeSession()
    await fpi._clear_family_import_incomplete(session, _ctx())
    sql, _ = session.executed[0]
    assert "metadata - 'import_incomplete'" in sql
