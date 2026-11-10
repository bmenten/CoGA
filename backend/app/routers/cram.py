import asyncio
from dataclasses import dataclass
import logging
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse, RedirectResponse
import pysam
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.family_folders import PACKAGES_FOLDER, family_folders, path_in_family_folder
from ..core.object_storage import (
    configured_object_key,
    object_exists,
    object_key,
    presigned_get_url,
    storage_is_remote,
)
from ..core.postgres import get_postgres_session
from ..core.url_path import url_path
from ..dependencies import get_current_user
from ..schemas import AlignmentManifestEntryOut
from ..services.family_package_source import local_family_package_folder, within_family_package
from ..services.metadata_service import get_family_record
from ..services.access_control import CurrentUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cram", tags=["cram"])

DATA_DIR = Path(__file__).resolve().parents[3] / "data"

# Where a family's aligned reads may sit, most-specific first, as (the folder of the data
# directory that holds the family's folder, the folder in the family's folder). The
# original convention is a flat `<data>/<family>/<sample>.cram`; an imported family
# package keeps its pipeline layout instead (`<data>/families/<family>/bams/<sample>.cram`),
# so both are probed rather than requiring the files to be copied or symlinked after
# import. A file is found only inside the family's own folders: see
# _alignment_candidate_paths. These are the fallback: where the alignments import recorded
# the reads, in the package folder the family's import read, comes first
# (_local_alignment_path), so a package on a share mounted as a second import root is
# served where it lies.
_ALIGNMENT_LAYOUTS: tuple[tuple[str, str], ...] = (
    ("", ""),  # <data>/<family>/<sample>.cram
    (PACKAGES_FOLDER, "bams"),  # <data>/families/<family>/bams/<sample>.cram
    (PACKAGES_FOLDER, "alignments"),  # <data>/families/<family>/alignments/<sample>.cram
    (PACKAGES_FOLDER, ""),  # <data>/families/<family>/<sample>.cram
    ("", "bams"),  # <data>/<family>/bams/<sample>.cram
)


def _alignment_candidate_paths(
    family_id: str, sample_id: str, ext: str, suffix: str = ""
) -> list[Path]:
    """Where the layouts put ``<sample>.<ext><suffix>`` in the family's own folders.

    The IDs come from the request, and may hold ``/`` and ``..`` (the ID rule refuses both
    for a new ID since SEC-15, not in a request or an ID stored before). Each path is joined
    and normalised as a string, and one that leaves the family's folder (a sample ID such as
    ``../F2/S2``) is dropped before anything is looked up. One that
    stays is kept only when its real path lies inside one of the family's folders too, so
    a symlink cannot lead into another family's folder or out of the data directory
    (``core/family_folders.py``). The endpoints check the IDs against the family's members
    first; this holds even for a caller that does not.
    """
    file_name = f"{sample_id}.{ext}{suffix}"
    folders = family_folders(DATA_DIR, family_id)
    own_folders = list(folders.values())
    candidates: list[Path] = []
    for parent, sub_folder in _ALIGNMENT_LAYOUTS:
        folder = folders.get(parent)
        if folder is None:
            continue
        path = path_in_family_folder(folder, own_folders, sub_folder, file_name)
        if path is not None:
            candidates.append(Path(path))
    return candidates


def _alignment_path(
    family_id: str, sample_id: str, ext: str, suffix: str = ""
) -> Path | None:
    """The alignment file in the family's own folders, or ``None`` when none of the
    layouts holds it there. Nothing else is looked at."""
    return next(
        (
            candidate
            for candidate in _alignment_candidate_paths(family_id, sample_id, ext, suffix)
            if candidate.exists()
        ),
        None,
    )


# ---------------------------------------------------------------------------
# Remote mode (STORAGE_BACKEND=gcs/s3)
# ---------------------------------------------------------------------------

# The index each alignment format is served with.
_INDEX_SUFFIXES = {"cram": ".crai", "bam": ".bai"}


