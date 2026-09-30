from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import get_current_admin_user
from ..services.family_metadata_context import build_family_metadata_context, build_sample_metadata_context
from ..services.access_control import CurrentUser
from ..services.raw_import_files_pg import record_upload_file_obj
from ..services.variant_upload_service import upload_structural_variant_file

router = APIRouter(prefix="/structural-variants", tags=["structural_variants"])


@router.post("/upload/{sample_id}")
async def upload_structural_variants(
    sample_id: str,
    file: UploadFile = File(...),
    overwrite: bool = False,
    source_format: str = "auto",
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
):
    sample_context = await build_sample_metadata_context(
        session,
        sample_identifier=sample_id,
        user=user,
    )
    family_context = await build_family_metadata_context(
        session,
        family_identifier=sample_context.family_id,
        user=user,
    )
    result = await upload_structural_variant_file(
        session,
        family_context=family_context,
        sample_context=sample_context,
        file=file,
        overwrite=overwrite,
        format_hint=source_format,  # type: ignore[arg-type]
    )
    await record_upload_file_obj(
        session,
        file=file,
        family_uuid=sample_context.family_uuid,
        family_id=sample_context.family_id,
        sample_uuid=sample_context.sample_uuid,
        scope="individual",
        dataset="structural_variants",
    )
    return result
