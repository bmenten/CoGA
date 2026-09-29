from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import get_current_admin_user, get_current_user
from ..schemas import (
    HpoOntologyImportOut,
    HpoOntologyImportRequest,
    HpoTermDetailOut,
    HpoTermOut,
)
from ..services.hpo_service import (
    ensure_authorized_hpo_ontology_path,
    get_hpo_term_details,
    import_hpo_ontology,
    search_hpo_terms,
)
from ..services.access_control import CurrentUser

router = APIRouter(prefix="/hpo", tags=["hpo"])


@router.get("/search", response_model=List[HpoTermOut])
async def search_hpo(
    q: str = Query(min_length=1),
    limit: int = Query(default=20, ge=1, le=50),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> List[HpoTermOut]:
    _ = user
    return await search_hpo_terms(session, query=q, limit=limit)


@router.post("/import", response_model=HpoOntologyImportOut)
async def import_hpo(
    request: HpoOntologyImportRequest,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> HpoOntologyImportOut:
    _ = user
    try:
        return await import_hpo_ontology(
            session,
            path=ensure_authorized_hpo_ontology_path(request.path),
            release_version=request.release_version,
            release_date=request.release_date,
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{hpo_id}", response_model=HpoTermDetailOut)
async def get_hpo_term(
    hpo_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> HpoTermDetailOut:
    _ = user
    return await get_hpo_term_details(session, hpo_id=hpo_id)
