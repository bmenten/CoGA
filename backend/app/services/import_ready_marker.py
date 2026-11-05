"""The ready marker a pipeline writes last into a package folder: ``coga-ready.json``.

A watched import folder (``import_watcher``) holds pipeline output that other hosts copy in. The
marker says that a package folder is complete, what each of its files is (path, size and checksum),
and where the package belongs: the project, the family, the analysis, the reference assembly, the
pipeline and its run. The format is ``coga-ready/1`` (docs/data-import.md, "Watched import
folders"); this module reads it and checks a folder against it, reading no file but the marker and
the files it lists.

- ``read_ready_marker`` reads and parses the marker: a regular file in the folder (never a link),
  at most ``MARKER_MAX_BYTES``, JSON of the format, holding no NUL and no lone surrogate (which no
  stored text can hold). A field the format requires that is missing or
  empty is an issue; a field it does not know is kept.
- ``check_marker_files`` checks each listed file: its path stays in the folder, it exists with the
  listed size, and, when asked, its SHA-256 or MD5 is the listed one. A file missing, or of another
  size, means the copy has not finished; a checksum that differs, a copy that differs from what the
  pipeline made. A file in the folder the marker does not list is reported apart.

The checks of what the marker says against the package and CoGA (the project, the family, the
samples) are the watcher's.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .family_package_common import manifest_text_problem
from .family_package_fingerprint import PACKAGE_FINGERPRINT_MAX_ENTRIES, PackageFolderTooLarge

MARKER_FILE_NAME = "coga-ready.json"
MARKER_SCHEMA = "coga-ready/1"
# A marker lists the files of one package: a few hundred, at most some thousands.
MARKER_MAX_BYTES = 20 * 1024 * 1024

# The analyses a marker may name; the watcher refuses another (docs/data-import.md).
ANALYSIS_TYPES = frozenset(
    {"wgs", "wgs_trio", "pgt_m", "monogenic_nipt", "long_read_wgs", "long_read_couple"}
)

# Written by a file browser, never by a pipeline: not reported as unlisted.
_IGNORED_NAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini", MARKER_FILE_NAME})
_IGNORED_PREFIXES = ("._", ".coga-ready.json.")

_HEX64 = r"^[0-9a-f]{64}$"
_HEX32 = r"^[0-9a-f]{32}$"


class MarkerIssue(BaseModel):
    """What is wrong with a marker, or with the folder against it: a code and a fixed sentence;
    ``path`` is a path in the package when the issue is about one file."""

    code: str
    message: str
    path: str | None = None


class MarkerFile(BaseModel):
    model_config = ConfigDict(extra="allow")

    path: str = Field(min_length=1)
    size: int = Field(ge=0)
    sha256: str | None = Field(default=None, pattern=_HEX64)
    md5: str | None = Field(default=None, pattern=_HEX32)


class MarkerSample(BaseModel):
    model_config = ConfigDict(extra="allow")

    sample_id: str = Field(min_length=1)
    role: str | None = None
    sex: Literal["male", "female", "unknown"] | None = None


class MarkerPipeline(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str | None = None
    version: str | None = None


class MarkerRun(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None


class MarkerQc(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: Literal["pass", "warn", "fail"] | None = None
    report: str | None = None


class ReadyMarker(BaseModel):
    """A marker as the format ``coga-ready/1`` defines it. The fields the format requires are
    optional here, so that a marker without one is read and told what it lacks
    (``missing_fields``) rather than refused whole."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    schema_: str | None = Field(default=None, alias="schema")
    written_at: str | None = None
    project: str | None = None
    family_id: str | None = None
    analysis_type: str | None = None
    assembly: str | None = None
    pipeline: MarkerPipeline | None = None
    run: MarkerRun | None = None
    files: list[MarkerFile] | None = None
    samples: list[MarkerSample] | None = None
    request_id: str | None = None
    manifest: str | None = None
    qc: MarkerQc | None = None


def missing_fields(marker: ReadyMarker) -> list[str]:
    """The fields the format requires that the marker lacks or leaves empty, in its order."""
    values: list[tuple[str, Any]] = [
        ("written_at", marker.written_at),
        ("project", marker.project),
        ("family_id", marker.family_id),
        ("analysis_type", marker.analysis_type),
        ("assembly", marker.assembly),
        ("pipeline.name", marker.pipeline.name if marker.pipeline else None),
        ("pipeline.version", marker.pipeline.version if marker.pipeline else None),
        ("run.id", marker.run.id if marker.run else None),
        ("files", marker.files),
    ]
    return [name for name, value in values if value in (None, "", [])]


