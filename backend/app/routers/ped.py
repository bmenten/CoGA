from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import get_current_admin_user, get_current_user
from ..schemas import ManualPedFamilyCreate, PedUploadResult
from ..services.bed_service import precompute_family_lineage_safe
from ..services.clickhouse_family_variants import precompute_family_ranking_safe
from ..services.metadata_service import CurrentUser
from ..services.ped_service import create_manual_family_data, upload_ped_data

router = APIRouter(prefix="/ped", tags=["ped"])


def _schedule_lineage_refresh(
    background_tasks: BackgroundTasks, result: PedUploadResult, user: CurrentUser
) -> None:
    """A PED upload defines/changes the pedigree, which drives the haplotype lineage
    colours. Refresh each touched family's precomputed genome-overview lineage in the
    background (a no-op for families without variant data yet; the hash guard keeps
    the overview safe-but-grey until the new precompute lands)."""
    for fam in result.families:
        if fam.family_id:
            background_tasks.add_task(precompute_family_lineage_safe, fam.family_id, user)
            background_tasks.add_task(precompute_family_ranking_safe, fam.family_id, user)


@router.post("/upload", response_model=PedUploadResult)
async def upload_ped(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    overwrite: bool = False,
    project_id: str | None = None,
    roi_query: str | None = Form(default=None),
    inheritance_model: str | None = Form(default=None),
    obligate_carriers: str | None = Form(default=None),
    proven_carriers: str | None = Form(default=None),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
):
    result = await upload_ped_data(
        session,
        file,
        overwrite,
        user,
        project_id,
        roi_query=roi_query,
        inheritance_model=inheritance_model,
        obligate_carriers=obligate_carriers,
        proven_carriers=proven_carriers,
    )
    _schedule_lineage_refresh(background_tasks, result, user)
    return result


@router.post("/manual", response_model=PedUploadResult)
async def create_manual_family(
    family: ManualPedFamilyCreate,
    background_tasks: BackgroundTasks,
    overwrite: bool = False,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
):
    result = await create_manual_family_data(session, family, overwrite, user)
    _schedule_lineage_refresh(background_tasks, result, user)
    return result
