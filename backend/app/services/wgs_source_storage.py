"""Verified, project-scoped WGS source snapshots for ancestry PCA."""
from __future__ import annotations
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import json
import logging
from pathlib import Path
from typing import Any
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from ..core.coga_logging import describe_error
from ..core.sql import require_uuid
from ..wgs_schemas import WgsSourceOut
from .access_control import CurrentUser, ReadOnlyAccess, RecordNotVisible, visible_metadata_project_ids, user_can_change_metadata_projects
from .clickhouse_variant_storage import get_family_small_variant_data_version, _small_table_name, _execute as storage_execute
from .family_metadata_context import FamilyMetadataContext, build_family_metadata_context
from .family_package_common import FamilyPackageBundle, _resolve_package_path
from .family_package_source import _ensure_authorized_package_path, staged_package_source_async
from .metadata_service import get_accessible_family_mapping
from .wgs_evidence import WgsInputError, sha256_file

logger = logging.getLogger(__name__)

_SOURCES = """
    SELECT id::text, import_job_id::text, package_path, relative_vcf, sha256, storage_version, storage_fingerprint,
           samples, absent_record_is_hom_ref, file_size, file_mtime_ns, imported_at,
           raw_file_id::text, qualification
    FROM wgs_sources WHERE family_uuid = CAST(:family AS uuid) AND project_id = CAST(:project AS uuid)
    ORDER BY imported_at DESC, id DESC
"""

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
            raise HTTPException(400, "Choose one of this family's projects for ancestry.")
        project = projects[0]
    context = await build_family_metadata_context(session, family_identifier=family_id, user=user, project_id=project)
    if change and not user_can_change_metadata_projects([project], user):
        raise ReadOnlyAccess()
    return context, project


async def _source_state(session: AsyncSession, context: FamilyMetadataContext, source: WgsSourceOut | None, project: str) -> str:
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
        logger.warning("Ancestry genotype verification failed: %s", describe_error(exc))
        return "genotype_storage_unavailable"
    if source.raw_file_id:
        try:
            path = await retained_source_path(session, source, context.family_uuid)
            stat = await asyncio.to_thread(path.stat)
            if stat.st_size != source.file_size or stat.st_mtime_ns != source.file_mtime_ns:
                return "source_changed"
        except (OSError, HTTPException, WgsInputError):
            return "source_unavailable"
    elif "://" not in source.package_path:
        try:
            package_file = await asyncio.to_thread(_resolve_package_path, Path(source.package_path), source.relative_vcf)
            if package_file is None:
                return "source_unavailable"
            stat = await asyncio.to_thread(package_file.stat)
            if stat.st_size != source.file_size or stat.st_mtime_ns != source.file_mtime_ns:
                return "source_changed"
        except (OSError, HTTPException):
            return "source_unavailable"
    return "ready"


async def retained_source_path(session: AsyncSession, source: WgsSourceOut, family_uuid: str) -> Path:
    from .raw_import_files_pg import local_file_to_download
    from .wgs_source_service import retained_file

    record = await retained_file(session, source.raw_file_id or "", family_uuid)
    path = Path(await asyncio.to_thread(local_file_to_download, record))
    expected = Path(source.package_path) / source.relative_vcf
    if record["sha256"] != source.sha256 or path != expected:
        raise WgsInputError("The retained WGS source record changed; register the source again.")
    return path


@asynccontextmanager
async def source_file(session: AsyncSession, source: WgsSourceOut, family_uuid: str) -> AsyncIterator[Path]:
    if source.raw_file_id:
        yield await retained_source_path(session, source, family_uuid)
        return
    if "://" not in source.package_path:
        await asyncio.to_thread(_ensure_authorized_package_path, Path(source.package_path))
    async with staged_package_source_async(source.package_path) as staged:
        path = await asyncio.to_thread(_resolve_package_path, Path(staged.root), source.relative_vcf)
        if path is None:
            raise WgsInputError("The WGS source is no longer available within its import package.")
        yield path


