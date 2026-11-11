from __future__ import annotations

import errno
import json
import os
from pathlib import Path
from typing import Any

from fastapi import HTTPException
import yaml

from ..core.config import settings
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


def package_folder_path(folder_path: str | Path) -> str:
    """The package folder as it should be stored and shown: the path with ``~`` expanded,
    normalised."""
    # String operations only: nothing is read here. The import checks the folder against
    # FAMILY_IMPORT_ROOTS before it touches the file system.
    return os.path.normpath(os.path.expanduser(str(folder_path)))


def _authorized_local_roots() -> list[Path]:
    """The FAMILY_IMPORT_ROOTS folders, ``~`` expanded and symlinks resolved."""
    return [Path(root).expanduser().resolve() for root in settings.family_import_roots]


def local_family_package_folder(package_folder: object) -> str | None:
    """The folder a family's import read (``families.metadata.package_import.folder_path``),
    as its real path, when it lies below a FAMILY_IMPORT_ROOTS entry; ``None`` otherwise.

    A file the import recorded on a sample, relative to the package (its aligned reads, its
    signal files, its QC report), is looked up in this folder first (``routers/cram.py``,
    ``routers/signal_tracks.py``, ``routers/family_qc_reports.py``). A package is read where it
    lies, so one on a mounted share or bucket keeps its files there.

    The folder comes back from the database as data, so it is checked as the import checks a
    folder it reads: ``None`` for no folder, a relative path or one holding NUL, the import
    root itself, and a folder that lies, symlinks resolved, outside every import root.
    Also ``None`` for any folder when no import root is configured: the empty
    FAMILY_IMPORT_ROOTS of development admits any folder to import from, but nothing is served
    from a folder no root lists. Blocking: the folder's symlinks are resolved on disk.
    """
    if not isinstance(package_folder, str) or not package_folder:
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
    """The folder ``entry``, a name in an import root, leads to, as selection takes it
    (``_ensure_authorized_package_path``: its symlinks resolved); None when the package list
    leaves it out.

    Left out: a folder selection refuses, a symlink that leads out of FAMILY_IMPORT_ROOTS
    (403) or one that loops (400). Nothing in a folder left out is looked up.
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
    """The package folders under each import root that can be added to Deliveries by hand
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
        # Every look-up of the path raised ValueError on it: a 500.
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
    """``path`` resolved, when it lies under FAMILY_IMPORT_ROOTS; 403 otherwise. Callers go
    through ``_ensure_authorized_package_path``."""
    resolved = path.expanduser().resolve()
    allowed_roots = _authorized_local_roots()
    if any(resolved == root or root in resolved.parents for root in allowed_roots):
        return resolved
    # Open only when no root is configured at all: the explicit "unrestricted" development
    # default (family_import_roots=[]).
    if not allowed_roots:
        return resolved
    roots = ", ".join(str(root) for root in allowed_roots)
    raise HTTPException(
        status_code=403,
        detail=f"Family import path is outside configured FAMILY_IMPORT_ROOTS: {roots}",
    )


def package_folder_link_issues(root: Path) -> list[FamilyImportValidationIssue]:
    """A ``package_folder_link`` warning for each folder of the package at ``root`` that the
    package list and Discover read by its layout and that is a symbolic link to a folder:
    the PED folder (``ped/``), a long-read dataset folder (``snv/``, ``repeats/``, ...), a
    folder in one (a sample's) and a folder in that.

    They follow such a link wherever it leads: a PED in a linked ``ped/`` lists the package,
    and Discover proposes a member from a linked per-sample folder, though what it holds may
    be another family's. The owner chose a warning over a refusal, so Discover and
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
