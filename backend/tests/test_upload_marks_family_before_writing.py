"""A single-file upload marks the family before its first ClickHouse write (SAFE-28).

ClickHouse has no transactions, and an upload that replaces stored data deletes it before it
writes the file's rows: a process that stops after that delete -- a restart, a crash,
running out of memory -- runs none of the upload's clean-up, and left the family without
its data, or with part of the file's, and nothing marking it. An import marks a family
before its first write (its ``import_unfinished`` entry); an upload did not.

Now the small-variant, structural-variant and interval-track uploads write a mark into
``import_unfinished`` first, under a key of their own (``upload-…``), committed on a
connection of their own: it names what the upload may leave partly written or missing, one
sample at a time. The upload's commit removes it; a failure removes it once nothing is
missing; else it stays (or, for a small-variant upload that replaced its callset, becomes
the flag SAFE-25 writes). It gates sign-out as an import's entry does, and the gate's
message names a single-file upload; each item goes once an upload or an import with
overwrite has brought it again. The import job's claim and its heartbeat read the jobs
alone, so they never take such a mark for a running or stopped import. (The repeat-expansion
upload writes Postgres alone, in the transaction that deletes what it replaces: no mark.)

Against fakes at the stores' edge, as test_small_variant_upload_reads_before_replacing.py:
ClickHouse (written at once, kept whatever becomes of the request) and the family row's
import state in Postgres (a write is the transaction's until it commits; the mark's commits
at once on a connection of its own).
"""

from __future__ import annotations

import copy
from io import BytesIO
import json
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException, UploadFile
import pytest

from backend.app.routers import bed as bed_router
from backend.app.routers import families_small_variants as snv_router
from backend.app.services import (
    annotation_manifest_service,
    bed_service,
    family_package_import,
    family_package_jobs,
    family_package_registration,
    report_signout_service,
    variant_upload_service,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext

_FAMILY = FamilyMetadataContext(
    family_uuid="family-uuid",
    family_id="F1",
    project_ids=["project-1"],
    sample_rows=[
        {"sample_uuid": "uuid-S1", "sample_id": "S1", "sex": "female", "role": "proband"},
        {"sample_uuid": "uuid-S2", "sample_id": "S2", "sex": "male", "role": "sibling"},
    ],
    sample_uuid_to_name={"uuid-S1": "S1", "uuid-S2": "S2"},
    sample_name_to_uuid={"S1": "uuid-S1", "S2": "uuid-S2"},
    affected_sample_names=["S1"],
    assembly_id="assembly-1",
    assembly_name="GRCh38",
)
_S1 = SampleMetadataContext(
    sample_uuid="uuid-S1",
    sample_id="S1",
    family_uuid="family-uuid",
    family_id="F1",
    sex="female",
    project_ids=["project-1"],
    assembly_id="assembly-1",
    assembly_name="GRCh38",
)
_HEADER = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\tS2\n"


def _calls(count: int) -> str:
    return "".join(
        f"1\t{1000 + index * 10}\t.\tA\tG\t50\tPASS\t.\tGT:DP\t0/1:30\t0/0:31\n" for index in range(count)
    )


_CLAIR3 = {"source": "clair3", "samples": None}
_COVERAGE_S1 = {"source": None, "samples": ["S1"]}
# What the coverage BED upload below brings: S1's BED, declared as read depth (TRACE-9).
_DECLARED_COVERAGE_S1 = {**_COVERAGE_S1, "content": "read_depth"}


class _ProcessStopped(BaseException):
    """The process running the upload stops there (a restart, a crash, running out of
    memory): nothing after it runs, neither the rest of the request nor its clean-up."""


class _Postgres:
    """The family row's import state (``flag``: import_incomplete, ``unfinished``:
    import_unfinished, ``imported``: imported_scopes), as the uploads read and write it. A
    write is the transaction's until it commits; a rollback discards it. ``connection()`` is
    the connection an upload writes its mark on: committed at once, and recorded among the
    stores' ``events``."""

    def __init__(self, *, flag: Any = None, unfinished: Any = None, imported: Any = None) -> None:
        self.committed: dict[str, Any] = {"flag": flag, "unfinished": unfinished, "imported": imported}
        self.working = copy.deepcopy(self.committed)
        self.commits = 0
        self.events: list[tuple[Any, ...]] = []
        self.unavailable: str | None = None  # SQL that fails, as a database that went away

    @staticmethod
    def _apply(state: dict[str, Any], params: dict[str, Any]) -> None:
        if "entry" in params:
            state["unfinished"] = {**(state["unfinished"] or {}), params["import_key"]: json.loads(params["entry"])}
            return
        if set(params) == {"family_uuid", "import_key"}:  # an entry removed alone
            left = dict(state["unfinished"] or {})
            left.pop(params["import_key"], None)
            state["unfinished"] = left or None
            return
        recorded = json.loads(params.get("imported_scopes", "null"))
        if recorded is not None:
            state["imported"] = recorded
        if "unfinished" in params:
            state["unfinished"] = json.loads(params["unfinished"])
        if "flag" in params:
            state["flag"] = json.loads(params["flag"])

    async def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        if self.unavailable and self.unavailable in sql:
            raise ConnectionError("database unavailable")
        if sql.startswith("SELECT") and "FROM families" in sql:
            row = {
                "family_uuid": "family-uuid",
                "flag": copy.deepcopy(self.working["flag"]),
                "unfinished": copy.deepcopy(self.working["unfinished"]),
                "imported_scopes": copy.deepcopy(self.working["imported"]),
            }
            return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: row))
        assert sql.startswith("UPDATE families"), sql
        self._apply(self.working, dict(params or {}))
        return SimpleNamespace(rowcount=1)

    async def commit(self) -> None:
        self.committed = copy.deepcopy(self.working)
        self.commits += 1

    async def rollback(self) -> None:
        self.working = copy.deepcopy(self.committed)

    def connection(self):
        database = self

        class _Connection:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc) -> bool:
                return False

            async def execute(self, statement, params=None):
                assert " ".join(str(statement).split()).startswith("UPDATE families")
                for state in (database.committed, database.working):
                    database._apply(state, dict(params or {}))
                database.events.append(("mark",))
                return SimpleNamespace(rowcount=1)

            async def commit(self) -> None:
                return None

        return _Connection()

    def mark(self) -> tuple[str, dict[str, Any]]:
        """The one committed entry: an upload's mark."""
        [(key, entry)] = (self.committed["unfinished"] or {}).items()
        return key, entry


