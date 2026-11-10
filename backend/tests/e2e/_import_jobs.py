"""Package-import jobs as the end-to-end tests drive them.

A job is queued over the API as the import page queues it (``POST /api/family-imports``),
claimed by its id as the worker's claim takes it (``claim_next_family_import_job``: the
status, the worker, the start and the heartbeat), and run as the worker runs it
(``run_family_import_job``), so that a test decides which job runs, when, and with what held
or failing. The packages are synthetic. Used by the import, sign-out and deletion modules of
this folder; each keeps its own reading of what a job and a family hold.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import text


def one_sample_package(
    root: Path,
    family_id: str,
    *,
    variants: int,
    first_position: int = 100,
    datasets: tuple[str, ...] = ("snv", "coverage"),
) -> Path:
    """A one-sample package (``{family_id}_S1``): a family VCF of ``variants`` heterozygous
    SNVs on chromosome 1, 100 bases apart from ``first_position``, and a coverage BED of three
    bins; ``datasets`` names which of the two the manifest lists."""
    sample = f"{family_id}_S1"  # sample ids are unique across families
    root.mkdir(parents=True, exist_ok=True)
    (root / "family.ped").write_text(f"{family_id}\t{sample}\t0\t0\t1\t2\n", encoding="utf-8")
    manifest = [f"schema_version: 1\nfamily_id: {family_id}\nped: family.ped\ndatasets:\n"]
    if "snv" in datasets:
        manifest.append("  snv:\n    family_vcf: snv/family.vcf\n")
        (root / "snv").mkdir(exist_ok=True)
        (root / "snv" / "family.vcf").write_text(
            "##fileformat=VCFv4.2\n"
            '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
            f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}\n"
            + "".join(
                f"1\t{first_position + 100 * i}\t.\tA\tG\t60\tPASS\t.\tGT\t0/1\n" for i in range(variants)
            ),
            encoding="utf-8",
        )
    if "coverage" in datasets:
        manifest.append(f"  coverage:\n    per_sample:\n      {sample}:\n        bed: coverage/S1.bed\n")
        (root / "coverage").mkdir(exist_ok=True)
        (root / "coverage" / "S1.bed").write_text(
            "1\t0\t1000\t31.0\n1\t1000\t2000\t29.5\n1\t2000\t3000\t30.2\n", encoding="utf-8"
        )
    (root / "manifest.yaml").write_text("".join(manifest), encoding="utf-8")
    return root


async def queue_import(
    folder: Path,
    project_id: str | None,
    *,
    conflict_mode: str = "cancel",
    family_id: str | None = None,
    dry_run: bool = False,
    requested_by: Any = None,
    manifest_text: str | None = None,
) -> str:
    """Queue an import of ``folder`` as the approval of a delivery does
    (``import_delivery_review.approve_delivery`` calls ``queue_family_import_job`` so), as
    ``requested_by`` (the e2e administrator when None), and return the job's id."""
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services.family_package_jobs import queue_family_import_job
    from backend.tests.e2e import _harness

    async with get_postgres_sessionmaker()() as session:
        if requested_by is None:
            requested_by, _project_id, _assembly_id = await _harness.ensure_e2e_project(session)
        job = await queue_family_import_job(
            session,
            folder_path=str(folder),
            project_id=project_id,
            dry_run=dry_run,
            requested_family_id=family_id,
            conflict_mode=conflict_mode,
            requested_by=requested_by,
            manifest_text=manifest_text,
        )
    return str(job.id)


async def claim_import_job(job_id: str, worker_id: str) -> None:
    """Claim this queued job by its id as the worker's claim takes a queued job: it is
    ``validating``, under ``worker_id``, started and beating now."""
    from backend.app.core.postgres import get_postgres_sessionmaker

    async with get_postgres_sessionmaker()() as session:
        await session.execute(
            text(
                "UPDATE family_import_jobs SET status = 'validating', worker_id = :w, "
                "started_at = now(), heartbeat_at = now(), completed_at = NULL, error = NULL "
                "WHERE id = CAST(:j AS uuid) AND status = 'queued'"
            ),
            {"j": job_id, "w": worker_id},
        )
        await session.commit()


