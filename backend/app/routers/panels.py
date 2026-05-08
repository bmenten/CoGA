from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import (
    get_current_user,
)
from ..schemas import GenePanelOut, GenePanelCreate, GenePanelCreateResponse
from ..services.metadata_service import CurrentUser
from ..services.panel_metadata_service import (
    create_panel_data,
    delete_panel_data,
    get_panel_or_404,
    list_panels_data,
)

router = APIRouter(prefix="/panels", tags=["panels"])


@router.get("/", response_model=List[GenePanelOut])
async def list_panels(
    session: AsyncSession = Depends(get_postgres_session),
    current_user: CurrentUser = Depends(get_current_user),
) -> List[GenePanelOut]:
    return await list_panels_data(session)


@router.get("/{panel_id}", response_model=GenePanelOut)
async def get_panel(
    panel_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    current_user: CurrentUser = Depends(get_current_user),
) -> GenePanelOut:
    return await get_panel_or_404(session, panel_id)


@router.post("/", response_model=GenePanelCreateResponse, status_code=201)
async def create_panel(
    panel: GenePanelCreate,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> GenePanelCreateResponse:
    return await create_panel_data(session, panel, user)


@router.delete("/{panel_id}", status_code=204)
async def delete_panel(
    panel_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> None:
    await delete_panel_data(session, panel_id, user)
