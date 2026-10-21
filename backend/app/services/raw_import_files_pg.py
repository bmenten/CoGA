"""Provenance tracking for raw source files used to import family/sample data.

Every importer records the original file it ingested here so the Admin -> Data ->
Families page can offer complete traceability: file name, type, scope, associated
sample, storage path, human-readable size, SHA-256 checksum, and a download link
with on-demand integrity verification.

Two kinds of records exist:

* ``managed`` files are copies we own under ``data/raw_imports/<family_id>/``. These
  come from web uploads where the original bytes would otherwise be discarded after
  parsing. We delete them when the owning family is deleted.
* referenced files live at their original on-disk location (e.g. a family package
  import directory). We never move or delete those; we only record where they are.
  For a package imported from a bucket, that location is the object's gs:// or s3://
  URI: such a file is verified against the store's record of the object
  (``metadata.store_object``) rather than re-hashed, and is not downloaded here.

The recorded ``storage_path`` is read back as data: an edited row can name any path. So a
file is read (downloaded, verified, or checked for in the list) only where the import may
have put it: below a local FAMILY_IMPORT_ROOTS folder, in the staging folder of a package
downloaded from a bucket, or in the managed upload folder (``data/raw_imports``), its real
path, symlinks resolved, inside one of them; an object only below a remote
FAMILY_IMPORT_ROOTS entry. A family or sample delete unlinks a managed copy only inside the
upload folder. A package folder need not be named after its family, nor a managed folder
after the family's present ID, so neither rule looks at the family (SEC-29).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any, Iterable

from fastapi import HTTPException, UploadFile
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.coga_logging import scrub_log
from ..core.object_storage import is_remote_uri, parse_remote_uri, remote_object_identity
from ..core.sql import canonical_uuid
# The import's folder rules, read through their module (a test that moves them moves them here).
from . import family_package_source

# Repo-level data directory (matches routers/cram.py DATA_DIR resolution).
DATA_DIR = Path(__file__).resolve().parents[3] / "data"
logger = logging.getLogger(__name__)

MANAGED_SUBDIR = "raw_imports"

_CHUNK_SIZE = 1024 * 1024  # 1 MiB streaming reads keep large VCF/CRAM out of memory.

# Ordered so that multi-part extensions (.vcf.gz, .g.vcf.gz) match before the
# bare extension. Maps a recognised suffix to the displayed file-type label.
_FILE_TYPE_RULES: tuple[tuple[str, str], ...] = (
    (".g.vcf.gz", "GVCF"),
    (".gvcf.gz", "GVCF"),
    (".g.vcf", "GVCF"),
    (".gvcf", "GVCF"),
    (".vcf.gz", "VCF"),
    (".vcf.bgz", "VCF"),
    (".vcf", "VCF"),
    (".bcf", "BCF"),
    (".cram", "CRAM"),
    (".crai", "CRAI"),
    (".bam", "BAM"),
    (".bai", "BAI"),
    (".bed.gz", "BED"),
    (".bed", "BED"),
    (".tsv.gz", "TSV"),
    (".tsv", "TSV"),
    (".csv.gz", "CSV"),
    (".csv", "CSV"),
    (".json", "JSON"),
    (".tbi", "TBI"),
    (".csi", "CSI"),
    (".idx", "IDX"),
    (".ped", "PED"),
    (".txt", "TXT"),
)

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def infer_file_type(file_name: str) -> str:
    lowered = (file_name or "").lower()
    for suffix, label in _FILE_TYPE_RULES:
        if lowered.endswith(suffix):
            return label
    ext = Path(lowered).suffix.lstrip(".")
    return ext.upper() if ext else "FILE"


def _safe_name(file_name: str) -> str:
    cleaned = _SAFE_NAME.sub("_", Path(file_name or "file").name).strip("._")
    return cleaned or "file"


def _managed_dir(family_id: str) -> Path:
    return DATA_DIR / MANAGED_SUBDIR / _safe_name(family_id)


# --- Where a recorded file may be read or deleted (SEC-29) ------------------------------

# The answers to a download or Verify of a file whose recorded location CoGA does not read.
OUTSIDE_FOLDERS_DETAIL = (
    "The file's recorded storage path lies outside the folders CoGA reads source files from "
    "(FAMILY_IMPORT_ROOTS and its upload folder), so the file is not read."
)
OUTSIDE_IMPORT_ROOTS_DETAIL = (
    "The file's recorded storage path names no object below FAMILY_IMPORT_ROOTS, so the "
    "object store is not asked about it."
)


def _managed_root() -> str:
    """The real path of the managed upload folder, ``data/raw_imports``."""
    return os.path.realpath(DATA_DIR / MANAGED_SUBDIR)


def _inside(path: str, folder: str) -> bool:
    """Whether the real path ``path`` lies inside the real path ``folder``, told from the
    strings (as the import's folder guard tells it, so a root of ``/`` holds every path)."""
    return Path(folder) in Path(path).parents


def readable_folders() -> tuple[str, ...]:
    """The real paths of the local folders a recorded file is read from: the managed upload
    folder, the staging folder a bucket package is downloaded to, and each local
    FAMILY_IMPORT_ROOTS folder.

    With FAMILY_IMPORT_ROOTS empty -- the development mode in which the import takes a
    package folder anywhere -- they are the upload and staging folders alone: a recorded
    path is data, and nothing else would bound it. A folder that cannot be found (the
    staging folder cannot be made, a root runs through a symlink loop) admits nothing.
    Blocking (file-system look-ups).
    """
    folders = [_managed_root()]
    try:
        folders.append(str(family_package_source._staging_root()))
    except OSError:
        pass
    try:
        folders.extend(str(root) for root in family_package_source._authorized_local_roots())
    except (OSError, RuntimeError, ValueError):
        pass
    return tuple(folders)


def readable_local_path(storage_path: str, folders: Iterable[str] | None = None) -> str | None:
    """The real path of the local file ``storage_path`` names, when it may be read there;
    ``None`` otherwise.

    The recorded path must be absolute, and its real path, symlinks resolved, must lie inside
    one of ``folders`` (``readable_folders()`` unless given), so a symlink that leads out of
    them is not followed. The file is read by the real path returned, never by the recorded
    one. Blocking.
    """
    if not storage_path or not os.path.isabs(storage_path):
        return None
    try:
        real = os.path.realpath(storage_path)
    except (OSError, ValueError):
        # A NUL in the recorded path (ValueError).
        return None
    allowed = readable_folders() if folders is None else tuple(folders)
    return real if any(_inside(real, folder) for folder in allowed) else None


def readable_remote_uri(uri: str) -> bool:
    """Whether the object store may be asked about the object ``uri`` names: one below a
    remote FAMILY_IMPORT_ROOTS entry, the only folders a package's files are read from,
    written as nothing but scheme, bucket and key, with no empty, ``.`` or ``..`` segment in
    its key (the rules the CRAM endpoint signs a recorded location by, without its
    configured bucket: a package may come from any bucket the roots name)."""
    try:
        location = parse_remote_uri(uri)
    except ValueError:
        return False
    if location.uri != uri or any(segment in {"", ".", ".."} for segment in location.key.split("/")):
        return False
    return family_package_source.within_remote_import_roots(uri)


def _refuse_outside_folders(record: dict[str, Any], detail: str) -> HTTPException:
    """The 403 for a read of a file whose recorded location CoGA does not read, logged with
    the record and the path it names, so an edited row leaves a trace."""
    logger.warning(
        "Raw file %s was not read: its recorded storage path %s is outside FAMILY_IMPORT_ROOTS "
        "and the upload folder",
        scrub_log(record.get("id")),
        scrub_log(record.get("storage_path")),
    )
    return HTTPException(status_code=403, detail=detail)


def local_file_to_download(record: dict[str, Any]) -> str:
    """The real path of the file to serve for a download of ``record``.

    409 for a file kept in an object store (it is not downloaded through CoGA), 403 for a
    recorded path outside the folders a file is read from (``readable_local_path``), 410
    when the file is no longer there. Blocking.
    """
    storage_path = record.get("storage_path") or ""
    if is_remote_uri(storage_path):
        # Not gone: it is in the bucket the package was imported from.
        raise HTTPException(
            status_code=409,
            detail="The file is kept in an object store at its gs:// or s3:// URI; it is not downloaded through CoGA.",
        )
    path = readable_local_path(storage_path) if storage_path else None
    if storage_path and path is None:
        raise _refuse_outside_folders(record, OUTSIDE_FOLDERS_DETAIL)
    if path is None or not os.path.isfile(path):
        raise HTTPException(
            status_code=410,
            detail="The source file is no longer available at its storage path.",
        )
    return path


def _hash_and_size(path: Path) -> tuple[str | None, int | None]:
    """Stream a file from disk computing SHA-256 and byte size, never loading the
    whole file into memory. Returns (None, None) if the file is missing."""
    if not path.exists() or not path.is_file():
        return None, None
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
    return digest.hexdigest(), total


def _hash_and_size_bytes(content: bytes) -> tuple[str, int]:
    return hashlib.sha256(content).hexdigest(), len(content)


async def store_managed_file(family_id: str, file_name: str, content: bytes) -> Path:
    """Persist uploaded bytes under the managed raw-import directory and return the
    path. A short uuid prefix keeps same-named uploads from colliding."""

    def _write() -> Path:
        target_dir = _managed_dir(family_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{uuid.uuid4().hex[:12]}__{_safe_name(file_name)}"
        target.write_bytes(content)
        return target

    return await asyncio.to_thread(_write)


async def record_raw_import_file(
    session: AsyncSession,
    *,
    family_uuid: str,
    sample_uuid: str | None,
    scope: str,
    dataset: str,
    file_name: str,
    storage_path: str,
    managed: bool,
    source: str,
    file_size: int | None = None,
    sha256: str | None = None,
    compute_checksum: bool = True,
    file_type: str | None = None,
    metadata: dict[str, Any] | None = None,
    checksum_path: Path | None = None,
) -> None:
    """Upsert a single provenance row.

    When ``compute_checksum`` is set and the checksum/size are not supplied, the
    file at ``storage_path`` is streamed to derive them -- or ``checksum_path``, a
    local copy of it, when ``storage_path`` is not a local file (the staged copy of an
    object in a bucket). Insertion is idempotent on (family, sample, storage_path) so
    re-imports refresh rather than duplicate.
    """
    if (sha256 is None or file_size is None) and compute_checksum:
        computed_sha, computed_size = await asyncio.to_thread(
            _hash_and_size, checksum_path or Path(storage_path)
        )
        if sha256 is None:
            sha256 = computed_sha
        if file_size is None:
            file_size = computed_size

    await session.execute(
        text(
            """
            INSERT INTO raw_import_files (
                family_id, sample_id, scope, dataset, file_name, file_type,
                storage_path, managed, file_size, sha256, source, metadata
            )
            VALUES (
                CAST(:family_id AS uuid),
                CAST(:sample_id AS uuid),
                :scope, :dataset, :file_name, :file_type,
                :storage_path, :managed, :file_size, :sha256, :source,
                CAST(:metadata AS jsonb)
            )
            ON CONFLICT (
                family_id,
                COALESCE(sample_id, '00000000-0000-0000-0000-000000000000'::uuid),
                storage_path
            )
            DO UPDATE SET
                scope = EXCLUDED.scope,
                dataset = EXCLUDED.dataset,
                file_name = EXCLUDED.file_name,
                file_type = EXCLUDED.file_type,
                managed = EXCLUDED.managed,
                file_size = EXCLUDED.file_size,
                sha256 = EXCLUDED.sha256,
                source = EXCLUDED.source,
                metadata = EXCLUDED.metadata,
                created_at = timezone('utc', now())
            """
        ),
        {
            "family_id": family_uuid,
            "sample_id": sample_uuid,
            "scope": scope,
            "dataset": dataset or "",
            "file_name": file_name,
            "file_type": file_type or infer_file_type(file_name),
            "storage_path": storage_path,
            "managed": managed,
            "file_size": file_size,
            "sha256": sha256,
            "source": source,
            "metadata": json.dumps(metadata or {}),
        },
    )


async def record_uploaded_file(
    session: AsyncSession,
    *,
    family_uuid: str,
    family_id: str,
    sample_uuid: str | None,
    scope: str,
    dataset: str,
    source: str,
    file_name: str,
    content: bytes,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Store uploaded bytes as a managed copy and record provenance for them.

    Best-effort: provenance must never fail an otherwise-successful import.
    """
    try:
        # SHA-256 over a whole uploaded payload blocks the event loop; offload it
        # (store_managed_file already does its write off-loop).
        sha256, file_size = await asyncio.to_thread(_hash_and_size_bytes, content)
        stored = await store_managed_file(family_id, file_name, content)
        await record_raw_import_file(
            session,
            family_uuid=family_uuid,
            sample_uuid=sample_uuid,
            scope=scope,
            dataset=dataset,
            file_name=file_name,
            storage_path=str(stored),
            managed=True,
            source=source,
            file_size=file_size,
            sha256=sha256,
            compute_checksum=False,
            metadata=metadata,
        )
        await session.commit()
    except Exception:
        # Provenance must not fail the import, but a missing record must leave a trace (#686).
        logger.warning(
            "Raw-file provenance for family %s (%s, %s) was not recorded",
            scrub_log(family_id),
            scrub_log(dataset),
            scrub_log(file_name),
            exc_info=True,
        )
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001 - nothing more to do after a failed rollback
            pass
        return


async def record_upload_file_obj(
    session: AsyncSession,
    *,
    file: UploadFile,
    family_uuid: str,
    family_id: str,
    sample_uuid: str | None,
    scope: str,
    dataset: str,
    source: str = "web",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Re-read an already-consumed UploadFile and record it as a managed provenance
    file. Safe to call after the import succeeded; never raises."""
    try:
        await file.seek(0)
        content = await file.read()
    except Exception:
        logger.warning(
            "Raw-file provenance for family %s (%s, %s) was not recorded: the upload could not be re-read",
            scrub_log(family_id),
            scrub_log(dataset),
            scrub_log(file.filename or "upload"),
            exc_info=True,
        )
        content = b""
    if not content:
        return
    await record_uploaded_file(
        session,
        family_uuid=family_uuid,
        family_id=family_id,
        sample_uuid=sample_uuid,
        scope=scope,
        dataset=dataset,
        source=source,
        file_name=file.filename or "upload",
        content=content,
        metadata=metadata,
    )


def _row_to_dict(row: Any, folders: Iterable[str] | None = None) -> dict[str, Any]:
    """A row as the API serves it, with whether its file is there to download. ``folders``:
    ``readable_folders()``, when the caller already has them."""
    record = dict(row)
    storage_path = record.get("storage_path") or ""
    # A file kept in an object store is not a local path: it is not checked when the
    # list is built (that would cost a store request per row) -- Verify asks the store
    # -- and it is not downloaded through CoGA.
    in_object_store = is_remote_uri(storage_path)
    exists: bool | None = None
    if not in_object_store:
        # A recorded path outside the folders a file is read from is shown unavailable,
        # whether or not a file lies there: it is not looked up.
        path = readable_local_path(storage_path, folders) if storage_path else None
        exists = path is not None and os.path.isfile(path)
    record["in_object_store"] = in_object_store
    record["exists"] = exists
    record["download_available"] = bool(exists)
    created = record.get("created_at")
    if created is not None and not isinstance(created, str):
        record["created_at"] = created.isoformat()
    return record


def _rows_to_dicts(rows: Iterable[Any]) -> list[dict[str, Any]]:
    folders = readable_folders()
    return [_row_to_dict(row, folders) for row in rows]


async def list_raw_import_files(session: AsyncSession, family_uuid: str) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT
                rif.id::text AS id,
                rif.scope,
                rif.dataset,
                rif.file_name,
                rif.file_type,
                rif.storage_path,
                rif.managed,
                rif.file_size,
                rif.sha256,
                rif.source,
                rif.metadata,
                rif.created_at,
                s.sample_id AS sample_identifier
            FROM raw_import_files rif
            LEFT JOIN samples s ON s.id = rif.sample_id
            WHERE rif.family_id = CAST(:family_uuid AS uuid)
            ORDER BY
                CASE WHEN rif.scope = 'family' THEN 0 ELSE 1 END,
                lower(COALESCE(s.sample_id, '')),
                rif.dataset,
                lower(rif.file_name)
            """
        ),
        {"family_uuid": family_uuid},
    )
    # Each local file is looked up on the file system: in a worker thread, off the event loop
    # (SEC-14), as a family can hold many files on a slow mount.
    return await asyncio.to_thread(_rows_to_dicts, result.mappings().all())


async def get_raw_import_file(session: AsyncSession, file_id: str) -> dict[str, Any] | None:
    file_uuid = canonical_uuid(file_id)
    if file_uuid is None:
        return None
    result = await session.execute(
        text(
            """
            SELECT
                rif.id::text AS id,
                rif.scope,
                rif.dataset,
                rif.file_name,
                rif.file_type,
                rif.storage_path,
                rif.managed,
                rif.file_size,
                rif.sha256,
                rif.source,
                rif.metadata,
                rif.created_at,
                s.sample_id AS sample_identifier
            FROM raw_import_files rif
            LEFT JOIN samples s ON s.id = rif.sample_id
            WHERE rif.id = CAST(:file_id AS uuid)
            """
        ),
        {"file_id": file_uuid},
    )
    row = result.mappings().first()
    if row is None:
        return None
    # Its file is looked up on the file system: off the event loop (SEC-14).
    return await asyncio.to_thread(_row_to_dict, row)


# Inline checksum verification is bounded so a multi-GB CRAM/BAM can't tie up a
# worker (and the synchronous Verify response) for minutes. Above the size cap the
# hash is skipped and reported as `too_large`; the wait_for is a backstop for slow
# storage on sub-cap files.
_VERIFY_MAX_BYTES = 2 * 1024**3  # 2 GiB
_VERIFY_TIMEOUT_SECONDS = 30


async def verify_raw_import_file(record: dict[str, Any]) -> dict[str, Any]:
    """Recompute the SHA-256 of the stored file and compare to the recorded value; for a
    file kept in an object store, compare the object with the store's record of it.

    A recorded location CoGA does not read from is refused (403) before anything is read or
    the store is asked: a local path outside the folders a file is read from
    (``readable_local_path``), an object not below a remote FAMILY_IMPORT_ROOTS entry
    (``readable_remote_uri``)."""
    storage_path = record.get("storage_path") or ""
    if is_remote_uri(storage_path):
        if not readable_remote_uri(storage_path):
            raise _refuse_outside_folders(record, OUTSIDE_IMPORT_ROOTS_DETAIL)
        return await asyncio.to_thread(_verify_object_in_store, record)
    expected = record.get("sha256")
    real = await asyncio.to_thread(readable_local_path, storage_path) if storage_path else None
    if storage_path and real is None:
        raise _refuse_outside_folders(record, OUTSIDE_FOLDERS_DETAIL)
    if real is None or not await asyncio.to_thread(os.path.isfile, real):
        return {
            "file_id": record["id"],
            "status": "missing",
            "expected_sha256": expected,
            "computed_sha256": None,
            "message": "The source file is no longer present at its storage path.",
        }
    # Read by its real path: the path checked is the path hashed.
    path = Path(real)
    try:
        file_size: int | None = (await asyncio.to_thread(path.stat)).st_size
    except OSError:
        file_size = None
    if file_size is not None and file_size > _VERIFY_MAX_BYTES:
        return {
            "file_id": record["id"],
            "status": "too_large",
            "expected_sha256": expected,
            "computed_sha256": None,
            "message": (
                f"File is {file_size / 1024**3:.1f} GB; inline checksum verification is "
                f"skipped above {_VERIFY_MAX_BYTES // 1024**3} GB to avoid blocking the request."
            ),
        }
    try:
        computed_sha, computed_size = await asyncio.wait_for(
            asyncio.to_thread(_hash_and_size, path),
            timeout=_VERIFY_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        return {
            "file_id": record["id"],
            "status": "too_large",
            "expected_sha256": expected,
            "computed_sha256": None,
            "message": (
                f"Checksum verification timed out after {_VERIFY_TIMEOUT_SECONDS}s; "
                "the file is too large to verify inline."
            ),
        }
    if not expected:
        return {
            "file_id": record["id"],
            "status": "unverifiable",
            "expected_sha256": None,
            "computed_sha256": computed_sha,
            "message": "No checksum was recorded for this file; nothing to verify against.",
        }
    if computed_sha == expected:
        return {
            "file_id": record["id"],
            "status": "verified",
            "expected_sha256": expected,
            "computed_sha256": computed_sha,
            "message": "Checksum matches; file integrity verified.",
        }
    return {
        "file_id": record["id"],
        "status": "mismatch",
        "expected_sha256": expected,
        "computed_sha256": computed_sha,
        "message": "Checksum does NOT match the recorded value. The file may be corrupted or altered.",
    }


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _verify_object_in_store(record: dict[str, Any]) -> dict[str, Any]:
    """Check a file kept in an object store against what was recorded at import.

    The object must still exist, with the recorded size and, where recorded, the same
    generation (GCS) or version id (S3); an S3 object without a version id is compared
    by ETag, which changes when it is rewritten. The bytes are not re-hashed: a staged
    file was hashed at import, and an alignment is tens of GB. Blocking (store request).
    """
    base = {
        "file_id": record["id"],
        "expected_sha256": record.get("sha256"),
        "computed_sha256": None,
    }
    try:
        current = remote_object_identity(record["storage_path"])
    except Exception as exc:
        # Not a verdict on the file: the store did not answer.
        raise HTTPException(
            status_code=502, detail="The object store could not be reached to verify this file."
        ) from exc
    if current is None:
        return {
            **base,
            "status": "missing",
            "message": "The object is no longer in the store at its recorded location.",
        }
    recorded = _json_object(_json_object(record.get("metadata")).get("store_object"))
    checks: list[tuple[str, Any, Any]] = []  # (what, recorded, found now)
    if record.get("file_size") is not None:
        checks.append(("size", record["file_size"], current.get("size")))
    if recorded.get("generation") is not None:
        checks.append(("generation", recorded["generation"], current.get("generation")))
    if recorded.get("version_id") is not None:
        checks.append(("version id", recorded["version_id"], current.get("version_id")))
    elif current.get("store") == "s3" and recorded.get("etag") is not None:
        checks.append(("ETag", recorded["etag"], current.get("etag")))
    if not checks:
        return {
            **base,
            "status": "unverifiable",
            "message": "Nothing was recorded about this object at import; there is nothing to compare it with.",
        }
    differences = [f"its {what} is {now!r}, recorded {then!r}" for what, then, now in checks if then != now]
    if differences:
        return {
            **base,
            "status": "mismatch",
            "message": "The object in the store is not the one imported: " + "; ".join(differences) + ".",
        }
    compared = " and ".join(what for what, _then, _now in checks)
    return {
        **base,
        "status": "verified",
        "message": f"The object is in the store with the recorded {compared}. Its content is not re-hashed here.",
    }


def _managed_file_to_unlink(storage_path: str, managed_root: str) -> str | None:
    """The file a managed row names, as it is unlinked: the recorded path, normalised, with
    its folder's symlinks resolved; ``None`` unless it lies inside the upload folder
    ``managed_root`` (a real path), and its real path too.

    Every managed copy is written there (``store_managed_file``), so a row naming any other
    place -- a package file, an absolute path elsewhere, ``..`` that climbs out, a symlink
    that leads out -- names no managed copy, and nothing is unlinked for it. A symlink inside
    the folder that stays inside it is unlinked itself, never its target.
    """
    if not storage_path or not os.path.isabs(storage_path):
        return None
    try:
        path = os.path.normpath(storage_path)
        entry = os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))
        real = os.path.realpath(entry)
    except (OSError, ValueError):
        return None
    if _inside(entry, managed_root) and _inside(real, managed_root):
        return entry
    return None


def _unlink_managed_files(rows: Iterable[tuple[str, str]]) -> int:
    """Unlink the managed copy each ``(storage_path, record id)`` names, where it is a file
    inside the upload folder; returns the number unlinked. A row naming any other place is
    skipped with a log line, and its path is never unlinked. Blocking."""
    managed_root = _managed_root()
    removed = 0
    for storage_path, record_id in rows:
        target = _managed_file_to_unlink(storage_path or "", managed_root)
        if target is None:
            logger.warning(
                "The file of raw-file record %s was not deleted: its recorded storage path %s "
                "is outside the upload folder",
                scrub_log(record_id),
                scrub_log(storage_path),
            )
            continue
        try:
            if os.path.isfile(target):
                os.unlink(target)
                removed += 1
        except OSError:
            continue
    return removed


async def purge_family_managed_files(session: AsyncSession, family_uuid: str) -> int:
    """Delete managed file copies owned by a family from disk, each only inside the upload
    folder (``_managed_file_to_unlink``). Database rows cascade away when the family is
    removed. Returns the number of files unlinked."""
    result = await session.execute(
        text(
            """
            SELECT storage_path, id::text AS id
            FROM raw_import_files
            WHERE family_id = CAST(:family_uuid AS uuid)
              AND managed = TRUE
            """
        ),
        {"family_uuid": family_uuid},
    )
    return await asyncio.to_thread(_unlink_managed_files, [(row[0], row[1]) for row in result.fetchall()])


async def purge_sample_managed_files(session: AsyncSession, sample_uuid: str) -> int:
    """Delete managed file copies owned by a single sample from disk, each only inside the
    upload folder (``_managed_file_to_unlink``). Database rows cascade away when the sample
    is removed. Returns the number of files unlinked."""
    result = await session.execute(
        text(
            """
            SELECT storage_path, id::text AS id
            FROM raw_import_files
            WHERE sample_id = CAST(:sample_uuid AS uuid)
              AND managed = TRUE
            """
        ),
        {"sample_uuid": sample_uuid},
    )
    return await asyncio.to_thread(_unlink_managed_files, [(row[0], row[1]) for row in result.fetchall()])
