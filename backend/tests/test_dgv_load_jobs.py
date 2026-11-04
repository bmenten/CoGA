"""Loads of the pinned DGV release (``dgv_load_jobs``): the file read, checked and committed whole.

The DGV variants were loaded only by hand (``scripts/import_dgv.py``, or an upload too small for
DGV's file). A load now streams the release pinned per assembly (``DGV_PINNED_RELEASES``) from
DGV, inserts its rows in one transaction and commits only once the whole file has been read and
is the pinned one by size and SHA-256 (TRACE-14); anything else commits nothing. A download that
stalls or drops is resumed from the byte it reached. The job keeps the administrator who asked
for it and runs, and commits, only while that account is an active administrator (SEC-35); a load
a start queues is no account's. The database, DGV and the background task are stood in for here;
``integration/test_dgv_load_jobs_postgres.py`` runs the load against real Postgres.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import date, datetime, timezone
from typing import Any

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from backend.app.core import config
from backend.app.core.config import DgvPinnedRelease
from backend.app.services import dgv_load_jobs as dgv
from backend.app.services.access_control import CurrentUser

NOW = datetime(2026, 11, 4, tzinfo=timezone.utc)
REQUESTER_ID = "00000000-0000-0000-0000-0000000000d2"
URL = "https://dgv.example/dgv/docs/GRCh38_hg38_variants_2025-12-01.txt"
_HEADER = (
    "variantaccession\tchr\tstart\tend\tvarianttype\tvariantsubtype\treference\tpubmedid\tmethod\t"
    "platform\tmergedvariants\tsupportingvariants\tmergedorsample\tfrequency\tsamplesize\t"
    "observedgains\tobservedlosses\tcohortdescription\tgenes\tsamples"
)


def _dgv_file(rows: int = 25) -> bytes:
    """A synthetic DGV variants file in DGV's layout: the header, then ``rows`` variants."""
    lines = [_HEADER]
    for index in range(rows):
        subtype = ("duplication", "deletion", "gain+loss")[index % 3]
        lines.append(
            f"nsv{index:07d}\t{1 + index % 22}\t{10_000 + index * 1000}\t{12_000 + index * 1000}\tCNV\t"
            f"{subtype}\tStudy_{index % 4}\t1\tSequencing\t\t\tnssv{index}\tM\t\t3202\t1\t0\t\t\"\"\t"
        )
    return ("\n".join(lines) + "\n").encode()


def _pin(payload: bytes, url: str = URL) -> DgvPinnedRelease:
    return DgvPinnedRelease(
        release="2025-12-01", url=url, sha256=hashlib.sha256(payload).hexdigest(), size_bytes=len(payload)
    )


def _account(*, is_active: bool = True, role: str = "admin") -> CurrentUser:
    return CurrentUser(
        id=REQUESTER_ID,
        username="lab.admin",
        email="lab.admin@example.org",
        role=role,
        is_active=is_active,
        created_at=NOW,
    )


# --- the pins --------------------------------------------------------------------------------------


def test_each_pinned_release_names_its_file_its_sha256_and_its_size() -> None:
    assert set(config.DGV_PINNED_RELEASES) == {"GRCh37", "GRCh38"}
    for assembly, pin in config.DGV_PINNED_RELEASES.items():
        assert pin.url.startswith("https://dgv.tcag.ca/dgv/docs/"), assembly
        assert pin.url.endswith(f"_variants_{pin.release}.txt"), assembly
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", pin.release)
        assert re.fullmatch(r"[0-9a-f]{64}", pin.sha256), assembly
        assert pin.size_bytes > 100_000_000, assembly
    assert "GRCh38_hg38" in config.DGV_PINNED_RELEASES["GRCh38"].url
    assert "GRCh37_hg19" in config.DGV_PINNED_RELEASES["GRCh37"].url


def test_the_recorded_release_names_the_release_the_file_and_its_sha256() -> None:
    pin = _pin(b"x")
    assert dgv.recorded_release(pin, "ab" * 32) == (
        f"2025-12-01 (GRCh38_hg38_variants_2025-12-01.txt, SHA-256 {'ab' * 32})"
    )