@dataclass(frozen=True, slots=True)
class MarkerRead:
    """A marker as read: its text's SHA-256 (None when it could not be read), the marker (None
    when it is no marker of the format) and what is wrong with it."""

    sha256: str | None
    marker: ReadyMarker | None
    issues: list[MarkerIssue]
    payload: dict[str, Any] | None = None


def _issue(code: str, message: str, path: str | None = None) -> MarkerIssue:
    return MarkerIssue(code=code, message=message, path=path)


def _unstorable(value: Any) -> bool:
    """Whether a key or a string anywhere in the parsed marker holds a NUL or a lone surrogate."""
    if isinstance(value, str):
        return manifest_text_problem(value) is not None
    if isinstance(value, dict):
        return any(_unstorable(key) or _unstorable(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_unstorable(item) for item in value)
    return False


def read_ready_marker(folder: Path) -> MarkerRead:
    """Read ``coga-ready.json`` in the package folder ``folder``. Never follows a link, never reads
    more than ``MARKER_MAX_BYTES``."""
    path = folder / MARKER_FILE_NAME
    try:
        info = os.lstat(path)
    except OSError:
        return MarkerRead(None, None, [_issue("marker_missing", "The folder holds no coga-ready.json.")])
    if not stat.S_ISREG(info.st_mode):
        return MarkerRead(
            None, None, [_issue("marker_not_a_file", "coga-ready.json is a link or no regular file, and is not read.")]
        )
    if info.st_size > MARKER_MAX_BYTES:
        return MarkerRead(
            None,
            None,
            [_issue("marker_too_large", f"coga-ready.json is larger than {MARKER_MAX_BYTES // (1024 * 1024)} MB.")],
        )
    try:
        raw = path.read_bytes()
    except OSError:
        return MarkerRead(None, None, [_issue("marker_unreadable", "coga-ready.json could not be read.")])
    digest = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return MarkerRead(digest, None, [_issue("marker_not_json", "coga-ready.json is no JSON text.")])
    if not isinstance(payload, dict):
        return MarkerRead(digest, None, [_issue("marker_not_json", "coga-ready.json is no JSON object.")])
    # A NUL or a lone surrogate (JSON escapes can carry both) is no text Postgres stores: the
    # marker is refused before anything of it is kept.
    if _unstorable(payload):
        return MarkerRead(
            digest,
            None,
            [_issue("marker_unstorable", "coga-ready.json holds a NUL character or a lone surrogate, which CoGA cannot keep.")],
        )
    if payload.get("schema") != MARKER_SCHEMA:
        return MarkerRead(
            digest,
            None,
            [_issue("marker_schema_unknown", f"coga-ready.json is not of the format {MARKER_SCHEMA}.")],
            payload,
        )
    try:
        marker = ReadyMarker.model_validate(payload)
    except ValidationError as exc:
        where = ", ".join(sorted({".".join(str(part) for part in error["loc"]) for error in exc.errors()}))
        return MarkerRead(
            digest,
            None,
            [_issue("marker_invalid", f"coga-ready.json does not follow {MARKER_SCHEMA}: {where}.")],
            payload,
        )
    issues = [
        _issue("marker_field_missing", f"coga-ready.json does not give {name}.") for name in missing_fields(marker)
    ]
    if marker.analysis_type and marker.analysis_type not in ANALYSIS_TYPES:
        issues.append(
            _issue(
                "marker_analysis_type_unknown",
                f"coga-ready.json names the analysis type '{marker.analysis_type}', which CoGA does not know.",
            )
        )
    return MarkerRead(digest, marker, issues, payload)


def marker_path_in_folder(folder: Path, written: str) -> str | None:
    """The marker's path ``written``, as a path in the package (``a/b.vcf``), or None when it
    leaves the package: absolute, with ``..`` or a backslash, or through a link out of it."""
    if "\\" in written or "\x00" in written:
        return None
    pure = PurePosixPath(written)
    if pure.is_absolute() or any(part in ("..", "") for part in written.split("/")) or str(pure) == ".":
        return None
    resolved = os.path.realpath(folder / pure)
    base = os.path.realpath(folder)
    if not resolved.startswith(base + os.sep):
        return None
    return pure.as_posix()


def _checksums(path: Path, *, md5: bool) -> tuple[str, str | None]:
    """The file's SHA-256, and its MD5 when asked, in one read."""
    sha256_digest = hashlib.sha256()
    md5_digest = hashlib.md5(usedforsecurity=False) if md5 else None
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            sha256_digest.update(block)
            if md5_digest is not None:
                md5_digest.update(block)
    return sha256_digest.hexdigest(), md5_digest.hexdigest() if md5_digest is not None else None


@dataclass(slots=True)
class FileCheck:
    """A folder checked against its marker's ``files``: the issues that keep it from import,
    whether the copy is still running (``incomplete``: a file missing or of another size), the
    files present that the marker does not list, and each listed file's SHA-256 as computed."""

    issues: list[MarkerIssue] = field(default_factory=list)
    incomplete: bool = False
    unlisted: list[str] = field(default_factory=list)
    sha256: dict[str, str] = field(default_factory=dict)


def _folder_files(folder: Path) -> list[str]:
    """Every regular file in the package folder, as a path in it, but those a file browser writes
    and the marker itself; links are not followed."""
    found: list[str] = []
    pending = [(folder, "")]
    seen = 0
    while pending:
        current, relative = pending.pop()
        try:
            with os.scandir(current) as listing:
                entries = list(listing)
        except OSError:
            continue
        for entry in entries:
            if entry.name in _IGNORED_NAMES or entry.name.startswith(_IGNORED_PREFIXES):
                continue
            seen += 1
            if seen > PACKAGE_FINGERPRINT_MAX_ENTRIES:
                raise PackageFolderTooLarge(PACKAGE_FINGERPRINT_MAX_ENTRIES)
            path = f"{relative}/{entry.name}" if relative else entry.name
            try:
                if entry.is_dir(follow_symlinks=False):
                    pending.append((Path(entry.path), path))
                elif entry.is_file(follow_symlinks=False):
                    found.append(path)
            except OSError:
                continue
    return sorted(found)


def check_marker_files(folder: Path, files: Iterable[MarkerFile], *, checksums: bool) -> FileCheck:
    """Check the package folder ``folder`` against the files its marker lists; with ``checksums``
    each file is read to compare its SHA-256, or its MD5 when the marker gives only that, and
    every file's SHA-256 is kept. Without, only paths and sizes are compared (a cheap check
    whether the copy has finished)."""
    check = FileCheck()
    listed: dict[str, MarkerFile] = {}
    for entry in files:
        path = marker_path_in_folder(folder, entry.path)
        if path is None:
            check.issues.append(
                _issue("marker_path_outside", "coga-ready.json lists a path that leaves the package folder.")
            )
            continue
        if path in listed:
            check.issues.append(_issue("marker_path_twice", "coga-ready.json lists a file twice.", path))
            continue
        if entry.sha256 is None and entry.md5 is None:
            check.issues.append(_issue("marker_checksum_missing", "coga-ready.json gives no checksum of a file.", path))
        listed[path] = entry
    for path, entry in sorted(listed.items()):
        try:
            info = os.lstat(folder / path)
        except OSError:
            check.incomplete = True
            check.issues.append(_issue("file_missing", "A file coga-ready.json lists is not in the folder yet.", path))
            continue
        if not stat.S_ISREG(info.st_mode):
            check.issues.append(_issue("file_not_regular", "A file coga-ready.json lists is a link or a folder.", path))
            continue
        if info.st_size != entry.size:
            check.incomplete = True
            check.issues.append(
                _issue("file_size_differs", "A file has another size than coga-ready.json lists: the copy may still run.", path)
            )
            continue
        if not checksums:
            continue
        try:
            sha256, md5 = _checksums(folder / path, md5=entry.sha256 is None and entry.md5 is not None)
        except OSError:
            check.issues.append(_issue("file_unreadable", "A file coga-ready.json lists could not be read.", path))
            continue
        if (entry.sha256 is not None and sha256 != entry.sha256) or (entry.sha256 is None and md5 != entry.md5):
            check.issues.append(
                _issue(
                    "file_checksum_differs",
                    "A file's checksum differs from the one in coga-ready.json: the copy is not what the pipeline made.",
                    path,
                )
            )
            continue
        check.sha256[path] = sha256
    present = set(_folder_files(folder))
    check.unlisted = sorted(present - set(listed))
    return check
