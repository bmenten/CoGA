"""Durable, project-scoped exploratory PGS catalogue, provenance and calculation queue."""
from __future__ import annotations

import asyncio
from contextlib import suppress
import hashlib
import json
import logging
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.coga_logging import describe_error
from ..core.config import settings
from ..core.postgres import get_postgres_sessionmaker
from ..core.sql import require_uuid
from ..pgs_schemas import FamilyPgsOut, PgsCatalogueOut, PgsResultOut, PgsRunOut, PgsSourceOut, PgsVersionOut
from .access_control import CurrentUser, ReadOnlyAccess, RecordNotVisible, user_can_change_metadata_projects, visible_metadata_project_ids
from .clickhouse_variant_storage import get_family_small_variant_data_version
from .clickhouse_variant_storage import _execute as storage_execute
from .clickhouse_variant_storage import _small_table_name
from .family_metadata_context import FamilyMetadataContext, build_family_metadata_context
from .family_package_common import FamilyPackageBundle, _resolve_package_path
from .family_package_source import _ensure_authorized_package_path, staged_package_source_async
from .family_variant_write_lock import try_share_family_variant_writes
from .metadata_service import get_accessible_family_mapping, get_current_user_by_id
from .pgs_scoring import CALCULATION_VERSION, SHORTLIST, PgsInputError, PgsReferenceError, Weight, parse_weights, score_file, sha256_file, weight_dicts

logger = logging.getLogger(__name__)
NOTICE = "Exploratory raw PGS only. No clinical interpretation, ancestry correction or clinical sign-out."
INTERRUPTED = "Calculation interrupted; no partial results were published. Request a new calculation."

_VERSIONS = """
    SELECT v.id::text, v.pgs_id, v.version, v.revision, m.archived, v.study, v.source_url,
           v.genome_build, v.weights_sha256, v.resolved_sha256, v.reference,
           v.variant_count, v.created_at, v.catalog_metadata, v.scoring_status, v.scoring_error
    FROM pgs_model_versions v JOIN pgs_models m USING (pgs_id)
    ORDER BY v.pgs_id, v.revision DESC
"""
_SOURCES = """
    SELECT id::text, import_job_id::text, package_path, relative_vcf, sha256, storage_version, storage_fingerprint,
           samples, absent_record_is_hom_ref, file_size, file_mtime_ns, imported_at
    FROM pgs_sources WHERE family_uuid = CAST(:family AS uuid) AND project_id = CAST(:project AS uuid)
    ORDER BY imported_at DESC, id DESC
"""
_RUNS = """
    SELECT id::text, source_id::text, version_ids, status, error, requested_at, started_at, finished_at
    FROM pgs_runs WHERE family_uuid = CAST(:family AS uuid) AND project_id = CAST(:project AS uuid)
    ORDER BY requested_at DESC
"""


async def catalogue(session: AsyncSession) -> PgsCatalogueOut:
    rows = (await session.execute(text(_VERSIONS))).mappings().all()
    return PgsCatalogueOut(shortlist=list(SHORTLIST), versions=[PgsVersionOut(**dict(row)) for row in rows])


async def genotype_fingerprint(family_uuid: str, project: str) -> str:
    # Fixed assembly and allowlisted table helper; identifiers never come from a request.
    # Count + independent sum/XOR aggregates also catch direct row loss/edits that bypass
    # the application's mutation/version contract. No genotype values leave the server.
    table = _small_table_name("GRCh38", "entries")
    rows = await storage_execute(f"""
        SELECT count(), sum(h), groupBitXor(h) FROM (
            SELECT cityHash64(tuple(chrom, pos, ref, alt, source, filters,
                `calls.sampleId`, `calls.gt`, `calls.filters`)) AS h
            FROM {table}
            WHERE family_guid = %(family)s AND project_guid = %(project)s AND sign = 1
        )
    """, {"family": family_uuid, "project": project})
    return "ch-gt-v1:" + ":".join(str(int(v)) for v in rows[0])


