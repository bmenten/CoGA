from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import get_current_user
from ..schemas import ClinicalCnvOut
from ..services.reference_metadata_service import (
    get_clinical_cnv_by_id_data,
    get_clinical_cnvs_data,
    list_clinical_cnvs_catalog_data,
)

# Reference data requires authentication (a router-level dependency gates every
# endpoint, including any added later).
router = APIRouter(
    prefix="/cnvs",
    tags=["cnvs"],
    dependencies=[Depends(get_current_user)],
)


# Literal-prefix routes are declared before the generic "/{assembly}/{chrom}"
# route so they take precedence in match order.
@router.get("/entry/{cnv_id}", response_model=ClinicalCnvOut)
async def get_clinical_cnv_entry(
    cnv_id: str,
    session: AsyncSession = Depends(get_postgres_session),
) -> ClinicalCnvOut:
    return await get_clinical_cnv_by_id_data(session, cnv_id=cnv_id)


@router.get("/{assembly}/catalog", response_model=List[ClinicalCnvOut])
async def list_clinical_cnv_catalog(
    assembly: str,
    search: str | None = Query(None),
    limit: int = Query(500, ge=1, le=2000),
    session: AsyncSession = Depends(get_postgres_session),
) -> List[ClinicalCnvOut]:
    return await list_clinical_cnvs_catalog_data(
        session,
        assembly=assembly,
        search=search,
        limit=limit,
    )


@router.get("/{assembly}/{chrom}", response_model=List[ClinicalCnvOut])
async def get_clinical_cnvs(
    assembly: str,
    chrom: str,
    start: int = Query(0, ge=0),
    end: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_postgres_session),
) -> List[ClinicalCnvOut]:
    return await get_clinical_cnvs_data(
        session,
        assembly=assembly,
        chrom=chrom,
        start=start,
        end=end,
    )
