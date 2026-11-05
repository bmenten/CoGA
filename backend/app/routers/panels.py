from typing import List, Optional

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import (
    get_current_superuser,
    get_current_user,
)
from ..schemas import (
    GenePanelOut,
    GenePanelCreate,
    GenePanelCreateResponse,
    GenePanelUpdate,
    GenePanelVersionDetail,
    GenePanelVersionListOut,
    GeneSymbolCheckOut,
    GeneSymbolCheckRequest,
    MendeliomeRegenerateResponse,
    PanelAppImportRequest,
    PanelAppImportResponse,
    PanelAppPanelCountsOut,
    PanelAppPanelSearchResponse,
)
from ..services.access_control import CurrentUser
from ..services.panel_metadata_service import (
    check_gene_symbols,
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
from ..services.panelapp_service import (
    fetch_panelapp_panel,
    panelapp_confidence_counts,
    search_panelapp_panels,
)

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


@router.get("/panelapp/{panelapp_id}/counts", response_model=PanelAppPanelCountsOut)
async def panelapp_panel_counts(
    panelapp_id: int = Path(ge=1),
    version: Optional[str] = Query(None, max_length=32, pattern=r"^[0-9]+(\.[0-9]+)*$"),
    current_user: CurrentUser = Depends(get_current_user),
) -> PanelAppPanelCountsOut:
    """How many of a PanelApp panel's genes, STRs and regions are green, amber, red or
    unrated, at the version given (PanelApp's current one without), read from PanelApp."""
    del current_user
    return panelapp_confidence_counts(await fetch_panelapp_panel(panelapp_id, version=version))


@router.post("/genes/check", response_model=GeneSymbolCheckOut)
async def check_panel_gene_symbols(
    payload: GeneSymbolCheckRequest,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_superuser),
) -> GeneSymbolCheckOut:
    """Which gene names a panel would get coordinates for, before an admin saves them. It only
    reads, but it is a POST (the names can be many) and so an admin's, like the edit it
    serves."""
    return await check_gene_symbols(session, payload.genes)


@router.post("/import/panelapp", response_model=PanelAppImportResponse, status_code=201)
async def import_panelapp_panel(
    request: PanelAppImportRequest,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_superuser),
) -> PanelAppImportResponse:
    return await import_panelapp_panel_data(session, request, user)


@router.post("/mendeliome/regenerate", response_model=MendeliomeRegenerateResponse)
async def regenerate_mendeliome_panel(
    force: bool = Query(False, description="Create a new version even if the gene set is unchanged."),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_superuser),
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
    user: CurrentUser = Depends(get_current_superuser),
) -> GenePanelCreateResponse:
    return await create_panel_data(session, panel, user)


@router.put("/{panel_id}", response_model=GenePanelCreateResponse)
async def update_panel(
    panel_id: str,
    payload: GenePanelUpdate,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_superuser),
) -> GenePanelCreateResponse:
    return await update_panel_data(session, panel_id, payload, user)


@router.delete("/{panel_id}", status_code=204)
async def delete_panel(
    panel_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_superuser),
) -> None:
    await delete_panel_data(session, panel_id, user)