# --- reading the file ------------------------------------------------------------------------------


def test_the_header_names_the_columns_and_comments_are_skipped() -> None:
    lines = ["#comment", _HEADER, _dgv_file(2).decode().splitlines()[1], "track name=dgv", ""]
    rows, header = dgv.parse_dgv_lines(lines, header_index=None, assembly_id="asm-1")

    assert header is not None and header["variantsubtype"] == 5
    [row] = rows
    assert (row["accession"], row["chr"], row["start"], row["end"]) == ("nsv0000000", "1", 10_000, 12_000)
    assert (row["variant_class"], row["sample_size"], row["source"]) == ("gain", 3202, "Study_0")


class _InsertSession:
    def __init__(self) -> None:
        self.inserted: list[dict[str, Any]] = []


@pytest.fixture()
def inserts(monkeypatch: pytest.MonkeyPatch) -> list[list[dict[str, Any]]]:
    batches: list[list[dict[str, Any]]] = []

    async def insert_batch(_session: Any, rows: list[dict[str, Any]]) -> None:
        batches.append(list(rows))

    monkeypatch.setattr(dgv, "insert_dgv_batch", insert_batch)
    monkeypatch.setattr(dgv, "_DGV_INSERT_CHUNK", 7)
    monkeypatch.setattr(dgv, "_PARSE_BLOCK", 300)  # parsed part by part as it arrives
    return batches


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), event_hooks={"request": [dgv._https_only]}
    )


async def _no_progress(*_args: int) -> None:
    return None


def _load(payload_pin: DgvPinnedRelease, handler) -> tuple[int, str]:
    async def run() -> tuple[int, str]:
        async with _client(handler) as client:
            return await dgv.load_dgv_release(
                object(), assembly_id="asm-1", pin=payload_pin, progress=_no_progress, client=client
            )

    return asyncio.run(run())


def test_the_pinned_file_is_inserted_in_batches_and_its_sha256_returned(inserts: list) -> None:
    payload = _dgv_file(25)
    pin = _pin(payload)

    inserted, digest = _load(pin, lambda request: httpx.Response(200, content=payload))

    assert (inserted, digest) == (25, pin.sha256)
    assert [len(batch) for batch in inserts] == [7, 7, 7, 4]
    assert inserts[0][0]["assembly_id"] == "asm-1"


def test_another_file_is_refused_once_read_whole(inserts: list) -> None:
    # The rows already sent stay in the caller's transaction, which it does not commit.
    payload = _dgv_file(25)
    pin = _pin(payload)._replace(sha256="0" * 64)

    with pytest.raises(dgv.DgvFileRefused, match="not the pinned release 2025-12-01") as refused:
        _load(pin, lambda request: httpx.Response(200, content=payload))
    assert "Nothing was loaded" in str(refused.value)


def test_a_file_larger_than_the_pin_is_refused_as_it_arrives(inserts: list) -> None:
    payload = _dgv_file(25)
    pin = _pin(payload)._replace(size_bytes=len(payload) - 1)

    with pytest.raises(dgv.DgvFileRefused, match="larger than the pinned release"):
        _load(pin, lambda request: httpx.Response(200, content=payload))


def test_a_file_without_dgv_rows_is_refused(inserts: list) -> None:
    payload = (_HEADER + "\n").encode()

    with pytest.raises(dgv.DgvFileRefused, match="holds no DGV variant rows"):
        _load(_pin(payload), lambda request: httpx.Response(200, content=payload))
    assert inserts == []


def test_dgv_is_read_over_https_only(inserts: list) -> None:
    payload = _dgv_file(3)

    with pytest.raises(dgv.DgvFileRefused, match="non-HTTPS"):
        _load(_pin(payload, url="http://dgv.example/x_variants_2025-12-01.txt"), lambda r: httpx.Response(200, content=payload))

    def redirect_to_http(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://dgv.example/elsewhere.txt"})

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(redirect_to_http),
            follow_redirects=True,
            event_hooks={"request": [dgv._https_only]},
        ) as client:
            await dgv.load_dgv_release(object(), assembly_id="asm-1", pin=_pin(payload), progress=_no_progress, client=client)

    with pytest.raises(dgv.DgvFileRefused, match="non-HTTPS"):
        asyncio.run(run())


