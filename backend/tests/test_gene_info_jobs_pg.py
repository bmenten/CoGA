from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
from uuid import UUID

import pytest

from backend.app.schemas import GeneBulkRefreshOut
from backend.app.services import gene_info_jobs_pg
from backend.app.services.access_control import CurrentUser


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value


class _MappingsResult:
    def __init__(self, row):
        self._row = row

    def one(self):
        return self._row


class _JobInsertResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return _MappingsResult(self._row)


class _RecordingSession:
    def __init__(self, value=0) -> None:
        self.value = value
        self.sql: str | None = None
        self.params = None

    async def execute(self, statement, params=None):
        self.sql = str(statement)
        self.params = params
        return _ScalarResult(self.value)


@pytest.mark.asyncio
async def test_count_distinct_human_gene_symbols_uses_uuid_assembly_filter() -> None:
    assembly_uuid = "11111111-1111-4111-8111-111111111111"
    session = _RecordingSession(value=7)

    count = await gene_info_jobs_pg._count_distinct_human_gene_symbols(
        session,
        assembly_ids=[assembly_uuid],
    )

    assert count == 7
    assert session.sql is not None
    assert "assembly_id IN (" in session.sql
    assert "POSTCOMPILE_assembly_ids" in session.sql
    assert "assembly_id::text IN :assembly_ids" not in session.sql
    assert session.params == {"assembly_ids": [UUID(assembly_uuid)]}


@pytest.mark.asyncio
async def test_queue_startup_gene_reference_refresh_if_needed_uses_local_dbnsfp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assembly_uuid = "11111111-1111-4111-8111-111111111111"
    now = datetime.now(timezone.utc)
    job_row = {
        "id": "22222222-2222-4222-8222-222222222222",
        "scope": "all_human",
        "symbol": None,
        "status": "queued",
        "active_slot": gene_info_jobs_pg.ACTIVE_GENE_REFERENCE_SLOT,
        "worker_id": None,
        "requested_by": "startup-bootstrap",
        "requested_at": now,
        "started_at": None,
        "heartbeat_at": None,
        "completed_at": None,
        "total_symbols": 0,
        "completed_symbols": 0,
        "updated_records": 0,
        "human_assemblies": 0,
        "current_symbol": None,
        "error": None,
        "metadata": {},
    }

    class _StartupSession:
        def __init__(self) -> None:
            self.calls = 0
            self.commits = 0

        async def execute(self, statement, params=None):
            self.calls += 1
            sql = str(statement)
            if "COUNT(*) FROM gene_info" in sql:
                return _ScalarResult(0)
            if "FROM gene_info_refresh_jobs" in sql and "LIMIT 1" in sql:
                return _ScalarResult(None)
            if "INSERT INTO gene_info_refresh_jobs" in sql:
                return _JobInsertResult(job_row)
            raise AssertionError(f"Unexpected SQL: {sql}")

        async def commit(self):
            self.commits += 1

    async def fake_get_human_context(session):
        return gene_info_jobs_pg.HumanGeneContext(
            species={"name": "Homo sapiens"},
            assemblies=[{"id": assembly_uuid, "assembly_name": "GRCh38", "version": "hg38"}],
        )

    async def fake_count_symbols(session, *, assembly_ids):
        assert assembly_ids == [assembly_uuid]
        return 3

    monkeypatch.setattr(
        gene_info_jobs_pg,
        "find_local_dbnsfp_gene_path",
        lambda: Path("/data/ref-data/dbNSFP5.4_gene.gz"),
    )
    monkeypatch.setattr(gene_info_jobs_pg, "_get_human_context", fake_get_human_context)
    monkeypatch.setattr(gene_info_jobs_pg, "_count_distinct_human_gene_symbols", fake_count_symbols)

    session = _StartupSession()
    job = await gene_info_jobs_pg.queue_startup_gene_reference_refresh_if_needed(session)

    assert job is not None
    assert job.scope == "all_human"
    assert job.requested_by == "startup-bootstrap"
    assert session.commits == 1


