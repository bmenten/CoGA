"""Durable ancestry jobs using independent, verified and project-scoped WGS sources."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..ancestry_schemas import AncestryAnalysisOut, AncestryReferenceOut, AncestryRunOut, FamilyAncestryOut
from ..core.coga_logging import describe_error
from ..core.config import settings
from ..core.postgres import get_postgres_sessionmaker
from ..core.sql import require_uuid
from ..wgs_schemas import WgsSourceOut
from .access_control import CurrentUser, user_can_change_metadata_projects
from .ancestry_projection import CALCULATION_VERSION, DEFAULT_REFERENCE, MIN_CALL_RATE, MIN_MARKERS, load_reference, project_file
from .family_variant_write_lock import try_share_family_variant_writes
from .metadata_service import get_current_user_by_id
from .wgs_evidence import WgsInputError
from . import wgs_source_storage as wgs

logger = logging.getLogger(__name__)
NOTICE = "Genetic similarity to public reference populations; not ancestry percentages or ethnic identity. Excluded from clinical sign-out."
INTERRUPTED = "Ancestry calculation interrupted; no partial results were published. Request a new calculation."
_RUNS = """
    SELECT id::text, source_id::text, reference_sha256, status, error, requested_at, started_at, finished_at
    FROM ancestry_runs WHERE family_uuid = CAST(:family AS uuid) AND project_id = CAST(:project AS uuid)
    ORDER BY requested_at DESC, id DESC