class _StallingStream(httpx.AsyncByteStream):
    """Serves the first part of the file, then stalls (a read timeout)."""

    def __init__(self, part: bytes) -> None:
        self.part = part

    async def __aiter__(self):
        yield self.part
        raise httpx.ReadTimeout("stalled")


def test_a_stalled_download_is_resumed_from_the_byte_it_reached(inserts: list) -> None:
    payload = _dgv_file(25)
    cut = len(payload) // 2 + 3  # inside a line
    asked: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.headers.get("range"))
        if "range" not in request.headers:
            return httpx.Response(200, stream=_StallingStream(payload[:cut]))
        assert request.headers["range"] == f"bytes={cut}-"
        return httpx.Response(
            206, content=payload[cut:], headers={"content-range": f"bytes {cut}-{len(payload) - 1}/{len(payload)}"}
        )

    inserted, digest = _load(_pin(payload), handler)

    assert asked == [None, f"bytes={cut}-"]
    assert (inserted, digest) == (25, hashlib.sha256(payload).hexdigest())
    accessions = [row["accession"] for batch in inserts for row in batch]
    assert accessions == [f"nsv{index:07d}" for index in range(25)]  # no line lost or doubled


def test_a_connection_dropped_after_the_last_byte_needs_no_resume(inserts: list) -> None:
    payload = _dgv_file(25)
    asked: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.headers.get("range"))
        return httpx.Response(200, stream=_StallingStream(payload))  # the whole file, then a stall

    inserted, digest = _load(_pin(payload), handler)

    assert asked == [None]  # no range request past the end, which DGV would answer 416
    assert (inserted, digest) == (25, hashlib.sha256(payload).hexdigest())


def test_a_download_the_server_restarts_from_the_beginning_is_refused(inserts: list) -> None:
    payload = _dgv_file(25)
    cut = len(payload) // 2

    def handler(request: httpx.Request) -> httpx.Response:
        if "range" not in request.headers:
            return httpx.Response(200, stream=_StallingStream(payload[:cut]))
        return httpx.Response(200, content=payload)  # the whole file again, not the rest

    with pytest.raises(dgv.DgvFileRefused, match=f"did not resume the download .* at byte {cut}"):
        _load(_pin(payload), handler)


def test_a_download_that_keeps_stalling_gives_up(inserts: list) -> None:
    payload = _dgv_file(25)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("unreachable")

    with pytest.raises(httpx.ConnectTimeout):
        _load(_pin(payload), handler)


# --- queueing ------------------------------------------------------------------------------------


def _job_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "id": "job-1",
        "assembly_id": "asm-1",
        "assembly_name": "GRCh38",
        "release": "2025-12-01",
        "source_url": config.DGV_PINNED_RELEASES["GRCh38"].url,
        "status": "queued",
        "requested_by": "lab.admin@example.org",
        "requested_at": NOW,
        "started_at": None,
        "completed_at": None,
        "bytes_read": 0,
        "bytes_total": None,
        "inserted": 0,
        "error": None,
    }
    row.update(overrides)
    return row


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def mappings(self) -> "_Result":
        return self

    def all(self) -> list[Any]:
        return self._rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalar_one(self) -> Any:
        return self._rows[0]


class _Session:
    def __init__(self, *, assembly: dict | None = None, insert_error: Exception | None = None, jobs=None) -> None:
        self.assembly, self.insert_error, self.jobs = assembly, insert_error, jobs or []
        self.statements: list[tuple[str, dict]] = []
        self.committed = self.rolled_back = False

    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        sql = " ".join(str(statement).split())
        self.statements.append((sql, params or {}))
        if sql.startswith("UPDATE dgv_load_jobs"):
            return _Result([])
        if sql.startswith("SELECT EXISTS"):  # whether the assembly had DGV rows
            return _Result([False])
        if "FROM assemblies" in sql:
            return _Result([self.assembly] if self.assembly else [])
        if sql.startswith("INSERT INTO dgv_load_jobs"):
            if self.insert_error:
                raise self.insert_error
            return _Result([_job_row(requested_by=(params or {}).get("requested_by"))])
        if "FROM dgv_load_jobs" in sql:
            return _Result(self.jobs)
        return _Result([])

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