@dataclass(frozen=True, slots=True)
class _RecordedAlignment:
    """A sample's alignment where the import recorded it, checked. In remote mode the
    locations are keys in the configured bucket that passed ``_recorded_key``; in local
    mode they are file paths in the family's package folder, ``folder``, that passed
    ``_recorded_local_path``."""

    format: str
    location: str
    index_location: str | None = None
    folder: str | None = None


def _recorded_key(uri: object, suffix: str, package_folder: object) -> str | None:
    """The key a recorded location names, when it may be served; ``None`` otherwise.

    The location comes back from ``samples.metadata``, which the import writes but which
    is read here as data. It is served only when it names an object in the configured
    bucket (``configured_object_key``: that bucket and scheme, nothing but scheme,
    bucket and key, no empty/``.``/``..`` segment), in the family's own package folder
    below the remote FAMILY_IMPORT_ROOTS (``within_family_package``: the bucket folder the
    family's import read, ``package_folder``), and with the extension of the file it
    stands for. A tampered row can therefore point at most at another object of the
    family's own package, never at another family's package in the import area, other
    objects of the bucket or another bucket (SEC-27). The endpoints check family
    membership before they read the row.
    """
    key = configured_object_key(uri)
    if key is None or not key.lower().endswith(suffix) or not within_family_package(str(uri), package_folder):
        return None
    return key


def _recorded_alignment(value: object, package_folder: object) -> _RecordedAlignment | None:
    """Parse ``samples.metadata['alignment']``; ``None`` unless it is usable, in the
    family's package folder ``package_folder``."""
    if not isinstance(value, dict):
        return None
    fmt = value.get("format")
    if not isinstance(fmt, str) or fmt not in _INDEX_SUFFIXES:
        return None
    key = _recorded_key(value.get("uri"), f".{fmt}", package_folder)
    if key is None:
        return None
    return _RecordedAlignment(
        format=fmt,
        location=key,
        index_location=_recorded_key(value.get("index_uri"), _INDEX_SUFFIXES[fmt], package_folder),
    )


def _recorded_local_path(relative: object, extension: str, folder: str) -> str | None:
    """The file a package-relative path the import recorded names in the family's package
    folder ``folder`` (local mode), when it may be served; ``None`` otherwise.

    The path comes back from ``samples.metadata`` as data: an edited row can hold ``..`` or
    an absolute path. It is served only when it has the extension of the file it stands for
    and, joined to the folder and normalised as a string, stays inside it, and its real path,
    symlinks resolved, lies inside it too (``core/family_folders.py``). A tampered row can
    therefore point at most at another file of that kind in the family's own package, as in
    remote mode (SEC-27). Blocking: symlinks are resolved on disk.
    """
    if not isinstance(relative, str) or not relative.lower().endswith(extension):
        return None
    return path_in_family_folder(folder, [folder], relative)


def _recorded_local_alignment(value: object, package_folder: object) -> _RecordedAlignment | None:
    """Parse ``samples.metadata['alignment']`` as the import of a local package wrote it
    (``path``, ``index_path``: relative to the package); ``None`` unless it is usable, in the
    family's package folder ``package_folder`` below a local FAMILY_IMPORT_ROOTS entry
    (``local_family_package_folder``). Blocking."""
    if not isinstance(value, dict):
        return None
    fmt = value.get("format")
    if not isinstance(fmt, str) or fmt not in _INDEX_SUFFIXES:
        return None
    folder = local_family_package_folder(package_folder)
    if folder is None:
        return None
    path = _recorded_local_path(value.get("path"), f".{fmt}", folder)
    if path is None:
        return None
    return _RecordedAlignment(
        format=fmt,
        location=path,
        index_location=_recorded_local_path(value.get("index_path"), _INDEX_SUFFIXES[fmt], folder),
        folder=folder,
    )