async def end_stale_job_as_a_worker_does(job_id: str) -> dict[str, Any] | None:
    """Ten minutes on, the job's heartbeat is stale, and a worker claims it as the worker loop
    does (``claim_next_family_import_job``, then ``handle_claimed_family_import_job``, which
    drops an overwrite's backups): a running job's claim ends it as interrupted. Return the
    claim of this job (its status and the status it was claimed from), or ``None`` when no
    claim reached it."""
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services import family_package_import as package_import

    sm = get_postgres_sessionmaker()
    async with sm() as s:
        await s.execute(
            text(
                "UPDATE family_import_jobs SET heartbeat_at = now() - interval '11 minutes' "
                "WHERE id = CAST(:j AS uuid)"
            ),
            {"j": job_id},
        )
        await s.commit()
    reclaimer = f"e2e-reclaim-{uuid4().hex[:8]}"
    for _ in range(50):
        async with sm() as s:
            claimed = await package_import.claim_next_family_import_job(s, worker_id=reclaimer)
        if claimed is None:
            return None
        try:
            await package_import.handle_claimed_family_import_job(claimed, worker_id=reclaimer)
        except Exception:  # noqa: BLE001 - the worker logs it and goes on
            pass
        if claimed["id"] == job_id:
            return {key: claimed[key] for key in ("status", "claimed_from")}
    return None


async def import_backups(job_id: str) -> list[str]:
    """The ClickHouse backup tables this job's import made of its family."""
    from backend.app.services.clickhouse_family_snapshot import list_import_backup_tables

    return [table.name for table in await list_import_backup_tables(owner=job_id)]


async def variant_write_locks_free(family_uuid: str) -> bool:
    """Whether no writer holds the family's variant-write locks."""
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services.family_variant_write_lock import try_share_family_variant_writes

    async with get_postgres_sessionmaker()() as s:
        free = await try_share_family_variant_writes(s, family_uuid)
        await s.rollback()
    return free


class WarningRecords(logging.Handler):
    """The warnings a logger logs, kept to assert on (add it to the logger, then remove it)."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def saying(self, words: str) -> list[str]:
        return [record.getMessage() for record in self.records if words in record.getMessage()]


async def job_outcome(job_id: str) -> dict[str, Any]:
    """How the job ended: its status, the family it names, its error and validation errors,
    and whether it was ended (``completed_at`` set)."""
    from backend.app.core.postgres import get_postgres_sessionmaker

    async with get_postgres_sessionmaker()() as session:
        row = (
            await session.execute(
                text(
                    "SELECT status, family_id, error, validation_errors, completed_at IS NOT NULL AS ended "
                    "FROM family_import_jobs WHERE id = CAST(:j AS uuid)"
                ),
                {"j": job_id},
            )
        ).mappings().one()
    return dict(row)


async def run_import_job(folder: Path, project_id: str, worker_id: str) -> dict[str, Any]:
    """Queue an import of ``folder`` as an approved delivery does, claim it as the worker does and run it;
    return its ``job_outcome``, with ``raised`` naming what the run raised, if it did."""
    from backend.app.services import family_package_import

    job_id = await queue_import(folder, project_id)
    await claim_import_job(job_id, worker_id)
    try:
        await family_package_import.run_family_import_job(job_id=job_id, worker_id=worker_id)
    except Exception as exc:  # noqa: BLE001 - the worker logs it and goes on
        return {**await job_outcome(job_id), "raised": type(exc).__name__}
    return await job_outcome(job_id)


async def row_counts() -> dict[str, int]:
    """How many families, samples and import jobs Postgres holds, to show that nothing of a
    refused package was written."""
    from backend.app.core.postgres import get_postgres_sessionmaker

    async with get_postgres_sessionmaker()() as session:
        row = (
            await session.execute(
                text(
                    "SELECT (SELECT count(*) FROM families) AS families, "
                    "(SELECT count(*) FROM samples) AS samples, "
                    "(SELECT count(*) FROM family_import_jobs) AS jobs"
                )
            )
        ).mappings().one()
    return dict(row)


def issue_codes(response: dict[str, Any]) -> list[str]:
    """The codes of the errors a captured Discover or Validate answer reports."""
    return [issue["code"] for issue in response["json"]["errors"]]
