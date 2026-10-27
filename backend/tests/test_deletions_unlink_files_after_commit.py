"""The admin's sample and family deletes unlink the kept copies of uploaded files only once
they have committed (SAFE-35).

A web upload keeps a copy of its file under ``data/raw_imports`` (a ``managed`` row of
``raw_import_files``, with the file's size and SHA-256), and deleting the sample or the family
deletes that copy. It was unlinked before the deletion committed: a deletion that then failed
(its commit, or a write after the unlink) rolled the rows back, and the records named files
that were gone, a traceability gap that Verify reports as missing. Now the deletion reads the
copies its rows name in its transaction and unlinks them once it has committed. A copy that
cannot be unlinked then is left, a file without a record, never a record without its file: the
deletion stands, and a log line names the record, the file and the error's kind, never its
text.

The deletions run as written against a fake of the request's transaction, which answers the
read of the managed rows and can fail the commit, with the stores' other writes patched out;
the copies are real files under ``tmp_path``. The IDs are synthetic.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.services import admin_service
from backend.app.services import raw_import_files_pg as rif
from backend.tests._deletion_mark_fakes import UnmarkedDeletion


def _place(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


@pytest.fixture()
def copies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """The family's kept copies: one of a family-level upload and one of sample S2's, in the
    family's upload folder under the data directory."""
    data = Path(os.path.realpath(tmp_path)) / "data"
    monkeypatch.setattr(rif, "DATA_DIR", data)
    uploads = data / "raw_imports" / "F1"
    return SimpleNamespace(
        family=_place(uploads / "0a1b2c__family.vcf", "family upload"),
        sample=_place(uploads / "6a7b8c__s2.vcf", "S2 upload"),
    )


class _Transaction:
    """The request's transaction: it answers the read of the managed rows (``(storage_path,
    record id)``, the family's for its uuid, S2's for its uuid) and records every other
    statement; ``failing_commits`` commits fail. ``at_commit`` is which copies were still on
    disk when each commit was asked for."""

    def __init__(self, files: SimpleNamespace, *, failing_commits: int = 0) -> None:
        self.files = files
        self.failing_commits = failing_commits
        self.statements: list[str] = []
        self.at_commit: list[dict[str, bool]] = []
        self.commits = 0

    async def execute(self, statement: Any, params: Any = None) -> Any:
        sql = " ".join(str(statement).split())
        params = dict(params or {})
        if "FROM raw_import_files" in sql:
            rows = [(str(self.files.sample), "rec-s2")]
            if params.get("family_uuid") == "family-uuid":
                rows.append((str(self.files.family), "rec-family"))
            elif params.get("sample_uuid") != "uuid-S2":
                rows = []
            return SimpleNamespace(fetchall=lambda: rows)
        self.statements.append(sql.split(" WHERE ")[0])
        return SimpleNamespace(fetchall=lambda: [], rowcount=0)

    async def commit(self) -> None:
        self.at_commit.append({"family": self.files.family.exists(), "sample": self.files.sample.exists()})
        if self.failing_commits:
            self.failing_commits -= 1
            raise ConnectionError("the commit failed")
        self.commits += 1

    async def rollback(self) -> None:
        return None