def _hgnc_context(records: dict[str, dict]) -> object:
    from backend.app.services.gene_info_bulk_sources import (
        GeneBulkSourceDataset,
        HumanGeneBulkContext,
    )

    return HumanGeneBulkContext(
        datasets={
            "hgnc_complete_set": GeneBulkSourceDataset(
                name="HGNC complete set",
                source_url="https://example.test/hgnc",
                status="success",
                records_by_symbol=records,
            )
        }
    )


HGNC_RECORDS = {
    "BRCA1": {"profile": {"hgnc_id": "HGNC:1100"}, "aliases": [], "previous_symbols": []},
    "LMTK1": {"profile": {"hgnc_id": "HGNC:21"}, "aliases": [], "previous_symbols": ["AATK"]},
    "SCN1A": {"profile": {"hgnc_id": "HGNC:10585"}, "aliases": [], "previous_symbols": []},
}


class _RowsResult:
    def mappings(self):
        return self

    def all(self):
        return []


class _GeneRowsSession:
    def __init__(self) -> None:
        self.sql: list[str] = []

    async def execute(self, statement, params=None):
        self.sql.append(str(statement))
        return _RowsResult()


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol", [None, "BRCA1"])
async def test_the_gene_refresh_loads_no_exons(monkeypatch: pytest.MonkeyPatch, symbol) -> None:
    # A whole-cohort refresh holds every gene row of every human assembly for hours; with
    # their exon lists the API process grew to gigabytes and an import beside it ran out
    # of memory. The refresh writes a gene's locus and annotation, nothing of its exons.
    async def human_context(_session):
        return gene_info_jobs_pg.HumanGeneContext(
            species={"id": "species-uuid", "name": "Homo sapiens"},
            assemblies=[{"id": "11111111-1111-4111-8111-111111111111", "assembly_name": "GRCh38"}],
        )

    monkeypatch.setattr(gene_info_jobs_pg, "_get_human_context", human_context)
    session = _GeneRowsSession()
    await gene_info_jobs_pg._load_human_gene_groups(session, symbol=symbol)  # type: ignore[arg-type]
    [sql] = session.sql
    columns = sql.split("FROM genes")[0]
    for unused in ("exons", "strand", "extra", "source"):
        assert unused not in columns
    for needed in ("gene_id", "hgnc_symbol", "chr", "start", '"end"', "biotype", "description"):
        assert needed in columns


def test_expand_groups_folds_a_renamed_symbol_onto_its_current_name() -> None:
    # The assembly annotation still calls this gene AATK; HGNC renamed it to LMTK1, and
    # every current source keys on the new name. The locus has to follow the rename.
    grouped = {"AATK": [{"assembly_id": "a1", "gene_id": "g1", "chr": "17", "start": 1, "end": 2}]}

    result = gene_info_jobs_pg._expand_groups_with_hgnc(
        grouped,
        assembly_ids=["a1"],
        bulk_context=_hgnc_context(HGNC_RECORDS),
        symbol=None,
    )

    assert "AATK" not in result
    assert result["LMTK1"][0]["gene_id"] == "g1"


def test_expand_groups_adds_hgnc_genes_the_assembly_does_not_carry() -> None:
    grouped = {"BRCA1": [{"assembly_id": "a1", "gene_id": "g1", "chr": "17", "start": 1, "end": 2}]}

    result = gene_info_jobs_pg._expand_groups_with_hgnc(
        grouped,
        assembly_ids=["a1", "a2"],
        bulk_context=_hgnc_context(HGNC_RECORDS),
        symbol=None,
    )

    assert set(result) == {"BRCA1", "LMTK1", "SCN1A"}
    # One placeholder row per human assembly, with no locus of its own.
    assert [doc["assembly_id"] for doc in result["SCN1A"]] == ["a1", "a2"]
    assert result["SCN1A"][0]["gene_id"] is None
    # The gene the assembly does place keeps its real row.
    assert result["BRCA1"][0]["gene_id"] == "g1"


