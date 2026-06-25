from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import (
    get_current_user,
)
from ..schemas import (
    GenePanelOut,
    GenePanelCreate,
    GenePanelCreateResponse,
    GenePanelUpdate,
    GenePanelVersionDetail,
    GenePanelVersionListOut,
    MendeliomeRegenerateResponse,
    PanelAppImportRequest,
    PanelAppImportResponse,
    PanelAppPanelSearchResponse,
)
from ..services.metadata_service import CurrentUser
from ..services.panel_metadata_service import (
    create_panel_data,
    delete_panel_data,
    get_panel_or_404,
    get_panel_version,
    import_panelapp_panel_data,
    list_panel_versions,
    list_panels_data,
    regenerate_mendeliome,
    update_panel_data,
)
from ..services.panelapp_service import search_panelapp_panels

router = APIRouter(prefix="/panels", tags=["panels"])


@router.get("/", response_model=List[GenePanelOut])
async def list_panels(
    session: AsyncSession = Depends(get_postgres_session),
    current_user: CurrentUser = Depends(get_current_user),
) -> List[GenePanelOut]:
    return await list_panels_data(session)


@router.get("/panelapp/search", response_model=PanelAppPanelSearchResponse)
async def search_panelapp(
    query: str = Query("", max_length=120),
    page_size: int = Query(20, ge=1, le=50),
    current_user: CurrentUser = Depends(get_current_user),
) -> PanelAppPanelSearchResponse:
    del current_user
    results, count = await search_panelapp_panels(query, page_size=page_size)
    return PanelAppPanelSearchResponse(results=results, count=count)


@router.post("/import/panelapp", response_model=PanelAppImportResponse, status_code=201)
async def import_panelapp_panel(
    request: PanelAppImportRequest,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> PanelAppImportResponse:
    return await import_panelapp_panel_data(session, request, user)


@router.post("/mendeliome/regenerate", response_model=MendeliomeRegenerateResponse)
async def regenerate_mendeliome_panel(
    force: bool = Query(False, description="Create a new version even if the gene set is unchanged."),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> MendeliomeRegenerateResponse:
    return await regenerate_mendeliome(session, user, force=force)


@router.get("/{panel_id}/versions", response_model=GenePanelVersionListOut)
async def list_panel_version_history(
    panel_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    current_user: CurrentUser = Depends(get_current_user),
) -> GenePanelVersionListOut:
    return await list_panel_versions(session, panel_id)


@router.get("/{panel_id}/versions/{version}", response_model=GenePanelVersionDetail)
async def get_panel_version_detail(
    panel_id: str,
    version: int,
    session: AsyncSession = Depends(get_postgres_session),
    current_user: CurrentUser = Depends(get_current_user),
) -> GenePanelVersionDetail:
    return await get_panel_version(session, panel_id, version)


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


@router.put("/{panel_id}", response_model=GenePanelCreateResponse)
async def update_panel(
    panel_id: str,
    payload: GenePanelUpdate,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> GenePanelCreateResponse:
    return await update_panel_data(session, panel_id, payload, user)


@router.delete("/{panel_id}", status_code=204)
async def delete_panel(
    panel_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> None:
    await delete_panel_data(session, panel_id, user)
