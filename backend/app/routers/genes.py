from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import get_current_user
from ..schemas import GeneOut, GeneProfileOut, GeneSearchResultOut
from ..services.gene_metadata_service import build_gene_profile, search_genes
from ..services.metadata_service import CurrentUser
from ..services.reference_metadata_service import get_gene_region_records

router = APIRouter(prefix="/genes", tags=["genes"])


@router.get("/search", response_model=List[GeneSearchResultOut])
async def search_gene_symbols(
    q: str = Query(..., min_length=2),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> List[GeneSearchResultOut]:
    _ = user
    return await search_genes(session, query=q)


@router.get("/profile", response_model=GeneProfileOut)
async def get_gene_profile(
    symbol: str = Query(..., min_length=1),
    assembly_id: str | None = Query(default=None),
    family_id: str | None = Query(default=None),
    project_id: str | None = Query(default=None),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> GeneProfileOut:
    return await build_gene_profile(
        session,
        symbol=symbol,
        assembly_id=assembly_id,
        family_id=family_id,
        project_id=project_id,
        user=user,
    )


@router.get("/{assembly}/{chrom}", response_model=List[GeneOut])
async def get_genes(
    assembly: str,
    chrom: str,
    start: int = Query(0, ge=0),
    end: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_postgres_session),
) -> List[GeneOut]:
    return await get_gene_region_records(
        session,
        assembly=assembly,
        chrom=chrom,
        start=start,
        end=end,
    )
