from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import get_current_user
from ..schemas import ReferenceReadsOut, ReferenceSequenceOut
from ..services.metadata_service import CurrentUser, get_accessible_sample_mapping
from ..services.reference_service import get_reference_reads_data, get_reference_sequence_data

router = APIRouter(prefix="/reference", tags=["reference"])

ChromQuery = Annotated[str, Query(min_length=1)]
StartQuery = Annotated[int, Query(ge=0)]
EndQuery = Annotated[int, Query(ge=0)]


@router.get("/sequence", response_model=ReferenceSequenceOut)
async def get_reference_sequence(
    chrom: ChromQuery,
    start: StartQuery,
    end: EndQuery,
) -> ReferenceSequenceOut:
    return get_reference_sequence_data(chrom=chrom, start=start, end=end)


@router.get("/reads/{sample_id}", response_model=ReferenceReadsOut)
async def get_reference_reads(
    sample_id: str,
    chrom: ChromQuery,
    start: StartQuery,
    end: EndQuery,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> ReferenceReadsOut:
    await get_accessible_sample_mapping(session, sample_id, user)
    return get_reference_reads_data(sample_id=sample_id, chrom=chrom, start=start, end=end)