async def register_full_wgs(session: AsyncSession, *, bundle: FamilyPackageBundle, context: FamilyMetadataContext,
                            project_id: str | None, user: CurrentUser | None, datasets: list[Any],
                            job_id: str | None, expected_sha256: str) -> None:
    """Called only after the entire real import succeeded, before staging is removed.

    Legacy packages, dry runs, per-sample VCFs, imputed sources and skipped datasets never
    produce a source. The declaration is an explicit WGS assumption, not a claim of coverage.
    """
    declaration = bundle.manifest.metadata.get("ancestry")
    if not isinstance(declaration, dict) or declaration.get("full_wgs") is not True:
        return
    if not isinstance(declaration.get("allow_unfiltered", False), bool):
        raise WgsInputError("The unfiltered-record policy must be an explicit boolean.")
    from .family_package_datasets import snv_dataset_callset

    snv = bundle.manifest.datasets.get("snv")
    if (user is None or context.assembly_name != "GRCh38" or declaration.get("genome_build") != "GRCh38"
            or not isinstance(declaration.get("absent_record_is_hom_ref"), bool)
            or snv is None or not snv.family_vcf or snv.per_sample or snv_dataset_callset(bundle) != "clair3"
            or not any(d.dataset_type == "snv" and d.status == "imported" for d in datasets)):
        raise WgsInputError("Ancestry requires an imported joint GT full-WGS VCF on GRCh38 and an explicit absence assumption.")
    project = project_id or (context.project_ids[0] if len(context.project_ids) == 1 else None)
    if project is None or project not in context.project_ids:
        raise WgsInputError("Ancestry requires one explicit project for its WGS source.")
    path = _resolve_package_path(bundle.root, snv.family_vcf)
    root = await asyncio.to_thread(bundle.root.resolve)
    if path is None:
        raise WgsInputError("The WGS VCF must be contained in its import package.")
    checksum = await asyncio.to_thread(sha256_file, path)
    if checksum != expected_sha256:
        raise WgsInputError("The WGS source changed during import; no Ancestry source was registered.")
    stat = await asyncio.to_thread(path.stat)
    token = await get_family_small_variant_data_version("GRCh38", context.family_uuid)
    fingerprint = await genotype_fingerprint(context.family_uuid, project)
    _ = (await session.execute(text("""
        INSERT INTO wgs_sources(family_uuid, family_identifier, project_id, import_job_id,
            package_path, relative_vcf, sha256, storage_version, storage_fingerprint, samples, absent_record_is_hom_ref,
            file_size, file_mtime_ns, qualification)
        VALUES (CAST(:family AS uuid), :identifier, CAST(:project AS uuid), CAST(:job AS uuid),
                :path, :relative, :checksum, :token, :fingerprint, CAST(:samples AS jsonb), :assume, :size, :mtime,
                CAST(:qualification AS jsonb))
        RETURNING id::text
    """), {"family": context.family_uuid, "identifier": context.family_id, "project": project, "job": job_id,
            "path": bundle.source_uri or str(root), "relative": str(path.relative_to(root)),
            "checksum": checksum, "token": token, "fingerprint": fingerprint, "samples": json.dumps(sorted(context.sample_name_to_uuid)),
            "assume": declaration["absent_record_is_hom_ref"], "size": stat.st_size, "mtime": stat.st_mtime_ns,
            "qualification": json.dumps({"full_wgs_declared": True,
                                          "allow_unfiltered": declaration.get("allow_unfiltered", False)})})).scalar_one()
    # The caller commits the source after its completed import.


async def latest_source(session: AsyncSession, family_uuid: str, project: str) -> WgsSourceOut:
    row = (await session.execute(text(_SOURCES), {"family": family_uuid, "project": project})).mappings().first()
    if row is None:
        raise HTTPException(409, "No verified WGS source is available.")
    return WgsSourceOut(**dict(row))
