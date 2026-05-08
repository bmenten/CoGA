from typing import Literal

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import get_current_admin_user, get_current_user
from ..services.bed_service import (
    fetch_bed_batch_json,
    fetch_bed_batch_text,
    fetch_bed_json,
    fetch_bed_text,
    upload_bed_data,
)
from ..services.family_metadata_context import build_sample_metadata_context
from ..services.metadata_service import CurrentUser

router = APIRouter(prefix="/bed", tags=["bed"])


@router.post("/upload/{sample_id}/{bed_type}")
async def upload_bed(
    sample_id: str,
    bed_type: str,
    file: UploadFile = File(...),
    overwrite: bool = False,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> dict[str, int]:
    sample_context = await build_sample_metadata_context(
        session,
        sample_identifier=sample_id,
        user=user,
    )
    return await upload_bed_data(
        session,
        sample_context=sample_context,
        bed_type=bed_type,
        file=file,
        overwrite=overwrite,
    )


@router.get(
    "/{sample_id}/{bed_type}/batch",
    response_class=PlainTextResponse,
    response_model=None,
)
async def fetch_bed_batch(
    sample_id: str,
    bed_type: str,
    chrom: list[str] = Query(...),
    window: int | None = None,
    format: Literal["text", "json"] = "text",
    limit: int = Query(100000, ge=1, le=1000000),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> Response:
    sample_context = await build_sample_metadata_context(
        session,
        sample_identifier=sample_id,
        user=user,
    )
    if format == "json":
        return await fetch_bed_batch_json(
            session,
            sample_context=sample_context,
            bed_type=bed_type,
            chroms=chrom,
            window=window,
            limit=limit,
        )
    return await fetch_bed_batch_text(
        session,
        sample_context=sample_context,
        bed_type=bed_type,
        chroms=chrom,
        window=window,
        limit=limit,
    )


@router.get("/{sample_id}/{bed_type}", response_class=PlainTextResponse, response_model=None)
async def fetch_bed(
    sample_id: str,
    bed_type: str,
    chrom: str,
    window: int | None = None,
    format: Literal["text", "json"] = "text",
    limit: int = Query(100000, ge=1, le=1000000),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> Response:
    sample_context = await build_sample_metadata_context(
        session,
        sample_identifier=sample_id,
        user=user,
    )
    if format == "json":
        return await fetch_bed_json(
            session,
            sample_context=sample_context,
            bed_type=bed_type,
            chrom=chrom,
            window=window,
            limit=limit,
        )
    return await fetch_bed_text(
        session,
        sample_context=sample_context,
        bed_type=bed_type,
        chrom=chrom,
        window=window,
        limit=limit,
    )
