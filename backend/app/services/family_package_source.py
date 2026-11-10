from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import errno
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Any

from fastapi import HTTPException
import yaml

from ..core.config import settings
from ..core.object_storage import (
    download_prefix,
    is_remote_uri,
    parse_remote_uri,
    remote_uri_within,
)
from ..schemas import FamilyImportValidationIssue

from .family_identifiers import visible
from .family_package_common import (
    NUL,
    NUL_IN_PATH,
    PED_FOLDER,
    PackageManifest,
    _issue,
    runs_through_symlink_loop,
)
from .family_package_long_read import LONG_READ_SAMPLE_DATASET_FOLDERS, long_read_sample_ids
from .coga_package import PACKAGE_FILE_NAME


# Aligned reads and their indexes. Staging a remote package leaves these in the store:
# a whole-genome CRAM is tens of GB, /tmp on Cloud Run is memory, and nothing in the
# import reads them -- the alignments importer records where they lie and the genome
# browser streams them from the store through signed URLs. `.csi` alone is ambiguous:
# it also indexes VCF/BCF files, which the importers do read, so a CSI stays in the
# store only when it names the alignment it indexes (`<sample>.bam.csi`).
_ALIGNMENT_SUFFIXES = (".cram", ".crai", ".bam", ".bai", ".bam.csi", ".cram.csi")


def is_alignment_file(name: str) -> bool:
    """Whether a package file is aligned reads or an index of them, by its name."""
    return name.lower().endswith(_ALIGNMENT_SUFFIXES)


@dataclass(frozen=True, slots=True)
class StagedPackage:
    """A family package ready for the path-based import code.

    ``root`` is the local folder to read; ``source_uri`` the ``gs://``/``s3://`` folder
    it was staged from (``None`` for a local package, used in place). A remote package's
    alignments stay in the store: ``remote_only_files`` holds their package-relative
    paths, so validation and provenance know the files exist although ``root`` lacks
    them.
    """

    root: str
    source_uri: str | None = None
    remote_only_files: frozenset[str] = frozenset()


def package_folder_path(folder_path: str | Path) -> str:
    """The package folder as it should be stored and shown: a remote URI unchanged, a local
    path with ``~`` expanded.

    Passing a ``gs://`` or ``s3://`` URI through ``Path`` collapses the scheme's double slash
    (``gs:/bucket/...``), and ``is_remote_uri`` no longer recognises the result. A queued
    import is later run from the stored value, so it would look for a local folder.
    """
    if is_remote_uri(folder_path):
        return str(folder_path).strip()
    # String operations only: nothing is read here. The import checks the folder against
    # FAMILY_IMPORT_ROOTS before it touches the file system.
    return os.path.normpath(os.path.expanduser(str(folder_path)))


# The staged copy of the package the running import or validation downloaded from a bucket:
# the one place under the staging root the folder guard admits. A typed path into the
# staging root, or a root entry linked there, read another import's staged copy (#850,
# SEC-27).
_CURRENT_STAGED_ROOT: ContextVar[Path | None] = ContextVar("coga_current_staged_package", default=None)


def _staging_root() -> Path:
    """Local scratch dir under which s3:// packages are staged for an import.

    It must be this process's own folder. One planted on a shared temp dir, a symlink or
    another user's folder, would put what it leads to under the staging root, so it is
    refused (PermissionError, an OSError) rather than followed (SEC-27)."""
    root = Path(tempfile.gettempdir()) / "coga-family-imports"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = os.lstat(root)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise PermissionError(f"The staging folder {root} is not this process's own folder")
    return root.resolve()


def _authorized_local_roots() -> list[Path]:
    return [
        Path(root).expanduser().resolve()
        for root in settings.family_import_roots
        if not is_remote_uri(root)
    ]


def _authorized_s3_roots() -> list[str]:
    return [root.strip() for root in settings.family_import_roots if is_remote_uri(root)]


def within_remote_import_roots(uri: str) -> bool:
    """Whether a remote URI names an object below one of the remote FAMILY_IMPORT_ROOTS:
    the only folders package files are read from, so the only place a location the
    import recorded may point."""
    return any(remote_uri_within(uri, root) for root in _authorized_s3_roots())


