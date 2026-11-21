import asyncio
from dataclasses import dataclass
from urllib.parse import urlencode
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
import pysam
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.family_folders import PACKAGES_FOLDER, family_folders, path_in_family_folder
from ..core.postgres import get_postgres_session
from ..core.url_path import url_path
from ..dependencies import get_current_user
from ..schemas import AlignmentManifestEntryOut
from ..services.family_analysis_types import ANALYSIS_KEY_MAX_LENGTH
from ..services.family_package_source import local_family_package_folder
from ..services.metadata_service import get_family_record
from ..services.access_control import CurrentUser

router = APIRouter(prefix="/cram", tags=["cram"])

DATA_DIR = Path(__file__).resolve().parents[3] / "data"

# Where a family's aligned reads may sit, most-specific first, as (the folder of the data
# directory that holds the family's folder, the folder in the family's folder): a flat
# `<data>/<family>/<sample>.cram`, or an imported family package's own pipeline layout
# (`<data>/families/<family>/bams/<sample>.cram`), so the files need not be copied or
# symlinked after import. A file is found only inside the family's own folders: see
# _alignment_candidate_paths. They serve only a sample no import recorded an alignment of: a
# recorded alignment is served from the package folder of the analysis whose import recorded
# it, and from nowhere else (_local_alignment_path), so a package on a mounted share or bucket
# is served where it lies, and one analysis's reads never stand in for another's.
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

    The IDs come from the request, and may hold ``/`` and ``..`` (the ID rule refuses both in
    a stored ID, but a request is not held to it). Each path is joined
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


# The index each alignment format is served with.
_INDEX_SUFFIXES = {"cram": ".crai", "bam": ".bai"}


@dataclass(frozen=True, slots=True)
class _RecordedAlignment:
    """A sample's alignment where the import recorded it, checked: file paths in the family's
    package folder, ``folder``, that passed ``_recorded_local_path``."""

    format: str
    location: str
    index_location: str | None = None
    folder: str | None = None


def _recorded_local_path(relative: object, extension: str, folder: str) -> str | None:
    """The file a package-relative path the import recorded names in the family's package
    folder ``folder``, when it may be served; ``None`` otherwise.

    The path comes back from ``samples.metadata`` as data: an edited row can hold ``..`` or
    an absolute path. It is served only when it has the extension of the file it stands for
    and, joined to the folder and normalised as a string, stays inside it, and its real path,
    symlinks resolved, lies inside it too (``core/family_folders.py``). A tampered row can
    therefore point at most at another file of that kind in the family's own package, never
    at another family's package in the import area. The endpoints check family membership
    before they read the row. Blocking: symlinks are resolved on disk.
    """
    if not isinstance(relative, str) or not relative.lower().endswith(extension):
        return None
    return path_in_family_folder(folder, [folder], relative)


