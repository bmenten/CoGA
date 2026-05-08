from __future__ import annotations

from typing import Dict

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import get_current_admin_user, get_current_user
from ..schemas import RepeatExpansionUploadResult
from ..services.family_metadata_context import build_sample_metadata_context
from ..services.metadata_service import CurrentUser
from ..services.repeat_expansion_pg import (
    clear_sample_repeat_expansions,
    decode_repeat_upload_text,
    ingest_trgt_text,
)


router = APIRouter(prefix="/repeat-expansions", tags=["repeat_expansions"])


@router.post("/upload/{sample_id}", response_model=RepeatExpansionUploadResult)
async def upload_repeat_expansions(
    sample_id: str,
    file: UploadFile = File(...),
    overwrite: bool = False,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> Dict[str, int | str]:
    sample_context = await build_sample_metadata_context(
        session,
        sample_identifier=sample_id,
        user=user,
    )
    existing_result = await session.execute(
        text(
            """
            SELECT COUNT(*)
            FROM repeat_expansions
            WHERE sample_id = CAST(:sample_id AS uuid)
              AND source = 'trgt'
            """
        ),
        {"sample_id": sample_context.sample_uuid},
    )
    existing = int(existing_result.scalar_one() or 0)
    if existing and not overwrite:
        raise HTTPException(
            status_code=409,
            detail="Repeat expansion data already exist for this sample",
        )
    if existing:
        await clear_sample_repeat_expansions(
            session,
            sample_uuid=sample_context.sample_uuid,
        )

    text_value = await decode_repeat_upload_text(file)
    result = await ingest_trgt_text(
        session,
        sample_context=sample_context,
        text_value=text_value,
        metadata={
            "source": "trgt",
            "filename": file.filename,
            "uploaded_from": "web",
        },
    )
    return result
