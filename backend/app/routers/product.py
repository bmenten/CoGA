from fastapi import APIRouter, Depends, Query

from ..dependencies import get_current_user
from ..schemas import GithubReleaseCatalogOut
from ..services.github_releases_service import get_github_release_catalog
from ..services.metadata_service import CurrentUser

router = APIRouter(prefix="/product", tags=["product"])


@router.get("/releases", response_model=GithubReleaseCatalogOut)
async def list_product_releases(
    refresh: bool = Query(default=False),
    user: CurrentUser = Depends(get_current_user),
) -> GithubReleaseCatalogOut:
    del user
    return await get_github_release_catalog(refresh=refresh)
