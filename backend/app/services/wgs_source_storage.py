"""Verified, project-scoped WGS source snapshots for the exploratory ancestry PCA.

A source names the whole-genome VCF a PCA projection reads, with what it was checked against
when it was registered: its checksum, size and modification time, the family's samples, and the
version token and genotype fingerprint of the family's stored small variants. A source is
``ready`` only while all of these still hold. Nothing here writes variants or enters the
clinical sign-out.
"""
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
from .access_control import (
    CurrentUser,
    ReadOnlyAccess,
    RecordNotVisible,
    user_can_change_metadata_projects,
    visible_metadata_project_ids,
)
from .clickhouse_variant_storage import (
    _execute as storage_execute,
    _small_table_name,
    get_family_small_variant_data_version,
)
from .family_metadata_context import FamilyMetadataContext, build_family_metadata_context
from .family_package_common import FamilyPackageBundle, _resolve_package_path
from .family_package_source import _ensure_authorized_package_path, staged_package_source_async
from .family_variant_write_lock import try_share_family_variant_writes
from .metadata_service import get_accessible_family_mapping
from .nipt import FAMILY_ANALYSIS_TYPE_KEY, MONOGENIC_NIPT_ANALYSIS_TYPE, is_cfdna_sample_metadata
from .wgs_evidence import WgsInputError, read_vcf_sample_columns, sha256_file

logger = logging.getLogger(__name__)

_SOURCES = """
    SELECT id::text, import_job_id::text, package_path, relative_vcf, sha256, storage_version, storage_fingerprint,
           samples, absent_record_is_hom_ref, file_size, file_mtime_ns, imported_at,
           raw_file_id::text, qualification
    FROM wgs_sources WHERE family_uuid = CAST(:family AS uuid) AND project_id = CAST(:project AS uuid)
    ORDER BY imported_at DESC, id DESC
"""


async def genotype_fingerprint(family_uuid: str, project: str) -> str:
    """Count, sum and XOR of a hash of every stored call of the family's small variants.

    The data-version token moves on every write through CoGA; this also catches rows lost or
    changed some other way (a merge, a direct edit). No genotype leaves the server.
    """
    # Fixed assembly and allowlisted table helper; identifiers never come from a request.
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


def _has_non_individual_samples(family_metadata: dict[str, Any], context: FamilyMetadataContext) -> bool:
    """A monogenic NIPT family, or one with an embryo or a maternal-plasma cfDNA sample.

    A PCA projection reads an individual's own diploid genotypes: a cfDNA sample is a mixture
    of mother and fetus, and an embryo's calls come from a few amplified cells.
    """
    if family_metadata.get(FAMILY_ANALYSIS_TYPE_KEY) == MONOGENIC_NIPT_ANALYSIS_TYPE:
        return True
    return any(
        str(row.get("role") or "").lower() == "embryo" or is_cfdna_sample_metadata(row.get("sample_metadata"))
        for row in context.sample_rows
    )


async def source_state(session: AsyncSession, context: FamilyMetadataContext, source: WgsSourceOut | None,
                       project: str, *, deep: bool = False) -> str:
    """``ready`` when ``source`` still describes the family, or the first reason it does not.

    With no source, ``no_full_wgs_source`` means the family could register one. ``deep`` also
    compares the stored genotype fingerprint, a scan of the family's calls: done when a
    calculation is queued and before it publishes, not on every read of the page.
    """
    if context.assembly_name != "GRCh38":
        return "GRCh38_required"
    family_metadata = (await session.execute(text("SELECT metadata FROM families WHERE id = CAST(:id AS uuid)"),
                                             {"id": context.family_uuid})).scalar_one_or_none()
    if not isinstance(family_metadata, dict):
        family_metadata = {}
    if _has_non_individual_samples(family_metadata, context):
        return "unsupported_samples"
    if family_metadata.get("import_incomplete") not in (None, False) or family_metadata.get("import_unfinished"):
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
        if deep and await genotype_fingerprint(context.family_uuid, project) != source.storage_fingerprint:
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
    """The local path of a source registered from a retained upload, still the same record."""
    from .raw_import_files_pg import local_file_to_download
    from .wgs_source_service import retained_file

    record = await retained_file(session, source.raw_file_id or "", family_uuid)
    path = Path(await asyncio.to_thread(local_file_to_download, record))
    expected = Path(source.package_path) / source.relative_vcf
    if record["sha256"] != source.sha256 or path != expected:
        raise WgsInputError("The retained WGS source record changed; register the source again.")
    return path


