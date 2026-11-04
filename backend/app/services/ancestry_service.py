"""Durable exploratory ancestry PCA jobs on the family's stored callsets.

Outside CoGA's intended purpose (TF-01 §4): a projection shows genetic similarity to public
reference populations, is never read clinically, and never enters the sign-out or the signed
record.

A calculation reads the family's small variants at the reference markers from ClickHouse
(``ancestry_callsets``), which takes about a second. It checks the family under its
variant-write locks and releases them before it reads; it takes them again only to check that
nothing was written in between and to publish, so no import or sign-out waits for it. What
keeps another worker off a running job is the job's own row lock, held on a connection of its
own until the job ends: a job whose process stopped loses it, and the next worker to look ends
the job as interrupted.

A run is a user's request, or the worker's own: every half minute it queues one for each
family whose small-variant data version no run has read yet, once no import of the family is
running. Nothing in the import or the upload calls it. When no run waits, it scans a VCF file
a package declared for ancestry (``ancestry_files``); every calculation reads the scan.
"""
from __future__ import annotations

import asyncio
import functools
import json
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..ancestry_schemas import (
    AncestryAnalysisOut,
    AncestryReferenceOut,
    AncestryRunOut,
    AncestrySampleChoiceIn,
    FamilyAncestryOut,
)
from ..core.coga_logging import describe_error
from ..core.config import settings
from ..core.postgres import get_postgres_sessionmaker
from ..core.sql import require_uuid
from .access_control import (
    CurrentUser,
    ReadOnlyAccess,
    RecordNotVisible,
    user_can_change_metadata_projects,
    visible_metadata_project_ids,
)
from . import ancestry_choices as choices_store
from . import ancestry_files
from .ancestry_callsets import (
    CALLSET_LABELS,
    COMPLETE_SHARE,
    MIN_COVERAGE_RATIO,
    project_family,
    read_marker_rows,
    rows_digest,
)
from .ancestry_projection import (
    CALCULATION_VERSION,
    DEFAULT_REFERENCE,
    MIN_CALL_RATE,
    MIN_MARKERS,
    AncestryInputError,
    AncestryReference,
    load_reference,
)
from .clickhouse_variant_storage import (
    _execute as storage_execute,
    _small_table_name,
    get_family_small_variant_data_version,
)
from .family_metadata_context import FamilyMetadataContext, _resolve_family_assembly, build_family_metadata_context
from .family_variant_write_lock import try_share_family_variant_writes
from .metadata_service import get_accessible_family_mapping, get_current_user_by_id

logger = logging.getLogger(__name__)
INTERRUPTED = "Ancestry calculation interrupted; no partial results were published. Request a new calculation."
# How often the worker looks for families whose genotypes changed since their last run.
SWEEP_INTERVAL_SECONDS = 30.0
# Why a family cannot be calculated now, by family_state, as the page shows it.
STATE_NOTICES = {
    "GRCh38_required": "Ancestry PCA needs a family on GRCh38.",
    "import_incomplete": "The family's last import did not complete; import it again before ancestry PCA.",
    "import_in_progress": "An import of this family is running; ancestry PCA is calculated once it ends.",
}
_RUNS = """
    SELECT id::text, source_id::text, trigger, reference_sha256, status, error, requested_at, started_at, finished_at
    FROM ancestry_runs WHERE family_uuid = CAST(:family AS uuid) AND project_id = CAST(:project AS uuid)
    ORDER BY requested_at DESC, id DESC
"""
_IMPORT_RUNNING = """
    SELECT 1 FROM family_import_jobs
    WHERE status IN ('queued', 'validating', 'running')
      AND (family_id = :family OR metadata->>'requested_family_id' = :family)
    LIMIT 1
"""
_MEMBERS = """
    SELECT s.sample_id, s.metadata AS sample_metadata, fm.role
    FROM family_members fm JOIN samples s ON s.id = fm.sample_id
    WHERE fm.family_id = CAST(:family AS uuid) AND fm.active
    ORDER BY lower(s.sample_id), s.sample_id
"""


