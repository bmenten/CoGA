from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import bindparam, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_sessionmaker
from ..core.sql import uuid_list_bindparam, uuid_values
from ..schemas import (
    GeneBulkRefreshOut,
    GeneInfoRefreshJobOut,
    GeneInfoSourceSummaryOut,
    GeneReferenceAdminStatusOut,
)
from .gene_info_bulk_sources import load_human_gene_bulk_context
from .gene_info_external import fetch_external_gene_bundle

logger = logging.getLogger(__name__)

ACTIVE_GENE_REFERENCE_SLOT = "gene_reference"
GENE_REFERENCE_WORKER_POLL_SECONDS = 2.0
GENE_REFERENCE_STALE_HEARTBEAT = timedelta(minutes=5)


@dataclass(slots=True)
class HumanGeneContext:
    species: dict[str, Any]
    assemblies: list[dict[str, Any]]


def _assembly_priority(assembly_name: str) -> tuple[int, str]:
    if assembly_name == "GRCh38":
        return (0, assembly_name)
    if assembly_name in {"T2T-CHM13", "T2T-CHM13v2.0"} or assembly_name.startswith("T2T-CHM13"):
        return (1, assembly_name)
    if assembly_name in {"GRCh37", "hg19"}:
        return (2, assembly_name)
    return (9, assembly_name)


def _gene_locus(doc: dict[str, Any]) -> str:
    chrom = str(doc.get("chr", ""))
    display = chrom if chrom.startswith("chr") else f"chr{chrom}"
    return f"{display}:{int(doc.get('start', 0)):,}-{int(doc.get('end', 0)):,}"


def _serialize_job(mapping: dict[str, Any]) -> GeneInfoRefreshJobOut:
    return GeneInfoRefreshJobOut(
        id=str(mapping["id"]),
        scope=mapping["scope"],
        symbol=mapping.get("symbol"),
        status=mapping["status"],
        active_slot=mapping.get("active_slot"),
        worker_id=mapping.get("worker_id"),
        requested_by=mapping["requested_by"],
        requested_at=mapping["requested_at"],
        started_at=mapping.get("started_at"),
        heartbeat_at=mapping.get("heartbeat_at"),
        completed_at=mapping.get("completed_at"),
        total_symbols=int(mapping.get("total_symbols") or 0),
        completed_symbols=int(mapping.get("completed_symbols") or 0),
        updated_records=int(mapping.get("updated_records") or 0),
        human_assemblies=int(mapping.get("human_assemblies") or 0),
        current_symbol=mapping.get("current_symbol"),
        error=mapping.get("error"),
        metadata=mapping.get("metadata") or {},
    )


async def _get_human_context(session: AsyncSession) -> HumanGeneContext:
    species_result = await session.execute(
        text(
            """
            SELECT id::text AS id, name, common_name
            FROM species
            WHERE name = 'Homo sapiens' OR lower(common_name) = 'human'
            ORDER BY CASE WHEN name = 'Homo sapiens' THEN 0 ELSE 1 END
            LIMIT 1
            """
        )
    )
    species_row = species_result.mappings().first()
    if species_row is None:
        raise HTTPException(status_code=404, detail="Human reference species not found")

    assemblies_result = await session.execute(
        text(
            """
            SELECT id::text AS id, assembly_name, version
            FROM assemblies
            WHERE species_id = CAST(:species_id AS uuid)
            """
        ),
        {"species_id": species_row["id"]},
    )
    assemblies = [dict(row) for row in assemblies_result.mappings().all()]
    if not assemblies:
        raise HTTPException(status_code=404, detail="No human assemblies found")
    assemblies.sort(key=lambda row: _assembly_priority(str(row["assembly_name"])))
    return HumanGeneContext(species=dict(species_row), assemblies=assemblies)