@asynccontextmanager
async def source_file(source: WgsSourceOut, retained: Path | None) -> AsyncIterator[Path]:
    """The source's VCF on local disk while the block runs: ``retained`` (a retained upload's
    path, from :func:`retained_source_path`) as it is, a package's file from its folder, or a
    gs:// or s3:// package staged to a temporary folder."""
    if source.raw_file_id:
        if retained is None:
            raise WgsInputError("The retained WGS source is unavailable; register the source again.")
        yield retained
        return
    if "://" not in source.package_path:
        await asyncio.to_thread(_ensure_authorized_package_path, Path(source.package_path))
    async with staged_package_source_async(source.package_path) as staged:
        path = await asyncio.to_thread(_resolve_package_path, Path(staged.root), source.relative_vcf)
        if path is None:
            raise WgsInputError("The WGS source is no longer available within its import package.")
        yield path


def package_ancestry_declaration(bundle: FamilyPackageBundle) -> dict[str, Any] | None:
    """The manifest's ``metadata.ancestry`` when it declares a full-WGS source, else None."""
    declaration = bundle.manifest.metadata.get("ancestry")
    if isinstance(declaration, dict) and declaration.get("full_wgs") is True:
        return declaration
    return None


async def register_full_wgs(session: AsyncSession, *, bundle: FamilyPackageBundle, context: FamilyMetadataContext,
                            project_id: str | None, datasets: list[Any], job_id: str | None,
                            expected_sha256: str, expected_version: str) -> None:
    """Register the package's family SNV VCF as the family's WGS source, after its import.

    Called once the import completed and released the family's variant writes, with the VCF's
    checksum taken before its datasets ran and the family's data-version token taken when they
    had: a file changed since, or a write of the family's variants in between, registers
    nothing. Commits the source; raises :class:`WgsInputError` on a package it cannot take.
    The declaration is an explicit WGS assumption, not a claim of coverage.
    """
    declaration = package_ancestry_declaration(bundle)
    if declaration is None:
        return
    if not isinstance(declaration.get("allow_unfiltered", False), bool):
        raise WgsInputError("The unfiltered-record policy must be an explicit boolean.")
    from .family_package_datasets import snv_dataset_callset

    snv = bundle.manifest.datasets.get("snv")
    if (context.assembly_name != "GRCh38" or declaration.get("genome_build") != "GRCh38"
            or not isinstance(declaration.get("absent_record_is_hom_ref"), bool)
            or snv is None or not snv.family_vcf or snv.per_sample or snv_dataset_callset(bundle) != "clair3"
            or not any(d.dataset_type == "snv" and d.status == "imported" for d in datasets)):
        raise WgsInputError("Ancestry requires an imported joint GT full-WGS VCF on GRCh38 and an explicit absence assumption.")
    project = project_id or (context.project_ids[0] if len(context.project_ids) == 1 else None)
    if project is None or project not in context.project_ids:
        raise WgsInputError("Ancestry requires one explicit project for its WGS source.")
    path = _resolve_package_path(bundle.root, snv.family_vcf)
    if path is None:
        raise WgsInputError("The WGS VCF must be contained in its import package.")
    root = await asyncio.to_thread(bundle.root.resolve)
    samples = sorted(context.sample_name_to_uuid)
    # The projection reads each member's own column by name: a VCF whose columns are not
    # exactly the family's samples (renamed in the manifest, or another family's) is refused.
    columns = await asyncio.to_thread(read_vcf_sample_columns, path)
    if sorted(columns) != samples or len(columns) != len(set(columns)):
        raise WgsInputError("The WGS VCF must contain exactly this family's sample columns.")
    if await asyncio.to_thread(sha256_file, path) != expected_sha256:
        raise WgsInputError("The WGS source changed during import; no ancestry source was registered.")
    stat = await asyncio.to_thread(path.stat)
    if not await try_share_family_variant_writes(session, context.family_uuid):
        raise WgsInputError("The family's variants are being written again; no ancestry source was registered.")
    if await get_family_small_variant_data_version("GRCh38", context.family_uuid) != expected_version:
        raise WgsInputError("The family's variants changed after the import; no ancestry source was registered.")
    fingerprint = await genotype_fingerprint(context.family_uuid, project)
    await session.execute(text("""
        INSERT INTO wgs_sources(family_uuid, family_identifier, project_id, import_job_id,
            package_path, relative_vcf, sha256, storage_version, storage_fingerprint, samples, absent_record_is_hom_ref,
            file_size, file_mtime_ns, qualification)
        VALUES (CAST(:family AS uuid), :identifier, CAST(:project AS uuid), CAST(:job AS uuid),
                :path, :relative, :checksum, :token, :fingerprint, CAST(:samples AS jsonb), :assume, :size, :mtime,
                CAST(:qualification AS jsonb))
    """), {"family": context.family_uuid, "identifier": context.family_id, "project": project, "job": job_id,
            "path": bundle.source_uri or str(root), "relative": str(path.relative_to(root)),
            "checksum": expected_sha256, "token": expected_version, "fingerprint": fingerprint,
            "samples": json.dumps(samples), "assume": declaration["absent_record_is_hom_ref"],
            "size": stat.st_size, "mtime": stat.st_mtime_ns,
            "qualification": json.dumps({"full_wgs_declared": True,
                                          "allow_unfiltered": declaration.get("allow_unfiltered", False)})})
    await session.commit()


