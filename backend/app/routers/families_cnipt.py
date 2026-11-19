"""The conventional NIPT's result for a family (``cnipt_service``)."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import get_current_user
from ..schemas import FamilyCniptOut
from ..services.access_control import CurrentUser
from ..services.cnipt_service import get_family_cnipt_result

router = APIRouter()


@router.get("/{family_id}/cnipt", response_model=FamilyCniptOut)
async def get_family_cnipt(
    family_id: str,
    project_id: str | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> FamilyCniptOut:
    """Each maternal plasma's conventional NIPT result: per target (each autosome, the sex
    chromosomes, segmental CNVs) high risk, inconclusive, low risk, no result or not assessed,
    with the findings, the fetal fractions and the bounds it applied (QA's or provisional)."""
    return await get_family_cnipt_result(session, family_id=family_id, user=user, project_id=project_id)