@functools.lru_cache(maxsize=4)
def _load_reference_cached(path: str, size: int, mtime_ns: int) -> AncestryReference:
    # Keyed by the file's size and modification time: a replaced model is read again.
    return load_reference(Path(path))


def configured_reference() -> AncestryReference:
    path = Path(settings.ancestry_reference_path) if settings.ancestry_reference_path else DEFAULT_REFERENCE
    try:
        stat = path.stat()
    except OSError as exc:
        raise AncestryInputError("The ancestry reference is unavailable or failed validation.") from exc
    return _load_reference_cached(str(path), stat.st_size, stat.st_mtime_ns)


async def family_scope(session: AsyncSession, family_id: str, user: CurrentUser, project_id: str | None,
                       *, change: bool = False) -> tuple[FamilyMetadataContext, str]:
    """The family and the one project an ancestry read or change is about.

    A family outside the caller's projects, and a project outside the family's visible ones,
    answer 404 as an unknown one does; ``change`` refuses a reader of the project.
    """
    project = require_uuid(project_id, "Project not found", status_code=404) if project_id else None
    family = await get_accessible_family_mapping(session, family_id, user)
    projects = visible_metadata_project_ids(family.get("project_ids"), user)
    if project is not None and project not in projects:
        raise RecordNotVisible("project", "Project not found")
    if project is None:
        if len(projects) != 1:
            raise HTTPException(400, "Choose one of this family's projects for ancestry.")
        project = projects[0]
    context = await build_family_metadata_context(session, family_identifier=family_id, user=user, project_id=project)
    if change and not user_can_change_metadata_projects([project], user):
        raise ReadOnlyAccess()
    return context, project


async def family_state(session: AsyncSession, family_uuid: str, family_identifier: str, assembly_name: str | None) -> str:
    """``ready`` when the family's callsets can be read now, or the first reason they cannot."""
    if assembly_name != "GRCh38":
        return "GRCh38_required"
    metadata = (await session.execute(text("SELECT metadata FROM families WHERE id = CAST(:id AS uuid)"),
                                      {"id": family_uuid})).scalar_one_or_none()
    if not isinstance(metadata, dict):
        metadata = {}
    if metadata.get("import_incomplete") not in (None, False) or metadata.get("import_unfinished"):
        return "import_incomplete"
    if (await session.execute(text(_IMPORT_RUNNING), {"family": family_identifier})).first():
        return "import_in_progress"
    return "ready"


async def _members(session: AsyncSession, family_uuid: str) -> list[dict[str, Any]]:
    rows = (await session.execute(text(_MEMBERS), {"family": family_uuid})).mappings().all()
    return [{"sample_id": str(r["sample_id"]), "role": r["role"],
             "sample_metadata": r["sample_metadata"] if isinstance(r["sample_metadata"], dict) else {}}
            for r in rows]


async def _storage_version(family_uuid: str) -> str | None:
    try:
        return await get_family_small_variant_data_version("GRCh38", family_uuid)
    except Exception as exc:  # noqa: BLE001 - shown as "not known to be current"; no values logged
        logger.warning("Ancestry data-version check failed: %s", describe_error(exc))
        return None