async def _fetch_species_rows(session: AsyncSession) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT id::text AS id, name, common_name
            FROM species
            ORDER BY lower(name)
            """
        )
    )
    return [dict(row) for row in result.mappings().all()]


async def _load_human_gene_groups(
    session: AsyncSession,
    *,
    symbol: str | None = None,
) -> tuple[HumanGeneContext, list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    human_context = await _get_human_context(session)
    assembly_ids = [assembly["id"] for assembly in human_context.assemblies]
    species_docs = await _fetch_species_rows(session)
    params: dict[str, Any] = {"assembly_ids": assembly_ids}
    if symbol:
        query = text(
            """
            SELECT
                assembly_id::text AS assembly_id,
                gene_id,
                hgnc_symbol,
                chr,
                start,
                "end",
                exons,
                strand,
                biotype,
                description,
                source,
                extra
            FROM genes
            WHERE assembly_id IN :assembly_ids
              AND lower(hgnc_symbol) = lower(:symbol)
            ORDER BY hgnc_symbol, assembly_id
            """
        ).bindparams(uuid_list_bindparam("assembly_ids"))
        params["symbol"] = symbol.strip()
    else:
        query = text(
            """
            SELECT
                assembly_id::text AS assembly_id,
                gene_id,
                hgnc_symbol,
                chr,
                start,
                "end",
                exons,
                strand,
                biotype,
                description,
                source,
                extra
            FROM genes
            WHERE assembly_id IN :assembly_ids
            ORDER BY hgnc_symbol, assembly_id
            """
        ).bindparams(uuid_list_bindparam("assembly_ids"))
    params["assembly_ids"] = uuid_values(assembly_ids)
    result = await session.execute(query, params)
    grouped_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for row in result.mappings().all():
        gene_row = dict(row)
        symbol_key = str(gene_row.get("hgnc_symbol", "")).strip()
        if not symbol_key:
            continue
        grouped_by_symbol.setdefault(symbol_key, []).append(gene_row)
    return human_context, species_docs, grouped_by_symbol


async def _count_distinct_human_gene_symbols(
    session: AsyncSession,
    *,
    assembly_ids: list[str],
) -> int:
    query = text(
        """
        SELECT COUNT(DISTINCT hgnc_symbol)
        FROM genes
        WHERE assembly_id IN :assembly_ids
          AND hgnc_symbol IS NOT NULL
          AND hgnc_symbol <> ''
        """
    ).bindparams(uuid_list_bindparam("assembly_ids"))
    result = await session.execute(query, {"assembly_ids": uuid_values(assembly_ids)})
    return int(result.scalar_one() or 0)


async def _aggregate_gene_info_source_summaries(
    session: AsyncSession,
    *,
    assembly_ids: list[str],
) -> list[GeneInfoSourceSummaryOut]:
    query = text(
        """
        SELECT
            source.key AS source,
            MAX(NULLIF(source.value->>'fetched_at', '')::timestamptz) AS latest_fetched_at,
            COUNT(*) FILTER (WHERE source.value->>'status' = 'success') AS success_count,
            COUNT(*) FILTER (WHERE source.value->>'status' = 'missing') AS missing_count,
            COUNT(*) FILTER (WHERE source.value->>'status' = 'error') AS error_count,
            COUNT(*) AS record_count
        FROM gene_info gi
        CROSS JOIN LATERAL jsonb_each(COALESCE(gi.source_status, '{}'::jsonb)) AS source(key, value)
        WHERE gi.assembly_id IN :assembly_ids
        GROUP BY source.key
        ORDER BY source.key
        """
    ).bindparams(uuid_list_bindparam("assembly_ids"))
    result = await session.execute(query, {"assembly_ids": uuid_values(assembly_ids)})
    return [
        GeneInfoSourceSummaryOut(
            source=row["source"],
            latest_fetched_at=row.get("latest_fetched_at"),
            success_count=int(row.get("success_count") or 0),
            missing_count=int(row.get("missing_count") or 0),
            error_count=int(row.get("error_count") or 0),
            record_count=int(row.get("record_count") or 0),
        )
        for row in result.mappings().all()
    ]


async def list_gene_reference_admin_status(
    session: AsyncSession,
) -> GeneReferenceAdminStatusOut:
    human_context = await _get_human_context(session)
    assembly_ids = [assembly["id"] for assembly in human_context.assemblies]
    human_gene_symbols = await _count_distinct_human_gene_symbols(
        session,
        assembly_ids=assembly_ids,
    )
    total_result = await session.execute(
        text("SELECT COUNT(*) FROM gene_info WHERE assembly_id IN :assembly_ids").bindparams(
            uuid_list_bindparam("assembly_ids")
        ),
        {"assembly_ids": uuid_values(assembly_ids)},
    )
    total_cached_records = int(total_result.scalar_one() or 0)
    source_summaries = await _aggregate_gene_info_source_summaries(
        session,
        assembly_ids=assembly_ids,
    )

    recent_result = await session.execute(
        text(
            """
            SELECT
                id::text AS id,
                scope,
                symbol,
                status,
                active_slot,
                worker_id,
                requested_by,
                requested_at,
                started_at,
                heartbeat_at,
                completed_at,
                total_symbols,
                completed_symbols,
                updated_records,
                human_assemblies,
                current_symbol,
                error,
                metadata
            FROM gene_info_refresh_jobs
            ORDER BY requested_at DESC
            LIMIT 12
            """
        )
    )
    recent_jobs = [_serialize_job(dict(row)) for row in recent_result.mappings().all()]
    active_job = next((job for job in recent_jobs if job.status in {"queued", "running"}), None)
    if active_job is None:
        active_result = await session.execute(
            text(
                """
                SELECT
                    id::text AS id,
                    scope,
                    symbol,
                    status,
                    active_slot,
                    worker_id,
                    requested_by,
                    requested_at,
                    started_at,
                    heartbeat_at,
                    completed_at,
                    total_symbols,
                    completed_symbols,
                    updated_records,
                    human_assemblies,
                    current_symbol,
                    error,
                    metadata
                FROM gene_info_refresh_jobs
                WHERE status IN ('queued', 'running')
                ORDER BY requested_at DESC
                LIMIT 1
                """
            )
        )
        active_row = active_result.mappings().first()
        active_job = _serialize_job(dict(active_row)) if active_row is not None else None

    last_completed_result = await session.execute(
        text(
            """
            SELECT completed_at
            FROM gene_info_refresh_jobs
            WHERE status = 'completed'
            ORDER BY completed_at DESC NULLS LAST
            LIMIT 1
            """
        )
    )
    last_completed_at = last_completed_result.scalar_one_or_none()
    return GeneReferenceAdminStatusOut(
        active_job=active_job,
        recent_jobs=recent_jobs,
        source_summaries=source_summaries,
        total_cached_records=total_cached_records,
        human_gene_symbols=human_gene_symbols,
        human_assemblies=len(human_context.assemblies),
        last_completed_at=last_completed_at,
    )


async def queue_gene_reference_refresh_job(
    session: AsyncSession,
    *,
    scope: str,
    requested_by: str,
    symbol: str | None = None,
) -> GeneInfoRefreshJobOut:
    if scope == "symbol" and not str(symbol or "").strip():
        raise HTTPException(status_code=400, detail="Gene symbol is required")
    now = datetime.now(timezone.utc)
    try:
        result = await session.execute(
            text(
                """
                INSERT INTO gene_info_refresh_jobs (
                    scope,
                    symbol,
                    status,
                    active_slot,
                    requested_by,
                    requested_at,
                    metadata
                )
                VALUES (
                    :scope,
                    :symbol,
                    'queued',
                    :active_slot,
                    :requested_by,
                    :requested_at,
                    '{}'::jsonb
                )
                RETURNING
                    id::text AS id,
                    scope,
                    symbol,
                    status,
                    active_slot,
                    worker_id,
                    requested_by,
                    requested_at,
                    started_at,
                    heartbeat_at,
                    completed_at,
                    total_symbols,
                    completed_symbols,
                    updated_records,
                    human_assemblies,
                    current_symbol,
                    error,
                    metadata
                """
            ),
            {
                "scope": scope,
                "symbol": symbol.strip() if symbol else None,
                "active_slot": ACTIVE_GENE_REFERENCE_SLOT,
                "requested_by": requested_by,
                "requested_at": now,
            },
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="A gene reference refresh job is already active",
        ) from exc
    row = result.mappings().one()
    return _serialize_job(dict(row))


async def claim_next_gene_reference_refresh_job(
    session: AsyncSession,
    *,
    worker_id: str,
) -> dict[str, Any] | None:
    now = datetime.now(timezone.utc)
    stale_before = now - GENE_REFERENCE_STALE_HEARTBEAT
    result = await session.execute(
        text(
            """
            WITH candidate AS (
                SELECT id
                FROM gene_info_refresh_jobs
                WHERE active_slot = :active_slot
                  AND (
                        status = 'queued'
                     OR (status = 'running' AND heartbeat_at IS NULL)
                     OR (status = 'running' AND heartbeat_at < :stale_before)
                  )
                ORDER BY requested_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            UPDATE gene_info_refresh_jobs AS job
            SET
                status = 'running',
                worker_id = :worker_id,
                started_at = COALESCE(job.started_at, :now),
                heartbeat_at = :now,
                completed_at = NULL,
                error = NULL
            FROM candidate
            WHERE job.id = candidate.id
            RETURNING
                job.id::text AS id,
                job.scope,
                job.symbol,
                job.status,
                job.active_slot,
                job.worker_id,
                job.requested_by,
                job.requested_at,
                job.started_at,
                job.heartbeat_at,
                job.completed_at,
                job.total_symbols,
                job.completed_symbols,
                job.updated_records,
                job.human_assemblies,
                job.current_symbol,
                job.error,
                job.metadata
            """
        ),
        {
            "active_slot": ACTIVE_GENE_REFERENCE_SLOT,
            "stale_before": stale_before,
            "worker_id": worker_id,
            "now": now,
        },
    )
    row = result.mappings().first()
    if row is None:
        await session.rollback()
        return None
    await session.commit()
    return dict(row)


async def _upsert_gene_info_row(
    session: AsyncSession,
    *,
    assembly_id: str,
    symbol: str,
    gene_doc: dict[str, Any],
    external_bundle: dict[str, Any],
    now: datetime,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO gene_info (
                assembly_id,
                hgnc_symbol,
                gene_id,
                display_name,
                summary,
                aliases,
                previous_symbols,
                ensembl_gene_id,
                ncbi_gene_id,
                hgnc_id,
                omim_gene_id,
                gene_type,
                location,
                homologs,
                source_status,
                extra,
                updated_at,
                created_at
            )
            VALUES (
                CAST(:assembly_id AS uuid),
                :hgnc_symbol,
                :gene_id,
                :display_name,
                :summary,
                CAST(:aliases AS jsonb),
                CAST(:previous_symbols AS jsonb),
                :ensembl_gene_id,
                :ncbi_gene_id,
                :hgnc_id,
                :omim_gene_id,
                :gene_type,
                :location,
                CAST(:homologs AS jsonb),
                CAST(:source_status AS jsonb),
                CAST(:extra AS jsonb),
                :updated_at,
                :created_at
            )
            ON CONFLICT (assembly_id, hgnc_symbol) DO UPDATE
            SET
                gene_id = EXCLUDED.gene_id,
                display_name = EXCLUDED.display_name,
                summary = EXCLUDED.summary,
                aliases = EXCLUDED.aliases,
                previous_symbols = EXCLUDED.previous_symbols,
                ensembl_gene_id = EXCLUDED.ensembl_gene_id,
                ncbi_gene_id = EXCLUDED.ncbi_gene_id,
                hgnc_id = EXCLUDED.hgnc_id,
                omim_gene_id = EXCLUDED.omim_gene_id,
                gene_type = EXCLUDED.gene_type,
                location = EXCLUDED.location,
                homologs = EXCLUDED.homologs,
                source_status = EXCLUDED.source_status,
                extra = EXCLUDED.extra,
                updated_at = EXCLUDED.updated_at
            """
        ),
        {
            "assembly_id": assembly_id,
            "hgnc_symbol": symbol,
            "gene_id": str(gene_doc.get("gene_id")),
            "display_name": external_bundle.get("display_name") or gene_doc.get("description"),
            "summary": external_bundle.get("summary") or gene_doc.get("description"),
            "aliases": json.dumps(external_bundle.get("aliases", [])),
            "previous_symbols": json.dumps(external_bundle.get("previous_symbols", [])),
            "ensembl_gene_id": external_bundle.get("ensembl_gene_id"),
            "ncbi_gene_id": external_bundle.get("ncbi_gene_id"),
            "hgnc_id": external_bundle.get("hgnc_id"),
            "omim_gene_id": external_bundle.get("omim_gene_id"),
            "gene_type": external_bundle.get("gene_type") or gene_doc.get("biotype"),
            "location": external_bundle.get("location") or _gene_locus(gene_doc),
            "homologs": json.dumps(external_bundle.get("homologs", [])),
            "source_status": json.dumps(external_bundle.get("source_status", {})),
            "extra": json.dumps(external_bundle.get("extra", {})),
            "updated_at": now,
            "created_at": now,
        },
    )


