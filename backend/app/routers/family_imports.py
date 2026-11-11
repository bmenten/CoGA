import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import get_current_superuser
from ..schemas import (
    FamilyPackageCandidateOut,
    FamilyPackageImportJobOut,
    ImportDeliveryAdd,
    ImportDeliveryApprove,
    ImportDeliveryApproveOut,
    ImportDeliveryArchive,
    ImportDeliveryOut,
    ImportDeliveryPackageOut,
    ImportDeliveryRefuse,
    ImportDeliveryRevise,
    ImportDeliveryRevisedOut,
)
from ..schemas.imports import ImportDeliveryState
from ..services.family_package_jobs import (
    get_family_import_job,
    list_family_import_jobs,
)
from ..services.family_package_source import scan_family_import_packages
from ..services.family_package_common import manifest_text_problem
from ..services.import_delivery_review import approve_delivery, refuse_delivery
from ..services.import_delivery_revisions import delivery_package, revise_delivery
from ..services.import_manual_delivery import add_manual_delivery
from ..services.import_watcher import list_import_deliveries
from ..services.imported_package_folders import folders_changed_during_import, imported_package_folders
from ..services.package_changed_after_import import archive_gone_delivery
from ..services.access_control import CurrentUser, is_admin_user

router = APIRouter(prefix="/family-imports", tags=["family_imports"])

# Overwriting an existing family replaces its data: an admin's, as every replacement and deletion
# of a family's data is (REQ-SEC-002). A superuser imports new families and adds missing datasets.
OVERWRITE_ADMIN_ONLY_DETAIL = (
    "Only an administrator can overwrite an existing family: import with update to add the "
    "missing datasets, or ask an administrator."
)


@router.get("", response_model=list[FamilyPackageImportJobOut])
async def list_family_package_import_jobs(
    limit: int = 25,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_superuser),
) -> list[FamilyPackageImportJobOut]:
    return await list_family_import_jobs(
        session,
        user=user,
        limit=max(1, min(limit, 100)),
    )


@router.get("/packages", response_model=list[FamilyPackageCandidateOut])
async def list_available_family_packages(
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_superuser),
) -> list[FamilyPackageCandidateOut]:
    # A superuser and an administrator see every project's families, so the families imported
    # from a folder tell the caller nothing they may not know.
    del user
    # Offloaded: it lists the import folders, which may be a network share or a mounted bucket.
    packages = await asyncio.to_thread(scan_family_import_packages)
    folders = [str(package["folder_path"]) for package in packages]
    imported = await imported_package_folders(session, folders)
    changed = await folders_changed_during_import(session, folders)
    return [
        FamilyPackageCandidateOut(
            **package,
            imported_families=imported.get(str(package["folder_path"]), []),
            changed_during_import=str(package["folder_path"]) in changed,
        )
        for package in packages
    ]


@router.get("/deliveries", response_model=list[ImportDeliveryOut])
async def list_family_package_deliveries(
    state: list[ImportDeliveryState] | None = Query(default=None),
    limit: int = 100,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_superuser),
) -> list[ImportDeliveryOut]:
    """The packages pipelines delivered into the watched import folders, the latest change first;
    ``state`` (repeatable) keeps those in one of the states named."""
    del user
    return await list_import_deliveries(session, states=list(state) if state else None, limit=limit)


@router.post("/deliveries", response_model=ImportDeliveryOut)
async def add_family_package_delivery(
    payload: ImportDeliveryAdd,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_superuser),
) -> ImportDeliveryOut:
    """Add a package folder to Deliveries by hand: CoGA makes its package from the folder (members
    and datasets as Discover proposes them) and checks it as a pipeline's delivery; it is then
    corrected, approved or refused the same way (import_manual_delivery.py)."""
    return await add_manual_delivery(
        session,
        folder_path=payload.folder_path,
        project_id=payload.project_id,
        family_id=payload.family_id,
        analysis_type=payload.analysis_type,
        user=user,
    )