class _ClickHouse:
    """The family's small-variant rows by callset and the sample's coverage track, written
    at once and kept whatever becomes of the request. With ``stop`` the next insert stops
    the process; with ``fail`` it raises."""

    def __init__(self, events: list[tuple[Any, ...]], *, clair3: int = 2, coverage: int = 3) -> None:
        self.events = events
        self.rows = {"clair3": [f"stored-{index}" for index in range(clair3)]}
        self.coverage = [f"bin-{index}" for index in range(coverage)]
        self.stop = False
        self.fail = False

    def _insert(self) -> None:
        if self.stop:
            raise _ProcessStopped()
        if self.fail:
            raise RuntimeError("ClickHouse insert failed")

    async def count(self, _assembly, _family, *, project_ids=None, source=None) -> int:
        return len(self.rows.get(source, []))

    async def delete(self, _assembly, _family, *, source=None) -> None:
        self.events.append(("delete", source))
        self.rows[source] = []

    async def insert(self, _assembly, _family, _projects, records, **_kwargs) -> None:
        self._insert()
        self.events.append(("insert", len(records)))
        for record in records:
            self.rows.setdefault(record.source, []).append(record.variant_id)

    async def count_track(self, *_args, **_kwargs) -> int:
        return len(self.coverage)

    async def delete_track(self, *_args, **_kwargs) -> None:
        self.events.append(("delete track",))
        self.coverage = []

    async def insert_track(self, _assembly, rows) -> None:
        self._insert()
        self.events.append(("insert track", len(rows)))
        self.coverage.extend(str(row["start"]) for row in rows)


