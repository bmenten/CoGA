"""Lightweight lookup endpoints: the active family-status catalogue, which the family-detail
page's status picker reads, and the list of active users.

The family page's "Assigned to" and "Reviewed by" pickers do not read the user list: they
read the family's own, its projects' analysts and the administrators
(``GET /families/{family_id}/assignable-users``).

The status catalogue is open to any signed-in user; the admin endpoints manage the full
catalogue. The user list gives every active account's name and e-mail address, so it answers
administrators only (SEC-33); the admin endpoints manage the accounts themselves
(``GET /auth/users``).
"""

from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import get_current_admin_user, get_current_user
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
    user: CurrentUser = Depends(get_current_admin_user),
) -> List[UserRefOut]:
    """Every active account's name and e-mail address, for administrators only (SEC-33)."""
    del user
    return await list_assignable_users(session)