def within_family_package(uri: str, package_folder: object) -> bool:
    """Whether the remote URI ``uri`` names an object in a family's own package folder,
    ``package_folder``: the bucket folder its import read
    (``families.metadata.package_import.folder_path``), below a remote FAMILY_IMPORT_ROOTS
    entry. A location the import recorded on a sample (its aligned reads, its signal files)
    is served only from there (SEC-27): a changed row can point at most at another of the
    family's own files, never at another family's package in the import area.

    False for a family without such a folder (never imported from a bucket), and for a folder
    that is a bucket's root, holds an empty, ``.`` or ``..`` segment, or lies outside the
    import roots. The URI is checked against the import roots too."""
    if not isinstance(package_folder, str) or not within_remote_import_roots(package_folder):
        return False
    try:
        folder = parse_remote_uri(package_folder)
    except ValueError:
        return False
    if any(segment in {"", ".", ".."} for segment in folder.key.strip("/").split("/")):
        return False
    return remote_uri_within(uri, package_folder) and within_remote_import_roots(uri)


def local_family_package_folder(package_folder: object) -> str | None:
    """The local folder a family's import read (``families.metadata.package_import.folder_path``),
    as its real path, when it lies below a local FAMILY_IMPORT_ROOTS entry; ``None`` otherwise.

    A file the import of a local package recorded on a sample, relative to the package (its
    aligned reads, its signal files, its QC report), is looked up in this folder first
    (``routers/cram.py``, ``routers/signal_tracks.py``, ``routers/family_qc_reports.py``). A
    package is read where it lies, so one on a share mounted as a second import root
    (``/data/share``) keeps its files there; they used to be looked up only under
    ``<data>/families/<family>``, and the genome browser found none of them.

    The folder comes back from the database as data, so it is checked as the import checks a
    folder it reads: ``None`` for no folder, a ``gs://`` or ``s3://`` URI (a package imported
    from a bucket, ``within_family_package``), a relative path or one holding NUL, the import
    root itself, and a folder that lies, symlinks resolved, outside every local import root.
    Also ``None`` for any folder when no local import root is configured: the empty
    FAMILY_IMPORT_ROOTS of development admits any folder to import from, but nothing is served
    from a folder no root lists. Blocking: the folder's symlinks are resolved on disk.
    """
    if not isinstance(package_folder, str) or not package_folder or is_remote_uri(package_folder):
        return None
    if NUL in package_folder or not os.path.isabs(package_folder):
        return None
    try:
        folder = os.path.realpath(package_folder)
        roots = [str(root) for root in _authorized_local_roots()]
    except (OSError, RuntimeError, ValueError):
        return None
    if not any(folder.startswith(root.rstrip(os.sep) + os.sep) for root in roots):
        return None
    return folder


def _listed_package_folder(entry: Path) -> Path | None:
    """The folder ``entry``, a name in a local import root, leads to, as selection takes it
    (``_ensure_authorized_package_path``: its symlinks resolved); None when the package list
    leaves it out.

    Left out: a folder selection refuses, a symlink that leads out of FAMILY_IMPORT_ROOTS
    (403) or one that loops (400), and one in the staging root, which is no import root
    (selection takes there only the package the running import staged). Nothing in a
    folder left out is looked up. The list read the manifest of a folder linked out of the
    root, showed the package under that manifest's family and analysis type, and named its
    path outside the roots, which selection then refused.
    """
    try:
        folder = _ensure_authorized_package_path(entry)
    except HTTPException:
        return None
    if not any(folder == root or root in folder.parents for root in _authorized_local_roots()):
        return None
    return folder


def _addable_package(folder: Path) -> bool | None:
    """Whether the folder ``folder`` holds a PED (True) or the long-read layout (False), so it can
    be added by hand; None when it holds neither, or its own package file."""
    if os.path.lexists(folder / PACKAGE_FILE_NAME):
        return None
    # A folder named like a PED (``FAM1.ped/``) is none, as Discover reads it.
    ped_paths = [path for path in sorted(folder.glob("*.ped")) if path.is_file()] or [
        path for path in sorted((folder / PED_FOLDER).glob("*.ped")) if path.is_file()
    ]
    if ped_paths:
        return True
    return False if long_read_sample_ids(folder) else None


