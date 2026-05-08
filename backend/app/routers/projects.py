from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import get_current_admin_user, get_current_user
from ..schemas import ProjectCreate, ProjectDashboardOut, ProjectOut, ProjectUpdate
from ..services.metadata_service import (
    CurrentUser,
    create_project_record,
    delete_project_record,
    list_project_dashboards,
    update_project_record,
)

router = APIRouter(prefix="/projects", tags=["projects"])


def _require_uuid(value: str, detail: str) -> None:
    try:
        UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=detail) from exc


@router.post("/", response_model=ProjectOut, status_code=201)
async def create_project(
    project_in: ProjectCreate,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> ProjectOut:
    _require_uuid(project_in.species_id, "Invalid species id")
    _require_uuid(project_in.assembly_id, "Invalid assembly id")
    for user_id in project_in.user_ids:
        _require_uuid(user_id, f"Invalid user id: {user_id}")
    return await create_project_record(
        session,
        name=project_in.name,
        description=project_in.description,
        species_id=project_in.species_id,
        assembly_id=project_in.assembly_id,
        user_ids=project_in.user_ids,
    )


@router.put("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: str,
    project_in: ProjectUpdate,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> ProjectOut:
    _require_uuid(project_id, "Invalid project id")
    if project_in.species_id is not None:
        _require_uuid(project_in.species_id, "Invalid species id")
    if project_in.assembly_id is not None:
        _require_uuid(project_in.assembly_id, "Invalid assembly id")
    for user_id in project_in.user_ids or []:
        _require_uuid(user_id, f"Invalid user id: {user_id}")
    return await update_project_record(
        session,
        project_id=project_id,
        name=project_in.name,
        description=project_in.description,
        species_id=project_in.species_id,
        assembly_id=project_in.assembly_id,
        user_ids=project_in.user_ids,
    )


@router.get("/", response_model=List[ProjectDashboardOut])
async def list_projects(
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> List[ProjectDashboardOut]:
    return await list_project_dashboards(session, user)


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> None:
    _require_uuid(project_id, "Invalid project id")
    await delete_project_record(session, project_id=project_id)
