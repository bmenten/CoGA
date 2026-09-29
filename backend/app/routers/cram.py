import asyncio
from dataclasses import dataclass
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse, RedirectResponse
import pysam
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.object_storage import (
    configured_object_key,
    object_exists,
    object_key,
    presigned_get_url,
    storage_is_remote,
)
from ..core.postgres import get_postgres_session
from ..dependencies import get_current_user
from ..schemas import AlignmentManifestEntryOut
from ..services.family_package_source import within_remote_import_roots
from ..services.metadata_service import get_family_record
from ..services.access_control import CurrentUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cram", tags=["cram"])

DATA_DIR = Path(__file__).resolve().parents[3] / "data"


# Where a family's aligned reads may sit, most-specific first. The original
# convention is a flat `<data>/<family>/<sample>.cram`; an imported family package
# keeps its pipeline layout instead (`<data>/families/<family>/bams/<sample>.cram`),
# so both are probed rather than requiring the files to be copied or symlinked after
# import. Every candidate stays under DATA_DIR — see _within_data_dir.
_ALIGNMENT_LAYOUTS: tuple[tuple[str, ...], ...] = (
    ("{family_id}",),
    ("families", "{family_id}", "bams"),
    ("families", "{family_id}", "alignments"),
    ("families", "{family_id}"),
    ("{family_id}", "bams"),
)


def _within_data_dir(path: Path) -> bool:
    """Reject a candidate that resolves outside the data directory.

    ``family_id``/``sample_id`` reach here from the URL. The served endpoints check
    them against the family's members first, but the containment check keeps a crafted
    id from escaping even if a future caller skips that step.
    """
    try:
        path.resolve().relative_to(DATA_DIR.resolve())
    except (ValueError, OSError):
        return False
    return True


def _alignment_candidate_paths(
    family_id: str, sample_id: str, ext: str, suffix: str = ""
) -> list[Path]:
    file_name = f"{sample_id}.{ext}{suffix}"
    candidates: list[Path] = []
    for layout in _ALIGNMENT_LAYOUTS:
        path = DATA_DIR.joinpath(*[part.format(family_id=family_id) for part in layout], file_name)
        if _within_data_dir(path):
            candidates.append(path)
    return candidates


def _alignment_path(
    family_id: str, sample_id: str, ext: str, suffix: str = ""
) -> Path:
    """The alignment file, or the conventional location when none of the layouts hold
    it (so callers report a consistent path in their 404)."""
    candidates = _alignment_candidate_paths(family_id, sample_id, ext, suffix)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if candidates else DATA_DIR / family_id / f"{sample_id}.{ext}{suffix}"


def _alignment_exists(family_id: str, sample_id: str, ext: str, suffix: str = "") -> bool:
    """Whether one of the data-directory layouts holds the file (local mode)."""
    return any(
        candidate.exists()
        for candidate in _alignment_candidate_paths(family_id, sample_id, ext, suffix)
    )


# ---------------------------------------------------------------------------
# Remote mode (STORAGE_BACKEND=gcs/s3)
# ---------------------------------------------------------------------------

# The index each alignment format is served with.
_INDEX_SUFFIXES = {"cram": ".crai", "bam": ".bai"}


@dataclass(frozen=True, slots=True)
class _RecordedAlignment:
    """A sample's alignment as the import recorded it in the object store: keys in the
    configured bucket that passed ``_recorded_key``."""

    format: str
    key: str
    index_key: str | None = None


def _recorded_key(uri: object, suffix: str) -> str | None:
    """The key a recorded location names, when it may be served; ``None`` otherwise.

    The location comes back from ``samples.metadata``, which the import writes but which
    is read here as data. It is served only when it names an object in the configured
    bucket (``configured_object_key``: that bucket and scheme, nothing but scheme,
    bucket and key, no empty/``.``/``..`` segment), below one of the remote
    FAMILY_IMPORT_ROOTS -- the folders every package is imported from -- and with the
    extension of the file it stands for. A tampered row can therefore point at most at
    another object of the import area, never at other objects of the bucket or at
    another bucket. The endpoints check family membership before they read the row.
    """
    key = configured_object_key(uri)
    if key is None or not key.lower().endswith(suffix) or not within_remote_import_roots(str(uri)):
        return None
    return key


