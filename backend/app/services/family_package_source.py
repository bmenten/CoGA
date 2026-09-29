from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
import json
import logging
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

from .family_package_common import PackageManifest  # noqa: F401


logger = logging.getLogger(__name__)


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
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _scan_manifest_info(manifest_path: Path) -> dict[str, Any]:
    """Loosely read a manifest's family_id / analysis_type for the package list."""
    data = _load_manifest_dict(manifest_path)
    return {"family_id": data.get("family_id"), "analysis_type": data.get("analysis_type")}


def _existing_manifest_dict(root: Path) -> dict[str, Any]:
    manifest_path = _find_manifest(root)
    return _load_manifest_dict(manifest_path) if manifest_path is not None else {}


def scan_family_import_packages() -> list[dict[str, Any]]:
    """List candidate family packages directly under each local import root.

    A candidate is an immediate subdirectory containing a manifest
    (manifest.yaml/.yml/.json) or a ``*.ped`` file. The folder path can then be
    selected in the import UI instead of typed by hand.
    """
    packages: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in _authorized_local_roots():
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir(), key=lambda path: path.name.lower()):
            if not child.is_dir():
                continue
            manifest_path = _find_manifest(child)
            ped_paths = sorted(child.glob("*.ped"))
            if manifest_path is None and not ped_paths:
                continue
            resolved = str(child.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            info = _scan_manifest_info(manifest_path) if manifest_path is not None else {}
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
        except Exception:
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


def _ensure_authorized_package_path(path: Path) -> Path:
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
