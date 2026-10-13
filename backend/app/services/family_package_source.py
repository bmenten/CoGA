from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
import errno
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

from fastapi import HTTPException
import yaml

from ..core.config import settings
from ..core.object_storage import (
    download_prefix,
    is_remote_uri,
    list_remote_package_candidates,
    remote_uri_within,
)
from ..schemas import FamilyImportValidationIssue

from .family_identifiers import visible
from .family_package_common import PED_FOLDER, PackageManifest, _issue, package_path_within, runs_through_symlink_loop
from .family_package_long_read import long_read_sample_ids


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


def _staging_root() -> Path:
    """Local scratch dir under which s3:// packages are staged for an import."""
    root = Path(tempfile.gettempdir()) / "coga-family-imports"
    root.mkdir(parents=True, exist_ok=True)
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


def _load_manifest_dict(manifest_path: Path) -> dict[str, Any]:
    """Loosely load a manifest (YAML/JSON) to a dict, or {} on any error."""
    try:
        text_value = manifest_path.read_text()
        if manifest_path.suffix in (".yaml", ".yml"):
            data = yaml.safe_load(text_value)
        else:
            data = json.loads(text_value)
    except Exception:  # noqa: BLE001 - an unreadable manifest is listed without its details
        return {}
    return data if isinstance(data, dict) else {}


def _scan_manifest_info(manifest_path: Path) -> dict[str, Any]:
    """Loosely read a manifest's family_id / analysis_type for the package list."""
    data = _load_manifest_dict(manifest_path)
    return {"family_id": data.get("family_id"), "analysis_type": data.get("analysis_type")}


def _existing_manifest_dict(root: Path) -> dict[str, Any]:
    manifest_path = _find_manifest(root)
    return _load_manifest_dict(manifest_path) if manifest_path is not None else {}


def _listed_package_folder(entry: Path) -> Path | None:
    """The folder ``entry``, a name in a local import root, leads to, as selection takes it
    (``_ensure_authorized_package_path``: its symlinks resolved); None when the package list
    leaves it out.

    Left out: a folder selection refuses, a symlink that leads out of FAMILY_IMPORT_ROOTS
    (403) or one that loops (400), and one in the staging root, which selection takes for a
    package downloaded from a bucket but which is no import root. Nothing in a folder left out
    is looked up. The list read the manifest of a folder linked out of the root, showed the
    package under that manifest's family and analysis type, and named its path outside the
    roots, which selection then refused.
    """
    try:
        folder = _ensure_authorized_package_path(entry)
    except HTTPException:
        return None
    if not any(folder == root or root in folder.parents for root in _authorized_local_roots()):
        return None
    return folder


