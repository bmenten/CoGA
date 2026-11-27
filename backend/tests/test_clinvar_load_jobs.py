"""Loads of ClinVar's weekly VCF (``clinvar_load_jobs``): found, downloaded, checked, stored and
released whole, or not at all.

A load finds the newest weekly file NCBI lists for the assembly, and ends ``unchanged`` when
that release is current already. It refuses a download whose MD5 is not NCBI's, and a file
whose ``##fileDate`` is not the date in its name or whose ``##reference`` is not the assembly.
Each record that names an allele is stored in the release's partition, keyed by its minimal
allele; the release becomes current only when the partition holds exactly the rows stored and
no more than 5 % fewer than the current release, and only while its requester is an active
administrator. Anything else drops the partition: nothing of the release stays. ClickHouse keeps
the current release and the one before it.

NCBI, ClickHouse and the job's Postgres rows are stood in for here, with a synthetic ClinVar
file (``fixtures/clinvar``); ``integration/test_clinvar_load_clickhouse.py`` runs the load on
real Postgres and ClickHouse.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from backend.app.services import clinvar_load_jobs as jobs
from backend.app.services.access_control import CurrentUser
from backend.app.services.clickhouse_variant_ids import small_allele_key
from backend.app.services.clinvar_download import (
    ClinvarFile,
    clinvar_http_client,
    download_clinvar_file,
    newest_clinvar_file,
)
from backend.app.services.clinvar_releases import ClinvarRelease
from backend.app.services.clinvar_vcf import (
    SKIP_NO_ALT,
    SKIP_NOT_AN_ALLELE,
    ClinvarFileReader,
    ClinvarFileRefused,
    release_of_file_name,
)

FIXTURE = Path(__file__).parent / "fixtures" / "clinvar" / "clinvar_20261122.vcf"
RELEASE = date(2026, 11, 22)
SOURCE = "https://clinvar.example/pub/clinvar"
FOLDER = f"{SOURCE}/vcf_GRCh38/"
REQUESTER_ID = "00000000-0000-0000-0000-0000000000c1"
NOW = datetime(2026, 11, 27, tzinfo=timezone.utc)


def _vcf(text: str | None = None) -> bytes:
    return gzip.compress((text if text is not None else FIXTURE.read_text()).encode())


def _account(*, is_active: bool = True, role: str = "admin") -> CurrentUser:
    return CurrentUser(
        id=REQUESTER_ID,
        username="lab.admin",
        email="lab.admin@example.org",
        role=role,
        is_active=is_active,
        created_at=NOW,
    )


def _ncbi(files: dict[str, bytes], *, md5: dict[str, str] | None = None) -> httpx.MockTransport:
    """NCBI's ClinVar folder for GRCh38 as a listing of ``files``, each with its ``.md5``."""
    stated = {name: hashlib.md5(body).hexdigest() for name, body in files.items()} | (md5 or {})

    def handle(request: httpx.Request) -> httpx.Response:
        if str(request.url) == FOLDER:
            links = "".join(f'<a href="{name}">{name}</a> <a href="{name}.md5">{name}.md5</a>\n' for name in files)
            return httpx.Response(200, text=f"<html><body><pre>{links}</pre></body></html>")
        name = request.url.path.rsplit("/", 1)[-1]
        if name.endswith(".md5") and name[:-4] in files:
            return httpx.Response(200, text=f"{stated[name[:-4]]}  {name[:-4]}\n")
        if name in files:
            return httpx.Response(200, content=files[name])
        return httpx.Response(404)

    return httpx.MockTransport(handle)


# --- the file ----------------------------------------------------------------------------------


def _read(text: str | None = None, *, assembly: str = "GRCh38") -> tuple[list[Any], ClinvarFileReader]:
    reader = ClinvarFileReader(assembly_name=assembly, release=RELEASE, file_name="clinvar_20261122.vcf.gz")
    return list(reader.rows((text if text is not None else FIXTURE.read_text()).splitlines())), reader


