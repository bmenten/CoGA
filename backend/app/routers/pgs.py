"""Exploratory PGS endpoints; no connection to report or clinical sign-out endpoints."""
from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import get_current_admin_user, get_current_user
from ..pgs_schemas import FamilyPgsOut, PgsArchiveIn, PgsCatalogueOut, PgsRunOut, PgsVersionOut
from ..services.access_control import CurrentUser
from ..services import pgs_service
from ..services.upload_safety import decode_upload_text

router = APIRouter(tags=["exploratory_pgs"])


@router.get("/pgs/models", response_model=PgsCatalogueOut)
async def get_pgs_models(session: AsyncSession = Depends(get_postgres_session), user: CurrentUser = Depends(get_current_user)) -> PgsCatalogueOut:
    return await pgs_service.catalogue(session)


@router.post("/admin/pgs/models/{pgs_id}/versions", response_model=PgsVersionOut, status_code=201)
async def publish_pgs_version(pgs_id: str, version: str = Form(...), study: str = Form(...), source_url: str = Form(...),
                              file: UploadFile = File(...), session: AsyncSession = Depends(get_postgres_session),
                              user: CurrentUser = Depends(get_current_admin_user)) -> PgsVersionOut:
    value = await decode_upload_text(file, kind="PGS weights")
    return await pgs_service.publish_version(session, pgs_id=pgs_id, version=version, study=study,
                                              source_url=source_url, weights_text=value)


@router.put("/admin/pgs/models/{pgs_id}/archive", response_model=PgsCatalogueOut)
async def archive_pgs_model(pgs_id: str, payload: PgsArchiveIn, session: AsyncSession = Depends(get_postgres_session),
                             user: CurrentUser = Depends(get_current_admin_user)) -> PgsCatalogueOut:
    return await pgs_service.set_archived(session, pgs_id, payload.archived)


@router.get("/families/{family_id}/pgs", response_model=FamilyPgsOut)
async def get_family_pgs(family_id: str, project_id: str | None = None, session: AsyncSession = Depends(get_postgres_session),
                          user: CurrentUser = Depends(get_current_user)) -> FamilyPgsOut:
    return await pgs_service.family_pgs(session, family_id, user, project_id)


@router.post("/families/{family_id}/pgs/calculate", response_model=PgsRunOut, status_code=202)
async def calculate_family_pgs(family_id: str, project_id: str | None = None, session: AsyncSession = Depends(get_postgres_session),
                               user: CurrentUser = Depends(get_current_user)) -> PgsRunOut:
    return await pgs_service.queue_family(session, family_id, user, project_id)