def scan_family_import_packages() -> list[dict[str, Any]]:
    """The package folders under each local import root that can be added to Deliveries by hand
    (import_manual_delivery.py): each one holding a ``*.ped`` file, at its top or in its ``ped/``
    folder (where the PGT pipeline writes it), or laid out per sample as the long-read pipeline
    writes its output (which has no PED for a couple screened for carriership), directly in a root
    or in a project folder in it (``<root>/<project>/<package>``, named in ``project_folder``). A
    folder that holds its own package file is left out: a pipeline delivers it through a watched
    folder.

    A folder selection would refuse is not listed, and nothing in it is read: one that is a
    symlink leading out of the import roots, or one that loops (``_listed_package_folder``).
    A symlink that stays under the roots is followed, and the folder it leads to listed.
    """
    packages: list[dict[str, Any]] = []
    seen: set[str] = set()

    def listed(entry: Path, project_folder: str | None) -> bool:
        """List the folder ``entry`` leads to when it can be added; whether it is one."""
        # Checked before anything in it is looked up, and read through the folder selection reads.
        folder = _listed_package_folder(entry)
        if folder is None or not folder.is_dir():
            return True
        has_ped = _addable_package(folder)
        if has_ped is None:
            return os.path.lexists(folder / PACKAGE_FILE_NAME)
        if str(folder) not in seen:
            seen.add(str(folder))
            packages.append(
                {
                    "folder_path": str(folder),
                    "name": entry.name,
                    "family_id": entry.name,
                    "has_ped": has_ped,
                    "project_folder": project_folder,
                }
            )
        return True

    for root in _authorized_local_roots():
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir(), key=lambda path: path.name.lower()):
            if listed(child, None):
                continue
            # A folder that is no package: a project folder, whose package folders are listed.
            folder = _listed_package_folder(child)
            if folder is None or not folder.is_dir():
                continue
            for inner in sorted(folder.iterdir(), key=lambda path: path.name.lower()):
                listed(inner, child.name)
    return packages


def _symlink_loop_error(exc: BaseException) -> bool:
    """Whether ``exc``, raised by ``Path.resolve``, reports a symlink loop: Python 3.12 raises
    RuntimeError for one, and a strict resolve an OSError with ELOOP."""
    return isinstance(exc, RuntimeError) or (isinstance(exc, OSError) and exc.errno == errno.ELOOP)


def _folder_loop_refusal(written: str) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail=f"Family import path '{visible(written)}' runs through a symlink loop",
    )


def _ensure_authorized_package_path(path: Path) -> Path:
    """The local package folder ``path`` names, ``~`` expanded and symlinks resolved, when it
    lies under FAMILY_IMPORT_ROOTS (``_authorized_package_folder``; 403 otherwise).

    400 when the path cannot be followed: it starts with ``~`` and the server has no such home
    folder (``~nouser/FAM1``), or it runs through a symlink loop. Both failed the request with
    a RuntimeError, a 500: ``Path.expanduser`` raised it for the home folder, and, on Python
    3.12, ``Path.resolve`` for the loop. From 3.13 ``Path.resolve`` returns a loop unresolved,
    so the authorized folder is looked up to find one, and a loop is refused on every version.
    """
    written = str(path)
    if NUL in written:
        # Every look-up of the path raised ValueError on it: a 500 (SAFE-5).
        raise HTTPException(status_code=400, detail=f"Family import path '{visible(written)}' {NUL_IN_PATH}")
    if os.path.expanduser(written).startswith("~"):
        raise HTTPException(
            status_code=400,
            detail=f"Family import path '{visible(written)}' starts with '~', and the server has no such home folder",
        )
    try:
        resolved = _authorized_package_folder(path)
    except (OSError, RuntimeError) as exc:
        if not _symlink_loop_error(exc):
            raise
        raise _folder_loop_refusal(written) from exc
    if runs_through_symlink_loop(resolved):
        raise _folder_loop_refusal(written)
    return resolved


