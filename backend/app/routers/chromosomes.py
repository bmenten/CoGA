from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..schemas import ChromosomeOut, ChromosomeSizeOut
from ..services.reference_metadata_service import (
    get_chromosome_data,
    list_chromosome_details_data,
    list_chromosome_sizes_data,
)

router = APIRouter(prefix="/chromosomes", tags=["chromosomes"])


@router.get("/{assembly}", response_model=list[ChromosomeSizeOut])
async def list_chromosomes(
    assembly: str,
    chrom: list[str] = Query(default_factory=list),
    session: AsyncSession = Depends(get_postgres_session),
) -> list[ChromosomeSizeOut]:
    return await list_chromosome_sizes_data(
        session,
        assembly=assembly,
        chroms=chrom,
    )


@router.get("/{assembly}/details", response_model=list[ChromosomeOut])
async def list_chromosome_details(
    assembly: str,
    chrom: list[str] = Query(default_factory=list),
    session: AsyncSession = Depends(get_postgres_session),
) -> list[ChromosomeOut]:
    return await list_chromosome_details_data(
        session,
        assembly=assembly,
        chroms=chrom,
    )


@router.get("/{assembly}/{chrom}", response_model=ChromosomeOut)
async def get_chromosome(
    assembly: str,
    chrom: str,
    session: AsyncSession = Depends(get_postgres_session),
) -> ChromosomeOut:
    return await get_chromosome_data(
        session,
        assembly=assembly,
        chrom=chrom,
    )