def active_versions(versions: list[PgsVersionOut]) -> list[PgsVersionOut]:
    """Only the latest version of a model may be selected; never fall back silently."""
    latest: dict[str, PgsVersionOut] = {}
    for version in versions:
        latest.setdefault(version.pgs_id, version)
    return [v for v in latest.values() if not v.archived and v.scoring_status == "ready"]


async def publish_version(session: AsyncSession, *, pgs_id: str, version: str | None, study: str,
                          source_url: str, weights_text: str, catalog_metadata: dict[str, Any] | None = None,
                          commit: bool = True) -> PgsVersionOut:
    url = urlparse(source_url)
    if re.fullmatch(r"PGS[0-9]{6}", pgs_id) is None:
        raise HTTPException(400, "Enter a valid PGS ID (PGS followed by six digits).")
    if (version is not None and (not version.strip() or len(version) > 100)) or not study.strip() or len(study) > 4000:
        raise HTTPException(400, "A version label and study citation are required.")
    if url.scheme != "https" or not url.netloc or url.username or url.password or len(source_url) > 2000:
        raise HTTPException(400, "Provide an HTTPS source URL without credentials.")
    scoring_status = "ready"
    scoring_error = None
    try:
        weights, reference = await asyncio.to_thread(parse_weights, weights_text, pgs_id, settings.pgs_reference_fasta or None)
    except PgsInputError as exc:
        if not catalog_metadata:
            raise HTTPException(400, str(exc)) from exc
        # Preserve a complete catalog file and all metadata even when it cannot be scored.
        # Such a version never enters a scoring queue and contains no partial weight set.
        weights, reference = [], {"reference": "unresolved"}
        scoring_status = "needs_reference" if isinstance(exc, PgsReferenceError) else "unsupported"
        scoring_error = str(exc)
    resolved = weight_dicts(weights)
    weights_sha = hashlib.sha256(weights_text.encode()).hexdigest()
    resolved_sha = hashlib.sha256(json.dumps(resolved, sort_keys=True).encode()).hexdigest()
    if version is None:
        if not catalog_metadata:
            raise HTTPException(400, "A version label is required.")
        material = weights_sha + resolved_sha + scoring_status + str(catalog_metadata["record_sha256"])
        version = "catalog-" + hashlib.sha256(material.encode()).hexdigest()[:24]
    version = version.strip()
    await session.execute(text("INSERT INTO pgs_models(pgs_id) VALUES (:pgs) ON CONFLICT DO NOTHING"), {"pgs": pgs_id})
    await session.execute(text("SELECT pgs_id FROM pgs_models WHERE pgs_id = :pgs FOR UPDATE"), {"pgs": pgs_id})
    latest = (await session.execute(text("SELECT version, revision FROM pgs_model_versions WHERE pgs_id = :pgs ORDER BY revision DESC LIMIT 1"),
                                    {"pgs": pgs_id})).mappings().first()
    if catalog_metadata and latest and (latest["version"] == version or latest["version"].startswith(version + "-r")):
        return next(v for v in (await catalogue(session)).versions if v.pgs_id == pgs_id and v.version == latest["version"])
    duplicate = (await session.execute(text("SELECT 1 FROM pgs_model_versions WHERE pgs_id = :pgs AND version = :version"),
                                       {"pgs": pgs_id, "version": version.strip()})).first()
    if duplicate:
        if catalog_metadata:
            # A Catalog rollback to previously seen content is a new current revision,
            # while consecutive identical imports reuse the latest immutable version.
            version += f"-r{int(latest['revision']) + 1 if latest else 1}"
        else:
            raise HTTPException(409, "That model version already exists; publish a new version label.")
    await session.execute(text("""
        INSERT INTO pgs_model_versions(pgs_id, version, revision, study, source_url, weights_sha256,
                                       resolved_sha256, reference, weights_text, weights, variant_count,
                                       catalog_metadata, scoring_status, scoring_error)
        SELECT :pgs, :version, COALESCE(MAX(revision), 0) + 1, :study, :url, :checksum,
               :resolved_checksum, CAST(:reference AS jsonb), :original, CAST(:weights AS jsonb), :count,
               CAST(:catalog AS jsonb), :scoring_status, :scoring_error
        FROM pgs_model_versions WHERE pgs_id = :pgs
    """), {"pgs": pgs_id, "version": version.strip(), "study": study.strip(), "url": source_url,
            "checksum": weights_sha,
            "resolved_checksum": resolved_sha,
            "reference": json.dumps(reference), "original": weights_text,
            "weights": json.dumps(resolved),
            "count": len(weights) if not catalog_metadata else int(catalog_metadata["scoring_file"]["variant_count"]),
            "catalog": json.dumps(catalog_metadata or {}), "scoring_status": scoring_status, "scoring_error": scoring_error})
    if commit:
        await session.commit()
    return next(v for v in (await catalogue(session)).versions if v.pgs_id == pgs_id and v.version == version.strip())