async def _refresh_grouped_human_gene_info(
    session: AsyncSession,
    *,
    job_id: str,
    worker_id: str,
    human_context: HumanGeneContext,
    species_docs: list[dict[str, Any]],
    grouped_by_symbol: dict[str, list[dict[str, Any]]],
) -> GeneBulkRefreshOut:
    updated_records = 0
    sorted_symbols = sorted(grouped_by_symbol)
    total_symbols = len(sorted_symbols)
    bulk_context = await load_human_gene_bulk_context(symbols=sorted_symbols)
    await session.execute(
        text(
            """
            UPDATE gene_info_refresh_jobs
            SET
                total_symbols = :total_symbols,
                human_assemblies = :human_assemblies,
                heartbeat_at = :heartbeat_at
            WHERE id = CAST(:job_id AS uuid)
              AND worker_id = :worker_id
            """
        ),
        {
            "job_id": job_id,
            "worker_id": worker_id,
            "total_symbols": total_symbols,
            "human_assemblies": len(human_context.assemblies),
            "heartbeat_at": datetime.now(timezone.utc),
        },
    )
    await session.commit()

    for index, symbol in enumerate(sorted_symbols, start=1):
        await session.execute(
            text(
                """
                UPDATE gene_info_refresh_jobs
                SET
                    current_symbol = :current_symbol,
                    completed_symbols = :completed_symbols,
                    total_symbols = :total_symbols,
                    updated_records = :updated_records,
                    heartbeat_at = :heartbeat_at
                WHERE id = CAST(:job_id AS uuid)
                  AND worker_id = :worker_id
                """
            ),
            {
                "job_id": job_id,
                "worker_id": worker_id,
                "current_symbol": symbol,
                "completed_symbols": index - 1,
                "total_symbols": total_symbols,
                "updated_records": updated_records,
                "heartbeat_at": datetime.now(timezone.utc),
            },
        )
        await session.commit()
        external_bundle = await fetch_external_gene_bundle(
            symbol=symbol,
            species_document=human_context.species,
            species_docs=species_docs,
            bulk_context=bulk_context,
        )
        now = datetime.now(timezone.utc)
        for doc in grouped_by_symbol[symbol]:
            await _upsert_gene_info_row(
                session,
                assembly_id=doc["assembly_id"],
                symbol=symbol,
                gene_doc=doc,
                external_bundle=external_bundle,
                now=now,
            )
            updated_records += 1
        await session.execute(
            text(
                """
                UPDATE gene_info_refresh_jobs
                SET
                    current_symbol = :current_symbol,
                    completed_symbols = :completed_symbols,
                    total_symbols = :total_symbols,
                    updated_records = :updated_records,
                    heartbeat_at = :heartbeat_at
                WHERE id = CAST(:job_id AS uuid)
                  AND worker_id = :worker_id
                """
            ),
            {
                "job_id": job_id,
                "worker_id": worker_id,
                "current_symbol": symbol,
                "completed_symbols": index,
                "total_symbols": total_symbols,
                "updated_records": updated_records,
                "heartbeat_at": datetime.now(timezone.utc),
            },
        )
        await session.commit()

    return GeneBulkRefreshOut(
        human_assemblies=len(human_context.assemblies),
        gene_symbols=total_symbols,
        updated_records=updated_records,
        completed_at=datetime.now(timezone.utc),
    )


