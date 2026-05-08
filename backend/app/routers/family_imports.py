from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import get_current_admin_user
from ..schemas import (
    FamilyPackageManifestBuildOut,
    FamilyPackageManifestBuildRequest,
    FamilyPackageImportCreate,
    FamilyPackageImportJobOut,
    FamilyPackageManifestWriteOut,
    FamilyPackageManifestWriteRequest,
    FamilyPackageValidationOut,
)
from ..services.family_package_import import (
    discover_family_package_manifest,
    get_family_import_job,
    list_family_import_jobs,
    queue_family_import_job,
    validate_family_package,
    write_family_package_manifest,
)
from ..services.metadata_service import CurrentUser

router = APIRouter(prefix="/family-imports", tags=["family_imports"])


@router.get("", response_model=list[FamilyPackageImportJobOut])
async def list_family_package_import_jobs(
    limit: int = 25,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> list[FamilyPackageImportJobOut]:
    return await list_family_import_jobs(
        session,
        user=user,
        limit=max(1, min(limit, 100)),
    )


@router.post("/manifest/discover", response_model=FamilyPackageManifestBuildOut)
async def discover_family_import_manifest(
    payload: FamilyPackageManifestBuildRequest,
    user: CurrentUser = Depends(get_current_admin_user),
) -> FamilyPackageManifestBuildOut:
    del user
    return discover_family_package_manifest(payload)


@router.post("/manifest/write", response_model=FamilyPackageManifestWriteOut)
async def write_family_import_manifest(
    payload: FamilyPackageManifestWriteRequest,
    user: CurrentUser = Depends(get_current_admin_user),
) -> FamilyPackageManifestWriteOut:
    del user
    return write_family_package_manifest(
        folder_path=payload.folder_path,
        manifest_yaml=payload.manifest_yaml,
        overwrite=payload.overwrite,
    )


@router.post("", response_model=FamilyPackageImportJobOut)
async def start_family_package_import(
    payload: FamilyPackageImportCreate,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> FamilyPackageImportJobOut:
    return await queue_family_import_job(
        session,
        folder_path=payload.folder_path,
        project_id=payload.project_id,
        dry_run=payload.dry_run,
        requested_family_id=payload.family_id,
        conflict_mode=payload.conflict_mode,
        requested_by=user.email,
    )


@router.post("/validate", response_model=FamilyPackageValidationOut)
async def validate_family_import_package(
    payload: FamilyPackageImportCreate,
    user: CurrentUser = Depends(get_current_admin_user),
) -> FamilyPackageValidationOut:
    del user
    return validate_family_package(payload.folder_path)


@router.get("/{job_id}", response_model=FamilyPackageImportJobOut)
async def get_family_package_import_job(
    job_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> FamilyPackageImportJobOut:
    return await get_family_import_job(
        session,
        job_id=job_id,
        user=user,
    )
