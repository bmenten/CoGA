"""Import public PGS Catalog GRCh38 files and metadata, without sending family data."""
from __future__ import annotations

import asyncio
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import logging
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.coga_logging import describe_error
from ..core.config import settings
from ..core.postgres import get_postgres_sessionmaker
from ..pgs_schemas import PgsCatalogImportOut, PgsCatalogImportsOut
from .access_control import CurrentUser, is_admin_user
from .bounded_download import download_bounded_bytes, gunzip_bounded
from .metadata_service import get_current_user_by_id
from .pgs_scoring import PgsInputError
from .pgs_service import publish_version

logger = logging.getLogger(__name__)
CATALOG = "https://www.pgscatalog.org"
FILES = "https://ftp.ebi.ac.uk"
INTERRUPTED = "Catalog import interrupted; no partial model version was published. Import the PGS ID again."
_COLUMNS = "id::text, pgs_id, status, version_id::text, error, requested_at, started_at, finished_at"


def catalog_client() -> httpx.AsyncClient:
    # No credentials, cookies, user/family identifiers or caller-provided URLs are sent.
    return httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10), follow_redirects=False,
                             headers={"User-Agent": "CoGA-exploratory-PGS/1", "Accept-Encoding": "identity"})


def _valid_id(value: str) -> str:
    if re.fullmatch(r"PGS[0-9]{6}", value) is None:
        raise HTTPException(400, "Enter a PGS ID followed by six digits.")
    return value


def scoring_header(value: str, pgs_id: str, expected_count: object) -> dict[str, Any]:
    """Validate the complete downloaded file before storing even a non-scorable model."""
    header: dict[str, str] = {}
    count = 0
    fields: list[str] | None = None
    for line in io.StringIO(value):
        if line.startswith("#"):
            key, separator, content = line[1:].partition("=")
            if separator:
                header[key.strip()] = content.strip()
        elif line.strip():
            row = next(csv.reader([line], delimiter="\t"))
            if fields is None:
                fields = row
                if len(fields) != len(set(fields)):
                    raise PgsInputError("The Catalog file has duplicate columns.")
            else:
                if len(row) != len(fields):
                    raise PgsInputError("The Catalog file contains an incomplete or malformed row.")
                count += 1
    if header.get("pgs_id") != pgs_id or header.get("HmPOS_build") != "GRCh38":
        raise PgsInputError("The Catalog file does not identify the requested PGS and GRCh38 harmonization.")
    if not {"hm_chr", "hm_pos", "effect_allele", "effect_weight"}.issubset(fields or []):
        raise PgsInputError("The GRCh38 Catalog file is missing required scoring columns.")
    if isinstance(expected_count, bool) or not isinstance(expected_count, (int, str)):
        raise PgsInputError("The Catalog metadata does not declare a valid variant count.")
    try:
        declared_count = int(header["variants_number"])
        api_count = int(expected_count)
    except (KeyError, ValueError, TypeError) as exc:
        raise PgsInputError("The Catalog file or metadata does not declare a valid variant count.") from exc
    if count < 1 or count != declared_count or count != api_count:
        raise PgsInputError("The Catalog file is incomplete or disagrees with the API variant count.")
    return {"header": header, "variant_count": count}


async def fetch_catalog(pgs_id: str) -> tuple[str, dict[str, Any]]:
    _valid_id(pgs_id)
    api_url = f"{CATALOG}/rest/score/{pgs_id}/"
    async with asyncio.timeout(300), catalog_client() as client:
        raw = await download_bounded_bytes(client, api_url, max_bytes=8 * 1024 * 1024,
                                           source="PGS Catalog metadata", none_on_404=True)
        if raw is None:
            raise PgsInputError("The PGS ID was not found in the PGS Catalog.")
        record = json.loads(raw)
        if not isinstance(record, dict) or record.get("id") != pgs_id:
            raise PgsInputError("The Catalog returned inconsistent score metadata.")
        harmonized = record.get("ftp_harmonized_scoring_files") or {}
        build = harmonized.get("GRCh38") if isinstance(harmonized, dict) else None
        url = build.get("positions") if isinstance(build, dict) else None
        expected_path = f"/pub/databases/spot/pgs/scores/{pgs_id}/ScoringFiles/Harmonized/{pgs_id}_hmPOS_GRCh38.txt.gz"
        if not isinstance(url, str):
            raise PgsInputError("This Catalog score has no harmonized GRCh38 scoring file.")
        location = urlparse(url)
        # Catalog URLs are data. Only the official EBI host and exact requested ID/build
        # may be fetched; redirects, userinfo, ports, query strings and fragments fail shut.
        if location.scheme not in {"https", "ftp"} or url != f"{location.scheme}://ftp.ebi.ac.uk{expected_path}":
            raise PgsInputError("The Catalog returned an unexpected GRCh38 download location.")
        url = FILES + expected_path
        compressed = await download_bounded_bytes(client, url, max_bytes=settings.max_upload_bytes,
                                                  source="PGS GRCh38 scoring file")
        if compressed is None:
            raise PgsInputError("The GRCh38 scoring file is unavailable.")
        decoded = await asyncio.to_thread(gunzip_bounded, compressed,
                                          max_bytes=settings.max_decompressed_upload_bytes, source="PGS scoring file")
        value = decoded.decode("utf-8")
        info = await asyncio.to_thread(scoring_header, value, pgs_id, record.get("variants_number"))
    metadata = {
        "provider": "PGS Catalog", "record": record,
        "record_sha256": hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest(),
        "api_response_sha256": hashlib.sha256(raw).hexdigest(),
        "api_url": api_url, "download_url": url, "genome_build": "GRCh38",
        "download_sha256": hashlib.sha256(compressed).hexdigest(),
        "retrieved_at": datetime.now(timezone.utc).isoformat(), "scoring_file": info,
    }
    return value, metadata


