"""Register a retained whole-genome upload as the family's WGS source, without rewriting
variant storage.

Registration reads the whole file three times (its checksum, every call, the checksum again),
which takes minutes for a whole genome. It holds no lock of the family's variant writes while
it does: it notes the family's data-version token and genotype fingerprint under those locks,
releases them, reads the file, and takes them again only to check that nothing was written in
between and to store the source. An import or a sign-out of the family never waits for it.
"""
from __future__ import annotations

import asyncio
import json
import zlib
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.sql import require_uuid
from ..wgs_schemas import WgsSourceCandidateOut, WgsSourceDeclaration, WgsSourceOut, WgsSourcesOut
from .access_control import CurrentUser, user_can_change_metadata_projects
from .family_metadata_context import FamilyMetadataContext
from .family_variant_write_lock import try_share_family_variant_writes
from .metadata_service import get_current_user_by_id
from .raw_import_files_pg import local_file_to_download
from .wgs_evidence import WgsEvidence, WgsInputError, open_vcf_text, sha256_file

_FILE = """
    SELECT id::text, family_id::text, file_name, storage_path, file_size, sha256, metadata
    FROM raw_import_files
    WHERE id = CAST(:id AS uuid) AND family_id = CAST(:family AS uuid)
      AND scope = 'family' AND dataset = 'small_variants' AND managed = TRUE
"""
_MAX_LINE = 16 * 1024 * 1024


async def retained_file(session: AsyncSession, file_id: str, family_uuid: str) -> dict[str, Any]:
    row = (await session.execute(text(_FILE), {"id": require_uuid(file_id, "Source file not found", status_code=404),
                                             "family": family_uuid})).mappings().first()
    if row is None:
        raise HTTPException(404, "Source file not found")
    return dict(row)


def scan_vcf(path: Path, expected_samples: list[str]) -> WgsEvidence:
    """Every record of a retained VCF: its evidence and the fingerprint of its calls."""
    evidence = WgsEvidence()
    found_header = False
    with open_vcf_text(path) as handle:
        while line := handle.readline(_MAX_LINE + 1):
            if len(line) > _MAX_LINE:
                raise WgsInputError("The retained VCF contains a line above the supported size.")
            if line.startswith("#"):
                if line.startswith("#CHROM\t"):
                    if found_header:
                        raise WgsInputError("The retained VCF has repeated sample headers.")
                    found_header = True
                evidence.header(line)
                if found_header and (sorted(evidence.samples) != sorted(expected_samples)
                                     or len(evidence.samples) != len(set(evidence.samples))):
                    raise WgsInputError("The retained VCF must contain exactly this family's sample columns.")
                continue
            if not line.strip():
                continue
            evidence.observe(line.rstrip("\r\n").split("\t"), fingerprint=True)
    if not found_header or not evidence.records:
        raise WgsInputError("The retained VCF has no complete genotype source.")
    return evidence


def verify_retained_vcf(path: Path, checksum: str, samples: list[str]) -> WgsEvidence:
    """Read the retained file whole and check it is the upload, unchanged while it was read,
    and a directly called GRCh38 GT VCF of exactly the family's samples."""
    if sha256_file(path) != checksum:
        raise WgsInputError("The retained VCF no longer matches its imported checksum.")
    evidence = scan_vcf(path, samples)
    if (evidence.builds - {"GRCh38"} or evidence.imputed or evidence.gvcf
            or evidence.malformed or not evidence.has_gt):
        raise WgsInputError("The retained file is not a supported directly called GRCh38 GT VCF.")
    if sha256_file(path) != checksum:
        raise WgsInputError("The retained VCF changed during source verification.")
    return evidence


async def stored_file_gt_fingerprint(family_uuid: str, project: str) -> str:
    from .clickhouse_variant_storage import _small_table_name, _execute as storage_execute

    table = _small_table_name("GRCh38", "entries")
    # Same UTF-8 payload and SHA-256 byte order as wgs_evidence.gt_hash. ARRAY JOIN
    # compares each sample call, independent of VCF row or sample-column ordering.
    rows = await storage_execute(f"""
        SELECT count(), sum(h), groupBitXor(h) FROM (
            SELECT reinterpretAsUInt64(substring(SHA256(concat(
                chrom, '\\t', toString(pos), '\\t', ref, '\\t', alt, '\\t', sample, '\\t', gt, '\\t',
                if(empty(filters), '.', arrayStringConcat(filters, ';')))), 1, 8)) AS h
            FROM {table}
            ARRAY JOIN `calls.sampleId` AS sample, `calls.gt` AS gt
            WHERE family_guid = %(family)s AND project_guid = %(project)s
              AND source = 'clair3' AND sign = 1
        )
    """, {"family": family_uuid, "project": project})
    return "wgs-file-gt-v1:" + ":".join(str(int(v)) for v in rows[0])


async def candidates(session: AsyncSession, family_id: str, user: CurrentUser, project_id: str | None) -> WgsSourcesOut:
    from .wgs_source_storage import family_scope

    context, project = await family_scope(session, family_id, user, project_id)
    rows = (await session.execute(text("""
        SELECT id::text, file_name, file_size, metadata FROM raw_import_files
        WHERE family_id = CAST(:family AS uuid) AND scope = 'family'
          AND dataset = 'small_variants' AND managed = TRUE
          AND COALESCE(metadata->>'callset', 'clair3') = 'clair3'
        ORDER BY created_at DESC, id DESC
    """), {"family": context.family_uuid})).mappings().all()
    files = [WgsSourceCandidateOut(file_id=row["id"], file_name=row["file_name"], file_size=row["file_size"])
             for row in rows]
    return WgsSourcesOut(candidates=files, can_register=context.assembly_name == "GRCh38"
                         and user_can_change_metadata_projects([project], user))