@pytest.fixture
def stores(monkeypatch):
    """``stores(database)``: the stores the uploads write, faked at their edge."""

    def install(database: _Postgres, **stored: int) -> _ClickHouse:
        clickhouse = _ClickHouse(database.events, **stored)

        async def nothing(*_args, **_kwargs):
            return None

        async def no_blocks(*_args, **_kwargs):
            return {}

        async def family_context(*_args, **_kwargs):
            return _FAMILY

        async def sample_context(*_args, **_kwargs):
            return _S1

        for name, value in {
            "lock_family_variant_writes": nothing,
            "count_family_small_variants": clickhouse.count,
            "delete_family_small_variants": clickhouse.delete,
            "insert_small_variant_records": clickhouse.insert,
            "refresh_family_small_variant_summaries": nothing,
            "get_track_presence_by_sample": no_blocks,
            "_fetch_chromosome_sizes": no_blocks,
        }.items():
            monkeypatch.setattr(variant_upload_service, name, value)
        for name, value in {
            "count_interval_track_rows": clickhouse.count_track,
            "delete_interval_tracks": clickhouse.delete_track,
            "delete_interval_track_sources": nothing,
            "insert_interval_track_rows": clickhouse.insert_track,
            "upsert_interval_track_source": nothing,
        }.items():
            monkeypatch.setattr(bed_service, name, value)
        monkeypatch.setattr(annotation_manifest_service, "merge_vcf_header_provenance", nothing)
        monkeypatch.setattr(snv_router, "build_family_metadata_context", family_context)
        monkeypatch.setattr(snv_router, "record_upload_file_obj", nothing)
        monkeypatch.setattr(bed_router, "build_sample_metadata_context", sample_context)
        monkeypatch.setattr(bed_router, "lock_family_variant_writes", nothing)
        monkeypatch.setattr(bed_router, "record_upload_file_obj", nothing)
        monkeypatch.setattr(
            family_package_registration, "get_postgres_sessionmaker", lambda: database.connection, raising=False
        )
        return clickhouse

    return install


async def _ends_request(database: _Postgres, upload) -> Any:
    """Run ``upload``; the request's session ends with it: what it did not commit is
    rolled back."""
    try:
        return await upload
    finally:
        await database.rollback()


async def _small_variants(database: _Postgres, body: str, *, overwrite: bool = True):
    return await _ends_request(
        database,
        snv_router.upload_family_small_variants(
            family_id="F1",
            file=UploadFile(file=BytesIO(body.encode()), filename="family.vcf"),
            overwrite=overwrite,
            source_format="clair3",
            session=database,  # type: ignore[arg-type]
            user=SimpleNamespace(id="admin", username="admin"),
        ),
    )


async def _coverage(database: _Postgres, *, overwrite: bool = True, body: str = "1\t0\t1000\tbin1\t31.0\n"):
    return await _ends_request(
        database,
        bed_router.upload_bed(
            sample_id="S1",
            bed_type="coverage",
            file=UploadFile(file=BytesIO(body.encode()), filename="coverage.bed"),
            overwrite=overwrite,
            content="read_depth",
            session=database,  # type: ignore[arg-type]
            user=SimpleNamespace(id="admin", username="admin"),
        ),
    )


# --- the small-variant upload --------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_small_variant_upload_whose_process_stops_after_its_delete_leaves_the_family_marked(stores) -> None:
    database = _Postgres()
    clickhouse = stores(database)
    clickhouse.stop = True

    with pytest.raises(_ProcessStopped):
        await _small_variants(database, _HEADER + _calls(3))

    # The callset is gone, and nothing of the upload's own transaction committed. Before:
    # nothing marked the family, which read as complete without its callset.
    assert clickhouse.rows["clair3"] == [] and database.commits == 0
    key, mark = database.mark()
    assert key.startswith("upload-")
    assert (mark["job_id"], mark["datasets"], mark["finished_datasets"]) == (None, ["snv"], [])
    assert mark["whole_family"] == [{"dataset": "snv", "scope": _CLAIR3}]
    # Written after the file was checked whole, before the delete.
    assert database.events == [("mark",), ("delete", "clair3")]


@pytest.mark.asyncio
async def test_a_small_variant_upload_with_nothing_stored_marks_the_family_before_its_first_insert(stores) -> None:
    database = _Postgres()
    clickhouse = stores(database, clair3=0)
    clickhouse.stop = True

    with pytest.raises(_ProcessStopped):
        await _small_variants(database, _HEADER + _calls(3), overwrite=False)

    # What it was writing may be partly written.
    assert database.mark()[1]["whole_family"] == [{"dataset": "snv", "scope": _CLAIR3}]


@pytest.mark.asyncio
async def test_a_small_variant_upload_that_completes_removes_its_mark_with_its_commit(stores) -> None:
    database = _Postgres()
    clickhouse = stores(database)

    result = await _small_variants(database, _HEADER + _calls(3))

    assert result.inserted == 3
    assert database.events == [("mark",), ("delete", "clair3"), ("insert", 3)]
    assert database.commits == 1
    assert database.committed["unfinished"] is None
    assert database.committed["imported"] == {"snv": [_CLAIR3]}
    assert len(clickhouse.rows["clair3"]) == 3