def test_each_record_that_names_an_allele_is_one_row_keyed_by_its_minimal_allele() -> None:
    rows, reader = _read()

    assert reader.records_read == 10
    assert dict(reader.skipped) == {SKIP_NO_ALT: 1, SKIP_NOT_AN_ALLELE: 1}
    by_id = {row.variation_id: row for row in rows}
    assert len(rows) == 8 and set(by_id) == {900001, 900002, 900003, 900004, 900005, 900006, 900007, 900010}
    deletion = by_id[900002]
    assert deletion.variant_id == "1-1000200-AG-A"
    assert deletion.allele_key == small_allele_key("GRCh38", "1-1000200-AG-A")
    assert deletion.classification == "Likely_pathogenic"
    assert deletion.classification_terms == ("likely pathogenic",)
    assert deletion.review_status == "reviewed by expert panel"
    assert deletion.conditions == ("Synthetic disorder 1", "not provided")
    assert deletion.genes == ("SYNG1", "SYNG2")
    assert deletion.allele_id == 910002 and not deletion.included_only
    # mtDNA, which ClinVar writes as MT, is keyed as M.
    assert by_id[900007].variant_id == "M-3243-A-G"
    # An untrimmed record is keyed by its minimal form.
    assert by_id[900010].variant_id == "3-4000300-CT-C"


def test_the_classification_is_kept_as_published_with_the_terms_the_filters_read() -> None:
    by_id = {row.variation_id: row for row in _read()[0]}

    aggregate = by_id[900006]
    assert aggregate.classification == "Pathogenic/Likely_pathogenic|risk_factor"
    assert aggregate.classification_terms == ("likely pathogenic", "pathogenic", "risk factor")
    # A percent-encoded comma is read as one.
    assert aggregate.conditions == ("Synthetic, X-linked disorder",)
    conflicting = by_id[900003]
    assert conflicting.conflicting == "Pathogenic(1)|Uncertain_significance(2)"
    assert conflicting.review_status == "criteria provided, conflicting classifications"


def test_a_variant_classified_only_as_part_of_a_combination_has_no_classification() -> None:
    included = {row.variation_id: row for row in _read()[0]}[900005]

    assert included.included_only
    assert included.classification == "" and included.classification_terms == ()


@pytest.mark.parametrize(
    ("line", "names"),
    [
        ("##fileDate=2026-11-15", "##fileDate=2026-11-15"),
        ("##fileDate=", "##fileDate=(none)"),
        ("##reference=GRCh37", "##reference=GRCh37"),
    ],
)
def test_a_header_that_is_not_the_release_or_the_assembly_is_refused(line: str, names: str) -> None:
    key = line.split("=", 1)[0]
    text = "\n".join(line if row.startswith(f"{key}=") else row for row in FIXTURE.read_text().splitlines())

    with pytest.raises(ClinvarFileRefused, match=names.replace("(", r"\(").replace(")", r"\)")):
        _read(text)


def test_a_record_before_the_header_ends_is_refused() -> None:
    lines = FIXTURE.read_text().splitlines()
    record = next(line for line in lines if not line.startswith("#"))

    with pytest.raises(ClinvarFileRefused, match="before its #CHROM"):
        _read("\n".join([lines[0], record, *lines[1:]]))


def test_a_file_loaded_for_the_other_assembly_is_refused() -> None:
    with pytest.raises(ClinvarFileRefused, match="not the assembly it is loaded for"):
        _read(assembly="GRCh37")


@pytest.mark.parametrize(
    ("name", "release"),
    [
        ("clinvar_20261122.vcf.gz", RELEASE),
        ("clinvar_20261322.vcf.gz", None),
        ("clinvar.vcf.gz", None),
        ("clinvar_20261122.vcf.gz.md5", None),
    ],
)
def test_the_release_is_the_date_in_the_file_name(name: str, release: date | None) -> None:
    assert release_of_file_name(name) == release


# --- NCBI --------------------------------------------------------------------------------------


@pytest.fixture()
def ncbi_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs.settings, "clinvar_source_url", SOURCE)


def test_the_newest_weekly_file_the_folder_lists_is_loaded(ncbi_source: None) -> None:
    transport = _ncbi({"clinvar_20261115.vcf.gz": b"", "clinvar_20261122.vcf.gz": b"", "clinvar.vcf.gz": b""})

    async def newest() -> ClinvarFile:
        async with clinvar_http_client(transport) as client:
            return await newest_clinvar_file(client, "GRCh38")

    file = asyncio.run(newest())
    assert file == ClinvarFile(release=RELEASE, url=f"{FOLDER}clinvar_20261122.vcf.gz")