async def set_archived(session: AsyncSession, pgs_id: str, archived: bool) -> PgsCatalogueOut:
    result = await session.execute(text("UPDATE pgs_models SET archived = :archived WHERE pgs_id = :pgs RETURNING pgs_id"),
                                   {"pgs": pgs_id, "archived": archived})
    if result.first() is None:
        raise HTTPException(404, "PGS model not found")
    await session.commit()
    return await catalogue(session)


async def family_scope(session: AsyncSession, family_id: str, user: CurrentUser, project_id: str | None,
                       *, change: bool = False) -> tuple[FamilyMetadataContext, str]:
    project = require_uuid(project_id, "Project not found", status_code=404) if project_id else None
    # Resolve family visibility first; a project outside its visible memberships is also 404.
    family = await get_accessible_family_mapping(session, family_id, user)
    projects = visible_metadata_project_ids(family.get("project_ids"), user)
    if project is not None and project not in projects:
        raise RecordNotVisible("project", "Project not found")
    if project is None:
        if len(projects) != 1:
            raise HTTPException(400, "Choose one of this family's projects for PGS.")
        project = projects[0]
    context = await build_family_metadata_context(session, family_identifier=family_id, user=user, project_id=project)
    if change and not user_can_change_metadata_projects([project], user):
        raise ReadOnlyAccess()
    return context, project


async def _source_state(session: AsyncSession, context: FamilyMetadataContext, source: PgsSourceOut | None, project: str) -> str:
    if context.assembly_name != "GRCh38":
        return "GRCh38_required"
    flags = (await session.execute(text("SELECT metadata FROM families WHERE id = CAST(:id AS uuid)"),
                                   {"id": context.family_uuid})).scalar_one_or_none()
    if not isinstance(flags, dict) or flags.get("import_incomplete") not in (None, False) or flags.get("import_unfinished"):
        return "import_incomplete"
    running = (await session.execute(text("""
        SELECT 1 FROM family_import_jobs
        WHERE status IN ('queued', 'validating', 'running')
          AND (family_id = :family OR metadata->>'requested_family_id' = :family)
        LIMIT 1
    """), {"family": context.family_id})).first()
    if running:
        return "import_in_progress"
    if source is None:
        return "no_full_wgs_source"
    if source.import_job_id:
        status = (await session.execute(text("SELECT status FROM family_import_jobs WHERE id = CAST(:id AS uuid)"),
                                        {"id": source.import_job_id})).scalar_one_or_none()
        if status != "completed":
            return "import_not_completed"
    if source.samples != sorted(context.sample_name_to_uuid):
        return "family_members_changed"
    try:
        if await get_family_small_variant_data_version("GRCh38", context.family_uuid) != source.storage_version:
            return "genotype_storage_changed"
        if await genotype_fingerprint(context.family_uuid, project) != source.storage_fingerprint:
            return "genotype_storage_changed"
    except Exception as exc:  # noqa: BLE001 - retain history on unavailable/deleted genotype storage
        logger.warning("PGS genotype verification failed: %s", describe_error(exc))
        return "genotype_storage_unavailable"
    if "://" not in source.package_path:
        try:
            path = await asyncio.to_thread(_resolve_package_path, Path(source.package_path), source.relative_vcf)
            if path is None:
                return "source_unavailable"
            stat = await asyncio.to_thread(path.stat)
            if stat.st_size != source.file_size or stat.st_mtime_ns != source.file_mtime_ns:
                return "source_changed"
        except (OSError, HTTPException):
            return "source_unavailable"
    return "ready"


