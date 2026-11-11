"""Provenance tracking for raw source files used to import family/sample data.

Every importer records the original file it ingested here so the Admin -> Data ->
Families page can offer complete traceability: file name, type, scope, associated
sample, storage path, human-readable size, SHA-256 checksum, and a download link
with on-demand integrity verification.

Two kinds of records exist:

* ``managed`` files are copies we own under ``data/raw_imports/<family_id>/``. These
  come from web uploads where the original bytes would otherwise be discarded after
  parsing. We delete them when the owning family or sample is deleted, once that deletion
  has committed: a deletion that fails keeps every file its records name.
* referenced files live at their original on-disk location (e.g. a family package
  import directory). We never move or delete those; we only record where they are.

The recorded ``storage_path`` is read back as data: an edited row can name any path. So a
file is read (downloaded, verified, or checked for in the list) only where an import of its
family put it: in the package folder the family's import read
(``families.metadata.package_import.folder_path``, below a local FAMILY_IMPORT_ROOTS entry,
``local_family_package_folder``), or in the managed upload folder (``data/raw_imports``),
which holds every managed copy; its real path, symlinks resolved, inside one of them. The IGV
endpoints serve a sample's recorded files by the same rule. So an edited row can point at
most at another file of its family's own package, never at another family's package in the
import area. The upload folder is not split by family: a managed folder need not be named
after the family's present ID. A family or sample delete unlinks a managed copy only inside
the upload folder.
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

from ..core.coga_logging import describe_error, scrub_log
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
# The random prefix of a managed copy's name (store_managed_file).
_MANAGED_COPY_PREFIX = re.compile(r"[0-9a-f]{12}")


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


# --- Where a recorded file may be read or deleted ---------------------------------------

# The answers to a download or Verify of a file whose recorded location CoGA does not read.
OUTSIDE_FOLDERS_DETAIL = (
    "The file's recorded storage path lies outside its family's package folder (below "
    "FAMILY_IMPORT_ROOTS) and CoGA's upload folder, so the file is not read."
)


def _managed_root() -> str:
    """The real path of the managed upload folder, ``data/raw_imports``."""
    return os.path.realpath(DATA_DIR / MANAGED_SUBDIR)


def _inside(path: str, folder: str) -> bool:
    """Whether the real path ``path`` lies inside the real path ``folder``, told from the
    strings (as the import's folder guard tells it, so a root of ``/`` holds every path)."""
    return Path(folder) in Path(path).parents


def readable_folders(package_folder: object = None) -> tuple[str, ...]:
    """The real paths of the folders a family's recorded file is read from: the managed
    upload folder, and the family's package folder ``package_folder`` (the folder its import
    read, ``families.metadata.package_import.folder_path``) when it lies below a
    FAMILY_IMPORT_ROOTS entry (``local_family_package_folder``).

    Every managed copy CoGA writes (an upload, a manifest kept for an import's job) is in the
    upload folder, and every file a package import records is in its package folder: a file
    of the family lies in one of the two, wherever the row says it is. Not the rest of the
    import area: another family's package there. Without a usable package folder (none
    recorded, the import root itself, outside the roots, FAMILY_IMPORT_ROOTS empty as in
    development) it is the upload folder alone. Blocking (file-system look-ups).
    """
    folders = [_managed_root()]
    package = family_package_source.local_family_package_folder(package_folder)
    if package is not None:
        folders.append(package)
    return tuple(folders)


def readable_local_path(storage_path: str, folders: Iterable[str] | None = None) -> str | None:
    """The real path of the local file ``storage_path`` names, when it may be read there;
    ``None`` otherwise.

    The recorded path must be absolute, and its real path, symlinks resolved, must lie inside
    one of ``folders`` (``readable_folders()``, the upload folder alone, unless given), so a
    symlink that leads out of them is not followed. The file is read by the real path
    returned, never by the recorded one. Blocking.
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


def _storage_path_kind(storage_path: Any) -> str:
    """What kind of place a refused storage path names, for its log line: never the path
    itself, which can name a family's folder, and so its ID (the owner's decision of
    2026-11-10). The record, by its ID, still holds the path."""
    value = str(storage_path or "").strip()
    if not value:
        return "no path"
    return "an absolute local path" if os.path.isabs(value) else "a relative local path"


def _refuse_outside_folders(record: dict[str, Any], detail: str) -> HTTPException:
    """The 403 for a read of a file whose recorded location CoGA does not read, logged with
    the record's ID and the kind of place its path names, so an edited row leaves a trace
    without the log naming another family's folder."""
    logger.warning(
        "Raw file %s was not read: its recorded storage path (%s) is outside its family's "
        "package folder and the upload folder",
        scrub_log(record.get("id")),
        _storage_path_kind(record.get("storage_path")),
    )
    return HTTPException(status_code=403, detail=detail)