def test_expand_groups_for_a_single_symbol_job_stays_single() -> None:
    result = gene_info_jobs_pg._expand_groups_with_hgnc(
        {},
        assembly_ids=["a1"],
        bulk_context=_hgnc_context(HGNC_RECORDS),
        symbol="AATK",
    )

    # A one-gene refresh must not turn into a whole-cohort refresh, and asking for the
    # old symbol must still reach the gene.
    assert set(result) == {"LMTK1"}


def test_expand_groups_is_a_no_op_without_a_usable_hgnc_dataset() -> None:
    grouped = {"AATK": [{"assembly_id": "a1", "gene_id": "g1"}]}

    assert (
        gene_info_jobs_pg._expand_groups_with_hgnc(
            grouped, assembly_ids=["a1"], bulk_context=None, symbol=None
        )
        is grouped
    )


def test_gene_locus_omits_coordinates_for_a_gene_the_assembly_does_not_place() -> None:
    assert gene_info_jobs_pg._gene_locus({"chr": "17", "start": 1, "end": 2}) == "chr17:1-2"
    assert gene_info_jobs_pg._gene_locus({"assembly_id": "a1", "gene_id": None}) is None


def test_expand_groups_drops_loci_hgnc_does_not_recognise_as_genes() -> None:
    # A genome annotation names far more features than there are genes: GENCODE places
    # ~77k symbols against HGNC's ~45k, the surplus being unnamed clone-based loci and
    # novel transcripts. Enriching those costs four external lookups each and yields
    # nothing, so HGNC decides what counts as a gene and the assembly only supplies loci.
    grouped = {
        "BRCA1": [{"assembly_id": "a1", "gene_id": "ENST1", "chr": "17", "start": 1, "end": 2}],
        "AC093323.1": [{"assembly_id": "a1", "gene_id": "ENST2", "chr": "1", "start": 3, "end": 4}],
    }

    result = gene_info_jobs_pg._expand_groups_with_hgnc(
        grouped,
        assembly_ids=["a1"],
        bulk_context=_hgnc_context(HGNC_RECORDS),
        symbol=None,
    )

    assert "AC093323.1" not in result
    assert "BRCA1" in result
    # The HGNC genes the assembly does not place are still added.
    assert {"BRCA1", "LMTK1", "SCN1A"} == set(result)


def test_expand_groups_keeps_a_renamed_locus_even_though_its_old_symbol_is_unknown() -> None:
    # AATK is not an approved symbol, so the "is this a gene?" check has to run against
    # the resolved name, not the one the assembly happens to carry.
    grouped = {"AATK": [{"assembly_id": "a1", "gene_id": "ENST3", "chr": "17", "start": 5, "end": 6}]}

    result = gene_info_jobs_pg._expand_groups_with_hgnc(
        grouped,
        assembly_ids=["a1"],
        bulk_context=_hgnc_context(HGNC_RECORDS),
        symbol=None,
    )

    assert result["LMTK1"][0]["gene_id"] == "ENST3"


# --- the account that requested the refresh (SEC-35) ---------------------------------------------
#
# A refresh is an administrator's (the routes require one), and the worker ran it whoever had
# asked for it by then. The job now keeps its requester by the account's id, and the worker
# runs it only for an active administrator, as the owner decided on 2026-10-28: a job whose
# requester no longer exists, has been deactivated or is no longer an administrator when a
# worker takes it up (a queued job, or one taken again after its worker stopped) ends failed,
# saying why, before any gene record is written, and frees the slot for the next refresh. The
# refresh a start queues (``startup-bootstrap``) is no account's, and runs.

