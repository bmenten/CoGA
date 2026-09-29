"""Lightweight authenticated lookup endpoints used to populate pickers on the
family-detail page: the active family-status catalogue and the list of users
that can be assigned to / recorded as reviewer of a family.

These are intentionally separate from the admin endpoints (which manage the full
status catalogue and user accounts) and expose only minimal, non-sensitive
fields to any signed-in user.
"""

from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import get_current_user
from ..schemas import FamilyStatusOut, UserRefOut
from ..services.family_status_service import list_assignable_users, list_family_statuses
from ..services.access_control import CurrentUser

router = APIRouter(tags=["lookups"])


@router.get("/family-statuses", response_model=List[FamilyStatusOut])
async def get_family_statuses(
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> List[FamilyStatusOut]:
    del user
    return await list_family_statuses(session)


@router.get("/users", response_model=List[UserRefOut])
async def get_assignable_users(
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> List[UserRefOut]:
    del user
    return await list_assignable_users(session)