@pytest.fixture()
def deletions(monkeypatch: pytest.MonkeyPatch) -> None:
    """The admin's deletes with every write but the transaction's own patched out: no
    ClickHouse data (the family has no assembly), no audit events, no mark."""

    async def sample_row(_session: Any, sample_id: str) -> dict[str, Any]:
        return {"sample_uuid": f"uuid-{sample_id}", "sample_id": sample_id, "family_uuid": "family-uuid", "family_id": "F1"}

    async def family_uuid(_session: Any, _family_id: str) -> str:
        return "family-uuid"

    async def sample_rows(_session: Any, family_uuids: list[str]) -> dict[str, list[Any]]:
        return {uuid: [] for uuid in family_uuids}

    async def nothing(*_args: Any, **_kwargs: Any) -> Any:
        return None

    async def none_counted(*_args: Any, **_kwargs: Any) -> int:
        return 0

    async def no_rows(*_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    async def no_pedigree(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"members": [], "relationships": []}

    for name, fake in {
        "_sample_row_or_404": sample_row,
        "_resolve_family_uuid": family_uuid,
        "_sample_rows_by_family": sample_rows,
        "_family_assembly_contexts": no_rows,
        "lock_family_variant_writes": nothing,
        "lock_family_pedigree": nothing,
        "delete_interval_track_sources": none_counted,
        "_small_variant_entries_without_sample": no_rows,
        "_structural_variant_rows_without_sample": no_rows,
        "record_phenotypes_deleted_with_sample": none_counted,
        "record_phenotypes_deleted_with_family": none_counted,
        "record_family_deleted": nothing,
        "read_structure_snapshot": no_pedigree,
        "record_structure_change": nothing,
        # The mark is test_deletions_mark_the_family.py's (SAFE-31).
        "FamilyDeletion": UnmarkedDeletion,
    }.items():
        monkeypatch.setattr(admin_service, name, fake)


async def _delete_sample(session: _Transaction) -> dict[str, Any]:
    return await admin_service.delete_sample_with_data(session, "S2", True)  # type: ignore[arg-type]


async def _delete_family(session: _Transaction) -> dict[str, Any]:
    return await admin_service.delete_family_with_data(session, "F1", True, reason="duplicate")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_sample_deletion_whose_commit_fails_keeps_the_samples_file(copies, deletions) -> None:
    session = _Transaction(copies, failing_commits=1)

    with pytest.raises(ConnectionError, match="the commit failed"):
        await _delete_sample(session)

    # The rollback keeps S2's record, and the file it names is still there (before: unlinked
    # before the commit, so the record named a file that was gone).
    assert "DELETE FROM samples" in session.statements and session.commits == 0
    assert copies.sample.read_text() == "S2 upload"
    assert copies.family.read_text() == "family upload"


@pytest.mark.asyncio
async def test_a_family_deletion_whose_commit_fails_keeps_the_familys_files(copies, deletions) -> None:
    session = _Transaction(copies, failing_commits=1)

    with pytest.raises(ConnectionError, match="the commit failed"):
        await _delete_family(session)

    # Before: both unlinked, their records rolled back.
    assert "DELETE FROM families" in session.statements and session.commits == 0
    assert (copies.family.read_text(), copies.sample.read_text()) == ("family upload", "S2 upload")


@pytest.mark.asyncio
async def test_a_sample_deletion_unlinks_its_file_once_it_has_committed(copies, deletions) -> None:
    session = _Transaction(copies)

    result = await _delete_sample(session)

    # The file was there when the deletion committed (before: already unlinked), and is gone
    # after; the family's upload stays. The deletion counts what it unlinked.
    assert session.at_commit == [{"family": True, "sample": True}]
    assert not copies.sample.exists() and copies.family.read_text() == "family upload"
    assert result["deleted"]["raw_import_files"] == 1


@pytest.mark.asyncio
async def test_a_family_deletion_unlinks_its_files_once_it_has_committed(copies, deletions) -> None:
    session = _Transaction(copies)

    result = await _delete_family(session)

    assert session.at_commit == [{"family": True, "sample": True}]
    assert not copies.family.exists() and not copies.sample.exists()
    assert result["deleted"]["raw_files"] == 2


@pytest.mark.asyncio
async def test_a_file_that_cannot_be_unlinked_after_the_commit_is_left_and_logged(
    copies, deletions, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    real_unlink = os.unlink
    refused = str(copies.sample)

    def unlink(path: Any, *args: Any, **kwargs: Any) -> None:
        if os.fspath(path) == refused:
            raise PermissionError(13, "Permission denied", refused)
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(rif.os, "unlink", unlink)
    session = _Transaction(copies)

    with caplog.at_level(logging.WARNING, logger=rif.__name__):
        result = await _delete_family(session)

    # The deletion stands: committed, counting the one file it unlinked. The other is left, a
    # file without a record, and said in the log by its record, its path and the error's kind,
    # never the error's text (before: skipped without a word).
    assert session.commits == 1 and result["deleted"]["raw_files"] == 1
    assert not copies.family.exists() and copies.sample.read_text() == "S2 upload"
    messages = [record.getMessage() for record in caplog.records if record.name == rif.__name__]
    assert messages == [
        f"The file of deleted raw-file record rec-s2 could not be unlinked (PermissionError) and is "
        f"left at {refused}"
    ]
    assert "Permission denied" not in messages[0]


@pytest.mark.asyncio
async def test_a_failure_of_the_unlinking_itself_leaves_the_deletion_standing(
    copies, deletions, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def unresolved() -> str:
        raise RuntimeError("the upload folder could not be resolved")

    monkeypatch.setattr(rif, "_managed_root", unresolved)
    session = _Transaction(copies)

    with caplog.at_level(logging.WARNING, logger=rif.__name__):
        result = await _delete_family(session)

    # Committed, and answered: nothing was unlinked, which the log says by the error's kind
    # (before: the error failed the deletion, before its commit).
    assert session.commits == 1 and result["deleted"]["raw_files"] == 0
    assert copies.family.exists() and copies.sample.exists()
    assert [record.getMessage() for record in caplog.records if record.name == rif.__name__] == [
        "The files of deleted raw-file records were not all unlinked (RuntimeError); any not "
        "unlinked are left in the upload folder"
    ]