async def run_gene_reference_refresh_job(
    *,
    job_id: str,
    worker_id: str,
) -> None:
    session_factory = get_postgres_sessionmaker()
    async with session_factory() as session:
        job_result = await session.execute(
            text(
                """
                SELECT id::text AS id, symbol
                FROM gene_info_refresh_jobs
                WHERE id = CAST(:job_id AS uuid)
                  AND worker_id = :worker_id
                  AND status = 'running'
                """
            ),
            {"job_id": job_id, "worker_id": worker_id},
        )
        job_row = job_result.mappings().first()
        if job_row is None:
            return
        try:
            human_context, species_docs, grouped_by_symbol = await _load_human_gene_groups(
                session,
                symbol=str(job_row.get("symbol") or "").strip() or None,
            )
            result = await _refresh_grouped_human_gene_info(
                session,
                job_id=job_id,
                worker_id=worker_id,
                human_context=human_context,
                species_docs=species_docs,
                grouped_by_symbol=grouped_by_symbol,
            )
            await session.execute(
                text(
                    """
                    UPDATE gene_info_refresh_jobs
                    SET
                        status = 'completed',
                        active_slot = NULL,
                        worker_id = NULL,
                        current_symbol = NULL,
                        completed_symbols = :completed_symbols,
                        total_symbols = :total_symbols,
                        updated_records = :updated_records,
                        human_assemblies = :human_assemblies,
                        heartbeat_at = :heartbeat_at,
                        completed_at = :completed_at
                    WHERE id = CAST(:job_id AS uuid)
                      AND worker_id = :worker_id
                    """
                ),
                {
                    "job_id": job_id,
                    "worker_id": worker_id,
                    "completed_symbols": result.gene_symbols,
                    "total_symbols": result.gene_symbols,
                    "updated_records": result.updated_records,
                    "human_assemblies": result.human_assemblies,
                    "heartbeat_at": datetime.now(timezone.utc),
                    "completed_at": result.completed_at,
                },
            )
            await session.commit()
        except Exception as exc:  # pragma: no cover
            await session.execute(
                text(
                    """
                    UPDATE gene_info_refresh_jobs
                    SET
                        status = 'failed',
                        active_slot = NULL,
                        worker_id = NULL,
                        current_symbol = NULL,
                        error = :error,
                        heartbeat_at = :heartbeat_at,
                        completed_at = :completed_at
                    WHERE id = CAST(:job_id AS uuid)
                    """
                ),
                {
                    "job_id": job_id,
                    "error": str(exc),
                    "heartbeat_at": datetime.now(timezone.utc),
                    "completed_at": datetime.now(timezone.utc),
                },
            )
            await session.commit()
            raise


async def gene_reference_refresh_worker(stop_event: asyncio.Event | None = None) -> None:
    session_factory = get_postgres_sessionmaker()
    worker_id = f"{os.getpid()}-{uuid4().hex}"
    while True:
        if stop_event is not None and stop_event.is_set():
            return
        try:
            async with session_factory() as session:
                job_row = await claim_next_gene_reference_refresh_job(
                    session,
                    worker_id=worker_id,
                )
            if job_row is None:
                await asyncio.sleep(GENE_REFERENCE_WORKER_POLL_SECONDS)
                continue
            await run_gene_reference_refresh_job(
                job_id=job_row["id"],
                worker_id=worker_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover
            logger.exception("Gene reference refresh worker encountered an unexpected error")
            await asyncio.sleep(GENE_REFERENCE_WORKER_POLL_SECONDS)


async def stop_gene_reference_worker(task: asyncio.Task[Any] | None, stop_event: asyncio.Event | None) -> None:
    if stop_event is not None:
        stop_event.set()
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
