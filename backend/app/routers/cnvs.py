from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..schemas import ClinicalCnvOut
from ..services.reference_metadata_service import get_clinical_cnvs_data

router = APIRouter(prefix="/cnvs", tags=["cnvs"])


@router.get("/{assembly}/{chrom}", response_model=List[ClinicalCnvOut])
async def get_clinical_cnvs(
    assembly: str,
    chrom: str,
    start: int = Query(0, ge=0),
    end: str = Query("0"),
    session: AsyncSession = Depends(get_postgres_session),
) -> List[ClinicalCnvOut]:
    try:
        end_val = int(end.split(":")[0])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid end parameter") from exc
    return await get_clinical_cnvs_data(
        session,
        assembly=assembly,
        chrom=chrom,
        start=start,
        end=end_val,
    )