def _recorded_local_alignments(rows: list[tuple[Any, ...]]) -> dict[str, _RecordedAlignment]:
    """``_recorded_local_alignment`` of each ``(sample_id, alignment, package folder)`` row
    that has a usable one. Blocking."""
    recorded: dict[str, _RecordedAlignment] = {}
    for sample_id, value, package_folder in rows:
        alignment = _recorded_local_alignment(value, package_folder)
        if alignment is not None:
            recorded[str(sample_id)] = alignment
    return recorded


async def _recorded_alignments(
    session: AsyncSession, sample_ids: list[str]
) -> dict[str, _RecordedAlignment]:
    """Where the import recorded each sample's alignment: an object in the store (remote
    mode), or a file in the local package folder the family's import read (local mode), such
    as a package on a share mounted as a second import root. Callers check first that the
    samples belong to the family and the user may read it.
    """
    if not sample_ids:
        return {}
    # Each sample's family's package folder, the folder its import read: a recorded location
    # is served only from there (SEC-27).
    result = await session.execute(
        text(
            """
            SELECT s.sample_id,
                   s.metadata -> 'alignment' AS alignment,
                   f.metadata -> 'package_import' ->> 'folder_path' AS package_folder
            FROM samples s
            JOIN families f ON f.id = s.family_id
            WHERE s.sample_id = ANY(:sample_ids)
              AND s.metadata ? 'alignment'
            """
        ),
        {"sample_ids": list(sample_ids)},
    )
    rows = [tuple(row) for row in result.all()]
    if not storage_is_remote():
        # Resolving the folder's symlinks reads the disk, a network share's too.
        return await asyncio.to_thread(_recorded_local_alignments, rows)
    recorded: dict[str, _RecordedAlignment] = {}
    for sample_id, value, package_folder in rows:
        alignment = _recorded_alignment(value, package_folder)
        if alignment is not None:
            recorded[str(sample_id)] = alignment
        elif isinstance(value, dict) and value.get("uri"):
            # The line names no sample: logs carry no values (SEC-37).
            logger.warning(
                "Ignoring a sample's recorded alignment location: it is not an object in the "
                "configured bucket in its family's package folder"
            )
    return recorded


def _alignment_candidate_keys(
    family_id: str, sample_id: str, ext: str, suffix: str = ""
) -> list[str]:
    file_name = f"{sample_id}.{ext}{suffix}"
    return [
        object_key(family_id, file_name),
        object_key(family_id, "bams", file_name),
        object_key(family_id, "alignments", file_name),
    ]


def _probed_alignment_key(family_id: str, sample_id: str, ext: str, suffix: str = "") -> str | None:
    """The first layout probe that holds the file, if any."""
    return next(
        (
            candidate
            for candidate in _alignment_candidate_keys(family_id, sample_id, ext, suffix)
            if object_exists(candidate)
        ),
        None,
    )


def _recorded_index_key(recorded: _RecordedAlignment) -> str | None:
    """The recorded alignment's index: the one the import recorded, else the one stored
    next to the alignment."""
    beside = f"{recorded.location}{_INDEX_SUFFIXES[recorded.format]}"
    candidates = dict.fromkeys(key for key in (recorded.index_location, beside) if key)
    return next((key for key in candidates if object_exists(key)), None)


def _remote_alignment_key(
    family_id: str,
    sample_id: str,
    ext: str,
    suffix: str = "",
    recorded: _RecordedAlignment | None = None,
) -> str | None:
    """The object to serve as ``<sample>.<ext><suffix>``, or ``None``.

    The recorded location comes first, and when its alignment is in the store it alone
    answers for the sample: the alignment and its index both come from it, so a
    recorded CRAM is never paired with an index a layout probe found elsewhere (IGV
    would read the reads at the wrong offsets), and the other format is not probed for.
    The layout probes are the fallback when nothing usable was recorded or the recorded
    object is gone.
    """
    if recorded is not None and object_exists(recorded.location):
        if ext != recorded.format:
            return None
        return _recorded_index_key(recorded) if suffix else recorded.location
    return _probed_alignment_key(family_id, sample_id, ext, suffix)