class PackageAncestrySource:
    """A package import's registration of its family VCF as the ancestry PCA's WGS source.

    The clinical import never fails or waits for it: each step logs what went wrong (its kind
    only, never its text) and leaves a line in the import's log instead. A cancellation still
    stops it, as it stops the import.
    """

    def __init__(self, bundle: FamilyPackageBundle, checksum: str) -> None:
        self.bundle = bundle
        self.checksum = checksum
        self.version: str | None = None

    @classmethod
    async def before_import(cls, bundle: FamilyPackageBundle, logs: list[str]) -> "PackageAncestrySource | None":
        """The declared source with its file's checksum, before any dataset reads the file."""
        if package_ancestry_declaration(bundle) is None:
            return None
        try:
            snv = bundle.manifest.datasets.get("snv")
            path = _resolve_package_path(bundle.root, snv.family_vcf if snv else None)
            if path is None:
                raise WgsInputError("The WGS VCF must be contained in its import package.")
            return cls(bundle, await asyncio.to_thread(sha256_file, path))
        except Exception as exc:  # noqa: BLE001 - optional exploratory source; sanitized type only
            logger.warning("Ancestry source precheck failed: %s", describe_error(exc))
            logs.append("The exploratory ancestry PCA's WGS source could not be read; no source was registered.")
            return None

    async def note_imported(self, context: FamilyMetadataContext) -> None:
        """The family's data-version token once the datasets are written, while the import
        still holds the family's variant writes: a write after it registers no source."""
        try:
            self.version = await get_family_small_variant_data_version("GRCh38", context.family_uuid)
        except Exception as exc:  # noqa: BLE001 - optional exploratory source; sanitized type only
            logger.warning("Ancestry source version check failed: %s", describe_error(exc))

    async def register(self, session: AsyncSession, *, context: FamilyMetadataContext, project_id: str | None,
                       datasets: list[Any], job_id: str | None, logs: list[str]) -> None:
        if self.version is None:
            logs.append("The exploratory ancestry PCA's WGS source was not registered: "
                        "the family's genotypes could not be checked.")
            return
        try:
            await register_full_wgs(session, bundle=self.bundle, context=context, project_id=project_id,
                                    datasets=datasets, job_id=job_id, expected_sha256=self.checksum,
                                    expected_version=self.version)
            logs.append("The family VCF is registered as the WGS source of the exploratory ancestry PCA.")
        except Exception as exc:  # noqa: BLE001 - optional exploratory source; sanitized type only
            await session.rollback()
            logger.warning("Ancestry source registration failed: %s", describe_error(exc))
            detail = str(exc) if isinstance(exc, WgsInputError) else "It could not be stored."
            logs.append("The exploratory ancestry PCA's WGS source was not registered. " + detail)


async def latest_source(session: AsyncSession, family_uuid: str, project: str) -> WgsSourceOut:
    row = (await session.execute(text(_SOURCES), {"family": family_uuid, "project": project})).mappings().first()
    if row is None:
        raise HTTPException(409, "No verified WGS source is available.")
    return WgsSourceOut(**dict(row))