async def family_ancestry(session: AsyncSession, family_id: str, user: CurrentUser, project_id: str | None,
                          run_id: str | None = None) -> FamilyAncestryOut:
    context, project = await family_scope(session, family_id, user, project_id)
    params = {"family": context.family_uuid, "project": project}
    state = await family_state(session, context.family_uuid, context.family_id, context.assembly_name)
    runs = [AncestryRunOut(**dict(r)) for r in (await session.execute(text(_RUNS), params)).mappings().all()]
    notices = []
    active = None
    try:
        active = (await asyncio.to_thread(configured_reference)).public
    except AncestryInputError:
        notices.append("The ancestry reference is unavailable or failed validation. Historical projections remain available.")
    selection = ""
    query_params = dict(params)
    if run_id is not None:
        query_params["run"] = require_uuid(run_id, "Ancestry analysis not found", status_code=404)
        selection = "AND r.id = CAST(:run AS uuid)"
    # selection is a fixed SQL fragment; the only caller-supplied value is bound.
    row = (await session.execute(text("""
        SELECT a.run_id::text, a.source_id::text, a.reference_sha256, a.samples, a.storage_version,
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
        current = row["storage_version"] is not None and row["storage_version"] == await _storage_version(context.family_uuid)
        analysis = AncestryAnalysisOut(**{k: row[k] for k in (
            "run_id", "source_id", "reference_sha256", "samples", "storage_version", "calculation_version",
            "assumptions", "created_at")},
            current_genotypes=current,
            current_reference=active is not None and row["reference_sha256"] == active.sha256)
    if state != "ready":
        notices.append(STATE_NOTICES.get(state, "Ancestry PCA is unavailable for this family."))
    can_change = user_can_change_metadata_projects([project], user)
    return FamilyAncestryOut(project_id=project, state=state, reference=reference, analysis=analysis,
                             active_reference_sha256=active.sha256 if active else None, runs=runs, notices=notices,
                             can_calculate=state == "ready" and active is not None and can_change,
                             choices=await choices_store.choices_out(session, context.family_uuid, project),
                             can_choose=can_change,
                             files=await ancestry_files.family_files(session, context.family_uuid, project))


async def _insert_run(session: AsyncSession, *, family_uuid: str, family_identifier: str, project: str,
                      reference: AncestryReferenceOut, trigger: str, requested_by: str | None,
                      storage_version: str | None) -> str:
    await session.execute(text("""
        INSERT INTO ancestry_reference_panels(sha256, snapshot) VALUES (:sha, CAST(:snapshot AS jsonb))
        ON CONFLICT (sha256) DO NOTHING
    """), {"sha": reference.sha256, "snapshot": reference.model_dump_json()})
    result = await session.execute(text("""
        INSERT INTO ancestry_runs(family_uuid, family_identifier, project_id, reference_sha256, requested_by,
                                  trigger, storage_version)
        VALUES (CAST(:family AS uuid), :identifier, CAST(:project AS uuid), :sha, CAST(:actor AS uuid),
                :trigger, :version)
        RETURNING id::text
    """), {"family": family_uuid, "identifier": family_identifier, "project": project, "sha": reference.sha256,
            "actor": requested_by, "trigger": trigger, "version": storage_version})
    return str(result.scalar_one())


async def queue_family(session: AsyncSession, family_id: str, user: CurrentUser, project_id: str | None) -> AncestryRunOut:
    context, project = await family_scope(session, family_id, user, project_id, change=True)
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                          {"key": f"ancestry-queue:{context.family_uuid}:{project}"})
    if not await try_share_family_variant_writes(session, context.family_uuid):
        raise HTTPException(409, "Genotypes are being written; retry once the import finishes.")
    snapshot = await family_ancestry(session, family_id, user, project)
    if not snapshot.can_calculate:
        raise HTTPException(409, "A GRCh38 family with a completed import and a valid ancestry reference is required.")
    if any(r.status in {"queued", "running"} for r in snapshot.runs):
        raise HTTPException(409, "An ancestry calculation is already queued or running for this family.")
    reference = (await asyncio.to_thread(configured_reference)).public
    run = await _insert_run(session, family_uuid=context.family_uuid, family_identifier=context.family_id,
                            project=project, reference=reference, trigger="user", requested_by=user.id,
                            storage_version=await _storage_version(context.family_uuid))
    await session.commit()
    return next(r for r in (await family_ancestry(session, family_id, user, project)).runs if r.id == run)


async def set_choice(session: AsyncSession, family_id: str, user: CurrentUser, project_id: str | None,
                     sample_id: str, choice: AncestrySampleChoiceIn,
                     bed: tuple[str, bytes] | None = None, bed_required: bool = False) -> FamilyAncestryOut:
    """Record how one individual is read from now on, and queue a calculation that reads it.

    ``bed``, a BED file's name and bytes, gives the regions of a chosen callset; the file is not
    kept, only the reference markers inside it and its checksum. ``bed_required``: the request
    was to give one (checked after the caller may change the project).
    """
    context, project = await family_scope(session, family_id, user, project_id, change=True)
    if bed_required and (bed is None or choice.callset is None):
        raise HTTPException(422, "Send the callset and the BED file.")
    member = next((m for m in await _members(session, context.family_uuid) if m["sample_id"] == sample_id), None)
    choices_store.check_choice(member, choice)
    if bed is not None and (choice.mode != "callset" or choice.panel_id is not None):
        raise HTTPException(422, "A BED file gives the regions of a chosen callset, instead of a panel.")
    regions = None
    if choice.mode == "callset" and (bed is not None or choice.panel_id is not None):
        try:
            reference = await asyncio.to_thread(configured_reference)
        except AncestryInputError as exc:
            raise HTTPException(409, str(exc)) from exc
        if bed is not None:
            name, data = bed
            intervals = choices_store.parse_bed(data)
            regions = choices_store.regions_record(
                source="bed", name=choices_store.file_label(name),
                loci=choices_store.marker_loci_in(reference, intervals, coding_only=False),
                interval_count=sum(len(spans) for spans in intervals.values()),
                reference_sha256=reference.public.sha256, data=data)
        else:
            panel, intervals = await choices_store.panel_intervals(session, str(choice.panel_id), context.assembly_id)
            if not intervals:
                raise HTTPException(409, "The gene panel has no regions on this family's assembly.")
            regions = choices_store.regions_record(
                source="panel", name=str(panel["name"]),
                loci=choices_store.marker_loci_in(reference, intervals, coding_only=True),
                interval_count=sum(len(spans) for spans in intervals.values()),
                reference_sha256=reference.public.sha256, panel_id=str(panel["id"]),
                panel_version=int(panel["version"]))
    elif choice.mode == "callset" and choice.keep_regions:
        regions = (await choices_store.current_choices(session, context.family_uuid, project)).get(sample_id, {}).get("regions")
        if regions is None:
            raise HTTPException(409, "There are no regions to keep for this individual.")
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                          {"key": f"ancestry-queue:{context.family_uuid}:{project}"})
    await choices_store.insert_choice(session, family_uuid=context.family_uuid, project=project, sample_id=sample_id,
                                      mode=choice.mode, callset=choice.callset, regions=regions, user_id=user.id)
    # A calculation that reads the choice: unless one is queued (it will read it), and when the
    # family can be read now. A run already running is followed by the sweep.
    state = await family_state(session, context.family_uuid, context.family_id, context.assembly_name)
    queued = (await session.execute(text("""
        SELECT 1 FROM ancestry_runs WHERE family_uuid = CAST(:family AS uuid) AND project_id = CAST(:project AS uuid)
          AND status = 'queued' LIMIT 1
    """), {"family": context.family_uuid, "project": project})).first()
    if state == "ready" and queued is None:
        try:
            reference_out = (await asyncio.to_thread(configured_reference)).public
        except AncestryInputError:
            reference_out = None
        if reference_out is not None:
            await _insert_run(session, family_uuid=context.family_uuid, family_identifier=context.family_id,
                              project=project, reference=reference_out, trigger="user", requested_by=user.id,
                              storage_version=await _storage_version(context.family_uuid))
    await session.commit()
    return await family_ancestry(session, family_id, user, project)


async def queue_changed_families(session: AsyncSession, family_uuids: list[str] | None = None) -> int:
    """Queue an automatic run for each family and project whose current small-variant data
    version no run has been queued for or read, once no import of the family runs. Returns how
    many it queued; commits them. ``family_uuids`` limits the sweep to those families."""
    try:
        reference = (await asyncio.to_thread(configured_reference)).public
    except AncestryInputError:
        return 0
    # Fixed assembly and allowlisted table helper; nothing here comes from a request.
    table = _small_table_name("GRCh38", "family_data_version")
    versions = {str(family): f"{int(count or 0)}:{int(total or 0)}" for family, count, total in
                (await storage_execute(f"SELECT family_guid, count(), sum(token) FROM {table} GROUP BY family_guid")
                 or [])}
    if family_uuids is not None:
        versions = {family: version for family, version in versions.items() if family in set(family_uuids)}
    if not versions:
        return 0
    # One sweep at a time across the worker processes: two would queue the same runs.
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('ancestry-sweep'))"))
    candidates = (await session.execute(text("""
        SELECT f.id::text AS family_uuid, f.family_id, fp.project_id::text AS project_id
        FROM families f JOIN family_projects fp ON fp.family_id = f.id
        WHERE f.id::text = ANY(CAST(:families AS text[]))
          AND NOT EXISTS (
            SELECT 1 FROM ancestry_runs r WHERE r.family_uuid = f.id AND r.project_id = fp.project_id
              AND r.status IN ('queued', 'running'))
          AND (
            -- Genotypes no run has been queued for or read.
            NOT EXISTS (
              SELECT 1 FROM ancestry_runs r WHERE r.family_uuid = f.id AND r.project_id = fp.project_id
                AND r.storage_version = (CAST(:versions AS jsonb) ->> f.id::text))
            -- Or a user's choice made, or a declared VCF file scanned, after the latest run was queued.
            OR EXISTS (
              SELECT 1 FROM ancestry_sample_choices c WHERE c.family_uuid = f.id AND c.project_id = fp.project_id
                AND c.chosen_at > (SELECT max(r.requested_at) FROM ancestry_runs r
                                   WHERE r.family_uuid = f.id AND r.project_id = fp.project_id))
            OR EXISTS (
              SELECT 1 FROM ancestry_files af JOIN ancestry_file_scans sc ON sc.file_id = af.id
              WHERE af.family_uuid = f.id AND af.project_id = fp.project_id
                AND sc.scanned_at > (SELECT max(r.requested_at) FROM ancestry_runs r
                                     WHERE r.family_uuid = f.id AND r.project_id = fp.project_id)))
        ORDER BY f.family_id, fp.project_id
    """), {"families": sorted(versions), "versions": json.dumps(versions)})).mappings().all()
    queued = 0
    for row in candidates:
        _, assembly = await _resolve_family_assembly(session, family_uuid=row["family_uuid"], project_id=row["project_id"])
        if await family_state(session, row["family_uuid"], row["family_id"], assembly) != "ready":
            continue
        await _insert_run(session, family_uuid=row["family_uuid"], family_identifier=row["family_id"],
                          project=row["project_id"], reference=reference, trigger="automatic", requested_by=None,
                          storage_version=versions[row["family_uuid"]])
        queued += 1
    await session.commit()
    return queued


async def claim_run(session: AsyncSession) -> str | None:
    # A running job whose row lock is free has lost its worker (its process stopped): the
    # lock is held from the job's start to its end, so SKIP LOCKED passes over every live one.
    # The minute covers the moment between a claim's commit and its worker taking the lock.
    await session.execute(text("""
        UPDATE ancestry_runs SET status = 'interrupted', error = :error, finished_at = now()
        WHERE id IN (SELECT id FROM ancestry_runs WHERE status = 'running'
          AND started_at < now() - interval '1 minute' FOR UPDATE SKIP LOCKED)
    """), {"error": INTERRUPTED})
    # A family whose import runs waits: its genotypes are being written.
    run = (await session.execute(text("""
        UPDATE ancestry_runs SET status = 'running', started_at = now()
        WHERE id = (SELECT r.id FROM ancestry_runs r
          WHERE r.status = 'queued' AND NOT EXISTS (
            SELECT 1 FROM family_import_jobs i WHERE i.status IN ('queued', 'validating', 'running')
              AND (i.family_id = r.family_identifier OR i.metadata->>'requested_family_id' = r.family_identifier))
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


async def _check_family(session: AsyncSession, run: dict) -> None:
    """The run's family may be read now: its requester (a user's run) may still change it, it is
    on GRCh38 with a completed import, and nothing writes its variants. Takes the family's
    variant-write locks for the rest of the session's transaction."""
    family_uuid, project = str(run["family_uuid"]), str(run["project_id"])
    if run["trigger"] == "user":
        user = await get_current_user_by_id(session, str(run["requested_by"]))
        if user is None or not user.is_active:
            raise AncestryInputError("The requester no longer has access to this project.")
        context, _ = await family_scope(session, run["family_identifier"], user, project, change=True)
        if context.family_uuid != family_uuid:
            raise AncestryInputError("The original family no longer exists.")
    linked = (await session.execute(text("""
        SELECT f.family_id FROM families f JOIN family_projects fp ON fp.family_id = f.id
        WHERE f.id = CAST(:family AS uuid) AND fp.project_id = CAST(:project AS uuid)
    """), {"family": family_uuid, "project": project})).scalar_one_or_none()
    if linked is None:
        raise AncestryInputError("The family is no longer in this project.")
    _, assembly = await _resolve_family_assembly(session, family_uuid=family_uuid, project_id=project)
    state = await family_state(session, family_uuid, str(linked), assembly)
    if state != "ready":
        raise AncestryInputError(STATE_NOTICES.get(state, "Ancestry PCA is unavailable for this family."))
    if not await try_share_family_variant_writes(session, family_uuid):
        raise AncestryInputError("Genotypes are being written; the calculation runs again after the import.")


def _assumptions(reference: AncestryReference, digest: str) -> dict[str, str | bool | float | int]:
    exome = reference.exome
    return {
        "genotypes": "the family's stored callsets (" + ", ".join(CALLSET_LABELS.values()).lower()
                     + ") at the reference markers; diploid GT; exact REF/ALT; no strand flips",
        "callset_kinds": "complete: explicit calls only; genome: an absent record is homozygous reference; "
                         "exome: an absent record is reference only inside the exome mask; partial: not projected",
        "min_coverage_ratio": MIN_COVERAGE_RATIO,
        "complete_share": COMPLETE_SHARE,
        "exome_mask": exome.definition if exome else "none: no callset is read as an exome",
        "exome_mask_sha256": exome.sha256 if exome else "",
        "filters": "PASS calls; unfiltered calls (FILTER=.) from imputed genotypes and from a callset "
                   "that filtered none of its calls",
        "min_usable_markers": MIN_MARKERS,
        "min_call_rate": MIN_CALL_RATE,
        "excluded": "maternal-plasma cfDNA samples and embryos",
        "projection": "least-squares fit of the fixed public reference axes to each individual's usable markers; "
                      "no ancestry percentages",
        "marker_rows": digest,
    }


async def calculate_run(run_id: str) -> None:
    run_id = require_uuid(run_id, "Ancestry run not found", status_code=404)
    try:
        async with get_postgres_sessionmaker()() as hold:
            # The job's row lock, held until the job ends: see the module docstring.
            row = (await hold.execute(text("SELECT * FROM ancestry_runs WHERE id = CAST(:id AS uuid) AND status = 'running' FOR UPDATE"),
                                      {"id": run_id})).mappings().first()
            if row is None:
                return
            run = dict(row)
            family_uuid, project = str(run["family_uuid"]), str(run["project_id"])
            # Checked under the family's variant-write locks, in a transaction that ends (and
            # releases them) before the callsets are read.
            async with get_postgres_sessionmaker()() as check:
                await _check_family(check, run)
                members = await _members(check, family_uuid)
                choices = await choices_store.current_choices(check, family_uuid, project)
                declared_rows, labels = await ancestry_files.file_rows(check, family_uuid, project)
                version = await get_family_small_variant_data_version("GRCh38", family_uuid)
                await check.rollback()
            await hold.execute(text("UPDATE ancestry_runs SET storage_version = :version WHERE id = CAST(:id AS uuid)"),
                               {"id": run_id, "version": version})
            reference = await asyncio.to_thread(configured_reference)
            if reference.public.sha256 != run["reference_sha256"]:
                raise AncestryInputError("The ancestry reference changed after queueing; request a new calculation.")
            rows = await read_marker_rows(family_uuid, project, reference) + declared_rows
            samples = await asyncio.to_thread(project_family, rows, reference, members, choices, labels)
            # Publish: the requester, the family and its genotypes must all still hold, checked
            # under the family's variant-write locks in the transaction that writes the result.
            await _check_family(hold, run)
            again = await read_marker_rows(family_uuid, project, reference) + (
                await ancestry_files.file_rows(hold, family_uuid, project))[0]
            if (await get_family_small_variant_data_version("GRCh38", family_uuid) != version
                    or rows_digest(again) != rows_digest(rows)):
                raise AncestryInputError("Genotypes were written during calculation; no results were published.")
            if await _members(hold, family_uuid) != members:
                raise AncestryInputError("The family's members changed during calculation; no results were published.")
            if await choices_store.current_choices(hold, family_uuid, project) != choices:
                raise AncestryInputError("A choice of how an individual is read was made during calculation; "
                                         "no results were published.")
            await hold.execute(text("""
                INSERT INTO ancestry_results(run_id, reference_sha256, samples, storage_version, calculation_version, assumptions)
                VALUES (CAST(:run AS uuid), :sha, CAST(:samples AS jsonb), :version, :calculation, CAST(:assumptions AS jsonb))
            """), {"run": run_id, "sha": reference.public.sha256,
                    "samples": json.dumps([s.model_dump(mode="json") for s in samples]), "version": version,
                    "calculation": CALCULATION_VERSION,
                    "assumptions": json.dumps(_assumptions(reference, rows_digest(rows)))})
            await hold.execute(text("UPDATE ancestry_runs SET status = 'completed', finished_at = now() WHERE id = CAST(:id AS uuid)"),
                               {"id": run_id})
            await hold.commit()
    except asyncio.CancelledError:
        await asyncio.shield(_finish_failed(run_id, "interrupted", INTERRUPTED))
        raise
    except Exception as exc:  # noqa: BLE001 - fixed errors only; no family/genotype values in logs
        logger.warning("Ancestry calculation failed: %s", describe_error(exc))
        error = str(exc) if isinstance(exc, AncestryInputError) else "Ancestry calculation failed; request a new calculation."
        await _finish_failed(run_id, "failed", error)


async def process_next_run() -> bool:
    async with get_postgres_sessionmaker()() as session:
        run = await claim_run(session)
    if run is None:
        return False
    await calculate_run(run)
    return True


async def scan_next_declared_file() -> bool:
    """Scan one VCF file a package declared for ancestry, if one waits (ancestry_files)."""
    try:
        reference = await asyncio.to_thread(configured_reference)
    except AncestryInputError:
        return False
    async with get_postgres_sessionmaker()() as session:
        return await ancestry_files.scan_next_file(session, reference)


async def sweep_changed_families() -> int:
    async with get_postgres_sessionmaker()() as session:
        try:
            return await queue_changed_families(session)
        except Exception:
            await session.rollback()
            raise


async def ancestry_worker(stop: asyncio.Event) -> None:
    """Take queued ancestry jobs one at a time until ``stop`` is set, and queue the automatic
    ones every ``SWEEP_INTERVAL_SECONDS``.

    Runs where the package-import workers run: in an import-worker process
    (``app.import_worker``), or in the API when it runs import workers itself
    (``FAMILY_IMPORT_WORKER_COUNT`` above 0).
    """
    next_sweep = 0.0
    while not stop.is_set():
        try:
            if time.monotonic() >= next_sweep:
                next_sweep = time.monotonic() + SWEEP_INTERVAL_SECONDS
                try:
                    await sweep_changed_families()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - a failed sweep never stops the queued runs
                    logger.warning("Ancestry sweep failed: %s", describe_error(exc))
            if await process_next_run():
                continue
            # Idle: scan a declared VCF file (minutes for a whole genome), then sweep at once so
            # the family's projection reads it.
            if await scan_next_declared_file():
                next_sweep = 0.0
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
