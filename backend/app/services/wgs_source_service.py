"""Register retained whole-genome uploads without rewriting variant storage."""
from __future__ import annotations

import asyncio
import gzip
import json
import zlib
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.sql import require_uuid
from ..wgs_schemas import WgsSourceOut, WgsSourceCandidateOut, WgsSourceDeclaration, WgsSourcesOut
from .access_control import CurrentUser, user_can_change_metadata_projects
from .family_variant_write_lock import try_share_family_variant_writes
from .metadata_service import get_current_user_by_id
from .wgs_evidence import WgsInputError, sha256_file
from .raw_import_files_pg import local_file_to_download
from .wgs_evidence import WgsEvidence

_FILE = """
    SELECT id::text, family_id::text, file_name, storage_path, file_size, sha256, metadata
    FROM raw_import_files
    WHERE id = CAST(:id AS uuid) AND family_id = CAST(:family AS uuid)
      AND scope = 'family' AND dataset = 'small_variants' AND managed = TRUE
"""


async def retained_file(session: AsyncSession, file_id: str, family_uuid: str) -> dict[str, Any]:
    row = (await session.execute(text(_FILE), {"id": require_uuid(file_id, "Source file not found", status_code=404),
                                             "family": family_uuid})).mappings().first()
    if row is None:
        raise HTTPException(404, "Source file not found")
    return dict(row)


async def retained_path(session: AsyncSession, file_id: str, family_uuid: str) -> Path:
    record = await retained_file(session, file_id, family_uuid)
    return Path(await asyncio.to_thread(local_file_to_download, record))


def scan_vcf(path: Path, expected_samples: list[str]) -> WgsEvidence:
    evidence = WgsEvidence()
    with path.open("rb") as handle:
        compressed = handle.read(2) == b"\x1f\x8b"
    opener = gzip.open if compressed else open
    found_header = False
    with opener(path, "rt", encoding="utf-8") as handle:
        while line := handle.readline(16 * 1024 * 1024 + 1):
            if len(line) > 16 * 1024 * 1024:
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
    files = []
    for row in rows:
        metadata = row["metadata"] if isinstance(row["metadata"], dict) else {}
        evidence = metadata.get("wgs_assessment", {})
        files.append(WgsSourceCandidateOut(file_id=row["id"], file_name=row["file_name"],
                                          file_size=row["file_size"],
                                          assessment=evidence if isinstance(evidence, dict) else {}))
    return WgsSourcesOut(candidates=files, can_register=context.assembly_name == "GRCh38"
                         and user_can_change_metadata_projects([project], user))


async def register(session: AsyncSession, family_id: str, user: CurrentUser, project_id: str | None,
                   declaration: WgsSourceDeclaration) -> WgsSourceOut:
    from . import wgs_source_storage as sources

    context, project = await sources.family_scope(session, family_id, user, project_id, change=True)
    if context.assembly_name != "GRCh38":
        raise HTTPException(409, "A GRCh38 family is required.")
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                          {"key": f"wgs-register:{context.family_uuid}:{project}"})
    if not await try_share_family_variant_writes(session, context.family_uuid):
        raise HTTPException(409, "Genotypes are being written; register the source after import finishes.")
    if await sources._source_state(session, context, None, project) != "no_full_wgs_source":
        raise HTTPException(409, "The family import must finish successfully before WGS registration.")
    record = await retained_file(session, declaration.file_id, context.family_uuid)
    metadata = record["metadata"] if isinstance(record["metadata"], dict) else {}
    if metadata.get("callset", "clair3") != "clair3":
        raise HTTPException(409, "Choose a directly called nuclear WGS VCF, not mitochondrial or imputed calls.")
    path = Path(await asyncio.to_thread(local_file_to_download, record))
    before = await sources.genotype_fingerprint(context.family_uuid, project)
    try:
        checksum = await asyncio.to_thread(sha256_file, path)
        if checksum != record["sha256"]:
            raise WgsInputError("The retained VCF no longer matches its imported checksum.")
        evidence = await asyncio.to_thread(scan_vcf, path, sorted(context.sample_name_to_uuid))
        assessment = evidence.summary()
        if (evidence.builds - {"GRCh38"} or evidence.imputed or evidence.gvcf
                or evidence.malformed or not evidence.has_gt):
            raise WgsInputError("The retained file is not a supported directly called GRCh38 GT VCF.")
        if evidence.fingerprint() != await stored_file_gt_fingerprint(context.family_uuid, project):
            raise WgsInputError("The retained VCF genotypes or site filters do not match the current primary callset.")
        if await asyncio.to_thread(sha256_file, path) != checksum:
            raise WgsInputError("The retained VCF changed during source verification.")
    except (WgsInputError, UnicodeError, OSError, EOFError, zlib.error) as exc:
        detail = str(exc) if isinstance(exc, WgsInputError) else "The retained VCF could not be verified."
        raise HTTPException(409, detail) from exc
    if before != await sources.genotype_fingerprint(context.family_uuid, project):
        raise HTTPException(409, "The primary genotypes changed during source verification.")
    fresh = await get_current_user_by_id(session, user.id)
    if fresh is None or not fresh.is_active:
        raise HTTPException(403, "The requester is no longer active.")
    await sources.family_scope(session, family_id, fresh, project, change=True)
    stat = await asyncio.to_thread(path.stat)
    qualification = {"full_wgs_declared": True, "genome_build": "GRCh38",
                     "allow_unfiltered": declaration.allow_unfiltered,
                     "declared_by": user.id, "assessment": assessment,
                     "file_gt_fingerprint": evidence.fingerprint(),
                     "coverage_is_not_proven_by_variant_distribution": True}
    token = await sources.get_family_small_variant_data_version("GRCh38", context.family_uuid)
    old = (await session.execute(text(sources._SOURCES), {"family": context.family_uuid, "project": project})).mappings().first()
    if (old and str(old["raw_file_id"]) == record["id"] and old["sha256"] == checksum
            and old["storage_fingerprint"] == before
            and old["storage_version"] == token
            and old["file_size"] == stat.st_size and old["file_mtime_ns"] == stat.st_mtime_ns
            and old["absent_record_is_hom_ref"] == declaration.absent_record_is_hom_ref
            and old["qualification"].get("allow_unfiltered", False) == declaration.allow_unfiltered):
        return WgsSourceOut(**dict(old))
    await session.execute(text("""
        INSERT INTO wgs_sources(family_uuid, family_identifier, project_id, package_path, relative_vcf,
            sha256, storage_version, storage_fingerprint, samples, absent_record_is_hom_ref,
            file_size, file_mtime_ns, raw_file_id, qualification, imported_at)
        VALUES (CAST(:family AS uuid), :identifier, CAST(:project AS uuid), :path, :relative,
            :checksum, :token, :fingerprint, CAST(:samples AS jsonb), :assume, :size, :mtime,
            CAST(:raw_file AS uuid), CAST(:qualification AS jsonb), clock_timestamp())
    """), {"family": context.family_uuid, "identifier": context.family_id, "project": project,
            "path": str(path.parent), "relative": path.name, "checksum": checksum, "token": token,
            "fingerprint": before, "samples": json.dumps(sorted(context.sample_name_to_uuid)),
            "assume": declaration.absent_record_is_hom_ref, "size": stat.st_size, "mtime": stat.st_mtime_ns,
            "raw_file": record["id"], "qualification": json.dumps(qualification)})
    await session.commit()
    return await sources.latest_source(session, context.family_uuid, project)