"""


def configured_reference():
    return load_reference(Path(settings.ancestry_reference_path) if settings.ancestry_reference_path else DEFAULT_REFERENCE)


async def family_ancestry(session: AsyncSession, family_id: str, user: CurrentUser, project_id: str | None,
                           run_id: str | None = None) -> FamilyAncestryOut:
    context, project = await wgs.family_scope(session, family_id, user, project_id)
    params = {"family": context.family_uuid, "project": project}
    sources = [WgsSourceOut(**dict(r)) for r in (await session.execute(text(wgs._SOURCES), params)).mappings().all()]
    source = sources[0] if sources else None
    state = await wgs._source_state(session, context, source, project)
    runs = [AncestryRunOut(**dict(r)) for r in (await session.execute(text(_RUNS), params)).mappings().all()]
    notices = [NOTICE]
    active = None
    try:
        active = (await asyncio.to_thread(configured_reference)).public
    except WgsInputError:
        notices.append("The ancestry reference is unavailable or failed validation. Historical projections remain available.")
    selection = ""
    query_params = dict(params)
    if run_id is not None:
        query_params["run"] = require_uuid(run_id, "Ancestry analysis not found", status_code=404)
        selection = "AND r.id = CAST(:run AS uuid)"
    # selection is a fixed SQL fragment; the only caller-supplied value is bound.
    row = (await session.execute(text("""
        SELECT a.run_id::text, a.source_id::text, a.reference_sha256, a.samples,
               a.calculation_version, a.assumptions, a.created_at, p.snapshot
        FROM ancestry_results a JOIN ancestry_runs r ON r.id = a.run_id
        JOIN ancestry_reference_panels p ON p.sha256 = a.reference_sha256
        WHERE r.family_uuid = CAST(:family AS uuid) AND r.project_id = CAST(:project AS uuid)
          AND r.status = 'completed'
    """ + selection + " ORDER BY a.created_at DESC, a.run_id DESC LIMIT 1"), query_params)).mappings().first()
    if run_id is not None and row is None:
        raise HTTPException(404, "Ancestry analysis not found")
    analysis = None
    reference = active
    if row is not None:
        reference = AncestryReferenceOut.model_validate(row["snapshot"])
        analysis = AncestryAnalysisOut(**{k: row[k] for k in (
            "run_id", "source_id", "reference_sha256", "samples", "calculation_version", "assumptions", "created_at")},
            current_source=state == "ready" and source is not None and row["source_id"] == source.id,
            current_reference=active is not None and row["reference_sha256"] == active.sha256)
    if state != "ready":
        notices.append("Ancestry calculation is unavailable: " + state.replace("_", " ") + ".")
    return FamilyAncestryOut(project_id=project, source_state=state, reference=reference, analysis=analysis,
                              active_reference_sha256=active.sha256 if active else None, runs=runs, notices=notices,
                              can_calculate=state == "ready" and active is not None
                              and user_can_change_metadata_projects([project], user))


async def queue_family(session: AsyncSession, family_id: str, user: CurrentUser, project_id: str | None) -> AncestryRunOut:
    context, project = await wgs.family_scope(session, family_id, user, project_id, change=True)
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                          {"key": f"ancestry-queue:{context.family_uuid}:{project}"})
    if not await try_share_family_variant_writes(session, context.family_uuid):
        raise HTTPException(409, "Genotypes are being written; retry once the import finishes.")
    snapshot = await family_ancestry(session, family_id, user, project)
    if not snapshot.can_calculate:
        raise HTTPException(409, "A verified GRCh38 full-WGS source and a valid ancestry reference are required.")
    if any(r.status in {"queued", "running"} for r in snapshot.runs):
        raise HTTPException(409, "An ancestry calculation is already queued or running for this family.")
    reference = (await asyncio.to_thread(configured_reference)).public
    source = (await session.execute(text(wgs._SOURCES), {"family": context.family_uuid, "project": project})).mappings().first()
    if source is None:
        raise HTTPException(409, "A verified full-WGS source is required.")
    await session.execute(text("""
        INSERT INTO ancestry_reference_panels(sha256, snapshot) VALUES (:sha, CAST(:snapshot AS jsonb))
        ON CONFLICT (sha256) DO NOTHING
    """), {"sha": reference.sha256, "snapshot": reference.model_dump_json()})
    result = await session.execute(text("""
        INSERT INTO ancestry_runs(family_uuid, family_identifier, project_id, source_id, reference_sha256, requested_by)
        VALUES (CAST(:family AS uuid), :identifier, CAST(:project AS uuid), CAST(:source AS uuid), :sha, CAST(:actor AS uuid))
        RETURNING id::text
    """), {"family": context.family_uuid, "identifier": context.family_id, "project": project,
            "source": source["id"], "sha": reference.sha256, "actor": user.id})
    run = str(result.scalar_one())
    await session.commit()
    return next(r for r in (await family_ancestry(session, family_id, user, project)).runs if r.id == run)


async def claim_run(session: AsyncSession) -> str | None:
    await session.execute(text("""
        UPDATE ancestry_runs SET status = 'interrupted', error = :error, finished_at = now()
        WHERE id IN (SELECT id FROM ancestry_runs WHERE status = 'running'
          AND started_at < now() - interval '10 minutes' FOR UPDATE SKIP LOCKED)
    """), {"error": INTERRUPTED})
    run = (await session.execute(text("""
        UPDATE ancestry_runs SET status = 'running', started_at = now()
        WHERE id = (SELECT r.id FROM ancestry_runs r JOIN wgs_sources s ON s.id = r.source_id
          LEFT JOIN family_import_jobs i ON i.id = s.import_job_id
          WHERE r.status = 'queued' AND (s.import_job_id IS NULL OR i.status NOT IN ('queued','validating','running') OR i.id IS NULL)
          ORDER BY r.requested_at LIMIT 1 FOR UPDATE OF r SKIP LOCKED)
        RETURNING id::text
    """))).scalar_one_or_none()
    await session.commit()
    return str(run) if run else None


async def _finish_failed(run_id: str, status: str, error: str) -> None:
    async with get_postgres_sessionmaker()() as session:
        await session.execute(text("""
            UPDATE ancestry_runs SET status = :status, error = :error, finished_at = now()
            WHERE id = CAST(:id AS uuid) AND status = 'running'
        """), {"id": run_id, "status": status, "error": error})
        await session.commit()


async def calculate_run(run_id: str) -> None:
    run_id = require_uuid(run_id, "Ancestry run not found", status_code=404)
    try:
        async with get_postgres_sessionmaker()() as session:
            run = (await session.execute(text("SELECT * FROM ancestry_runs WHERE id = CAST(:id AS uuid) AND status = 'running' FOR UPDATE"),
                                         {"id": run_id})).mappings().first()
            if run is None:
                return
            user = await get_current_user_by_id(session, str(run["requested_by"]))
            if user is None or not user.is_active:
                raise WgsInputError("The requester no longer has access to this project.")
            context, project = await wgs.family_scope(session, run["family_identifier"], user, str(run["project_id"]), change=True)
            if context.family_uuid != str(run["family_uuid"]):
                raise WgsInputError("The original family no longer exists.")
            if not await try_share_family_variant_writes(session, context.family_uuid):
                raise WgsInputError("Genotypes are being written; request calculation after import.")
            sources = (await session.execute(text(wgs._SOURCES), {"family": context.family_uuid, "project": project})).mappings().all()
            if not sources or str(sources[0]["id"]) != str(run["source_id"]):
                raise WgsInputError("A newer WGS source exists; request a new calculation.")
            source = WgsSourceOut(**dict(sources[0]))
            if await wgs._source_state(session, context, source, project) != "ready":
                raise WgsInputError("The import, family members or genotype storage changed; import full WGS again.")
            reference = await asyncio.to_thread(configured_reference)
            if reference.public.sha256 != run["reference_sha256"]:
                raise WgsInputError("The ancestry reference changed after queueing; request a new calculation.")
            allow_unfiltered = source.qualification.get("allow_unfiltered") is True
            async with wgs.source_file(session, source, context.family_uuid) as path:
                results = await asyncio.to_thread(project_file, path, source.sha256, reference, source.samples,
                                                  assume_ref=source.absent_record_is_hom_ref,
                                                  allow_unfiltered=allow_unfiltered)
            user = await get_current_user_by_id(session, str(run["requested_by"]))
            if user is None or not user.is_active or not user_can_change_metadata_projects([project], user):
                raise WgsInputError("The requester no longer has access to this project.")
            context, _ = await wgs.family_scope(session, run["family_identifier"], user, project, change=True)
            if await wgs._source_state(session, context, source, project) != "ready":
                raise WgsInputError("The family or import changed during calculation; no results were published.")
            assumptions = {"full_wgs": True, "absent_record_is_hom_ref": source.absent_record_is_hom_ref,
                           "allow_unfiltered": allow_unfiltered,
                           "source_limitations": "; ".join(source.qualification.get("assessment", {}).get("warnings", [])),
                           "min_usable_markers": MIN_MARKERS, "min_call_rate": MIN_CALL_RATE,
                           "missing_calls": "uncalled SNPs contribute zero after reference centering, only above QC thresholds",
                           "projection": "fixed public reference axes; no ancestry percentages",
                           "genotypes": "diploid GT; exact REF/ALT; " + ("PASS and explicitly accepted unfiltered records" if allow_unfiltered else "PASS") + "; no strand flips"}
            await session.execute(text("""
                INSERT INTO ancestry_results(run_id, source_id, reference_sha256, samples, calculation_version, assumptions)
                VALUES (CAST(:run AS uuid), CAST(:source AS uuid), :sha, CAST(:samples AS jsonb), :version, CAST(:assumptions AS jsonb))
            """), {"run": run_id, "source": source.id, "sha": reference.public.sha256,
                    "samples": json.dumps([r.model_dump() for r in results]),
                    "version": CALCULATION_VERSION + ("+unfiltered-v1" if allow_unfiltered else ""),
                    "assumptions": json.dumps(assumptions)})
            await session.execute(text("UPDATE ancestry_runs SET status = 'completed', finished_at = now() WHERE id = CAST(:id AS uuid)"),
                                  {"id": run_id})
            await session.commit()
    except asyncio.CancelledError:
        await asyncio.shield(_finish_failed(run_id, "interrupted", INTERRUPTED))
        raise
    except Exception as exc:  # noqa: BLE001 - fixed errors only; no family/genotype values in logs
        logger.warning("Ancestry calculation failed: %s", describe_error(exc))
        error = str(exc) if isinstance(exc, WgsInputError) else "Ancestry calculation failed; verify source availability and project access before retrying."
        await _finish_failed(run_id, "failed", error)


async def process_next_run() -> bool:
    async with get_postgres_sessionmaker()() as session:
        run = await claim_run(session)
    if run is None:
        return False
    await calculate_run(run)
    return True


async def ancestry_worker(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            if await process_next_run():
                continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - log only sanitized error details
            logger.warning("Ancestry worker failed: %s", describe_error(exc))
        try:
            await asyncio.wait_for(stop.wait(), timeout=2)
        except asyncio.TimeoutError:
            pass


async def stop_ancestry_worker(task: asyncio.Task, stop: asyncio.Event) -> None:
    stop.set()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
