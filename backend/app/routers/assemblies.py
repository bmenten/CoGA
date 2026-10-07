from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..core.sql import require_uuid
from ..dependencies import (
    get_current_admin_user,
    get_current_user,
)
from ..schemas import (
    AssemblyCreate,
    AssemblyOut,
    ReferenceAutoImportRequest,
    ReferenceAutoImportResult,
    ReferenceImportActivityOut,
    ReferenceImportSourceAssemblyOut,
    ReferenceImportSourceOrganismOut,
    AssemblyReferenceStatusOut,
    ReferenceUploadResult,
)
from ..services.metadata_service import create_assembly_record, list_assembly_records
from ..services.access_control import CurrentUser
from ..services.reference_metadata_service import (
    list_recent_reference_imports,
    list_reference_statuses,
    upload_reference_dataset,
)
from ..services.reference_source_service import (
    import_reference_from_ucsc,
    list_reference_source_assemblies,
    list_reference_source_organisms,
)

router = APIRouter(prefix="/assemblies", tags=["assemblies"])


@router.get("/", response_model=List[AssemblyOut])
async def list_all_assemblies(
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> List[AssemblyOut]:
    del user
    return await list_assembly_records(session)


@router.get("/reference-status", response_model=List[AssemblyReferenceStatusOut])
async def list_all_reference_statuses(
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> List[AssemblyReferenceStatusOut]:
    # Requires authentication: the response embeds import provenance (source +
    # performed-by operator email) that must not be exposed unauthenticated. Any
    # signed-in user may read it (the reference-data page is not admin-only).
    del user
    return await list_reference_statuses(session)


@router.get(
    "/reference-import/organisms",
    response_model=List[ReferenceImportSourceOrganismOut],
)
async def list_reference_import_organisms(
    user: CurrentUser = Depends(get_current_admin_user),
) -> List[ReferenceImportSourceOrganismOut]:
    return await list_reference_source_organisms()


@router.get(
    "/reference-import/assemblies",
    response_model=List[ReferenceImportSourceAssemblyOut],
)
async def list_reference_import_assemblies(
    tax_id: int,
    user: CurrentUser = Depends(get_current_admin_user),
) -> List[ReferenceImportSourceAssemblyOut]:
    return await list_reference_source_assemblies(tax_id=tax_id)


@router.post(
    "/reference-import",
    response_model=ReferenceAutoImportResult,
)
async def import_reference_data(
    request: ReferenceAutoImportRequest,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> ReferenceAutoImportResult:
    return await import_reference_from_ucsc(
        session,
        tax_id=request.tax_id,
        ucsc_genome=request.ucsc_genome,
        overwrite=request.overwrite,
        performed_by=user.email,
    )


@router.get(
    "/reference-import/recent",
    response_model=List[ReferenceImportActivityOut],
)
async def list_recent_reference_activity(
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> List[ReferenceImportActivityOut]:
    del user
    return await list_recent_reference_imports(session, limit=limit)


@router.get("/{species_id}", response_model=List[AssemblyOut])
async def list_assemblies(
    species_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> List[AssemblyOut]:
    del user
    species_uuid = require_uuid(species_id, "Invalid species id")
    return await list_assembly_records(session, species_id=species_uuid)


@router.post("/", response_model=AssemblyOut, status_code=201)
async def create_assembly(
    assembly_in: AssemblyCreate,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> AssemblyOut:
    species_uuid = require_uuid(assembly_in.species_id, "Invalid species id")
    return await create_assembly_record(
        session,
        species_id=species_uuid,
        assembly_name=assembly_in.assembly_name,
        version=assembly_in.version,
        release_date=assembly_in.release_date,
    )


@router.post(
    "/{assembly_id}/reference-upload/{dataset_type}",
    response_model=ReferenceUploadResult,
)
async def upload_reference_data(
    assembly_id: str,
    dataset_type: str,
    file: UploadFile = File(...),
    overwrite: bool = False,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> ReferenceUploadResult:
    if dataset_type not in {
        "cytobands",
        "genes",
        "blacklist",
        "clinical_cnvs",
        "segmental_duplications",
        "dgv",
    }:
        raise HTTPException(status_code=400, detail="Invalid reference dataset type")

    return await upload_reference_dataset(
        session,
        assembly_id=assembly_id,
        dataset_type=dataset_type,  # type: ignore[arg-type]
        file=file,
        overwrite=overwrite,
        performed_by=user.email,
    )