def test_a_folder_that_lists_no_weekly_file_is_refused(ncbi_source: None) -> None:
    async def newest() -> ClinvarFile:
        async with clinvar_http_client(_ncbi({"clinvar.vcf.gz": b""})) as client:
            return await newest_clinvar_file(client, "GRCh38")

    with pytest.raises(ClinvarFileRefused, match="lists no weekly VCF"):
        asyncio.run(newest())


def test_clinvar_is_read_over_https_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs.settings, "clinvar_source_url", "http://clinvar.example/pub/clinvar")

    async def newest() -> ClinvarFile:
        async with clinvar_http_client(_ncbi({})) as client:
            return await newest_clinvar_file(client, "GRCh38")

    with pytest.raises(ClinvarFileRefused, match="non-HTTPS"):
        asyncio.run(newest())


async def _no_progress(_read: int, _total: int | None) -> None:
    return None


def test_a_download_whose_md5_is_not_ncbis_is_refused(tmp_path: Path) -> None:
    body = _vcf()
    file = ClinvarFile(release=RELEASE, url=f"{FOLDER}clinvar_20261122.vcf.gz")
    transport = _ncbi({file.file_name: body}, md5={file.file_name: "0" * 32})

    async def download() -> None:
        async with clinvar_http_client(transport) as client:
            await download_clinvar_file(client, file, tmp_path, _no_progress)

    with pytest.raises(ClinvarFileRefused, match="not the 0{32} NCBI states"):
        asyncio.run(download())


def test_a_download_is_kept_with_its_md5_and_sha256(tmp_path: Path) -> None:
    body = _vcf()
    file = ClinvarFile(release=RELEASE, url=f"{FOLDER}clinvar_20261122.vcf.gz")

    async def download() -> Any:
        async with clinvar_http_client(_ncbi({file.file_name: body})) as client:
            return await download_clinvar_file(client, file, tmp_path, _no_progress)

    downloaded = asyncio.run(download())
    assert downloaded.path.read_bytes() == body and downloaded.size == len(body)
    assert downloaded.md5 == hashlib.md5(body).hexdigest()
    assert downloaded.sha256 == hashlib.sha256(body).hexdigest()


# --- the job -----------------------------------------------------------------------------------


def _release(day: date, records: int) -> ClinvarRelease:
    return ClinvarRelease(
        release=day,
        records=records,
        source_url=f"{FOLDER}clinvar_{day:%Y%m%d}.vcf.gz",
        source_version=f"{day.isoformat()} (clinvar_{day:%Y%m%d}.vcf.gz, SHA-256 x)",
        loaded_at=NOW,
        loaded_by="startup-bootstrap",
    )