def _recorded_local_index(recorded: _RecordedAlignment) -> Path | None:
    """The recorded alignment's index (local mode): the one the import recorded, else the one
    next to the alignment, in the family's package folder as the alignment is."""
    beside = None
    if recorded.folder is not None:
        beside = path_in_family_folder(
            recorded.folder, [recorded.folder], f"{recorded.location}{_INDEX_SUFFIXES[recorded.format]}"
        )
    candidates = dict.fromkeys(path for path in (recorded.index_location, beside) if path)
    return next((Path(path) for path in candidates if os.path.isfile(path)), None)


def _local_alignment_path(
    family_id: str,
    sample_id: str,
    ext: str,
    suffix: str = "",
    recorded: _RecordedAlignment | None = None,
) -> Path | None:
    """The file to serve as ``<sample>.<ext><suffix>`` (local mode), or ``None``.

    As in remote mode, the location the import recorded comes first (a package read where it
    lies, on a share mounted as a second import root, keeps its reads there), and when its
    alignment is on disk it alone answers for the sample: alignment and index both come from
    it, never an index a layout probe found elsewhere, and the other format is not probed
    for. The layouts in the family's own folders are the fallback when nothing usable was
    recorded or the recorded file is gone.
    """
    if recorded is not None and os.path.isfile(recorded.location):
        if ext != recorded.format:
            return None
        return _recorded_local_index(recorded) if suffix else Path(recorded.location)
    return _alignment_path(family_id, sample_id, ext, suffix)


def _serve_alignment(
    family_id: str,
    sample_id: str,
    ext: str,
    suffix: str,
    not_found_detail: str,
    recorded: _RecordedAlignment | None = None,
) -> Response:
    """Stream a local file, or redirect to a presigned/signed object URL in remote mode."""
    file_name = f"{sample_id}.{ext}{suffix}"
    if storage_is_remote():
        key = _remote_alignment_key(family_id, sample_id, ext, suffix, recorded)
        if key is None:
            raise HTTPException(status_code=404, detail=not_found_detail)
        # IGV follows the 302 and reads bytes (with HTTP range) straight from the store.
        return RedirectResponse(presigned_get_url(key, filename=file_name), status_code=302)
    path = _local_alignment_path(family_id, sample_id, ext, suffix, recorded)
    if path is None:
        raise HTTPException(status_code=404, detail=not_found_detail)
    return FileResponse(path)


def _head_alignment(
    family_id: str,
    sample_id: str,
    ext: str,
    suffix: str,
    not_found_detail: str,
    recorded: _RecordedAlignment | None = None,
) -> Response:
    if storage_is_remote():
        found = _remote_alignment_key(family_id, sample_id, ext, suffix, recorded) is not None
    else:
        found = _local_alignment_path(family_id, sample_id, ext, suffix, recorded) is not None
    if not found:
        raise HTTPException(status_code=404, detail=not_found_detail)
    return Response(status_code=200)