def scan_family_import_packages() -> list[dict[str, Any]]:
    """List candidate family packages directly under each local import root.

    A candidate is an immediate subdirectory containing a manifest
    (manifest.yaml/.yml/.json) or a ``*.ped`` file, at its top or in its ``ped/``
    folder (where the PGT pipeline writes it), or laid out per sample as the long-read
    pipeline writes its output (which has no PED for a couple screened for carriership).
    The folder path can then be selected in the import UI instead of typed by hand.

    A folder selection would refuse is not listed, and nothing in it is read: one that is a
    symlink leading out of the import roots, or one that loops (``_listed_package_folder``).
    A symlink that stays under the roots is followed, and the folder it leads to listed.
    """
    packages: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in _authorized_local_roots():
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir(), key=lambda path: path.name.lower()):
            # Checked before anything in it is looked up, and read through the folder
            # selection reads.
            folder = _listed_package_folder(child)
            if folder is None or not folder.is_dir():
                continue
            manifest_path = _find_manifest(folder)
            ped_paths = sorted(folder.glob("*.ped")) or sorted((folder / PED_FOLDER).glob("*.ped"))
            if manifest_path is None and not ped_paths and not long_read_sample_ids(folder):
                continue
            resolved = str(folder)
            if resolved in seen:
                continue
            seen.add(resolved)
            info: dict[str, Any] = {}
            # A manifest that is a symlink out of the package is not read: the package is
            # listed without its details, as an unreadable manifest is.
            if manifest_path is not None and manifest_link_out_of_package(folder) is None:
                info = _scan_manifest_info(manifest_path)
            family_id = info.get("family_id") or child.name
            packages.append(
                {
                    "folder_path": resolved,
                    "name": child.name,
                    "family_id": str(family_id),
                    "has_manifest": manifest_path is not None,
                    "has_ped": bool(ped_paths),
                    "analysis_type": info.get("analysis_type"),
                }
            )
    for root_uri in _authorized_s3_roots():
        # S3 listing is best-effort: a misconfigured or unreachable bucket must
        # not break the local scan.
        try:
            candidates = list_remote_package_candidates(root_uri)
        except Exception:  # noqa: BLE001 - an unreachable bucket must not break the local scan
            continue
        for candidate in candidates:
            uri = str(candidate["uri"])
            if uri in seen:
                continue
            seen.add(uri)
            name = str(candidate["name"])
            packages.append(
                {
                    "folder_path": uri,
                    "name": name,
                    "family_id": name,
                    "has_manifest": bool(candidate["has_manifest"]),
                    "has_ped": bool(candidate["has_ped"]),
                    "analysis_type": None,
                }
            )
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
    """``path`` resolved, when it lies under FAMILY_IMPORT_ROOTS or the staging root; 403
    otherwise. Callers go through ``_ensure_authorized_package_path``."""
    resolved = path.expanduser().resolve()
    staging_root = _staging_root()
    # A package staged from S3 lives under the staging root and is pre-authorized
    # (its s3:// source was checked before download).
    if resolved == staging_root or staging_root in resolved.parents:
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
        try:
            yield staged
        finally:
            shutil.rmtree(staged.root, ignore_errors=True)
    else:
        yield StagedPackage(root=str(folder_path))


@asynccontextmanager
async def staged_package_source_async(folder_path: str | Path):
    """Async variant: the download (and cleanup) run in a worker thread so the event
    loop is not blocked during a large package transfer."""
    if is_remote_uri(folder_path):
        staged = await asyncio.to_thread(_stage_s3_package, str(folder_path).strip())
        try:
            yield staged
        finally:
            await asyncio.to_thread(shutil.rmtree, staged.root, True)
    else:
        yield StagedPackage(root=str(folder_path))


def _manifest_candidates(root: Path) -> list[Path]:
    return [root / "manifest.yaml", root / "manifest.yml", root / "manifest.json"]


def _find_manifest(root: Path) -> Path | None:
    return next((candidate for candidate in _manifest_candidates(root) if candidate.is_file()), None)


def manifest_link_out_of_package(root: Path) -> Path | None:
    """The first manifest name in the package folder ``root`` (``manifest.yaml``, ``.yml``,
    ``.json``) that is a symlink leading out of the folder, or one that loops; None when none
    is.

    No manifest is read or written through it. Read, it was any YAML or JSON file the backend
    can read, and Discover and validation took the package's family and files from it;
    written, the manifest replaced or created a file wherever the link pointed. Told by the
    package guard (``package_path_within``), as for a PED or a dataset's file: a symlink that
    stays in the package is followed."""
    for candidate in _manifest_candidates(root):
        if candidate.is_symlink() and package_path_within(root, candidate.name) is None:
            return candidate
    return None


def manifest_outside_package_issue(link: Path) -> FamilyImportValidationIssue:
    """The error Discover and validation refuse a package with when ``link``, one of its
    manifest names, is a symlink out of the folder or one that loops."""
    return _issue(
        "manifest_outside_package",
        f"{link.name} is a symlink that leads out of the package folder, or one that loops: "
        "CoGA reads and writes a package's manifest only in the package folder. Put the "
        "manifest itself in the folder.",
        path=link,
    )


def _parse_manifest(path: Path) -> tuple[dict[str, Any], PackageManifest]:
    """Return both the raw parsed payload and the validated model so callers can
    inspect the original keys (e.g. schema_version presence) without re-reading
    and re-parsing the file from disk."""
    raw_text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        payload = json.loads(raw_text)
    else:
        payload = yaml.safe_load(raw_text)
    if not isinstance(payload, dict):
        raise ValueError("Manifest must contain a mapping/object at the top level")
    return payload, PackageManifest.model_validate(payload)