def _recorded_alignment(value: object) -> _RecordedAlignment | None:
    """Parse ``samples.metadata['alignment']``; ``None`` unless it is usable."""
    if not isinstance(value, dict):
        return None
    fmt = value.get("format")
    if not isinstance(fmt, str) or fmt not in _INDEX_SUFFIXES:
        return None
    key = _recorded_key(value.get("uri"), f".{fmt}")
    if key is None:
        return None
    return _RecordedAlignment(
        format=fmt,
        key=key,
        index_key=_recorded_key(value.get("index_uri"), _INDEX_SUFFIXES[fmt]),
    )


async def _recorded_alignments(
    session: AsyncSession, sample_ids: list[str]
) -> dict[str, _RecordedAlignment]:
    """Where the import recorded each sample's alignment in the object store.

    Remote mode only: a local deployment serves the data-directory layouts. Callers
    check first that the samples belong to the family and the user may read it.
    """
    if not sample_ids or not storage_is_remote():
        return {}
    result = await session.execute(
        text(
            """
            SELECT sample_id, metadata -> 'alignment' AS alignment
            FROM samples
            WHERE sample_id = ANY(:sample_ids)
              AND metadata ? 'alignment'
            """
        ),
        {"sample_ids": list(sample_ids)},
    )
    recorded: dict[str, _RecordedAlignment] = {}
    for sample_id, value in result.all():
        alignment = _recorded_alignment(value)
        if alignment is not None:
            recorded[str(sample_id)] = alignment
        elif isinstance(value, dict) and value.get("uri"):
            logger.warning(
                "Ignoring the recorded alignment location of sample %s: it is not an "
                "object in the configured bucket below FAMILY_IMPORT_ROOTS",
                sample_id,
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
    beside = f"{recorded.key}{_INDEX_SUFFIXES[recorded.format]}"
    candidates = dict.fromkeys(key for key in (recorded.index_key, beside) if key)
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
    if recorded is not None and object_exists(recorded.key):
        if ext != recorded.format:
            return None
        return _recorded_index_key(recorded) if suffix else recorded.key
    return _probed_alignment_key(family_id, sample_id, ext, suffix)


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
    path = _alignment_path(family_id, sample_id, ext, suffix)
    if not path.exists():
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
        found = _alignment_exists(family_id, sample_id, ext, suffix)
    if not found:
        raise HTTPException(status_code=404, detail=not_found_detail)
    return Response(status_code=200)


def _resolve_alignment_manifest_entry(
    family_id: str,
    sample_id: str,
    recorded: _RecordedAlignment | None = None,
) -> AlignmentManifestEntryOut | None:
    """In remote mode the URLs are short-lived presigned/signed URLs (absolute);
    otherwise they are backend-relative paths the frontend prefixes with the API base."""
    for fmt, ext, index_suffix in (("cram", "cram", ".crai"), ("bam", "bam", ".bai")):
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
        if not (_alignment_exists(family_id, sample_id, ext) and _alignment_exists(family_id, sample_id, ext, index_suffix)):
            continue
        return AlignmentManifestEntryOut(
            sample_id=sample_id,
            format=fmt,
            url=f"/cram/{family_id}/{sample_id}.{ext}",
            index_url=f"/cram/{family_id}/{sample_id}.{ext}{index_suffix}",
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
    cram_path = _alignment_path(family_id, sample_id, "cram")
    bam_path = _alignment_path(family_id, sample_id, "bam")
    if cram_path.exists():
        with pysam.AlignmentFile(str(cram_path), "rc") as af:
            return af.header.to_dict()
    if bam_path.exists():
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
