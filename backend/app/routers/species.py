from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import get_current_admin_user
from ..services.metadata_service import CurrentUser, create_species_record, list_species_records
from ..schemas import SpeciesCreate, SpeciesOut

router = APIRouter(prefix="/species", tags=["species"])


@router.get("/", response_model=List[SpeciesOut])
async def list_species(
    session: AsyncSession = Depends(get_postgres_session),
) -> List[SpeciesOut]:
    return await list_species_records(session)


@router.post("/", response_model=SpeciesOut, status_code=201)
async def create_species(
    species_in: SpeciesCreate,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> SpeciesOut:
    return await create_species_record(
        session,
        name=species_in.name,
        common_name=species_in.common_name,
        tax_id=species_in.tax_id,
    )