def _authorized_package_folder(path: Path) -> Path:
    """``path`` resolved, when it lies under FAMILY_IMPORT_ROOTS or in the package the running
    import staged; 403 otherwise. Callers go through ``_ensure_authorized_package_path``."""
    resolved = path.expanduser().resolve()
    # The package this import or validation staged from a bucket (its s3:// or gs:// source
    # was checked before the download), and nothing else under the staging root (SEC-27).
    staged = _CURRENT_STAGED_ROOT.get()
    if staged is not None and (resolved == staged or staged in resolved.parents):
        return resolved
    allowed_roots = _authorized_local_roots()
    if any(resolved == root or root in resolved.parents for root in allowed_roots):
        return resolved
    # Fail open ONLY when nothing is configured at all — the explicit "unrestricted" dev
    # default (family_import_roots=[]). When roots ARE configured, a local path outside
    # them is unauthorized: previously the guard fell open whenever no *local* root
    # matched, so an S3-only (remote-only) FAMILY_IMPORT_ROOTS silently allowed any
    # admin-supplied local path — an out-of-allowlist file read/write primitive.
    if not settings.family_import_roots:
        return resolved
    roots = ", ".join(str(root) for root in allowed_roots) or "(remote-only FAMILY_IMPORT_ROOTS)"
    raise HTTPException(
        status_code=403,
        detail=f"Family import path is outside configured FAMILY_IMPORT_ROOTS: {roots}",
    )


def _ensure_authorized_s3_source(uri: str) -> str:
    normalized = str(uri).strip()
    allowed = _authorized_s3_roots()
    if not allowed:
        raise HTTPException(
            status_code=403,
            detail="S3 family import sources require an s3:// entry in FAMILY_IMPORT_ROOTS",
        )
    for root in allowed:
        prefix = root.rstrip("/")
        if normalized == prefix or normalized.startswith(prefix + "/"):
            return normalized
    raise HTTPException(
        status_code=403,
        detail=f"S3 source is outside configured FAMILY_IMPORT_ROOTS: {', '.join(allowed)}",
    )


def _stage_s3_package(uri: str) -> StagedPackage:
    """Download a gs:// or s3:// package prefix into a fresh temp dir under the staging
    root, leaving its alignments in the store (see ``is_alignment_file``)."""
    _ensure_authorized_s3_source(uri)
    dest = Path(tempfile.mkdtemp(prefix="pkg-", dir=_staging_root()))
    left_in_store: set[str] = set()

    def leave_in_store(relative_path: str) -> bool:
        if not is_alignment_file(relative_path):
            return False
        left_in_store.add(relative_path)
        return True

    try:
        downloaded = download_prefix(uri, dest, skip=leave_in_store)
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    if downloaded == 0 and not left_in_store:
        shutil.rmtree(dest, ignore_errors=True)
        raise HTTPException(status_code=404, detail=f"No objects found at S3 family package source: {uri}")
    return StagedPackage(root=str(dest), source_uri=uri, remote_only_files=frozenset(left_in_store))


@contextmanager
def staged_package_source(folder_path: str | Path):
    """Yield a ``StagedPackage``. A gs:// or s3:// source is downloaded to a temp dir
    (cleaned up on exit), all but its alignments; a local path is yielded unchanged."""
    if is_remote_uri(folder_path):
        staged = _stage_s3_package(str(folder_path).strip())
        # Already resolved: mkdtemp made it in the resolved staging root.
        token = _CURRENT_STAGED_ROOT.set(Path(staged.root))
        try:
            yield staged
        finally:
            _CURRENT_STAGED_ROOT.reset(token)
            shutil.rmtree(staged.root, ignore_errors=True)
    else:
        yield StagedPackage(root=str(folder_path))


@asynccontextmanager
async def staged_package_source_async(folder_path: str | Path):
    """Async variant: the download (and cleanup) run in a worker thread so the event
    loop is not blocked during a large package transfer."""
    if is_remote_uri(folder_path):
        staged = await asyncio.to_thread(_stage_s3_package, str(folder_path).strip())
        # Already resolved: mkdtemp made it in the resolved staging root.
        token = _CURRENT_STAGED_ROOT.set(Path(staged.root))
        try:
            yield staged
        finally:
            _CURRENT_STAGED_ROOT.reset(token)
            await asyncio.to_thread(shutil.rmtree, staged.root, True)
    else:
        yield StagedPackage(root=str(folder_path))


