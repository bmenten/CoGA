from __future__ import annotations

from typing import Dict

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import get_current_admin_user
from ..schemas import RepeatExpansionUploadResult
from ..services.family_metadata_context import build_sample_metadata_context
from ..services.family_package_registration import record_family_upload, repeat_upload_scope
from ..services.family_variant_write_lock import VARIANT_TYPES, lock_family_variant_writes
from ..services.access_control import CurrentUser
from ..services.raw_import_files_pg import record_upload_file_obj
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
    # From the read below until the commit: a package import's restore rewrites the
    # family's repeat calls too, and the import holds these locks from its snapshot to its
    # restore, so the upload never falls in between. The sample must still be stored once
    # it is this upload's turn.
    await lock_family_variant_writes(
        session,
        sample_context.family_uuid,
        VARIANT_TYPES,
        samples=[sample_context.sample_uuid],
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
    # What the upload brings, recorded for the family (SAFE-19) in the transaction that
    # commits its calls: ingest_trgt_text commits both, or a refused file neither.
    dataset, scope = repeat_upload_scope(sample_context.sample_id)
    await record_family_upload(
        session,
        family_uuid=sample_context.family_uuid,
        dataset=dataset,
        scope=scope,
        overwrite=overwrite,
    )
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
    await record_upload_file_obj(
        session,
        file=file,
        family_uuid=sample_context.family_uuid,
        family_id=sample_context.family_id,
        sample_uuid=sample_context.sample_uuid,
        scope="individual",
        dataset="repeats_trgt",
    )
    return result