async def family_pgs(session: AsyncSession, family_id: str, user: CurrentUser, project_id: str | None) -> FamilyPgsOut:
    context, project = await family_scope(session, family_id, user, project_id)
    params = {"family": context.family_uuid, "project": project}
    sources = [PgsSourceOut(**dict(r)) for r in (await session.execute(text(_SOURCES), params)).mappings().all()]
    runs = [PgsRunOut(**dict(r)) for r in (await session.execute(text(_RUNS), params)).mappings().all()]
    versions = (await catalogue(session)).versions
    state = await _source_state(session, context, sources[0] if sources else None, project)
    result_rows = (await session.execute(text("""
        SELECT r.id::text, r.run_id::text, r.source_id::text, r.version_id::text, r.sample_id,
               r.raw_score, r.status, r.qc, r.calculation_version, r.assumptions, r.created_at
        FROM pgs_results r JOIN pgs_runs j ON j.id = r.run_id
        WHERE j.family_uuid = CAST(:family AS uuid) AND j.project_id = CAST(:project AS uuid)
          AND j.status = 'completed'
        ORDER BY r.created_at DESC, r.sample_id
    """), params)).mappings().all()
    results = [PgsResultOut(**dict(r), current_source=state == "ready" and r["source_id"] == sources[0].id) for r in result_rows]
    latest = {v.pgs_id: v for v in active_versions(versions)}
    covered = {r.version_id for r in results if r.current_source}
    pending = [v.id for v in latest.values() if v.id not in covered]
    notices = [NOTICE]
    if pending:
        notices.append("New or uncalculated model versions are available. Existing families are not recalculated automatically; request calculation for this family.")
    blocked: dict[str, PgsVersionOut] = {}
    for version in versions:
        blocked.setdefault(version.pgs_id, version)
    if any(not v.archived and v.scoring_status != "ready" for v in blocked.values()):
        notices.append("Some imported model versions are not ready for GT scoring: reference validation or unsupported model features require attention in administration.")
    if state != "ready":
        notices.append("PGS calculation is unavailable: " + state.replace("_", " ") + ". Historical results remain visible.")
    return FamilyPgsOut(project_id=project, source_state=state, sources=sources, runs=runs, results=results,
                        versions=versions, pending_version_ids=pending,
                        can_calculate=state == "ready" and bool(latest) and user_can_change_metadata_projects([project], user),
                        notices=notices)


async def _enqueue(session: AsyncSession, context: FamilyMetadataContext, project: str, source_id: str,
                   version_ids: list[str], user_id: str) -> str:
    result = await session.execute(text("""
        INSERT INTO pgs_runs(family_uuid, family_identifier, project_id, source_id, version_ids, requested_by)
        VALUES (CAST(:family AS uuid), :identifier, CAST(:project AS uuid), CAST(:source AS uuid),
                CAST(:versions AS jsonb), CAST(:user AS uuid)) RETURNING id::text
    """), {"family": context.family_uuid, "identifier": context.family_id, "project": project,
            "source": source_id, "versions": json.dumps(version_ids), "user": user_id})
    return str(result.scalar_one())