@pytest.mark.asyncio
async def test_a_small_variant_upload_the_check_refuses_writes_no_mark(stores) -> None:
    database = _Postgres()
    stores(database)

    with pytest.raises(HTTPException):
        await _small_variants(database, _HEADER.replace("\tS2\n", "\tS9\n") + _calls(3))

    assert database.events == [] and database.committed["unfinished"] is None


@pytest.mark.asyncio
async def test_a_small_variant_upload_that_cannot_mark_the_family_writes_nothing(stores, monkeypatch) -> None:
    database = _Postgres()
    clickhouse = stores(database)

    def unavailable():
        raise ConnectionError("database unavailable")

    monkeypatch.setattr(family_package_registration, "get_postgres_sessionmaker", unavailable, raising=False)

    with pytest.raises(ConnectionError):
        await _small_variants(database, _HEADER + _calls(3))

    assert database.events == [] and len(clickhouse.rows["clair3"]) == 2


# --- the interval-track (BED) upload ------------------------------------------------------


@pytest.mark.asyncio
async def test_a_bed_upload_whose_process_stops_after_its_delete_leaves_the_family_marked(stores) -> None:
    database = _Postgres()
    clickhouse = stores(database)
    clickhouse.stop = True

    with pytest.raises(_ProcessStopped):
        await _coverage(database)

    assert clickhouse.coverage == [] and database.commits == 0
    key, mark = database.mark()
    assert key.startswith("upload-")
    assert mark["datasets"] == ["bed_coverage"]
    assert mark["whole_family"] == [{"dataset": "bed_coverage", "scope": _DECLARED_COVERAGE_S1}]
    assert database.events == [("mark",), ("delete track",)]


@pytest.mark.asyncio
async def test_a_bed_upload_that_fails_after_its_delete_leaves_the_family_marked(stores) -> None:
    database = _Postgres()
    clickhouse = stores(database)
    clickhouse.fail = True

    with pytest.raises(RuntimeError, match="ClickHouse insert failed"):
        await _coverage(database)

    # The sample's track is gone: before, nothing marked the family.
    assert clickhouse.coverage == []
    assert database.mark()[1]["whole_family"] == [{"dataset": "bed_coverage", "scope": _DECLARED_COVERAGE_S1}]


@pytest.mark.asyncio
async def test_a_bed_upload_that_fails_before_its_first_write_removes_its_mark(stores) -> None:
    database = _Postgres()
    clickhouse = stores(database)
    # The record of what the upload brings, written before its first write, fails.
    database.unavailable = "FOR NO KEY UPDATE"

    with pytest.raises(ConnectionError):
        await _coverage(database)

    assert len(clickhouse.coverage) == 3
    assert database.committed["unfinished"] is None
    assert database.events == [("mark",)]


@pytest.mark.asyncio
async def test_a_bed_upload_that_completes_removes_its_mark_and_an_upload_again_strikes_an_old_one(stores) -> None:
    database = _Postgres()
    clickhouse = stores(database)
    clickhouse.stop = True
    with pytest.raises(_ProcessStopped):
        await _coverage(database)
    clickhouse.stop = False

    # Nothing of the track is stored now, but only an upload with overwrite strikes the
    # mark: one without removes nothing, as an import's update removes nothing.
    await _coverage(database, overwrite=False)
    assert database.mark()[1]["datasets"] == ["bed_coverage"]
    await _coverage(database, overwrite=True)

    assert database.committed["unfinished"] is None
    assert clickhouse.coverage == ["0"]


# --- what reads the mark ---------------------------------------------------------------------


_UPLOAD_KEY = "upload-0a1b2c3d4e5f60718293a4b5c6d7e8f9"
_DELETION_KEY = "deletion-9f8e7d6c5b4a39281706f5e4d3c2b1a0"


def _frozen(at: str, datasets: list[str]) -> dict[str, Any]:
    """An entry as the sign-out freezes it (``_import_unfinished_state``)."""
    return {"job_id": None, "at": at, "datasets": datasets, "finished_datasets": []}


