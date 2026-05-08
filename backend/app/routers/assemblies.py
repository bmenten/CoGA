from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import (
    get_current_admin_user,
)
from ..schemas import (
    AssemblyCreate,
    AssemblyOut,
    ReferenceAutoImportRequest,
    ReferenceAutoImportResult,
    ReferenceImportSourceAssemblyOut,
    ReferenceImportSourceOrganismOut,
    AssemblyReferenceStatusOut,
    ReferenceUploadResult,
)
from ..services.metadata_service import (
    CurrentUser,
    create_assembly_record,
    list_assembly_records,
)
from ..services.reference_metadata_service import (
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
) -> List[AssemblyOut]:
    return await list_assembly_records(session)


@router.get("/reference-status", response_model=List[AssemblyReferenceStatusOut])
async def list_all_reference_statuses(
    session: AsyncSession = Depends(get_postgres_session),
) -> List[AssemblyReferenceStatusOut]:
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
    )


@router.get("/{species_id}", response_model=List[AssemblyOut])
async def list_assemblies(
    species_id: str,
    session: AsyncSession = Depends(get_postgres_session),
) -> List[AssemblyOut]:
    try:
        UUID(species_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid species id") from exc
    return await list_assembly_records(session, species_id=species_id)


@router.post("/", response_model=AssemblyOut, status_code=201)
async def create_assembly(
    assembly_in: AssemblyCreate,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> AssemblyOut:
    try:
        UUID(assembly_in.species_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid species id") from exc
    return await create_assembly_record(
        session,
        species_id=assembly_in.species_id,
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
    if dataset_type not in {"cytobands", "genes", "blacklist", "clinical_cnvs"}:
        raise HTTPException(status_code=400, detail="Invalid reference dataset type")

    return await upload_reference_dataset(
        session,
        assembly_id=assembly_id,
        dataset_type=dataset_type,  # type: ignore[arg-type]
        file=file,
        overwrite=overwrite,
    )