async def queue_family(session: AsyncSession, family_id: str, user: CurrentUser, project_id: str | None) -> PgsRunOut:
    context, project = await family_scope(session, family_id, user, project_id, change=True)
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                          {"key": f"pgs-queue:{context.family_uuid}:{project}"})
    if not await try_share_family_variant_writes(session, context.family_uuid):
        raise HTTPException(409, "Genotypes are being written; retry once the import finishes.")
    snapshot = await family_pgs(session, family_id, user, project)
    if not snapshot.can_calculate:
        raise HTTPException(409, "A successful, unchanged GRCh38 full-WGS import and active models are required.")
    if any(run.status in {"queued", "running"} for run in snapshot.runs):
        raise HTTPException(409, "A PGS calculation is already queued or running for this family.")
    latest = {v.pgs_id: v.id for v in active_versions(snapshot.versions)}
    run_id = await _enqueue(session, context, project, snapshot.sources[0].id, list(latest.values()), user.id)
    await session.commit()
    return next(r for r in (await family_pgs(session, family_id, user, project)).runs if r.id == run_id)


async def register_full_wgs(session: AsyncSession, *, bundle: FamilyPackageBundle, context: FamilyMetadataContext,
                            project_id: str | None, user: CurrentUser | None, datasets: list[Any],
                            job_id: str | None, expected_sha256: str) -> None:
    """Called only after the entire real import succeeded, before staging is removed.

    Legacy packages, dry runs, per-sample VCFs, imputed sources and skipped datasets never
    produce a source. The declaration is an explicit WGS assumption, not a claim of coverage.
    """
    declaration = bundle.manifest.metadata.get("pgs")
    if not isinstance(declaration, dict) or declaration.get("full_wgs") is not True:
        return
    from .family_package_datasets import snv_dataset_callset

    snv = bundle.manifest.datasets.get("snv")
    if (user is None or context.assembly_name != "GRCh38" or declaration.get("genome_build") != "GRCh38"
            or not isinstance(declaration.get("absent_record_is_hom_ref"), bool)
            or snv is None or not snv.family_vcf or snv.per_sample or snv_dataset_callset(bundle) != "clair3"
            or not any(d.dataset_type == "snv" and d.status == "imported" for d in datasets)):
        raise PgsInputError("PGS requires an imported joint GT full-WGS VCF on GRCh38 and an explicit absence assumption.")
    project = project_id or (context.project_ids[0] if len(context.project_ids) == 1 else None)
    if project is None or project not in context.project_ids:
        raise PgsInputError("PGS requires one explicit project for its WGS source.")
    path = _resolve_package_path(bundle.root, snv.family_vcf)
    root = await asyncio.to_thread(bundle.root.resolve)
    if path is None:
        raise PgsInputError("The WGS VCF must be contained in its import package.")
    checksum = await asyncio.to_thread(sha256_file, path)
    if checksum != expected_sha256:
        raise PgsInputError("The WGS source changed during import; no PGS source was registered.")
    stat = await asyncio.to_thread(path.stat)
    token = await get_family_small_variant_data_version("GRCh38", context.family_uuid)
    fingerprint = await genotype_fingerprint(context.family_uuid, project)
    source = (await session.execute(text("""
        INSERT INTO pgs_sources(family_uuid, family_identifier, project_id, import_job_id,
            package_path, relative_vcf, sha256, storage_version, storage_fingerprint, samples, absent_record_is_hom_ref,
            file_size, file_mtime_ns)
        VALUES (CAST(:family AS uuid), :identifier, CAST(:project AS uuid), CAST(:job AS uuid),
                :path, :relative, :checksum, :token, :fingerprint, CAST(:samples AS jsonb), :assume, :size, :mtime)
        RETURNING id::text
    """), {"family": context.family_uuid, "identifier": context.family_id, "project": project, "job": job_id,
            "path": bundle.source_uri or str(root), "relative": str(path.relative_to(root)),
            "checksum": checksum, "token": token, "fingerprint": fingerprint, "samples": json.dumps(sorted(context.sample_name_to_uuid)),
            "assume": declaration["absent_record_is_hom_ref"], "size": stat.st_size, "mtime": stat.st_mtime_ns})).scalar_one()
    active = {v.pgs_id: v.id for v in active_versions((await catalogue(session)).versions)}
    if active:
        await _enqueue(session, context, project, str(source), list(active.values()), user.id)
    # The caller commits the source and its jobs together. Publishing a model never queues.