def _resolve_alignment_manifest_entry(
    family_id: str,
    sample_id: str,
    recorded: _RecordedAlignment | None = None,
) -> AlignmentManifestEntryOut | None:
    """In remote mode the URLs are short-lived presigned/signed URLs (absolute);
    otherwise they are backend-relative paths the frontend prefixes with the API base,
    with each ID percent-encoded as one segment (it may hold `/`, `?`, `#`, `%` or `..`)."""
    formats: tuple[tuple[Literal["cram", "bam"], str, str], ...] = (
        ("cram", "cram", ".crai"),
        ("bam", "bam", ".bai"),
    )
    for fmt, ext, index_suffix in formats:
        if storage_is_remote():
            data_key = _remote_alignment_key(family_id, sample_id, ext, "", recorded)
            index_key = (
                _remote_alignment_key(family_id, sample_id, ext, index_suffix, recorded)
                if data_key is not None
                else None
            )
            if data_key is None or index_key is None:
                continue
            return AlignmentManifestEntryOut(
                sample_id=sample_id,
                format=fmt,
                url=presigned_get_url(data_key, filename=f"{sample_id}.{ext}"),
                index_url=presigned_get_url(index_key, filename=f"{sample_id}.{ext}{index_suffix}"),
            )
        if (
            _local_alignment_path(family_id, sample_id, ext, "", recorded) is None
            or _local_alignment_path(family_id, sample_id, ext, index_suffix, recorded) is None
        ):
            continue
        return AlignmentManifestEntryOut(
            sample_id=sample_id,
            format=fmt,
            url=url_path("cram", family_id, f"{sample_id}.{ext}"),
            index_url=url_path("cram", family_id, f"{sample_id}.{ext}{index_suffix}"),
        )
    return None


async def _get_accessible_family_sample_ids(
    session: AsyncSession,
    family_id: str,
    user: CurrentUser,
) -> set[str]:
    family = await get_family_record(session, family_id, user)
    return {member.sample_id for member in family.members}


async def _ensure_accessible_alignment_sample(
    session: AsyncSession,
    family_id: str,
    sample_id: str,
    user: CurrentUser,
) -> None:
    sample_ids = await _get_accessible_family_sample_ids(session, family_id, user)
    if sample_id not in sample_ids:
        raise HTTPException(status_code=404, detail="Sample not found in family")


async def _accessible_recorded_alignment(
    session: AsyncSession,
    family_id: str,
    sample_id: str,
    user: CurrentUser,
) -> _RecordedAlignment | None:
    """Check the user may read the sample's reads, then look up where the import
    recorded them (remote mode). The check runs first, so nothing about a sample the
    user may not see is read, and a failure answers before any URL is issued."""
    await _ensure_accessible_alignment_sample(session, family_id, sample_id, user)
    return (await _recorded_alignments(session, [sample_id])).get(sample_id)


