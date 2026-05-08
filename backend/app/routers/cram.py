from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
import pysam
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import get_current_user
from ..schemas import AlignmentManifestEntryOut
from ..services.metadata_service import CurrentUser, get_family_record

router = APIRouter(prefix="/cram", tags=["cram"])

DATA_DIR = Path(__file__).resolve().parents[3] / "data"


def _alignment_path(
    family_id: str, sample_id: str, ext: str, suffix: str = ""
) -> Path:
    return DATA_DIR / family_id / f"{sample_id}.{ext}{suffix}"


def _resolve_alignment_manifest_entry(
    family_id: str,
    sample_id: str,
) -> AlignmentManifestEntryOut | None:
    cram_path = _alignment_path(family_id, sample_id, "cram")
    crai_path = _alignment_path(family_id, sample_id, "cram", ".crai")
    if cram_path.exists() and crai_path.exists():
        return AlignmentManifestEntryOut(
            sample_id=sample_id,
            format="cram",
            url=f"/cram/{family_id}/{sample_id}.cram",
            index_url=f"/cram/{family_id}/{sample_id}.cram.crai",
        )

    bam_path = _alignment_path(family_id, sample_id, "bam")
    bai_path = _alignment_path(family_id, sample_id, "bam", ".bai")
    if bam_path.exists() and bai_path.exists():
        return AlignmentManifestEntryOut(
            sample_id=sample_id,
            format="bam",
            url=f"/cram/{family_id}/{sample_id}.bam",
            index_url=f"/cram/{family_id}/{sample_id}.bam.bai",
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
    manifest: list[AlignmentManifestEntryOut] = []
    seen: set[str] = set()
    for sample_id in sample_ids:
        if sample_id in seen or sample_id not in family_sample_ids:
            continue
        seen.add(sample_id)
        entry = _resolve_alignment_manifest_entry(family_id, sample_id)
        if entry is not None:
            manifest.append(entry)
    return manifest


@router.get("/{family_id}/{sample_id}.cram")
async def get_cram(
    family_id: str,
    sample_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    await _ensure_accessible_alignment_sample(session, family_id, sample_id, user)
    path = _alignment_path(family_id, sample_id, "cram")
    if not path.exists():
        raise HTTPException(status_code=404, detail="CRAM file not found")
    return FileResponse(path)


@router.head("/{family_id}/{sample_id}.cram")
async def head_cram(
    family_id: str,
    sample_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    await _ensure_accessible_alignment_sample(session, family_id, sample_id, user)
    path = _alignment_path(family_id, sample_id, "cram")
    if not path.exists():
        raise HTTPException(status_code=404, detail="CRAM file not found")
    return Response(status_code=200)


@router.get("/{family_id}/{sample_id}.cram.crai")
async def get_crai(
    family_id: str,
    sample_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    await _ensure_accessible_alignment_sample(session, family_id, sample_id, user)
    path = _alignment_path(family_id, sample_id, "cram", ".crai")
    if not path.exists():
        raise HTTPException(status_code=404, detail="CRAI file not found")
    return FileResponse(path)


@router.head("/{family_id}/{sample_id}.cram.crai")
async def head_crai(
    family_id: str,
    sample_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    await _ensure_accessible_alignment_sample(session, family_id, sample_id, user)
    path = _alignment_path(family_id, sample_id, "cram", ".crai")
    if not path.exists():
        raise HTTPException(status_code=404, detail="CRAI file not found")
    return Response(status_code=200)


@router.get("/{family_id}/{sample_id}.bam")
async def get_bam(
    family_id: str,
    sample_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    await _ensure_accessible_alignment_sample(session, family_id, sample_id, user)
    path = _alignment_path(family_id, sample_id, "bam")
    if not path.exists():
        raise HTTPException(status_code=404, detail="BAM file not found")
    return FileResponse(path)


@router.head("/{family_id}/{sample_id}.bam")
async def head_bam(
    family_id: str,
    sample_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    await _ensure_accessible_alignment_sample(session, family_id, sample_id, user)
    path = _alignment_path(family_id, sample_id, "bam")
    if not path.exists():
        raise HTTPException(status_code=404, detail="BAM file not found")
    return Response(status_code=200)


@router.get("/{family_id}/{sample_id}.bam.bai")
async def get_bai(
    family_id: str,
    sample_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    await _ensure_accessible_alignment_sample(session, family_id, sample_id, user)
    path = _alignment_path(family_id, sample_id, "bam", ".bai")
    if not path.exists():
        raise HTTPException(status_code=404, detail="BAI file not found")
    return FileResponse(path)


@router.head("/{family_id}/{sample_id}.bam.bai")
async def head_bai(
    family_id: str,
    sample_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    await _ensure_accessible_alignment_sample(session, family_id, sample_id, user)
    path = _alignment_path(family_id, sample_id, "bam", ".bai")
    if not path.exists():
        raise HTTPException(status_code=404, detail="BAI file not found")
    return Response(status_code=200)


@router.get("/{family_id}/{sample_id}.cram.header")
async def get_cram_header(
    family_id: str,
    sample_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    """Return alignment header for quick M5/SN/LN inspection."""
    await _ensure_accessible_alignment_sample(session, family_id, sample_id, user)
    cram_path = _alignment_path(family_id, sample_id, "cram")
    bam_path = _alignment_path(family_id, sample_id, "bam")
    if cram_path.exists():
        with pysam.AlignmentFile(str(cram_path), "rc") as af:
            return af.header.to_dict()
    if bam_path.exists():
        with pysam.AlignmentFile(str(bam_path), "rb") as af:
            return af.header.to_dict()
    raise HTTPException(status_code=404, detail="No CRAM/BAM found for sample")
