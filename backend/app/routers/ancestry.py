"""Ancestry PCA and verified WGS inputs; excluded from clinical sign-out."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..ancestry_schemas import AncestryRunOut, FamilyAncestryOut
from ..dependencies import get_current_user
from ..wgs_schemas import WgsSourceOut, WgsSourceDeclaration, WgsSourcesOut
from ..services.access_control import CurrentUser
from ..services import ancestry_service
from ..services import wgs_source_service

router = APIRouter(tags=["ancestry"])


@router.get("/families/{family_id}/wgs-source", response_model=WgsSourcesOut)
async def get_wgs_source_candidates(family_id: str, project_id: str | None = None,
                                    session: AsyncSession = Depends(get_postgres_session),
                                    user: CurrentUser = Depends(get_current_user)) -> WgsSourcesOut:
    return await wgs_source_service.candidates(session, family_id, user, project_id)


@router.post("/families/{family_id}/wgs-source", response_model=WgsSourceOut)
async def register_family_wgs_source(family_id: str, declaration: WgsSourceDeclaration,
                                     project_id: str | None = None,
                                     session: AsyncSession = Depends(get_postgres_session),
                                     user: CurrentUser = Depends(get_current_user)) -> WgsSourceOut:
    return await wgs_source_service.register(session, family_id, user, project_id, declaration)


@router.get("/families/{family_id}/ancestry", response_model=FamilyAncestryOut)
async def get_family_ancestry(family_id: str, project_id: str | None = None, run_id: str | None = None,
                              session: AsyncSession = Depends(get_postgres_session),
                              user: CurrentUser = Depends(get_current_user)) -> FamilyAncestryOut:
    return await ancestry_service.family_ancestry(session, family_id, user, project_id, run_id)


@router.post("/families/{family_id}/ancestry/calculate", response_model=AncestryRunOut, status_code=202)
async def calculate_family_ancestry(family_id: str, project_id: str | None = None,
                                    session: AsyncSession = Depends(get_postgres_session),
                                    user: CurrentUser = Depends(get_current_user)) -> AncestryRunOut:
    return await ancestry_service.queue_family(session, family_id, user, project_id)