@pytest.fixture()
def started(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    runs: list[str] = []
    monkeypatch.setattr(dgv, "_start", runs.append)
    return runs


def test_an_assembly_without_a_pinned_release_is_refused(started: list) -> None:
    with pytest.raises(HTTPException) as refused:
        asyncio.run(dgv.queue_dgv_load(_Session(), assembly="T2T-CHM13", requested_by=_account()))
    assert refused.value.status_code == 400 and "upload its DGV file" in refused.value.detail
    assert started == []


def test_an_unknown_assembly_is_refused(started: list) -> None:
    with pytest.raises(HTTPException) as refused:
        asyncio.run(dgv.queue_dgv_load(_Session(), assembly="GRCh38", requested_by=_account()))
    assert refused.value.status_code == 404


def test_only_one_load_runs_at_a_time(started: list) -> None:
    session = _Session(
        assembly={"id": "asm-1", "assembly_name": "GRCh38"},
        insert_error=IntegrityError("INSERT", {}, Exception("duplicate key")),
    )
    with pytest.raises(HTTPException) as refused:
        asyncio.run(dgv.queue_dgv_load(session, assembly="GRCh38", requested_by=_account()))
    assert refused.value.status_code == 409 and session.rolled_back
    assert started == []


def test_a_load_keeps_its_requester_by_the_account_and_starts_in_the_background(started: list) -> None:
    session = _Session(assembly={"id": "asm-1", "assembly_name": "GRCh38"})
    job = asyncio.run(dgv.queue_dgv_load(session, assembly="GRCh38", requested_by=_account()))

    insert = next(params for sql, params in session.statements if sql.startswith("INSERT INTO dgv_load_jobs"))
    assert insert["requested_by_id"] == REQUESTER_ID and insert["requested_by"] == "lab.admin@example.org"
    assert insert["source_url"] == config.DGV_PINNED_RELEASES["GRCh38"].url and insert["release"] == "2025-12-01"
    closing = next(i for i, (sql, _) in enumerate(session.statements) if "stale_window" in sql)
    inserting = next(i for i, (sql, _) in enumerate(session.statements) if sql.startswith("INSERT"))
    assert closing < inserting and session.committed
    assert (job.status, started) == ("queued", ["job-1"])


def test_a_load_a_start_queues_is_no_accounts(started: list) -> None:
    session = _Session(assembly={"id": "asm-1", "assembly_name": "GRCh38"})
    asyncio.run(dgv.queue_dgv_load(session, assembly="GRCh38", requested_by=None))

    insert = next(params for sql, params in session.statements if sql.startswith("INSERT INTO dgv_load_jobs"))
    assert (insert["requested_by"], insert["requested_by_id"]) == (dgv.STARTUP_REQUESTER, None)


@pytest.mark.parametrize(
    ("assembly_name", "row", "expected_note"),
    [
        ("T2T-CHM13", None, None),  # no pinned release
        ("GRCh38", None, None),  # no such assembly
        ("GRCh38", {"id": "asm-1", "loaded": True}, None),  # already has DGV rows
    ],
)
def test_a_load_is_queued_only_for_a_pinned_assembly_without_dgv(
    started: list, assembly_name: str, row: dict | None, expected_note: str | None
) -> None:
    job, note = asyncio.run(
        dgv.queue_dgv_load_if_missing(_Session(assembly=row), assembly_name=assembly_name, requested_by=None)
    )
    assert (job, note, started) == (None, expected_note, [])


def test_an_assembly_without_dgv_gets_its_load(monkeypatch: pytest.MonkeyPatch, started: list) -> None:
    session = _Session(assembly={"id": "asm-1", "assembly_name": "GRCh38", "loaded": False})
    job, note = asyncio.run(dgv.queue_dgv_load_if_missing(session, assembly_name="GRCh38", requested_by=None))

    assert job is not None and started == ["job-1"]
    assert note == "The DGV 2025-12-01 load for GRCh38 has started in the background."


def test_a_load_that_waits_on_another_says_so(started: list) -> None:
    session = _Session(
        assembly={"id": "asm-1", "assembly_name": "GRCh38", "loaded": False},
        insert_error=IntegrityError("INSERT", {}, Exception("duplicate key")),
    )
    job, note = asyncio.run(dgv.queue_dgv_load_if_missing(session, assembly_name="GRCh38", requested_by=None))

    assert job is None and note is not None and "another DGV load is running" in note


def test_the_status_closes_a_silent_job_first_and_lists_the_pinned_releases() -> None:
    session = _Session(jobs=[_job_row(status="running"), _job_row(id="job-0", status="failed")])
    status = asyncio.run(dgv.get_dgv_load_status(session))

    assert "stale_window" in session.statements[0][0] and session.committed
    assert status.active_job is not None and str(status.active_job.id) == "job-1"
    assert {release.assembly_name for release in status.releases} == {"GRCh37", "GRCh38"}


# --- running a load --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("job", "account", "expected"),
    [
        ({"requested_by": dgv.STARTUP_REQUESTER, "requested_by_id": None}, None, None),
        ({"requested_by": "lab.admin@example.org", "requested_by_id": None}, None, dgv.REQUESTER_GONE_ERROR),
        ({"requested_by": "x", "requested_by_id": REQUESTER_ID}, _account(), None),
        ({"requested_by": "x", "requested_by_id": REQUESTER_ID}, _account(is_active=False), dgv.REQUESTER_INACTIVE_ERROR),
        ({"requested_by": "x", "requested_by_id": REQUESTER_ID}, _account(role="viewer"), dgv.REQUESTER_NOT_ADMIN_ERROR),
    ],
)
def test_a_load_runs_only_for_an_active_administrator_or_a_start(
    monkeypatch: pytest.MonkeyPatch, job: dict, account: CurrentUser | None, expected: str | None
) -> None:
    async def lookup(_session: Any, _user_id: str) -> CurrentUser | None:
        return account

    monkeypatch.setattr(dgv, "get_current_user_by_id", lookup)
    monkeypatch.setattr(dgv, "get_postgres_sessionmaker", lambda: (lambda: _Session()))
    assert asyncio.run(dgv._requester_refusal(job)) == expected