def _recorded_local_alignment(value: object, package_folder: object) -> _RecordedAlignment | None:
    """Parse one of ``samples.metadata['alignments']`` as the import of its analysis wrote it
    (``path``, ``index_path``: relative to the package); ``None`` unless it is usable, in that
    analysis's package folder ``package_folder`` below a FAMILY_IMPORT_ROOTS entry
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


def _recorded_local_alignments(
    rows: list[tuple[Any, ...]],
) -> dict[tuple[str, str], _RecordedAlignment | None]:
    """``_recorded_local_alignment`` of each ``(sample_id, analysis, alignment, package
    folder)`` row, by sample and analysis: None for one that is not usable. Blocking."""
    return {
        (str(sample_id), str(analysis)): _recorded_local_alignment(value, package_folder)
        for sample_id, analysis, value, package_folder in rows
    }


async def _recorded_alignments(
    session: AsyncSession, sample_ids: list[str]
) -> dict[tuple[str, str], _RecordedAlignment | None]:
    """Where each analysis's import recorded each sample's alignment, by sample and analysis
    (``samples.metadata.alignments``, keyed as the family's package records are): a file in
    that analysis's package folder, such as a package on a mounted share or bucket; None for a
    record that is not usable (``_recorded_local_alignment``). A sample sequenced short-read and
    then long-read has one of each. A sample with any record is served from its records alone,
    never from the family's folders. Callers check first that the samples belong to the family
    and the user may read it.
    """
    if not sample_ids:
        return {}
    # The package folder of the analysis whose import recorded each alignment
    # (family_package_records): a recorded location is served only from there.
    result = await session.execute(
        text(
            """
            SELECT s.sample_id,
                   a.analysis,
                   a.alignment,
                   f.metadata -> 'package_imports' -> a.analysis ->> 'folder_path' AS package_folder
            FROM samples s
            JOIN families f ON f.id = s.family_id
            CROSS JOIN LATERAL jsonb_each(
                CASE WHEN jsonb_typeof(s.metadata -> 'alignments') = 'object'
                     THEN s.metadata -> 'alignments' ELSE '{}'::jsonb END
            ) AS a(analysis, alignment)
            WHERE s.sample_id = ANY(:sample_ids)
            """
        ),
        {"sample_ids": list(sample_ids)},
    )
    rows = [tuple(row) for row in result.all()]
    # Resolving the folder's symlinks reads the disk, a network share's or a mounted bucket's too.
    return await asyncio.to_thread(_recorded_local_alignments, rows)


def _recorded_local_index(recorded: _RecordedAlignment) -> Path | None:
    """The recorded alignment's index: the one the import recorded, else the one next to the
    alignment, in the family's package folder as the alignment is."""
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
    """The file to serve as ``<sample>.<ext><suffix>``, or ``None``.

    An alignment an analysis's import recorded (``recorded``) is read where it lies, in that
    analysis's package folder (on a mounted share or bucket too), and from nowhere else: the
    alignment and its index both come from it, never an index a layout probe found elsewhere
    (IGV would read the reads at the wrong offsets), and the other format is not probed for.
    When its file is gone it is missing: the layouts in the family's folders could hold
    another analysis's reads, which would be shown under this analysis's name. Without a
    record (a sample no import recorded an alignment of), the layouts in the family's own
    folders are read.
    """
    if recorded is not None:
        if ext != recorded.format or not os.path.isfile(recorded.location):
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
    """Stream the file; IGV reads it with HTTP range requests."""
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
    if _local_alignment_path(family_id, sample_id, ext, suffix, recorded) is None:
        raise HTTPException(status_code=404, detail=not_found_detail)
    return Response(status_code=200)


def _resolve_alignment_manifest_entry(
    family_id: str,
    sample_id: str,
    recorded: _RecordedAlignment | None = None,
    analysis: str | None = None,
) -> AlignmentManifestEntryOut | None:
    """The URLs are backend-relative paths the frontend prefixes with the API base, with
    each ID percent-encoded as one segment (it may hold `?`, `#`, `%` or `..`), and the
    analysis whose alignment they serve as their query (``analysis``; none for one found in
    the family's folders)."""
    query = f"?{urlencode({'analysis': analysis})}" if analysis is not None else ""
    formats: tuple[tuple[Literal["cram", "bam"], str, str], ...] = (
        ("cram", "cram", ".crai"),
        ("bam", "bam", ".bai"),
    )
    for fmt, ext, index_suffix in formats:
        if (
            _local_alignment_path(family_id, sample_id, ext, "", recorded) is None
            or _local_alignment_path(family_id, sample_id, ext, index_suffix, recorded) is None
        ):
            continue
        return AlignmentManifestEntryOut(
            sample_id=sample_id,
            analysis_type=analysis,
            format=fmt,
            url=url_path("cram", family_id, f"{sample_id}.{ext}") + query,
            index_url=url_path("cram", family_id, f"{sample_id}.{ext}{index_suffix}") + query,
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
    analysis: str | None,
) -> _RecordedAlignment | None:
    """Check the user may read the sample's reads, then look up where the import of
    ``analysis`` recorded them; None without an analysis, for an alignment found in the
    family's folders. The check runs first, so nothing about a sample the user may not see
    is read, and a failure answers before any file is looked up. An analysis that recorded
    no usable alignment of the sample answers 404: its reads are never looked for elsewhere.
    Without an analysis, a sample whose alignment an import recorded answers 404 too: the
    family's folders serve only a sample no import recorded one of, as the manifest lists it."""
    await _ensure_accessible_alignment_sample(session, family_id, sample_id, user)
    records = await _recorded_alignments(session, [sample_id])
    if analysis is None:
        if any(sample == sample_id for sample, _recorded_analysis in records):
            raise HTTPException(
                status_code=404, detail="The sample's alignments are recorded per analysis: name the analysis"
            )
        return None
    recorded = records.get((sample_id, analysis))
    if recorded is None:
        raise HTTPException(status_code=404, detail="No alignment of this analysis is recorded for the sample")
    return recorded


# The analysis whose alignment of the sample a request reads (``samples.metadata.alignments``);
# none for one found in the family's folders.
_ANALYSIS = Query(default=None, max_length=ANALYSIS_KEY_MAX_LENGTH)


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
    # One query for where each analysis's import recorded every requested sample's reads.
    recorded = await _recorded_alignments(session, ordered_samples)
    # Each sample's alignment of each analysis that recorded a usable one, in the analyses'
    # order; only a sample no analysis recorded one of, the one found in the family's folders.
    wanted: list[tuple[str, str | None]] = []
    for sample_id in ordered_samples:
        records = {analysis: entry for (sample, analysis), entry in recorded.items() if sample == sample_id}
        if records:
            wanted.extend((sample_id, analysis) for analysis in sorted(records) if records[analysis] is not None)
        else:
            wanted.append((sample_id, None))
    # Resolving each alignment does several blocking file-system look-ups, on a share or a
    # mounted bucket too; run them in worker threads concurrently instead of serially
    # stalling the event loop. asyncio.gather preserves input order, so the manifest order is
    # unchanged.
    entries = await asyncio.gather(
        *(
            asyncio.to_thread(
                _resolve_alignment_manifest_entry,
                family_id,
                sample_id,
                recorded.get((sample_id, analysis)) if analysis is not None else None,
                analysis,
            )
            for sample_id, analysis in wanted
        )
    )
    return [entry for entry in entries if entry is not None]


@router.get("/{family_id}/{sample_id}.cram")
async def get_cram(
    family_id: str,
    sample_id: str,
    analysis: str | None = _ANALYSIS,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    recorded = await _accessible_recorded_alignment(session, family_id, sample_id, user, analysis)
    return await asyncio.to_thread(
        _serve_alignment, family_id, sample_id, "cram", "", "CRAM file not found", recorded
    )


@router.head("/{family_id}/{sample_id}.cram")
async def head_cram(
    family_id: str,
    sample_id: str,
    analysis: str | None = _ANALYSIS,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    recorded = await _accessible_recorded_alignment(session, family_id, sample_id, user, analysis)
    return await asyncio.to_thread(
        _head_alignment, family_id, sample_id, "cram", "", "CRAM file not found", recorded
    )


@router.get("/{family_id}/{sample_id}.cram.crai")
async def get_crai(
    family_id: str,
    sample_id: str,
    analysis: str | None = _ANALYSIS,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    recorded = await _accessible_recorded_alignment(session, family_id, sample_id, user, analysis)
    return await asyncio.to_thread(
        _serve_alignment, family_id, sample_id, "cram", ".crai", "CRAI file not found", recorded
    )


@router.head("/{family_id}/{sample_id}.cram.crai")
async def head_crai(
    family_id: str,
    sample_id: str,
    analysis: str | None = _ANALYSIS,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    recorded = await _accessible_recorded_alignment(session, family_id, sample_id, user, analysis)
    return await asyncio.to_thread(
        _head_alignment, family_id, sample_id, "cram", ".crai", "CRAI file not found", recorded
    )


@router.get("/{family_id}/{sample_id}.bam")
async def get_bam(
    family_id: str,
    sample_id: str,
    analysis: str | None = _ANALYSIS,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    recorded = await _accessible_recorded_alignment(session, family_id, sample_id, user, analysis)
    return await asyncio.to_thread(
        _serve_alignment, family_id, sample_id, "bam", "", "BAM file not found", recorded
    )


@router.head("/{family_id}/{sample_id}.bam")
async def head_bam(
    family_id: str,
    sample_id: str,
    analysis: str | None = _ANALYSIS,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    recorded = await _accessible_recorded_alignment(session, family_id, sample_id, user, analysis)
    return await asyncio.to_thread(
        _head_alignment, family_id, sample_id, "bam", "", "BAM file not found", recorded
    )


@router.get("/{family_id}/{sample_id}.bam.bai")
async def get_bai(
    family_id: str,
    sample_id: str,
    analysis: str | None = _ANALYSIS,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    recorded = await _accessible_recorded_alignment(session, family_id, sample_id, user, analysis)
    return await asyncio.to_thread(
        _serve_alignment, family_id, sample_id, "bam", ".bai", "BAI file not found", recorded
    )


@router.head("/{family_id}/{sample_id}.bam.bai")
async def head_bai(
    family_id: str,
    sample_id: str,
    analysis: str | None = _ANALYSIS,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    recorded = await _accessible_recorded_alignment(session, family_id, sample_id, user, analysis)
    return await asyncio.to_thread(
        _head_alignment, family_id, sample_id, "bam", ".bai", "BAI file not found", recorded
    )


def _read_alignment_header(
    family_id: str, sample_id: str, recorded: _RecordedAlignment | None = None
) -> dict:
    """Open the sample's CRAM/BAM and return its header dict.

    Blocking work (pysam open + header read), so callers must run it via
    ``asyncio.to_thread`` to avoid stalling the event loop.
    """
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
    analysis: str | None = _ANALYSIS,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    """Return alignment header for quick M5/SN/LN inspection."""
    recorded = await _accessible_recorded_alignment(session, family_id, sample_id, user, analysis)
    # Offload the blocking pysam open + header parse so a slow read from a share or a
    # mounted bucket cannot stall the event loop (matches the manifest handler's pattern).
    return await asyncio.to_thread(_read_alignment_header, family_id, sample_id, recorded)
