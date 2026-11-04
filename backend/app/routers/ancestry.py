"""Exploratory ancestry PCA on the family's stored callsets; excluded from clinical sign-out."""
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..ancestry_schemas import AncestryRunOut, AncestrySampleChoiceIn, FamilyAncestryOut
from ..dependencies import get_current_user
from ..services.access_control import CurrentUser
from ..services import ancestry_service
from ..services.ancestry_choices import MAX_BED_BYTES

router = APIRouter(tags=["ancestry"])


@router.get("/families/{family_id}/ancestry", response_model=FamilyAncestryOut)
async def get_family_ancestry(family_id: str, project_id: str | None = None, run_id: str | None = None,
                              session: AsyncSession = Depends(get_postgres_session),
                              user: CurrentUser = Depends(get_current_user)) -> FamilyAncestryOut:
    return await ancestry_service.family_ancestry(session, family_id, user, project_id, run_id)


@router.post("/families/{family_id}/ancestry/calculate", response_model=AncestryRunOut, status_code=202)
async def calculate_family_ancestry(family_id: str, project_id: str | None = None,
                                    session: AsyncSession = Depends(get_postgres_session),
                                    user: CurrentUser = Depends(get_current_user)) -> AncestryRunOut:
    return await ancestry_service.queue_family(session, family_id, user, project_id)


@router.put("/families/{family_id}/ancestry/choices/{sample_id}", response_model=FamilyAncestryOut)
async def choose_family_ancestry_reading(family_id: str, sample_id: str, choice: AncestrySampleChoiceIn,
                                         project_id: str | None = None,
                                         session: AsyncSession = Depends(get_postgres_session),
                                         user: CurrentUser = Depends(get_current_user)) -> FamilyAncestryOut:
    """How one individual is read: CoGA's own reading, one callset (with a gene panel's regions),
    or not at all. Queues a calculation that reads it."""
    return await ancestry_service.set_choice(session, family_id, user, project_id, sample_id, choice)


@router.put("/families/{family_id}/ancestry/choices/{sample_id}/bed", response_model=FamilyAncestryOut)
async def choose_family_ancestry_bed(family_id: str, sample_id: str,
                                     callset: Literal["clair3", "nipt", "glimpse2"] = Form(...),
                                     file: UploadFile = File(...), project_id: str | None = None,
                                     session: AsyncSession = Depends(get_postgres_session),
                                     user: CurrentUser = Depends(get_current_user)) -> FamilyAncestryOut:
    """Read one individual from one callset inside the regions of a BED file (a capture kit's).
    Only the reference markers inside it and its checksum are kept, not the file."""
    data = await file.read(MAX_BED_BYTES + 1)
    return await ancestry_service.set_choice(
        session, family_id, user, project_id, sample_id,
        AncestrySampleChoiceIn(mode="callset", callset=callset), bed=(file.filename or "", data))