class _World:
    """NCBI, the assembly's ClickHouse partitions, its recorded releases and the job's row."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        recorded: list[ClinvarRelease] | None = None,
        partitions: dict[date, int] | None = None,
        files: dict[str, bytes] | None = None,
        md5: dict[str, str] | None = None,
        requested_by: str = "startup-bootstrap",
        requester_id: str | None = None,
        refusals: list[str | None] | None = None,
    ) -> None:
        self.recorded = list(recorded or [])
        self.partitions = dict(partitions or {})
        self.dropped: list[date] = []
        self.released: list[tuple[date, int]] = []
        self.updates: list[tuple[str, dict[str, Any]]] = []
        self.count_offset = 0
        self.refusals = list(refusals or [])
        self.job = {
            "assembly_id": "00000000-0000-0000-0000-0000000000a1",
            "assembly_name": "GRCh38",
            "requested_by": requested_by,
            "requested_by_id": requester_id,
        }
        transport = _ncbi(files if files is not None else {"clinvar_20261122.vcf.gz": _vcf()}, md5=md5)
        monkeypatch.setattr(jobs.settings, "clinvar_source_url", SOURCE)
        monkeypatch.setattr(jobs, "clinvar_http_client", lambda: clinvar_http_client(transport))
        monkeypatch.setattr(jobs, "get_postgres_sessionmaker", _SessionMaker)
        monkeypatch.setattr(jobs, "recorded_clinvar_releases", self._recorded)
        monkeypatch.setattr(jobs, "_claim_job", self._claim)
        monkeypatch.setattr(jobs, "_update_job", self._update)
        monkeypatch.setattr(jobs, "_requester_refusal", self._refusal)
        monkeypatch.setattr(jobs, "_release", self._release)
        monkeypatch.setattr(jobs, "ensure_clinvar_table", self._ensure)
        monkeypatch.setattr(jobs, "insert_clinvar_rows", self._insert)
        monkeypatch.setattr(jobs, "count_clinvar_rows", self._count)
        monkeypatch.setattr(jobs, "stored_clinvar_releases", self._stored)
        monkeypatch.setattr(jobs, "drop_clinvar_release", self._drop)

    async def _recorded(self, _session: Any, _assembly: str) -> list[ClinvarRelease]:
        return sorted(self.recorded, key=lambda release: release.release, reverse=True)[:2]

    async def _claim(self, _job_id: str, _worker_id: str) -> dict[str, Any]:
        return dict(self.job)

    async def _update(self, _job_id: str, _worker: str | None, assignments: str, params: dict[str, Any]) -> bool:
        self.updates.append((assignments, params))
        return True

    async def _refusal(self, _job: dict[str, Any]) -> str | None:
        return self.refusals.pop(0) if self.refusals else None

    async def _release(self, _job: Any, file: ClinvarFile, _downloaded: Any, stored: int, _current: Any, _id: str) -> None:
        self.released.append((file.release, stored))
        self.recorded.append(_release(file.release, stored))

    async def _ensure(self, _assembly: str) -> None:
        return None

    async def _insert(self, _assembly: str, release: date, rows: list[Any]) -> None:
        self.partitions[release] = self.partitions.get(release, 0) + len(rows)

    async def _count(self, _assembly: str, release: date) -> int:
        return self.partitions.get(release, 0) + self.count_offset

    async def _stored(self, _assembly: str) -> set[date]:
        return set(self.partitions)

    async def _drop(self, _assembly: str, release: date) -> None:
        self.partitions.pop(release, None)
        self.dropped.append(release)

    def run(self) -> None:
        asyncio.run(jobs._run_job("00000000-0000-0000-0000-00000000f001"))

    @property
    def status(self) -> str:
        last = self.updates[-1][0]
        for status in ("completed", "unchanged", "failed"):
            if f"status = '{status}'" in last:
                return status
        return "running"

    @property
    def error(self) -> str:
        return str(self.updates[-1][1].get("error"))


class _SessionMaker:
    """``get_postgres_sessionmaker()``: its call and its session are this object."""

    def __call__(self) -> _SessionMaker:
        return self

    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_exc: Any) -> bool:
        return False


def test_a_new_release_is_stored_checked_released_and_the_two_newest_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    previous, before = _release(date(2026, 11, 15), 8), _release(date(2026, 11, 8), 8)
    world = _World(monkeypatch, recorded=[before, previous], partitions={before.release: 8, previous.release: 8})
    world.run()

    assert world.status == "completed"
    assert world.released == [(RELEASE, 8)]
    assert world.partitions == {RELEASE: 8, previous.release: 8}
    assert world.dropped == [before.release]
    found = next(params for assignments, params in world.updates if "release = :release" in assignments)
    assert found == {"release": RELEASE, "source_url": f"{FOLDER}clinvar_20261122.vcf.gz"}
    counts = [params for assignments, params in world.updates if "records_skipped" in assignments][-1]
    assert counts["records_read"] == 10 and counts["records_stored"] == 8
    assert counts["records_skipped"] == f'{{"{SKIP_NO_ALT}": 1, "{SKIP_NOT_AN_ALLELE}": 1}}'
    digests = next(params for assignments, params in world.updates if "source_md5" in assignments)
    assert digests["md5"] == hashlib.md5(_vcf()).hexdigest()


def test_the_release_that_is_current_already_is_not_downloaded_again(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _World(monkeypatch, recorded=[_release(RELEASE, 8)], partitions={RELEASE: 8})
    world.run()

    assert world.status == "unchanged"
    assert world.released == [] and world.partitions == {RELEASE: 8}
    assert not any("source_md5" in assignments for assignments, _ in world.updates)


def test_a_partition_an_interrupted_load_left_is_dropped_first(monkeypatch: pytest.MonkeyPatch) -> None:
    current = _release(date(2026, 11, 15), 8)
    # The same release half-written by a load whose process stopped, and an older one no
    # recorded release keeps.
    world = _World(monkeypatch, recorded=[current], partitions={current.release: 8, RELEASE: 3, date(2026, 10, 1): 5})
    world.run()

    assert world.status == "completed"
    assert world.partitions == {RELEASE: 8, current.release: 8}
    assert set(world.dropped) == {RELEASE, date(2026, 10, 1)}


@pytest.mark.parametrize(
    ("prepare", "reason"),
    [
        (lambda world: None, "MD5"),
        (lambda world: setattr(world, "count_offset", -1), "ClickHouse holds 7 rows"),
    ],
    ids=["md5", "count"],
)
def test_a_load_that_is_refused_keeps_nothing_and_the_current_release_stays(
    monkeypatch: pytest.MonkeyPatch, prepare: Callable[[_World], None], reason: str
) -> None:
    current = _release(date(2026, 11, 15), 8)
    md5 = {"clinvar_20261122.vcf.gz": "f" * 32} if reason == "MD5" else None
    world = _World(monkeypatch, recorded=[current], partitions={current.release: 8}, md5=md5)
    prepare(world)
    world.run()

    assert world.status == "failed"
    assert reason in world.error and "the current ClinVar release is unchanged" in world.error
    assert world.released == [] and world.partitions == {current.release: 8}


def test_a_file_whose_header_is_not_its_release_keeps_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    text = FIXTURE.read_text().replace("##fileDate=2026-11-22", "##fileDate=2026-11-21")
    world = _World(monkeypatch, files={"clinvar_20261122.vcf.gz": _vcf(text)})
    world.run()

    assert world.status == "failed" and "##fileDate=2026-11-21" in world.error
    assert world.partitions == {} and world.released == []


@pytest.mark.parametrize(("current_records", "released"), [(8, True), (9, False)])
def test_a_release_with_more_than_five_percent_fewer_records_is_not_released(
    monkeypatch: pytest.MonkeyPatch, current_records: int, released: bool
) -> None:
    """The file stores 8 records: as many as a current release of 8, and 11 % fewer than one of
    9, which is refused."""
    current = _release(date(2026, 11, 15), current_records)
    world = _World(monkeypatch, recorded=[current], partitions={current.release: current_records})
    world.run()

    if released:
        assert world.status == "completed" and world.released == [(RELEASE, 8)]
    else:
        assert world.status == "failed" and "more than 5% fewer than the current release" in world.error
        assert world.released == [] and RELEASE not in world.partitions


def test_a_requester_no_longer_an_active_administrator_releases_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _World(
        monkeypatch,
        requested_by="lab.admin@example.org",
        requester_id=REQUESTER_ID,
        refusals=[None, jobs.REQUESTER_INACTIVE_ERROR],
    )
    world.run()

    assert world.status == "failed" and world.error == jobs.REQUESTER_INACTIVE_ERROR
    assert world.released == [] and world.partitions == {} and world.dropped == [RELEASE]


def test_a_release_whose_transaction_fails_leaves_its_partition_to_the_next_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The commit may have made the release current before its answer was lost: its partition
    stays, and the next load drops it only if no recorded release keeps it."""
    world = _World(monkeypatch)

    async def lost(*_args: Any) -> None:
        raise ConnectionError("the commit's answer was lost")

    monkeypatch.setattr(jobs, "_release", lost)
    world.run()

    assert world.status == "failed" and "ConnectionError" in world.error
    assert world.partitions == {RELEASE: 8} and world.dropped == []