def test_the_gate_message_names_a_single_file_upload_that_did_not_finish() -> None:
    message = report_signout_service._import_gate_message(
        None, {_UPLOAD_KEY: _frozen("2026-10-14T08:30:00+00:00", ["sv_sniffles"])}
    )

    assert message == (
        "This family's data is incomplete: a single-file upload (2026-10-14T08:30:00+00:00) did "
        "not finish, so sv_sniffles may be partly written or missing. Upload or import again, "
        "with overwrite, what a single-file upload or a deletion did not finish, or acknowledge "
        "with a reason to sign out anyway."
    )


def test_the_gate_message_names_a_deletion_and_an_import_apart() -> None:
    message = report_signout_service._import_gate_message(
        None,
        {
            "job-1": {
                "job_id": "job-1",
                "at": "2026-10-13T09:00:00+00:00",
                "datasets": ["coverage"],
                "finished_datasets": [],
            },
            _DELETION_KEY: _frozen("2026-10-14T09:00:00+00:00", ["cnv", "sv_needlr"]),
        },
    )

    assert "an import (2026-10-13T09:00:00+00:00) stopped before it finished, so coverage" in message
    assert (
        "Also, a deletion of a sample's data (2026-10-14T09:00:00+00:00) did not finish, so cnv, "
        "sv_needlr may be partly written or missing." in message
    )
    assert (
        "Import again, with overwrite, what an import that stopped did not finish (an update keeps "
        "the data already there). Upload or import again, with overwrite, what a single-file upload "
        "or a deletion did not finish, or acknowledge" in message
    )


@pytest.mark.asyncio
async def test_the_sign_out_freezes_an_uploads_mark_as_an_unfinished_entry() -> None:
    mark = family_package_registration.FamilyWriteMark.begin(
        family_package_registration.UPLOAD_MARK_PREFIX, [("sv_sniffles", {"source": None, "samples": ["S1", "S2"]})]
    )

    class _Session:
        async def execute(self, *_args, **_kwargs):
            return SimpleNamespace(scalar_one_or_none=lambda: {mark.key: mark.entry()})

    state = await report_signout_service._import_unfinished_state(_Session(), "family-uuid")  # type: ignore[arg-type]

    # The snapshot's shape, its key kept: what wrote the entry is read from it again.
    assert state == {mark.key: {"job_id": None, "at": mark.at, "datasets": ["sv_sniffles"], "finished_datasets": []}}
    assert family_package_registration.unfinished_entry_writer(mark.key) == "upload"


@pytest.mark.asyncio
async def test_an_import_that_brings_an_uploads_data_again_strikes_it_off_the_mark_and_says_so() -> None:
    mark = family_package_registration.FamilyWriteMark.begin(
        family_package_registration.UPLOAD_MARK_PREFIX, [("snv", _CLAIR3), ("bed_coverage", _COVERAGE_S1)]
    )
    database = _Postgres(unfinished={mark.key: mark.entry()})
    context = SimpleNamespace(family_uuid="family-uuid", family_id="F1")

    left = await family_package_registration._clear_family_import_incomplete(
        database,  # type: ignore[arg-type]
        context,  # type: ignore[arg-type]
        import_key="job-2",
        rewritten={"snv": _CLAIR3},
        imported={"snv": _CLAIR3},
    )

    # The callset is back; the track only an upload of it brings again stays on the mark.
    assert left is not None
    assert left.unfinished[mark.key]["whole_family"] == [{"dataset": "bed_coverage", "scope": _COVERAGE_S1}]
    # The import's log names the upload, not an import's restore.
    message = family_package_import._still_unwritten_message("F1", left.unfinished)
    assert message.startswith(
        "Family F1 stays marked import-incomplete: an earlier write of its data did not finish "
        "(a single-file upload, still to bring again: bed_coverage)"
    )


@pytest.mark.asyncio
async def test_the_job_claim_reads_the_jobs_alone_and_never_an_uploads_mark() -> None:
    # A mark carries no job and a key no job has; the claim, which finds a stopped import by
    # its job's heartbeat, reads family_import_jobs alone.
    statements: list[str] = []

    class _Session:
        async def execute(self, statement, _params=None):
            statements.append(" ".join(str(statement).split()))
            return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: None))

        async def rollback(self) -> None:
            return None

    claimed = await family_package_jobs.claim_next_family_import_job(_Session(), worker_id="w1")  # type: ignore[arg-type]

    assert claimed is None
    [sql] = statements
    assert "family_import_jobs" in sql
    assert "import_unfinished" not in sql and " families " not in f"{sql} "