def package_folder_link_issues(root: Path) -> list[FamilyImportValidationIssue]:
    """A ``package_folder_link`` warning for each folder of the package at ``root`` that the
    package list and Discover read by its layout and that is a symbolic link to a folder:
    the PED folder (``ped/``), a long-read dataset folder (``snv/``, ``repeats/``, ...), a
    folder in one (a sample's) and a folder in that.

    They follow such a link wherever it leads: a PED in a linked ``ped/`` lists the package,
    and Discover proposes a member from a linked per-sample folder, though what it holds may
    be another family's. The owner chose a warning over a refusal (SEC-27), so Discover and
    the package validation report each such link, in name order: the folder in the package,
    and where it leads, as a path relative to the import folder that holds it, or as a
    folder outside the import folders. A message never names an absolute path. Nothing under
    a link is looked into, and a link that leads to no folder (a dangling one, one to a file,
    one that loops) is followed by nothing and not reported. The containment guards are
    unchanged: a file is read only when it lies in the package (``package_path_within``,
    ``_resolve_package_path``)."""
    issues = [
        _issue("package_folder_link", _package_folder_link_message(root, folder, target), path=folder)
        for folder in _layout_folders(root)
        if (target := _linked_folder_target(folder)) is not None
    ]
    return sorted(issues, key=lambda issue: issue.path or "")


def _layout_folders(root: Path) -> list[Path]:
    """The folders of the package the package list and Discover read by layout, as their
    paths in the package: ``ped/``, each long-read dataset folder, each folder in one and
    each folder in that. A folder that is a symbolic link is listed, but not looked into."""
    level = [root / name for name in LONG_READ_SAMPLE_DATASET_FOLDERS]
    folders: list[Path] = [root / PED_FOLDER, *level]
    for _depth in range(2):
        level = [child for folder in level if not folder.is_symlink() for child in _subfolders(folder)]
        folders.extend(level)
    return folders


def _subfolders(folder: Path) -> list[Path]:
    """The folders in ``folder`` (a symbolic link to a folder counts as one), but for hidden
    ones; none when it is no folder or cannot be listed."""
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return []
    found: list[Path] = []
    for entry in entries:
        try:
            is_folder = entry.is_dir()
        except OSError:
            # A link that loops: no folder.
            is_folder = False
        if is_folder and not entry.name.startswith("."):
            found.append(folder / entry.name)
    return found


def _linked_folder_target(folder: Path) -> str | None:
    """The real path of the folder ``folder`` leads to when it is a symbolic link; None when it
    is none, or leads to no folder."""
    if not folder.is_symlink():
        return None
    target = os.path.realpath(folder)
    return target if os.path.isdir(target) else None


def _package_folder_link_message(root: Path, folder: Path, target: str) -> str:
    """The warning for ``folder`` of the package at ``root``, a symbolic link to the folder
    ``target``: where it leads, relative to the import folder that holds it, or that it lies
    outside the import folders."""
    leads_to = "a folder outside the import folders"
    for import_root in _authorized_local_roots():
        if target == str(import_root):
            leads_to = "the top of an import folder"
            break
        if target.startswith(str(import_root) + os.sep):
            leads_to = f"'{visible(os.path.relpath(target, import_root))}' in the import folders"
            break
    return (
        f"Folder '{visible(folder.relative_to(root).as_posix())}' of the package is a symbolic link "
        f"to {leads_to}: check that what it holds belongs to this family."
    )


def parse_manifest_text(raw_text: str, *, json_format: bool = False) -> tuple[dict[str, Any], PackageManifest]:
    """A manifest's text parsed: its raw payload and its validated model. JSON with
    ``json_format``, else YAML (which a manifest CoGA keeps for an import job is)."""
    payload = json.loads(raw_text) if json_format else yaml.safe_load(raw_text)
    if not isinstance(payload, dict):
        raise ValueError("Manifest must contain a mapping/object at the top level")
    return payload, PackageManifest.model_validate(payload)