JOB = "00000000-0000-0000-0000-0000000000e0"
WORKER = "gene-worker-1"
REQUESTER_ID = "00000000-0000-0000-0000-0000000000e1"
REQUESTER_EMAIL = "gene.admin@example.org"


def _account(*, is_active: bool = True, role: str = "admin") -> CurrentUser:
    return CurrentUser(
        id=REQUESTER_ID,
        username="gene.admin",
        email=REQUESTER_EMAIL,
        first_name="Given-e1",
        last_name="Surname-e1",
        role=role,
        is_active=is_active,
        created_at=datetime.now(timezone.utc),
    )


class _FirstResult:
    def __init__(self, row) -> None:
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _RefreshJob:
    """A refresh job this worker has taken up (``running``), requested by ``requested_by`` and
    ``requested_by_id``, whose account the lookup finds as ``accounts`` holds it. Loading the
    sources, choosing the genes and writing their records are stand-ins that say they ran; the
    job's updates are recorded as the runner gives them."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        requested_by: str,
        requested_by_id: str | None,
        accounts: dict[str, CurrentUser],
    ) -> None:
        self.updates: list[tuple[str, dict]] = []
        self.commits = 0
        self.ran: list[str] = []
        self.looked_up: list[str] = []
        row = {"id": JOB, "symbol": None, "requested_by": requested_by, "requested_by_id": requested_by_id}
        job = self

        class _Session:
            async def execute(self, statement, params=None):
                sql = " ".join(str(statement).split())
                if sql.startswith("SELECT"):
                    return _FirstResult(row)
                job.updates.append((sql, params or {}))
                return _FirstResult(None)

            async def commit(self) -> None:
                job.commits += 1

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        async def lookup(_session, user_id: str) -> CurrentUser | None:
            self.looked_up.append(user_id)
            return accounts.get(user_id)

        async def load_sources(**_kwargs):
            self.ran.append("sources loaded")
            return None

        async def gene_groups(_session, **_kwargs):
            self.ran.append("genes chosen")
            return gene_info_jobs_pg.HumanGeneContext(species={}, assemblies=[]), {}

        async def write_records(_session, **_kwargs):
            self.ran.append("records written")
            return GeneBulkRefreshOut(
                human_assemblies=1, gene_symbols=0, updated_records=0, completed_at=datetime.now(timezone.utc)
            )

        monkeypatch.setattr(gene_info_jobs_pg, "get_postgres_sessionmaker", lambda: _Session)
        monkeypatch.setattr(gene_info_jobs_pg, "get_current_user_by_id", lookup)
        monkeypatch.setattr(gene_info_jobs_pg, "load_human_gene_bulk_context", load_sources)
        monkeypatch.setattr(gene_info_jobs_pg, "_load_human_gene_groups", gene_groups)
        monkeypatch.setattr(gene_info_jobs_pg, "_refresh_grouped_human_gene_info", write_records)

    async def run(self) -> None:
        await gene_info_jobs_pg.run_gene_reference_refresh_job(job_id=JOB, worker_id=WORKER)


_LOST_RIGHTS = [
    pytest.param(None, {}, "REQUESTER_GONE_ERROR", id="account-deleted-link-cleared"),
    pytest.param(REQUESTER_ID, {}, "REQUESTER_GONE_ERROR", id="no-account-has-the-id"),
    pytest.param(REQUESTER_ID, {REQUESTER_ID: _account(is_active=False)}, "REQUESTER_INACTIVE_ERROR", id="deactivated"),
    pytest.param(
        REQUESTER_ID,
        {REQUESTER_ID: _account(role="viewer")},
        "REQUESTER_NOT_ADMIN_ERROR",
        id="no-longer-an-administrator",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("requester_id", "accounts", "error"), _LOST_RIGHTS)
async def test_a_refresh_whose_requester_lost_their_rights_is_not_run(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    requester_id: str | None,
    accounts: dict[str, CurrentUser],
    error: str,
) -> None:
    job = _RefreshJob(monkeypatch, requested_by=REQUESTER_EMAIL, requested_by_id=requester_id, accounts=accounts)
    caplog.set_level(logging.DEBUG)

    await job.run()

    # Before, the refresh ran, whoever had asked for it.
    assert job.ran == [], "the refresh ran for an account that may no longer ask for it"
    expected = getattr(gene_info_jobs_pg, error)
    [(sql, params)] = job.updates
    # Ended failed, saying why, and the slot freed for the next refresh; only while the job is
    # this worker's.
    assert sql.startswith("UPDATE gene_info_refresh_jobs SET status = 'failed', active_slot = NULL, worker_id = NULL,")
    assert sql.endswith("WHERE id = CAST(:job_id AS uuid) AND worker_id = :worker_id")
    assert (params["job_id"], params["worker_id"], params["error"]) == (JOB, WORKER, expected)
    assert job.commits == 1
    assert "the account that requested this refresh" in expected
    assert "The gene records stay as they were" in expected
    # The log names neither the account nor its address.
    for personal in (REQUESTER_ID, REQUESTER_EMAIL, "gene.admin", "Given-e1", "Surname-e1"):
        assert personal not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "superuser"])
async def test_the_refresh_of_an_active_administrator_runs(monkeypatch: pytest.MonkeyPatch, role: str) -> None:
    job = _RefreshJob(
        monkeypatch,
        requested_by=REQUESTER_EMAIL,
        requested_by_id=REQUESTER_ID,
        accounts={REQUESTER_ID: _account(role=role)},
    )

    await job.run()

    assert job.ran == ["sources loaded", "genes chosen", "records written"]
    assert job.looked_up == [REQUESTER_ID]
    [(sql, _params)] = job.updates
    assert sql.startswith("UPDATE gene_info_refresh_jobs SET status = 'completed'")


@pytest.mark.asyncio
async def test_the_refresh_a_start_queues_is_no_accounts_and_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    job = _RefreshJob(monkeypatch, requested_by="startup-bootstrap", requested_by_id=None, accounts={})

    await job.run()

    assert job.ran == ["sources loaded", "genes chosen", "records written"]
    assert job.looked_up == []


class _InsertSession:
    """Records the job a refresh request inserts, and gives it back as Postgres would."""

    def __init__(self) -> None:
        self.params: dict = {}
        self.sql = ""

    async def execute(self, statement, params=None):
        self.sql = " ".join(str(statement).split())
        self.params = params or {}
        row = {
            "id": JOB,
            "scope": self.params["scope"],
            "symbol": self.params["symbol"],
            "status": "queued",
            "active_slot": self.params["active_slot"],
            "requested_by": self.params["requested_by"],
            "requested_at": self.params["requested_at"],
            "metadata": {},
        }
        return _JobInsertResult(row)

    async def commit(self) -> None:
        return None


@pytest.mark.asyncio
async def test_a_queued_refresh_keeps_its_requester_by_the_account_id() -> None:
    # As an import job's (SEC-32): the address it was asked under is shown with the job, and a
    # Microsoft sign-in may change it later.
    session = _InsertSession()

    job = await gene_info_jobs_pg.queue_gene_reference_refresh_job(
        session, scope="symbol", symbol="BRCA1", requested_by=_account()
    )

    assert "CAST(:requested_by_id AS uuid)" in session.sql
    assert (session.params["requested_by"], session.params["requested_by_id"]) == (REQUESTER_EMAIL, REQUESTER_ID)
    assert job.requested_by == REQUESTER_EMAIL


@pytest.mark.asyncio
async def test_the_refresh_a_start_queues_names_no_account() -> None:
    session = _InsertSession()

    await gene_info_jobs_pg.queue_gene_reference_refresh_job(session, scope="all_human", requested_by=None)

    assert (session.params["requested_by"], session.params["requested_by_id"]) == ("startup-bootstrap", None)
