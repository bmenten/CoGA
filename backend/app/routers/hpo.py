from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import get_current_user
from ..schemas import HpoTermOut
from ..services.hpo_service import search_hpo_terms
from ..services.access_control import CurrentUser

router = APIRouter(prefix="/hpo", tags=["hpo"])


@router.get("/search", response_model=List[HpoTermOut])
async def search_hpo(
    q: str = Query(min_length=1),
    limit: int = Query(default=20, ge=1, le=50),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> List[HpoTermOut]:
    _ = user
    return await search_hpo_terms(session, query=q, limit=limit)