class _Run:
    """The job run with its database writes, its load and its requester check stood in for."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, load=None, refusals=(None, None), job=None) -> None:
        self.updates: list[tuple[str, dict]] = []
        self.recorded: list[dict] = []
        self.session = _Session()
        self.refusals = list(refusals)
        claimed = job or {
            "assembly_id": "asm-1",
            "assembly_name": "GRCh38",
            "release": "2025-12-01",
            "source_url": config.DGV_PINNED_RELEASES["GRCh38"].url,
            "requested_by": "lab.admin@example.org",
            "requested_by_id": REQUESTER_ID,
        }

        async def claim(_job_id: str, _worker_id: str) -> dict:
            return dict(claimed)

        async def update(_job_id: str, _worker_id: str | None, assignments: str, params: dict) -> bool:
            self.updates.append((assignments, params))
            return True

        async def refusal(_job: dict) -> str | None:
            return self.refusals.pop(0)

        async def default_load(*_args: Any, **_kwargs: Any) -> tuple[int, str]:
            return 3, "cd" * 32

        async def record(_session: Any, **kwargs: Any) -> None:
            self.recorded.append(kwargs)

        monkeypatch.setattr(dgv, "_claim_job", claim)
        monkeypatch.setattr(dgv, "_update_job", update)
        monkeypatch.setattr(dgv, "_requester_refusal", refusal)
        monkeypatch.setattr(dgv, "load_dgv_release", load or default_load)
        monkeypatch.setattr(dgv, "record_reference_import", record)
        monkeypatch.setattr(dgv, "get_postgres_sessionmaker", lambda: (lambda: self.session))

    def run(self) -> None:
        asyncio.run(dgv._run_job("job-1"))

    @property
    def final(self) -> tuple[str, dict]:
        return self.updates[-1]


def test_a_complete_load_is_committed_and_recorded_with_its_release(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _Run(monkeypatch)
    run.run()

    deleted = [sql for sql, _ in run.session.statements if sql.startswith("DELETE FROM dgv_variants")]
    assert deleted and run.session.committed
    [record] = run.recorded
    pin = config.DGV_PINNED_RELEASES["GRCh38"]
    assert record["source_version"] == dgv.recorded_release(pin, "cd" * 32)
    assert (record["source_url"], record["source_release_date"]) == (pin.url, date(2025, 12, 1))
    assert (record["dataset_type"], record["inserted"], record["performed_by"]) == ("dgv", 3, "lab.admin@example.org")
    assert run.final[0].startswith("status = 'completed'") and run.final[1]["inserted"] == 3


def test_a_refused_file_commits_nothing_and_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refused(*_args: Any, **_kwargs: Any) -> tuple[int, str]:
        raise dgv.DgvFileRefused("Refused the DGV file x: it is not the pinned release.")

    run = _Run(monkeypatch, load=refused)
    run.run()

    assert not run.session.committed and run.recorded == []
    assert run.final[0] == dgv._FAILED and "not the pinned release" in run.final[1]["error"]


def test_a_failed_download_commits_nothing_and_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unreachable(*_args: Any, **_kwargs: Any) -> tuple[int, str]:
        raise httpx.ConnectError("no route to host")

    run = _Run(monkeypatch, load=unreachable)
    run.run()

    assert not run.session.committed
    assert run.final[1]["error"].startswith("The DGV file could not be downloaded: no route to host.")


def test_a_load_that_outlives_its_timeout_is_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    async def slow(*_args: Any, **_kwargs: Any) -> tuple[int, str]:
        await asyncio.sleep(5)
        return 1, "ab" * 32

    monkeypatch.setattr(dgv.settings, "dgv_load_timeout_seconds", 0.01)
    run = _Run(monkeypatch, load=slow)
    run.run()

    assert not run.session.committed and "stopped after 0.01 seconds" in run.final[1]["error"]


def test_a_requester_who_lost_their_rights_meanwhile_has_nothing_committed(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _Run(monkeypatch, refusals=(None, dgv.REQUESTER_NOT_ADMIN_ERROR))
    run.run()

    assert run.session.rolled_back and not run.session.committed and run.recorded == []
    assert run.final == (dgv._FAILED, {"error": dgv.REQUESTER_NOT_ADMIN_ERROR})


def test_a_requester_without_the_rights_has_nothing_downloaded(monkeypatch: pytest.MonkeyPatch) -> None:
    loads: list[Any] = []

    async def load(*args: Any, **kwargs: Any) -> tuple[int, str]:
        loads.append(kwargs)
        return 1, "ab" * 32

    run = _Run(monkeypatch, load=load, refusals=(dgv.REQUESTER_INACTIVE_ERROR,))
    run.run()

    assert loads == [] and run.session.statements == []
    assert run.final == (dgv._FAILED, {"error": dgv.REQUESTER_INACTIVE_ERROR})


def test_a_load_queued_for_another_pin_is_not_run(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _Run(
        monkeypatch,
        job={
            "assembly_id": "asm-1",
            "assembly_name": "GRCh38",
            "release": "2020-02-25",
            "source_url": "https://dgv.tcag.ca/dgv/docs/GRCh38_hg38_variants_2020-02-25.txt",
            "requested_by": dgv.STARTUP_REQUESTER,
            "requested_by_id": None,
        },
    )
    run.run()

    assert run.session.statements == []
    assert "no longer the one this load was queued for" in run.final[1]["error"]


def test_a_job_closed_while_it_loaded_commits_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def closed(*_args: Any, **_kwargs: Any) -> tuple[int, str]:
        raise dgv._JobClosed()

    run = _Run(monkeypatch, load=closed)
    run.run()

    assert not run.session.committed and run.updates == []