def test_a_download_that_fails_names_it_and_keeps_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _World(monkeypatch, files={})
    world.run()

    assert world.status == "failed" and "lists no weekly VCF" in world.error


def test_a_file_without_a_record_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    header = "\n".join(line for line in FIXTURE.read_text().splitlines() if line.startswith("#"))
    world = _World(monkeypatch, files={"clinvar_20261122.vcf.gz": _vcf(header)})
    world.run()

    assert world.status == "failed" and "holds no ClinVar record" in world.error
    assert world.partitions == {} and world.dropped == [RELEASE]


def test_a_download_that_cannot_reach_ncbi_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _World(monkeypatch)

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    monkeypatch.setattr(jobs, "clinvar_http_client", lambda: clinvar_http_client(httpx.MockTransport(unreachable)))
    world.run()

    assert world.status == "failed"
    assert world.error.startswith("The ClinVar file could not be downloaded (ConnectError)")


def test_a_requester_no_longer_an_administrator_runs_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _World(monkeypatch, refusals=[jobs.REQUESTER_NOT_ADMIN_ERROR])
    world.run()

    assert world.status == "failed" and world.error == jobs.REQUESTER_NOT_ADMIN_ERROR
    assert world.partitions == {} and world.dropped == []


def test_a_job_closed_while_it_ran_releases_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Taken for gone by the status read, the job is no longer this worker's: it stops at its
    next step and drops what it wrote."""
    world = _World(monkeypatch)

    async def closed(_job_id: str, _worker: str | None, assignments: str, params: dict[str, Any]) -> bool:
        world.updates.append((assignments, params))
        return "records_stored" not in assignments

    monkeypatch.setattr(jobs, "_update_job", closed)
    world.run()

    assert world.released == [] and world.partitions == {} and world.dropped == [RELEASE]
    assert not any("status = 'failed'" in assignments for assignments, _ in world.updates)


def test_a_job_no_longer_queued_is_not_run(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _World(monkeypatch)

    async def taken(_job_id: str, _worker_id: str) -> None:
        return None

    monkeypatch.setattr(jobs, "_claim_job", taken)
    world.run()

    assert world.updates == [] and world.partitions == {}


@pytest.mark.parametrize(
    ("job", "user", "refusal"),
    [
        ({"requested_by": "startup-bootstrap", "requested_by_id": None}, None, None),
        ({"requested_by": "lab.admin@example.org", "requested_by_id": REQUESTER_ID}, _account(), None),
        ({"requested_by": "lab.admin@example.org", "requested_by_id": None}, None, jobs.REQUESTER_GONE_ERROR),
        (
            {"requested_by": "lab.admin@example.org", "requested_by_id": REQUESTER_ID},
            _account(is_active=False),
            jobs.REQUESTER_INACTIVE_ERROR,
        ),
        (
            {"requested_by": "lab.admin@example.org", "requested_by_id": REQUESTER_ID},
            _account(role="analyst"),
            jobs.REQUESTER_NOT_ADMIN_ERROR,
        ),
    ],
    ids=["start", "active-admin", "gone", "deactivated", "demoted"],
)
def test_a_load_runs_for_an_active_administrator_or_a_start(
    monkeypatch: pytest.MonkeyPatch, job: dict[str, Any], user: CurrentUser | None, refusal: str | None
) -> None:
    async def lookup(_session: Any, user_id: str) -> CurrentUser | None:
        assert user_id == REQUESTER_ID
        return user

    monkeypatch.setattr(jobs, "get_postgres_sessionmaker", _SessionMaker)
    monkeypatch.setattr(jobs, "get_current_user_by_id", lookup)

    assert asyncio.run(jobs._requester_refusal(job)) == refusal


# --- queueing ----------------------------------------------------------------------------------


class _QueueSession:
    def __init__(self, *, assembly_id: str | None = "00000000-0000-0000-0000-0000000000a1", busy: bool = False) -> None:
        self.assembly_id, self.busy = assembly_id, busy
        self.statements: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        sql = " ".join(str(statement).split())
        self.statements.append((sql, params or {}))
        if sql.startswith("INSERT INTO clinvar_load_jobs") and self.busy:
            raise IntegrityError(sql, params, Exception("one active"))
        return _QueueResult(self, sql, params or {})

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class _QueueResult:
    def __init__(self, session: _QueueSession, sql: str, params: dict[str, Any]) -> None:
        self.session, self.sql, self.params = session, sql, params

    def scalar_one_or_none(self) -> str | None:
        return self.session.assembly_id

    def mappings(self) -> _QueueResult:
        return self

    def all(self) -> list[Any]:
        return []

    def one(self) -> dict[str, Any]:
        return {
            "id": "00000000-0000-0000-0000-00000000f001",
            "assembly_id": self.params["assembly_id"],
            "assembly_name": self.params["assembly_name"],
            "release": None,
            "source_url": None,
            "source_md5": None,
            "source_sha256": None,
            "status": "queued",
            "requested_by": self.params["requested_by"],
            "requested_at": NOW,
            "started_at": None,
            "completed_at": None,
            "bytes_read": 0,
            "bytes_total": None,
            "records_read": 0,
            "records_stored": 0,
            "records_skipped": {},
            "error": None,
        }


@pytest.fixture()
def started(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(jobs, "_start", calls.append)
    return calls


def test_an_administrators_load_is_queued_as_that_account_and_started(started: list[str]) -> None:
    session = _QueueSession()
    job = asyncio.run(jobs.queue_clinvar_load(session, assembly="GRCh38", requested_by=_account()))

    assert job.status == "queued" and job.requested_by == "lab.admin@example.org"
    insert = next(params for sql, params in session.statements if sql.startswith("INSERT INTO clinvar_load_jobs"))
    assert insert["requested_by_id"] == REQUESTER_ID
    assert started == [str(job.id)]


def test_a_start_queues_its_load_as_no_account(started: list[str]) -> None:
    session = _QueueSession()
    asyncio.run(jobs.queue_clinvar_load(session, assembly="GRCh38", requested_by=jobs.STARTUP_REQUESTER))

    insert = next(params for sql, params in session.statements if sql.startswith("INSERT INTO clinvar_load_jobs"))
    assert insert["requested_by"] == "startup-bootstrap" and insert["requested_by_id"] is None


@pytest.mark.parametrize(
    ("assembly", "session", "status"),
    [
        ("T2T-CHM13", _QueueSession(), 400),
        ("GRCh37", _QueueSession(assembly_id=None), 404),
        ("GRCh38", _QueueSession(busy=True), 409),
    ],
    ids=["no-clinvar-vcf", "unknown", "one-at-a-time"],
)
def test_a_load_that_cannot_be_queued_says_why(
    started: list[str], assembly: str, session: _QueueSession, status: int
) -> None:
    with pytest.raises(HTTPException) as refused:
        asyncio.run(jobs.queue_clinvar_load(session, assembly=assembly, requested_by=_account()))

    assert refused.value.status_code == status and started == []


def test_an_assembly_with_a_release_gets_no_load_at_a_start(
    started: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def current(_session: Any, _assembly: str) -> ClinvarRelease:
        return _release(RELEASE, 8)

    monkeypatch.setattr(jobs, "current_clinvar_release", current)
    session = _KnownSession()

    assert asyncio.run(jobs.queue_clinvar_load_if_missing(session, assembly_name="GRCh38", requested_by=None)) == (
        None,
        None,
    )
    assert started == []


def test_a_start_says_when_another_load_is_running(started: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    async def current(_session: Any, _assembly: str) -> None:
        return None

    async def busy(_session: Any, **_kwargs: Any) -> None:
        raise HTTPException(status_code=409)

    monkeypatch.setattr(jobs, "current_clinvar_release", current)
    monkeypatch.setattr(jobs, "queue_clinvar_load", busy)
    job, note = asyncio.run(jobs.queue_clinvar_load_if_missing(_KnownSession(), assembly_name="GRCh38", requested_by=None))

    assert job is None and note is not None and "another ClinVar load is running" in note


def test_a_start_queues_the_load_of_an_assembly_without_a_release(
    started: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def current(_session: Any, _assembly: str) -> None:
        return None

    queued: list[Any] = []

    async def queue(_session: Any, *, assembly: str, requested_by: Any) -> Any:
        queued.append((assembly, requested_by))
        return _QueueResult(_QueueSession(), "", {"assembly_id": "a", "assembly_name": assembly, "requested_by": requested_by}).one()

    monkeypatch.setattr(jobs, "current_clinvar_release", current)
    monkeypatch.setattr(jobs, "queue_clinvar_load", queue)
    _job, note = asyncio.run(jobs.queue_clinvar_load_if_missing(_KnownSession(), assembly_name="GRCh38", requested_by=None))

    assert queued == [("GRCh38", "startup-bootstrap")]
    assert note == "The ClinVar load for GRCh38 has started in the background."


class _KnownSession:
    async def execute(self, _statement: Any, _params: Any = None) -> Any:
        return self

    def scalar_one(self) -> bool:
        return True
