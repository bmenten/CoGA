"""The package files a validation read whole, remembered per file version.

The validation reads every package VCF to its end with the import's readers
(``family_package_common.read_package_vcf_whole``): for a genome's callset a minute or more. A
delivery is checked again when a superuser corrects it (Deliveries, *Correct…*), inside the
request, and its files rarely change between two checks. So a file read whole is remembered in
``package_file_reads``, per file and reader, with its size and its modification and change
times, and with what the read found (its sample columns, whether it holds a record). A later
validation of the file with those three unchanged takes what was found and reads nothing (the
owner's decision of 2026-11-21); a file that changed is read again.

Only a whole read that succeeded is remembered: a refusal is read again each time, so a file
the disk would not give at one check is never held unreadable after.

A validation runs in a thread (``validated_package``): the files its folder holds that are
remembered are loaded before, into the context the thread reads (``_REMEMBERED``), and what it
read whole is stored after. A failure to load or store them only costs the reads: it is logged,
and the validation reads every file.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from ..core.coga_logging import describe_error
from ..core.postgres import get_postgres_sessionmaker

if TYPE_CHECKING:
    from ..schemas import FamilyPackageValidationOut
    from .family_package_common import FamilyPackageBundle

logger = logging.getLogger(__name__)

# A file's version: its size, and its modification and change times in nanoseconds.
FileVersion = tuple[int, int, int]


@dataclass(frozen=True)
class RememberedRead:
    """What a whole read of a file of ``version`` found."""

    version: FileVersion
    columns: tuple[str, ...]
    holds_a_line: bool


@dataclass
class RememberedReads:
    """The remembered reads of a validation, by (path, reader): those loaded before it, and
    those it read whole, to store after it."""

    known: dict[tuple[str, str], RememberedRead] = field(default_factory=dict)
    read: dict[tuple[str, str], RememberedRead] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def find(self, path: Path, reader: str, version: FileVersion) -> RememberedRead | None:
        with self._lock:
            key = (file_key(path), reader)
            found = self.read.get(key) or self.known.get(key)
        return found if found is not None and found.version == version else None

    def keep(self, path: Path, reader: str, found: RememberedRead) -> None:
        with self._lock:
            self.read[(file_key(path), reader)] = found


_REMEMBERED: ContextVar[RememberedReads | None] = ContextVar("package_file_reads", default=None)


def file_key(path: Path) -> str:
    """The path a file is remembered under: absolute, as given (no link resolved)."""
    return os.path.abspath(path)


def file_version(path: Path) -> FileVersion | None:
    """``path``'s size and modification and change times, or None when it cannot be read."""
    try:
        status = path.stat()
    except OSError:
        return None
    return status.st_size, status.st_mtime_ns, status.st_ctime_ns


def remembered_read(path: Path, reader: str) -> tuple[RememberedRead | None, FileVersion | None]:
    """What a whole read of ``path`` with ``reader`` found, when the validation remembers one of
    the file as it is now; and the file's version, to remember a read under."""
    version = file_version(path)
    reads = _REMEMBERED.get()
    if reads is None or version is None:
        return None, version
    return reads.find(path, reader, version), version


def remember_read(
    path: Path, reader: str, version: FileVersion | None, *, columns: list[str], holds_a_line: bool
) -> None:
    """Remember that ``path``, of ``version``, read whole with ``reader``: only when the file is
    still that version, so a file that changed while it was read is read again next time."""
    reads = _REMEMBERED.get()
    if reads is None or version is None or file_version(path) != version:
        return
    reads.keep(path, reader, RememberedRead(version=version, columns=tuple(columns), holds_a_line=holds_a_line))


@contextmanager
def remembering(reads: RememberedReads) -> Iterator[RememberedReads]:
    """``reads`` as the remembered reads of what runs in this context (and the threads it
    starts with ``asyncio.to_thread``, which copies the context)."""
    token = _REMEMBERED.set(reads)
    try:
        yield reads
    finally:
        _REMEMBERED.reset(token)


_LOAD_SQL = text(
    """
    SELECT path, reader, size, mtime_ns, ctime_ns, columns, holds_a_line
    FROM package_file_reads
    WHERE starts_with(path, :prefix)
    """
)

_STORE_SQL = text(
    """
    INSERT INTO package_file_reads (path, reader, size, mtime_ns, ctime_ns, columns, holds_a_line, read_at)
    VALUES (:path, :reader, :size, :mtime_ns, :ctime_ns, :columns, :holds_a_line, now())
    ON CONFLICT (path, reader) DO UPDATE SET
        size = EXCLUDED.size,
        mtime_ns = EXCLUDED.mtime_ns,
        ctime_ns = EXCLUDED.ctime_ns,
        columns = EXCLUDED.columns,
        holds_a_line = EXCLUDED.holds_a_line,
        read_at = EXCLUDED.read_at
    """
)


async def load_remembered_reads(folder: str | Path) -> RememberedReads:
    """The remembered reads of the files under ``folder``; none when they cannot be loaded."""
    prefix = file_key(Path(folder)).rstrip(os.sep) + os.sep
    reads = RememberedReads()
    try:
        async with get_postgres_sessionmaker()() as session:
            rows = (await session.execute(_LOAD_SQL, {"prefix": prefix})).mappings().all()
    except Exception as exc:  # noqa: BLE001 - a cache: without it every file is read
        logger.warning("The remembered package file reads could not be loaded: %s", describe_error(exc))
        return reads
    for row in rows:
        reads.known[(str(row["path"]), str(row["reader"]))] = RememberedRead(
            version=(int(row["size"]), int(row["mtime_ns"]), int(row["ctime_ns"])),
            columns=tuple(str(column) for column in row["columns"]),
            holds_a_line=bool(row["holds_a_line"]),
        )
    return reads


async def store_remembered_reads(reads: RememberedReads) -> None:
    """Store the reads a validation made whole; nothing when they cannot be stored."""
    if not reads.read:
        return
    rows: list[dict[str, Any]] = [
        {
            "path": path,
            "reader": reader,
            "size": found.version[0],
            "mtime_ns": found.version[1],
            "ctime_ns": found.version[2],
            "columns": list(found.columns),
            "holds_a_line": found.holds_a_line,
        }
        for (path, reader), found in reads.read.items()
    ]
    try:
        async with get_postgres_sessionmaker()() as session:
            await session.execute(_STORE_SQL, rows)
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - a cache: without it the files are read next time
        logger.warning("The package file reads could not be remembered: %s", describe_error(exc))


async def validated_package(
    folder_path: str | Path, *, manifest_text: str
) -> tuple[FamilyPackageValidationOut, FamilyPackageBundle | None]:
    """``family_package_validation.load_validated_family_package`` of ``folder_path`` in a
    thread, reading whole only the files it does not remember as they are now, and remembering
    those it read whole."""
    from .family_package_validation import load_validated_family_package

    reads = await load_remembered_reads(folder_path)
    with remembering(reads):
        result = await asyncio.to_thread(load_validated_family_package, folder_path, manifest_text=manifest_text)
    await store_remembered_reads(reads)
    return result