def local_file_to_download(record: dict[str, Any]) -> str:
    """The real path of the file to serve for a download of ``record``.

    403 for a recorded path outside the folders its family's files are read from
    (``readable_folders``, with the family's ``package_folder`` the record was read with),
    410 when the file is no longer there. Blocking.
    """
    storage_path = record.get("storage_path") or ""
    folders = readable_folders(record.get("package_folder"))
    path = readable_local_path(storage_path, folders) if storage_path else None
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
) -> None:
    """Upsert a single provenance row.

    When ``compute_checksum`` is set and the checksum/size are not supplied, the
    file at ``storage_path`` is streamed to derive them. Insertion is idempotent on
    (family, sample, storage_path) so re-imports refresh rather than duplicate.
    """
    if (sha256 is None or file_size is None) and compute_checksum:
        computed_sha, computed_size = await asyncio.to_thread(_hash_and_size, Path(storage_path))
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
        # Provenance must not fail the import, but a missing record must leave a trace.
        # The family by its UUID, and no file name, which can hold a sample's ID.
        logger.warning(
            "Raw-file provenance for family %s (%s) was not recorded",
            family_uuid,
            scrub_log(dataset),
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
            "Raw-file provenance for family %s (%s) was not recorded: the upload could not be re-read",
            family_uuid,
            scrub_log(dataset),
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
    """A row as the API serves it, with whether its file is there to download. The row
    carries its family's ``package_folder``; ``folders``: ``readable_folders`` of it, when the
    caller already has them."""
    record = dict(row)
    storage_path = record.get("storage_path") or ""
    # A recorded path outside the folders its family's files are read from is shown
    # unavailable, whether or not a file lies there: it is not looked up.
    if folders is None:
        folders = readable_folders(record.get("package_folder"))
    path = readable_local_path(storage_path, folders) if storage_path else None
    exists = path is not None and os.path.isfile(path)
    record["outside_read_folders"] = bool(storage_path) and path is None
    record["exists"] = exists
    record["download_available"] = bool(exists)
    created = record.get("created_at")
    if created is not None and not isinstance(created, str):
        record["created_at"] = created.isoformat()
    return record


def _rows_to_dicts(rows: Iterable[Any]) -> list[dict[str, Any]]:
    # The rows are one family's, read with its package folder: its folders are found once.
    folders: dict[Any, tuple[str, ...]] = {}
    listed = []
    for row in rows:
        package_folder = row.get("package_folder")
        if package_folder not in folders:
            folders[package_folder] = readable_folders(package_folder)
        listed.append(_row_to_dict(row, folders[package_folder]))
    return listed


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
                s.sample_id AS sample_identifier,
                f.metadata -> 'package_import' ->> 'folder_path' AS package_folder
            FROM raw_import_files rif
            JOIN families f ON f.id = rif.family_id
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
    # Each local file is looked up on the file system: in a worker thread, off the event loop,
    # as a family can hold many files on a slow mount.
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
                s.sample_id AS sample_identifier,
                f.metadata -> 'package_import' ->> 'folder_path' AS package_folder
            FROM raw_import_files rif
            JOIN families f ON f.id = rif.family_id
            LEFT JOIN samples s ON s.id = rif.sample_id
            WHERE rif.id = CAST(:file_id AS uuid)
            """
        ),
        {"file_id": file_uuid},
    )
    row = result.mappings().first()
    if row is None:
        return None
    # Its file is looked up on the file system: off the event loop.
    return await asyncio.to_thread(_row_to_dict, row)


# Inline checksum verification is bounded so a multi-GB CRAM/BAM can't tie up a
# worker (and the synchronous Verify response) for minutes. Above the size cap the
# hash is skipped and reported as `too_large`; the wait_for is a backstop for slow
# storage on sub-cap files.
_VERIFY_MAX_BYTES = 2 * 1024**3  # 2 GiB
_VERIFY_TIMEOUT_SECONDS = 30


async def verify_raw_import_file(record: dict[str, Any]) -> dict[str, Any]:
    """Recompute the SHA-256 of the stored file and compare to the recorded value.

    A recorded location CoGA does not read from is refused (403) before anything is read: a
    path outside its family's package folder and the upload folder (``readable_folders``, with
    the record's ``package_folder``)."""
    storage_path = record.get("storage_path") or ""
    package_folder = record.get("package_folder")
    expected = record.get("sha256")

    def readable() -> str | None:
        return readable_local_path(storage_path, readable_folders(package_folder))

    real = await asyncio.to_thread(readable) if storage_path else None
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


def _managed_copy_handle(path: str | os.PathLike[str]) -> str:
    """How a log line names a managed copy: by the random prefix ``store_managed_file`` gives
    its name (``<12 hex>__…``), never by the rest of the name or by its folder, which hold the
    uploaded file's name and the family's ID. A name without that prefix is not named."""
    prefix, separator, _rest = os.path.basename(os.fspath(path)).partition("__")
    if separator and _MANAGED_COPY_PREFIX.fullmatch(prefix):
        return f"{prefix}__…"
    return "a file not named as CoGA names a copy"


def _unlink_managed_files(rows: Iterable[tuple[str, str]]) -> int:
    """Unlink the managed copy each ``(storage_path, record id)`` names, where it is a file
    inside the upload folder; returns the number unlinked. A row naming any other place is
    skipped with a log line, and its path is never unlinked. A copy that cannot be unlinked
    is left, with a log line naming the error by its kind (``describe_error``): its text
    quotes the path, and the record was deleted before this ran. Blocking."""
    managed_root = _managed_root()
    removed = 0
    for storage_path, record_id in rows:
        target = _managed_file_to_unlink(storage_path or "", managed_root)
        if target is None:
            logger.warning(
                "The file of raw-file record %s was not deleted: its recorded storage path (%s) "
                "is outside the upload folder",
                scrub_log(record_id),
                _storage_path_kind(storage_path),
            )
            continue
        try:
            if os.path.isfile(target):
                os.unlink(target)
                removed += 1
        except FileNotFoundError:  # gone meanwhile: nothing is left
            continue
        except OSError as exc:
            # Not by its path, which holds the family's ID and the uploaded file's name: by the
            # random prefix CoGA gave the copy's name, which finds it in the upload folder.
            logger.warning(
                "The file of deleted raw-file record %s could not be unlinked (%s) and is left "
                "in the upload folder as %s",
                scrub_log(record_id),
                describe_error(exc),
                _managed_copy_handle(target),
            )
    return removed


async def family_managed_files(session: AsyncSession, family_uuid: str) -> list[tuple[str, str]]:
    """The managed copies the family's rows name, ``(storage_path, record id)``: read in the
    transaction that deletes the family, before its rows go (they cascade away with it), for
    ``unlink_managed_files`` once that transaction has committed."""
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
    return [(row[0], row[1]) for row in result.fetchall()]


async def sample_managed_files(session: AsyncSession, sample_uuid: str) -> list[tuple[str, str]]:
    """The managed copies the sample's rows name, as ``family_managed_files`` reads the
    family's: for ``unlink_managed_files`` once the sample's deletion has committed."""
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
    return [(row[0], row[1]) for row in result.fetchall()]


async def unlink_managed_files(files: Iterable[tuple[str, str]]) -> int:
    """Unlink the managed copies ``files`` names (``family_managed_files``,
    ``sample_managed_files``) once the deletion of their rows has committed, each only inside
    the upload folder (``_managed_file_to_unlink``). Returns the number unlinked.

    Unlinked only after the commit, so a deletion that fails keeps every file its rows name;
    once it has committed the deletion stands whatever becomes of this: a copy that cannot be
    unlinked is left, a file without a record, never a record without its file, and the log
    says so. Never raises."""
    try:
        rows = list(files)
        return await asyncio.to_thread(_unlink_managed_files, rows) if rows else 0
    except Exception as exc:  # noqa: BLE001 - the deletion has committed; it stands
        logger.warning(
            "The files of deleted raw-file records were not all unlinked (%s); any not "
            "unlinked are left in the upload folder",
            describe_error(exc),
        )
        return 0
