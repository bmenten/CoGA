"""A ClinVar load on real Postgres and ClickHouse: released whole, or nothing of it kept.

The unit tests (``test_clinvar_load_jobs.py``) stand in for the job's rows, the release's
partition and its record. Against the real schemas, with NCBI served by a stand-in and a
synthetic ClinVar file (``fixtures/clinvar``), this checks that

* a load run to its end stores one row per allele in the release's partition, keyed by the
  allele in minimal form, records the release with its file and SHA-256 in
  ``reference_dataset_imports``, adds its event to the clinical audit chain
  ``system:clinvar-releases``, and ends ``completed`` with the records read, stored and
  skipped;
* a load of the release that is current already ends ``unchanged``;
* a newer release with more than 5 % fewer records is refused: its partition is dropped and
  the current release stays;
* ClickHouse keeps the current release and the one before it.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import gzip
import hashlib
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

from backend.tests.integration._datastores import fresh_assembly, fresh_species, run_closing_stores

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "clinvar" / "clinvar_20261122.vcf"
_SOURCE = "https://clinvar.example/pub/clinvar"


def _file(assembly: str, release: date, *, records: int | None = None) -> bytes:
    """The synthetic ClinVar file as the release of ``release`` for ``assembly``, its first
    ``records`` records only when given."""
    lines = _FIXTURE.read_text().splitlines()
    header = [
        f"##fileDate={release.isoformat()}" if line.startswith("##fileDate=")
        else f"##reference={assembly}" if line.startswith("##reference=")
        else line
        for line in lines
        if line.startswith("#")
    ]
    body = [line for line in lines if not line.startswith("#")]
    return gzip.compress("\n".join([*header, *body[:records]]).encode() + b"\n")


class _Ncbi:
    """NCBI's ClinVar folder of the test assembly, listing the files put in it."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def put(self, release: date, body: bytes) -> None:
        self.files[f"clinvar_{release:%Y%m%d}.vcf.gz"] = body

    def handle(self, request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        if not name:
            return httpx.Response(200, text="".join(f'<a href="{file}">{file}</a>\n' for file in self.files))
        if name.endswith(".md5") and name[:-4] in self.files:
            return httpx.Response(200, text=f"{hashlib.md5(self.files[name[:-4]]).hexdigest()}  {name[:-4]}\n")
        if name in self.files:
            return httpx.Response(200, content=self.files[name])
        return httpx.Response(404)


@pytest.fixture()
def clinvar(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, str, _Ncbi]:
    """The load-job module, an assembly name ClinVar publishes for in this test only, and NCBI."""
    from backend.app.services import clinvar_download, clinvar_load_jobs

    name = f"clinvar-{uuid4()}"
    ncbi = _Ncbi()
    folders = {name: "vcf_TEST"}
    monkeypatch.setattr(clinvar_download, "CLINVAR_ASSEMBLY_FOLDERS", folders)
    monkeypatch.setattr(clinvar_load_jobs, "CLINVAR_ASSEMBLY_FOLDERS", folders)
    monkeypatch.setattr(clinvar_load_jobs.settings, "clinvar_source_url", _SOURCE)
    monkeypatch.setattr(clinvar_load_jobs, "_start", lambda job_id: None)  # each test runs the job
    monkeypatch.setattr(
        clinvar_load_jobs,
        "clinvar_http_client",
        lambda: clinvar_download.clinvar_http_client(httpx.MockTransport(ncbi.handle)),
    )
    return clinvar_load_jobs, name, ncbi


async def _with_assembly(name: str, test: Any) -> None:
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services.clinvar_release_store import clinvar_table

    sm = get_postgres_sessionmaker()
    async with sm() as s:
        species = await fresh_species(s, name)
        await fresh_assembly(s, species, name)
        # One active load at a time, across the shared integration database.
        await s.execute(text("DELETE FROM clinvar_load_jobs WHERE status IN ('queued', 'running')"))
        await s.commit()
    try:
        await test(sm)
    finally:
        await execute_clickhouse(f"DROP TABLE IF EXISTS {clinvar_table(name)}")
        async with sm() as s:
            await s.execute(text("DELETE FROM species WHERE id = CAST(:s AS uuid)"), {"s": species})
            await s.commit()


async def _load(jobs: Any, sm: Any, assembly: str) -> dict[str, Any]:
    async with sm() as s:
        job = await jobs.queue_clinvar_load(s, assembly=assembly, requested_by=jobs.STARTUP_REQUESTER)
    await jobs._run_job(str(job.id))
    async with sm() as s:
        row = (
            await s.execute(
                text(
                    "SELECT status, error, release, source_sha256, records_read, records_stored, "
                    "records_skipped FROM clinvar_load_jobs WHERE id = CAST(:j AS uuid)"
                ),
                {"j": str(job.id)},
            )
        ).mappings().one()
    return dict(row)


def test_a_release_is_stored_recorded_and_audited_whole(clinvar: tuple[Any, str, _Ncbi]) -> None:
    jobs, assembly, ncbi = clinvar
    release = date(2026, 11, 22)
    body = _file(assembly, release)
    ncbi.put(release, body)

    async def test(sm: Any) -> None:
        from backend.app.core.clickhouse import execute_clickhouse
        from backend.app.services.clickhouse_variant_ids import small_allele_key
        from backend.app.services.clinvar_release_store import clinvar_table

        job = await _load(jobs, sm, assembly)
        assert job["status"] == "completed", job["error"]
        assert (job["release"], job["records_read"], job["records_stored"]) == (release, 10, 8)
        assert job["records_skipped"] == {"no ALT": 1, "not a sequence of bases": 1}
        assert job["source_sha256"] == hashlib.sha256(body).hexdigest()

        rows = await execute_clickhouse(
            f"SELECT release, variantId, classification, classification_terms, review_status, "
            f"conditions, genes, included_only FROM {clinvar_table(assembly)} "
            "WHERE allele_key = %(key)s",
            {"key": small_allele_key(assembly, "M-3243-A-G")},
        )
        assert rows == [
            (
                release,
                "M-3243-A-G",
                "Pathogenic",
                ["pathogenic"],
                "criteria provided, single submitter",
                ["Synthetic mitochondrial disorder"],
                ["MT-SYN1"],
                False,
            )
        ]
        async with sm() as s:
            recorded = (
                await s.execute(
                    text(
                        "SELECT r.inserted, r.source_version, r.source_release_date, r.source_url "
                        "FROM reference_dataset_imports r JOIN assemblies a ON a.id = r.assembly_id "
                        "WHERE a.assembly_name = :a AND r.dataset_type = 'clinvar'"
                    ),
                    {"a": assembly},
                )
            ).mappings().one()
            event = (
                await s.execute(
                    text(
                        "SELECT action, actor, after FROM clinical_audit_events "
                        "WHERE family_identifier = 'system:clinvar-releases' "
                        "AND metadata->>'assembly' = :a"
                    ),
                    {"a": assembly},
                )
            ).mappings().one()
            status = await jobs.get_clinvar_load_status(s)
        assert recorded["inserted"] == 8 and recorded["source_release_date"] == release
        assert recorded["source_version"] == (
            f"2026-11-22 (clinvar_20261122.vcf.gz, SHA-256 {hashlib.sha256(body).hexdigest()})"
        )
        assert recorded["source_url"] == f"{_SOURCE}/vcf_TEST/clinvar_20261122.vcf.gz"
        assert (event["action"], event["actor"]) == ("clinvar_release_current", "startup-bootstrap")
        assert event["after"]["release"] == "2026-11-22" and event["after"]["records"] == 8
        [held] = [entry for entry in status.assemblies if entry.assembly_name == assembly]
        assert held.current is not None and held.current.release == release and held.previous is None

        # The release that is current already is not loaded again.
        again = await _load(jobs, sm, assembly)
        assert again["status"] == "unchanged" and again["records_stored"] == 0

    run_closing_stores(lambda: _with_assembly(assembly, test), init_schema=True)


def test_a_refused_release_leaves_nothing_and_two_releases_are_kept(clinvar: tuple[Any, str, _Ncbi]) -> None:
    jobs, assembly, ncbi = clinvar
    first, second, refused, third = date(2026, 11, 8), date(2026, 11, 15), date(2026, 11, 22), date(2026, 11, 29)

    async def test(sm: Any) -> None:
        from backend.app.services.clinvar_release_store import stored_clinvar_releases

        ncbi.put(first, _file(assembly, first))
        assert (await _load(jobs, sm, assembly))["status"] == "completed"
        ncbi.put(second, _file(assembly, second))
        assert (await _load(jobs, sm, assembly))["status"] == "completed"
        assert await stored_clinvar_releases(assembly) == {first, second}

        # 7 of 8 records is 12.5 % fewer: refused, its partition dropped, the current kept.
        ncbi.put(refused, _file(assembly, refused, records=8))
        job = await _load(jobs, sm, assembly)
        assert job["status"] == "failed" and "more than 5% fewer" in job["error"]
        assert await stored_clinvar_releases(assembly) == {first, second}

        # A complete newer release becomes current; the one before the previous is dropped.
        ncbi.files.clear()
        ncbi.put(third, _file(assembly, third))
        assert (await _load(jobs, sm, assembly))["status"] == "completed"
        assert await stored_clinvar_releases(assembly) == {second, third}

    run_closing_stores(lambda: _with_assembly(assembly, test), init_schema=True)