def _reference_configured() -> bool:
    path = settings.pgs_reference_fasta
    return bool(path) and Path(path).is_file() and Path(f"{path}.fai").is_file()


async def list_imports(session: AsyncSession) -> PgsCatalogImportsOut:
    rows = (await session.execute(text(f"SELECT {_COLUMNS} FROM pgs_catalog_imports ORDER BY requested_at DESC LIMIT 50"))).mappings().all()
    return PgsCatalogImportsOut(jobs=[PgsCatalogImportOut(**dict(row)) for row in rows],
                                reference_configured=await asyncio.to_thread(_reference_configured))


async def queue_import(session: AsyncSession, pgs_id: str, user: CurrentUser) -> PgsCatalogImportOut:
    _valid_id(pgs_id)
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"pgs-catalog:{pgs_id}"})
    existing = (await session.execute(text(f"SELECT {_COLUMNS} FROM pgs_catalog_imports WHERE pgs_id = :pgs AND status IN ('queued','running') LIMIT 1"),
                                      {"pgs": pgs_id})).mappings().first()
    if existing:
        return PgsCatalogImportOut(**dict(existing))
    result = await session.execute(text(f"""
        INSERT INTO pgs_catalog_imports(pgs_id, requested_by) VALUES (:pgs, CAST(:actor AS uuid))
        RETURNING {_COLUMNS}
    """), {"pgs": pgs_id, "actor": user.id})
    job = PgsCatalogImportOut(**dict(result.mappings().one()))
    await session.commit()
    return job


async def _fail_import(job_id: str, status: str, error: str) -> None:
    async with get_postgres_sessionmaker()() as session:
        await session.execute(text("UPDATE pgs_catalog_imports SET status = :status, error = :error, finished_at = now() WHERE id = CAST(:id AS uuid) AND status = 'running'"),
                              {"id": job_id, "status": status, "error": error})
        await session.commit()


async def process_next_import() -> bool:
    async with get_postgres_sessionmaker()() as session:
        await session.execute(text("""
            UPDATE pgs_catalog_imports SET status = 'interrupted', error = :error, finished_at = now()
            WHERE id IN (SELECT id FROM pgs_catalog_imports WHERE status = 'running'
              AND started_at < now() - interval '10 minutes' FOR UPDATE SKIP LOCKED)
        """), {"error": INTERRUPTED})
        job_id = (await session.execute(text("""
            UPDATE pgs_catalog_imports SET status = 'running', started_at = now()
            WHERE id = (SELECT id FROM pgs_catalog_imports WHERE status = 'queued'
              ORDER BY requested_at LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING id::text
        """))).scalar_one_or_none()
        await session.commit()
    if job_id is None:
        return False
    await run_import(str(job_id))
    return True


async def run_import(job_id: str) -> None:
    try:
        async with get_postgres_sessionmaker()() as session:
            job = (await session.execute(text("SELECT pgs_id, requested_by FROM pgs_catalog_imports WHERE id = CAST(:id AS uuid) AND status = 'running' FOR UPDATE"),
                                         {"id": job_id})).mappings().first()
            if job is None:
                return
            requester = str(job["requested_by"])
            user = await get_current_user_by_id(session, requester)
            if user is None or not user.is_active or not is_admin_user(user):
                raise PgsInputError("The requester is no longer an active administrator.")
            value, metadata = await fetch_catalog(job["pgs_id"])
            record = metadata["record"]
            publication = record.get("publication") or {}
            study = ". ".join(str(publication[k]) for k in ("title", "journal", "date_publication", "doi") if publication.get(k))
            model = await publish_version(session, pgs_id=job["pgs_id"], version=None,
                                           study=study[:4000] or f"PGS Catalog {job['pgs_id']}",
                                           source_url=metadata["download_url"], weights_text=value,
                                           catalog_metadata=metadata, commit=False)
            user = await get_current_user_by_id(session, requester)
            if user is None or not user.is_active or not is_admin_user(user):
                raise PgsInputError("The requester's administrator rights changed during import.")
            await session.execute(text("UPDATE pgs_catalog_imports SET status = 'completed', version_id = CAST(:version AS uuid), finished_at = now() WHERE id = CAST(:id AS uuid)"),
                                  {"id": job_id, "version": model.id})
            await session.commit()
    except asyncio.CancelledError:
        await asyncio.shield(_fail_import(job_id, "interrupted", INTERRUPTED))
        raise
    except Exception as exc:  # noqa: BLE001 - retain the durable failure and log no external/patient values
        logger.warning("PGS Catalog import failed: %s", describe_error(exc))
        error = str(exc) if isinstance(exc, PgsInputError) else "Catalog import failed. Check the Catalog connection or scoring-file integrity and retry."
        await _fail_import(job_id, "failed", error)