@router.get("/{family_id}/manifest", response_model=list[AlignmentManifestEntryOut])
async def get_alignment_manifest(
    family_id: str,
    sample_ids: list[str] = Query(default_factory=list, alias="sample"),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    family_sample_ids = await _get_accessible_family_sample_ids(session, family_id, user)
    seen: set[str] = set()
    ordered_samples: list[str] = []
    for sample_id in sample_ids:
        if sample_id in seen or sample_id not in family_sample_ids:
            continue
        seen.add(sample_id)
        ordered_samples.append(sample_id)
    # One query for where the import recorded every requested sample's reads.
    recorded = await _recorded_alignments(session, ordered_samples)
    # Resolving each sample does several blocking S3 HEAD + presign calls; run them
    # in worker threads concurrently instead of serially stalling the event loop.
    # asyncio.gather preserves input order, so the manifest order is unchanged.
    entries = await asyncio.gather(
        *(
            asyncio.to_thread(
                _resolve_alignment_manifest_entry, family_id, sample_id, recorded.get(sample_id)
            )
            for sample_id in ordered_samples
        )
    )
    return [entry for entry in entries if entry is not None]


@router.get("/{family_id}/{sample_id}.cram")
async def get_cram(
    family_id: str,
    sample_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    recorded = await _accessible_recorded_alignment(session, family_id, sample_id, user)
    return await asyncio.to_thread(
        _serve_alignment, family_id, sample_id, "cram", "", "CRAM file not found", recorded
    )


@router.head("/{family_id}/{sample_id}.cram")
async def head_cram(
    family_id: str,
    sample_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    recorded = await _accessible_recorded_alignment(session, family_id, sample_id, user)
    return await asyncio.to_thread(
        _head_alignment, family_id, sample_id, "cram", "", "CRAM file not found", recorded
    )


@router.get("/{family_id}/{sample_id}.cram.crai")
async def get_crai(
    family_id: str,
    sample_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    recorded = await _accessible_recorded_alignment(session, family_id, sample_id, user)
    return await asyncio.to_thread(
        _serve_alignment, family_id, sample_id, "cram", ".crai", "CRAI file not found", recorded
    )


@router.head("/{family_id}/{sample_id}.cram.crai")
async def head_crai(
    family_id: str,
    sample_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    recorded = await _accessible_recorded_alignment(session, family_id, sample_id, user)
    return await asyncio.to_thread(
        _head_alignment, family_id, sample_id, "cram", ".crai", "CRAI file not found", recorded
    )


@router.get("/{family_id}/{sample_id}.bam")
async def get_bam(
    family_id: str,
    sample_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    recorded = await _accessible_recorded_alignment(session, family_id, sample_id, user)
    return await asyncio.to_thread(
        _serve_alignment, family_id, sample_id, "bam", "", "BAM file not found", recorded
    )


@router.head("/{family_id}/{sample_id}.bam")
async def head_bam(
    family_id: str,
    sample_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    recorded = await _accessible_recorded_alignment(session, family_id, sample_id, user)
    return await asyncio.to_thread(
        _head_alignment, family_id, sample_id, "bam", "", "BAM file not found", recorded
    )


@router.get("/{family_id}/{sample_id}.bam.bai")
async def get_bai(
    family_id: str,
    sample_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    recorded = await _accessible_recorded_alignment(session, family_id, sample_id, user)
    return await asyncio.to_thread(
        _serve_alignment, family_id, sample_id, "bam", ".bai", "BAI file not found", recorded
    )


@router.head("/{family_id}/{sample_id}.bam.bai")
async def head_bai(
    family_id: str,
    sample_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    recorded = await _accessible_recorded_alignment(session, family_id, sample_id, user)
    return await asyncio.to_thread(
        _head_alignment, family_id, sample_id, "bam", ".bai", "BAI file not found", recorded
    )


def _read_alignment_header(
    family_id: str, sample_id: str, recorded: _RecordedAlignment | None = None
) -> dict:
    """Open the sample's CRAM/BAM and return its header dict.

    Blocking work (pysam open + S3/htslib byte-range I/O), so callers must run
    it via ``asyncio.to_thread`` to avoid stalling the event loop.
    """
    if storage_is_remote():
        for ext, mode in (("cram", "rc"), ("bam", "rb")):
            key = _remote_alignment_key(family_id, sample_id, ext, "", recorded)
            if key is not None:
                # htslib reads the presigned https URL directly (with range requests).
                with pysam.AlignmentFile(presigned_get_url(key, filename=f"{sample_id}.{ext}"), mode) as af:
                    return af.header.to_dict()
        raise HTTPException(status_code=404, detail="No CRAM/BAM found for sample")
    cram_path = _local_alignment_path(family_id, sample_id, "cram", "", recorded)
    if cram_path is not None:
        with pysam.AlignmentFile(str(cram_path), "rc") as af:
            return af.header.to_dict()
    bam_path = _local_alignment_path(family_id, sample_id, "bam", "", recorded)
    if bam_path is not None:
        with pysam.AlignmentFile(str(bam_path), "rb") as af:
            return af.header.to_dict()
    raise HTTPException(status_code=404, detail="No CRAM/BAM found for sample")


@router.get("/{family_id}/{sample_id}.cram.header")
async def get_cram_header(
    family_id: str,
    sample_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    """Return alignment header for quick M5/SN/LN inspection."""
    recorded = await _accessible_recorded_alignment(session, family_id, sample_id, user)
    # Offload the blocking pysam open + header parse so a slow S3/htslib read
    # cannot stall the event loop (matches the manifest handler's pattern).
    return await asyncio.to_thread(_read_alignment_header, family_id, sample_id, recorded)
