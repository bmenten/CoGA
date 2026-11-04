"""Exploratory ancestry PCA on the family's stored callsets; excluded from clinical sign-out."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..ancestry_schemas import AncestryRunOut, FamilyAncestryOut
from ..dependencies import get_current_user
from ..services.access_control import CurrentUser
from ..services import ancestry_service

router = APIRouter(tags=["ancestry"])


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
