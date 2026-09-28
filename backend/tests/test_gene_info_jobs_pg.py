from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from backend.app.services import gene_info_jobs_pg


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