async def _registration_snapshot(session: AsyncSession, family_id: str, user: CurrentUser, project: str | None,
                                 file_id: str) -> tuple[FamilyMetadataContext, str, dict[str, Any], str, str]:
    """Check the family may register a source now and note what the stored genotypes are,
    under the family's variant-write locks; the caller ends the transaction."""
    from . import wgs_source_storage as sources

    context, project = await sources.family_scope(session, family_id, user, project, change=True)
    if context.assembly_name != "GRCh38":
        raise HTTPException(409, "A GRCh38 family is required.")
    if not await try_share_family_variant_writes(session, context.family_uuid):
        raise HTTPException(409, "Genotypes are being written; register the source after import finishes.")
    state = await sources.source_state(session, context, None, project)
    if state == "unsupported_samples":
        raise HTTPException(409, "Ancestry PCA reads individuals' own genotypes: not a family with embryo or cfDNA samples.")
    if state != "no_full_wgs_source":
        raise HTTPException(409, "The family import must finish successfully before WGS registration.")
    record = await retained_file(session, file_id, context.family_uuid)
    metadata = record["metadata"] if isinstance(record["metadata"], dict) else {}
    if metadata.get("callset", "clair3") != "clair3":
        raise HTTPException(409, "Choose a directly called nuclear WGS VCF, not mitochondrial or imputed calls.")
    token = await sources.get_family_small_variant_data_version("GRCh38", context.family_uuid)
    fingerprint = await sources.genotype_fingerprint(context.family_uuid, project)
    return context, project, record, token, fingerprint


async def register(session: AsyncSession, family_id: str, user: CurrentUser, project_id: str | None,
                   declaration: WgsSourceDeclaration) -> WgsSourceOut:
    from . import wgs_source_storage as sources

    try:
        context, project, record, token, before = await _registration_snapshot(
            session, family_id, user, project_id, declaration.file_id)
    finally:
        # Ends the transaction: the variant-write locks are released before the file is read.
        await session.rollback()
    samples = sorted(context.sample_name_to_uuid)
    try:
        path = Path(await asyncio.to_thread(local_file_to_download, record))
        evidence = await asyncio.to_thread(verify_retained_vcf, path, record["sha256"], samples)
        if evidence.fingerprint() != await stored_file_gt_fingerprint(context.family_uuid, project):
            raise WgsInputError("The retained VCF genotypes or site filters do not match the current primary callset.")
        stat = await asyncio.to_thread(path.stat)
    except (WgsInputError, UnicodeError, OSError, EOFError, zlib.error) as exc:
        detail = str(exc) if isinstance(exc, WgsInputError) else "The retained VCF could not be verified."
        raise HTTPException(409, detail) from exc

    # One registration of the family's source at a time, and none while its variants are written.
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                          {"key": f"wgs-register:{context.family_uuid}:{project}"})
    if not await try_share_family_variant_writes(session, context.family_uuid):
        await session.rollback()
        raise HTTPException(409, "Genotypes are being written; register the source after import finishes.")
    if await sources.get_family_small_variant_data_version("GRCh38", context.family_uuid) != token:
        await session.rollback()
        raise HTTPException(409, "The primary genotypes changed during source verification.")
    fresh = await get_current_user_by_id(session, user.id)
    if fresh is None or not fresh.is_active:
        await session.rollback()
        raise HTTPException(403, "The requester is no longer active.")
    await sources.family_scope(session, family_id, fresh, project, change=True)
    checksum = record["sha256"]
    old = (await session.execute(text(sources._SOURCES), {"family": context.family_uuid, "project": project})).mappings().first()
    if (old and str(old["raw_file_id"]) == record["id"] and old["sha256"] == checksum
            and old["storage_fingerprint"] == before
            and old["storage_version"] == token
            and old["file_size"] == stat.st_size and old["file_mtime_ns"] == stat.st_mtime_ns
            and old["absent_record_is_hom_ref"] == declaration.absent_record_is_hom_ref
            and old["qualification"].get("allow_unfiltered", False) == declaration.allow_unfiltered):
        await session.rollback()
        return WgsSourceOut(**dict(old))
    qualification = {"full_wgs_declared": True, "genome_build": "GRCh38",
                     "allow_unfiltered": declaration.allow_unfiltered,
                     "declared_by": user.id, "assessment": evidence.summary(),
                     "file_gt_fingerprint": evidence.fingerprint(),
                     "coverage_is_not_proven_by_variant_distribution": True}
    await session.execute(text("""
        INSERT INTO wgs_sources(family_uuid, family_identifier, project_id, package_path, relative_vcf,
            sha256, storage_version, storage_fingerprint, samples, absent_record_is_hom_ref,
            file_size, file_mtime_ns, raw_file_id, qualification, imported_at)
        VALUES (CAST(:family AS uuid), :identifier, CAST(:project AS uuid), :path, :relative,
            :checksum, :token, :fingerprint, CAST(:samples AS jsonb), :assume, :size, :mtime,
            CAST(:raw_file AS uuid), CAST(:qualification AS jsonb), clock_timestamp())
    """), {"family": context.family_uuid, "identifier": context.family_id, "project": project,
            "path": str(path.parent), "relative": path.name, "checksum": checksum, "token": token,
            "fingerprint": before, "samples": json.dumps(samples),
            "assume": declaration.absent_record_is_hom_ref, "size": stat.st_size, "mtime": stat.st_mtime_ns,
            "raw_file": record["id"], "qualification": json.dumps(qualification)})
    await session.commit()
    return await sources.latest_source(session, context.family_uuid, project)