async def claim_run(session: AsyncSession) -> str | None:
    # A running calculation holds FOR UPDATE on its run row through publication. A crashed
    # worker releases that lock; after ten minutes it is interrupted, never auto-retried.
    await session.execute(text("""
        UPDATE pgs_runs SET status = 'interrupted', error = :error, finished_at = now()
        WHERE id IN (SELECT id FROM pgs_runs WHERE status = 'running'
          AND started_at < now() - interval '10 minutes' FOR UPDATE SKIP LOCKED)
    """), {"error": INTERRUPTED})
    row = (await session.execute(text("""
        UPDATE pgs_runs SET status = 'running', started_at = now()
        WHERE id = (SELECT r.id FROM pgs_runs r JOIN pgs_sources s ON s.id = r.source_id
          LEFT JOIN family_import_jobs i ON i.id = s.import_job_id
          WHERE r.status = 'queued' AND (s.import_job_id IS NULL OR i.status NOT IN ('queued', 'validating', 'running') OR i.id IS NULL)
          ORDER BY r.requested_at LIMIT 1 FOR UPDATE OF r SKIP LOCKED)
        RETURNING id::text
    """))).scalar_one_or_none()
    await session.commit()
    return str(row) if row else None


async def _finish_failed(run_id: str, status: str, error: str) -> None:
    async with get_postgres_sessionmaker()() as session:
        await session.execute(text("""
            UPDATE pgs_runs SET status = :status, error = :error, finished_at = now()
            WHERE id = CAST(:id AS uuid) AND status = 'running'
        """), {"id": run_id, "status": status, "error": error})
        await session.commit()