@router.post("/deliveries/{delivery_id}/approve", response_model=ImportDeliveryApproveOut)
async def approve_family_package_delivery(
    delivery_id: str,
    payload: ImportDeliveryApprove,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_superuser),
) -> ImportDeliveryApproveOut:
    """Queue the import of a delivery that is ready, or whose family exists (update or overwrite),
    as a dry run or for real, as the caller (import_delivery_review.py)."""
    if payload.conflict_mode == "overwrite" and not payload.dry_run and not is_admin_user(user):
        raise HTTPException(status_code=403, detail=OVERWRITE_ADMIN_ONLY_DETAIL)
    delivery, job = await approve_delivery(
        session,
        delivery_id=delivery_id,
        user=user,
        revision=payload.revision,
        dry_run=payload.dry_run,
        conflict_mode=payload.conflict_mode,
        acknowledge_warnings=payload.acknowledge_warnings,
    )
    return ImportDeliveryApproveOut(delivery=delivery, job=job)


@router.get("/deliveries/{delivery_id}/package", response_model=ImportDeliveryPackageOut)
async def get_family_package_delivery_package(
    delivery_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_superuser),
) -> ImportDeliveryPackageOut:
    """A delivery's package as its import would read it now, the pipeline's file, what CoGA
    proposed, and every correction made in CoGA (import_delivery_revisions.py)."""
    del user
    return await delivery_package(session, delivery_id)


@router.post("/deliveries/{delivery_id}/revisions", response_model=ImportDeliveryRevisedOut)
async def revise_family_package_delivery(
    delivery_id: str,
    payload: ImportDeliveryRevise,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_superuser),
) -> ImportDeliveryRevisedOut:
    """Correct the package of a delivery that is ready or needs attention, with its changes or the
    whole package as edited, and a reason the audit trail keeps; the delivery is checked again at
    once (import_delivery_revisions.py)."""
    reason = payload.reason.strip()
    if not reason or manifest_text_problem(reason) is not None:
        raise HTTPException(status_code=400, detail="Give a reason in plain text to correct the delivery.")
    held = [change.model_dump() for change in payload.changes] if payload.package is None else payload.package
    if manifest_text_problem(json.dumps(held, ensure_ascii=False)) is not None:
        raise HTTPException(status_code=400, detail="A correction holds a NUL or a lone surrogate, which CoGA cannot keep.")
    return await revise_delivery(
        session,
        delivery_id=delivery_id,
        user=user,
        revision=payload.revision,
        changes=payload.changes,
        package=payload.package,
        reason=reason,
    )


@router.post("/deliveries/{delivery_id}/refuse", response_model=ImportDeliveryOut)
async def refuse_family_package_delivery(
    delivery_id: str,
    payload: ImportDeliveryRefuse,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_superuser),
) -> ImportDeliveryOut:
    """Refuse an open delivery, with a reason the audit trail keeps."""
    reason = payload.reason.strip()
    if not reason or manifest_text_problem(reason) is not None:
        raise HTTPException(status_code=400, detail="Give a reason in plain text to refuse the delivery.")
    return await refuse_delivery(session, delivery_id=delivery_id, user=user, reason=reason)


@router.post("/deliveries/{delivery_id}/archive", response_model=ImportDeliveryOut)
async def archive_family_package_delivery(
    delivery_id: str,
    payload: ImportDeliveryArchive,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_superuser),
) -> ImportDeliveryOut:
    """Confirm the gone package folder of an imported delivery as deliberately gone (archived), with
    a reason the audit trail keeps: a sign-out is no longer warned of it, and the family page and a
    signed record say the source files are archived (package_changed_after_import.py). 409 unless
    the delivery is changed after import with its folder gone."""
    reason = payload.reason.strip()
    if not reason or manifest_text_problem(reason) is not None:
        raise HTTPException(status_code=400, detail="Give a reason in plain text to confirm the folder as archived.")
    return await archive_gone_delivery(session, delivery_id=delivery_id, user=user, reason=reason)


@router.get("/{job_id}", response_model=FamilyPackageImportJobOut)
async def get_family_package_import_job(
    job_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_superuser),
) -> FamilyPackageImportJobOut:
    return await get_family_import_job(
        session,
        job_id=job_id,
        user=user,
    )
