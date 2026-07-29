import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse, RedirectResponse
import pysam
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.object_storage import object_exists, object_key, presigned_get_url, storage_is_remote
from ..core.postgres import get_postgres_session
from ..dependencies import get_current_user
from ..schemas import AlignmentManifestEntryOut
from ..services.metadata_service import CurrentUser, get_family_record

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


def _alignment_candidate_keys(
    family_id: str, sample_id: str, ext: str, suffix: str = ""
) -> list[str]:
    file_name = f"{sample_id}.{ext}{suffix}"
    return [
        object_key(family_id, file_name),
        object_key(family_id, "bams", file_name),
        object_key(family_id, "alignments", file_name),
    ]


def _alignment_key(family_id: str, sample_id: str, ext: str, suffix: str = "") -> str:
    candidates = _alignment_candidate_keys(family_id, sample_id, ext, suffix)
    for candidate in candidates:
        if object_exists(candidate):
            return candidate
    return candidates[0]


def _alignment_exists(family_id: str, sample_id: str, ext: str, suffix: str = "") -> bool:
    if storage_is_remote():
        return any(
            object_exists(candidate)
            for candidate in _alignment_candidate_keys(family_id, sample_id, ext, suffix)
        )
    return any(
        candidate.exists()
        for candidate in _alignment_candidate_paths(family_id, sample_id, ext, suffix)
    )


def _serve_alignment(
    family_id: str, sample_id: str, ext: str, suffix: str, not_found_detail: str
) -> Response:
    """Stream a local file, or redirect to a presigned/signed object URL in remote mode."""
    file_name = f"{sample_id}.{ext}{suffix}"
    if storage_is_remote():
        key = _alignment_key(family_id, sample_id, ext, suffix)
        if not object_exists(key):
            raise HTTPException(status_code=404, detail=not_found_detail)
        # IGV follows the 302 and reads bytes (with HTTP range) straight from the store.
        return RedirectResponse(presigned_get_url(key, filename=file_name), status_code=302)
    path = _alignment_path(family_id, sample_id, ext, suffix)
    if not path.exists():
        raise HTTPException(status_code=404, detail=not_found_detail)
    return FileResponse(path)


def _head_alignment(family_id: str, sample_id: str, ext: str, suffix: str, not_found_detail: str) -> Response:
    if not _alignment_exists(family_id, sample_id, ext, suffix):
        raise HTTPException(status_code=404, detail=not_found_detail)
    return Response(status_code=200)


def _resolve_alignment_manifest_entry(
    family_id: str,
    sample_id: str,
) -> AlignmentManifestEntryOut | None:
    """In s3 mode the URLs are short-lived presigned S3 URLs (absolute); otherwise
    they are backend-relative paths the frontend prefixes with the API base."""
    for fmt, ext, index_suffix in (("cram", "cram", ".crai"), ("bam", "bam", ".bai")):
        if not (_alignment_exists(family_id, sample_id, ext) and _alignment_exists(family_id, sample_id, ext, index_suffix)):
            continue
        if storage_is_remote():
            data_name = f"{sample_id}.{ext}"
            index_name = f"{sample_id}.{ext}{index_suffix}"
            return AlignmentManifestEntryOut(
                sample_id=sample_id,
                format=fmt,
                url=presigned_get_url(_alignment_key(family_id, sample_id, ext), filename=data_name),
                index_url=presigned_get_url(
                    _alignment_key(family_id, sample_id, ext, index_suffix), filename=index_name
                ),
            )
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
    # Resolving each sample does several blocking S3 HEAD + presign calls; run them
    # in worker threads concurrently instead of serially stalling the event loop.
    # asyncio.gather preserves input order, so the manifest order is unchanged.
    entries = await asyncio.gather(
        *(
            asyncio.to_thread(_resolve_alignment_manifest_entry, family_id, sample_id)
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
    await _ensure_accessible_alignment_sample(session, family_id, sample_id, user)
    return await asyncio.to_thread(
        _serve_alignment, family_id, sample_id, "cram", "", "CRAM file not found"
    )


@router.head("/{family_id}/{sample_id}.cram")
async def head_cram(
    family_id: str,
    sample_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    await _ensure_accessible_alignment_sample(session, family_id, sample_id, user)
    return await asyncio.to_thread(
        _head_alignment, family_id, sample_id, "cram", "", "CRAM file not found"
    )


@router.get("/{family_id}/{sample_id}.cram.crai")
async def get_crai(
    family_id: str,
    sample_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    await _ensure_accessible_alignment_sample(session, family_id, sample_id, user)
    return await asyncio.to_thread(
        _serve_alignment, family_id, sample_id, "cram", ".crai", "CRAI file not found"
    )


@router.head("/{family_id}/{sample_id}.cram.crai")
async def head_crai(
    family_id: str,
    sample_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    await _ensure_accessible_alignment_sample(session, family_id, sample_id, user)
    return await asyncio.to_thread(
        _head_alignment, family_id, sample_id, "cram", ".crai", "CRAI file not found"
    )


@router.get("/{family_id}/{sample_id}.bam")
async def get_bam(
    family_id: str,
    sample_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    await _ensure_accessible_alignment_sample(session, family_id, sample_id, user)
    return await asyncio.to_thread(
        _serve_alignment, family_id, sample_id, "bam", "", "BAM file not found"
    )


@router.head("/{family_id}/{sample_id}.bam")
async def head_bam(
    family_id: str,
    sample_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    await _ensure_accessible_alignment_sample(session, family_id, sample_id, user)
    return await asyncio.to_thread(
        _head_alignment, family_id, sample_id, "bam", "", "BAM file not found"
    )


@router.get("/{family_id}/{sample_id}.bam.bai")
async def get_bai(
    family_id: str,
    sample_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    await _ensure_accessible_alignment_sample(session, family_id, sample_id, user)
    return await asyncio.to_thread(
        _serve_alignment, family_id, sample_id, "bam", ".bai", "BAI file not found"
    )


@router.head("/{family_id}/{sample_id}.bam.bai")
async def head_bai(
    family_id: str,
    sample_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    await _ensure_accessible_alignment_sample(session, family_id, sample_id, user)
    return await asyncio.to_thread(
        _head_alignment, family_id, sample_id, "bam", ".bai", "BAI file not found"
    )


def _read_alignment_header(family_id: str, sample_id: str) -> dict:
    """Open the sample's CRAM/BAM and return its header dict.

    Blocking work (pysam open + S3/htslib byte-range I/O), so callers must run
    it via ``asyncio.to_thread`` to avoid stalling the event loop.
    """
    if storage_is_remote():
        for ext, mode in (("cram", "rc"), ("bam", "rb")):
            key = _alignment_key(family_id, sample_id, ext)
            if object_exists(key):
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
    await _ensure_accessible_alignment_sample(session, family_id, sample_id, user)
    # Offload the blocking pysam open + header parse so a slow S3/htslib read
    # cannot stall the event loop (matches the manifest handler's pattern).
    return await asyncio.to_thread(_read_alignment_header, family_id, sample_id)