async def calculate_run(run_id: str) -> None:
    run_id = require_uuid(run_id, "PGS run not found", status_code=404)
    try:
        async with get_postgres_sessionmaker()() as session:
            run = (await session.execute(text("SELECT * FROM pgs_runs WHERE id = CAST(:id AS uuid) AND status = 'running' FOR UPDATE"),
                                         {"id": run_id})).mappings().first()
            if run is None:
                return
            user = await get_current_user_by_id(session, str(run["requested_by"]))
            if user is None or not user.is_active:
                raise PgsInputError("The requester no longer has access to this project.")
            context, project = await family_scope(session, run["family_identifier"], user, str(run["project_id"]), change=True)
            if context.family_uuid != str(run["family_uuid"]):
                raise PgsInputError("The original family no longer exists.")
            if not await try_share_family_variant_writes(session, context.family_uuid):
                raise PgsInputError("Genotypes are being written; request calculation after import.")
            sources = (await session.execute(text(_SOURCES), {"family": context.family_uuid, "project": project})).mappings().all()
            if not sources or str(sources[0]["id"]) != str(run["source_id"]):
                raise PgsInputError("A newer WGS source exists; request a new calculation.")
            source = PgsSourceOut(**dict(sources[0]))
            if await _source_state(session, context, source, project) != "ready":
                raise PgsInputError("The import, family members or genotype storage changed; import full WGS again.")
            models: dict[str, list[Weight]] = {}
            for version in run["version_ids"]:
                stored = (await session.execute(text("SELECT weights, resolved_sha256, scoring_status FROM pgs_model_versions WHERE id = CAST(:id AS uuid)"),
                                                {"id": require_uuid(version, "PGS version not found", status_code=404)})).mappings().one()
                if stored["scoring_status"] != "ready":
                    raise PgsInputError("A selected model version is not ready for GT scoring.")
                if hashlib.sha256(json.dumps(stored["weights"], sort_keys=True).encode()).hexdigest() != stored["resolved_sha256"]:
                    raise PgsInputError("The stored model weights failed their checksum check.")
                models[version] = [Weight(**item) for item in stored["weights"]]
            if "://" not in source.package_path:
                await asyncio.to_thread(_ensure_authorized_package_path, Path(source.package_path))
            async with staged_package_source_async(source.package_path) as staged:
                path = await asyncio.to_thread(_resolve_package_path, Path(staged.root), source.relative_vcf)
                if path is None:
                    raise PgsInputError("The WGS source is no longer available within its import package.")
                results = await asyncio.to_thread(score_file, path, source.sha256, models, source.samples,
                                                  assume_ref=source.absent_record_is_hom_ref)
            # The transaction still holds the run row and shared variant-write locks.
            # Re-read flags/requester: permissions and imports can change during a long scan.
            user = await get_current_user_by_id(session, str(run["requested_by"]))
            if user is None or not user.is_active or not user_can_change_metadata_projects([project], user):
                raise PgsInputError("The requester no longer has access to this project.")
            context, _ = await family_scope(session, run["family_identifier"], user, project, change=True)
            if await _source_state(session, context, source, project) != "ready":
                raise PgsInputError("The family or import changed during calculation; no results were published.")
            assumptions = {"full_wgs": True, "absent_record_is_hom_ref": source.absent_record_is_hom_ref,
                           "genotypes": "diploid GT only; PASS records; no imputation or strand flips",
                           "scope": "exploratory; no ancestry correction; excluded from clinical sign-out"}
            for result in results:
                await session.execute(text("""
                    INSERT INTO pgs_results(run_id, source_id, version_id, sample_id, raw_score, status,
                                            qc, calculation_version, assumptions)
                    VALUES (CAST(:run AS uuid), CAST(:source AS uuid), CAST(:version AS uuid), :sample,
                            :score, :status, CAST(:qc AS jsonb), :calculation, CAST(:assumptions AS jsonb))
                """), {"run": run_id, "source": source.id, "version": result["version_id"], "sample": result["sample_id"],
                        "score": result["raw_score"], "status": result["status"], "qc": json.dumps(result["qc"]),
                        "calculation": CALCULATION_VERSION, "assumptions": json.dumps(assumptions)})
            await session.execute(text("UPDATE pgs_runs SET status = 'completed', finished_at = now() WHERE id = CAST(:id AS uuid)"),
                                  {"id": run_id})
            await session.commit()
    except asyncio.CancelledError:
        await asyncio.shield(_finish_failed(run_id, "interrupted", INTERRUPTED))
        raise
    except Exception as exc:  # noqa: BLE001 - log only the sanitized error type; publish no partial results
        logger.warning("PGS calculation failed: %s", describe_error(exc))
        message = str(exc) if isinstance(exc, PgsInputError) else "Calculation failed; verify source availability and project access before retrying."
        await _finish_failed(run_id, "failed", message)


async def pgs_worker(stop: asyncio.Event) -> None:
    from .pgs_catalog_service import process_next_import

    while not stop.is_set():
        try:
            if await process_next_import():
                continue
            async with get_postgres_sessionmaker()() as session:
                run_id = await claim_run(session)
            if run_id:
                await calculate_run(run_id)
                continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - worker stays available; error values never enter logs
            logger.warning("PGS worker failed: %s", describe_error(exc))
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=2)


async def stop_pgs_worker(task: asyncio.Task, stop: asyncio.Event) -> None:
    stop.set()
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
