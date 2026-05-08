from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import get_current_admin_user, get_current_user
from ..schemas import VariantPage
from ..services.clickhouse_family_variants import (
    get_family_structural_variants_page as get_family_structural_variants_clickhouse,
)
from ..services.family_metadata_context import build_family_metadata_context, build_sample_metadata_context
from ..services.metadata_service import CurrentUser
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
    return await upload_structural_variant_file(
        session,
        family_context=family_context,
        sample_context=sample_context,
        file=file,
        overwrite=overwrite,
        format_hint=source_format,  # type: ignore[arg-type]
    )


@router.get("/{sample_id}", response_model=VariantPage)
async def get_sample_structural_variants(
    sample_id: str,
    page: int = 1,
    page_size: int = 100,
    chr: str | None = None,
    start: int | None = None,
    end: int | None = None,
    length: int | None = None,
    min_length: int | None = None,
    type: str | None = None,
    source: str | None = None,
    panel_id: str | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> VariantPage:
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
    return await get_family_structural_variants_clickhouse(
        session,
        context=family_context,
        page=page,
        page_size=page_size,
        chr=chr,
        start=start,
        end=end,
        length=length,
        min_length=min_length,
        type=type,
        source=source,
        samples=[sample_context.sample_id],
        panel_id=panel_id,
    )
