import asyncio
from functools import partial
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import get_current_superuser
from ..schemas import (
    FamilyPackageCandidateOut,
    FamilyPackageManifestBuildOut,
    FamilyPackageManifestBuildRequest,
    FamilyPackageImportCreate,
    FamilyPackageImportJobOut,
    FamilyPackageManifestWriteOut,
    FamilyPackageManifestWriteRequest,
    FamilyPackageValidationOut,
    ImportDeliveryAdd,
    ImportDeliveryApprove,
    ImportDeliveryApproveOut,
    ImportDeliveryOut,
    ImportDeliveryPackageOut,
    ImportDeliveryRefuse,
    ImportDeliveryRevise,
    ImportDeliveryRevisedOut,
)
from ..schemas.imports import ImportDeliveryState
from ..services.family_package_import import db_pedigree_fallback
from ..services.family_package_discovery import (
    discover_family_package_manifest,
    write_family_package_manifest,
)
from ..services.family_package_registration import existing_family_sample_ids, manifest_roi_issue
from ..services.family_package_jobs import (
    get_family_import_job,
    list_family_import_jobs,
    queue_family_import_job,
)
from ..services.family_package_source import scan_family_import_packages
from ..services.family_package_validation import validate_family_package
from ..services.family_package_common import manifest_text_problem
from ..services.import_delivery_review import approve_delivery, refuse_delivery
from ..services.import_delivery_revisions import delivery_package, revise_delivery
from ..services.import_manual_delivery import add_manual_delivery
from ..services.import_watcher import list_import_deliveries
from ..services.imported_package_folders import folders_changed_during_import, imported_package_folders
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


@router.post("/manifest/discover", response_model=FamilyPackageManifestBuildOut)
async def discover_family_import_manifest(
    payload: FamilyPackageManifestBuildRequest,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_superuser),
) -> FamilyPackageManifestBuildOut:
    del user
    db_sample_ids = await existing_family_sample_ids(session, payload.family_id)
    return discover_family_package_manifest(payload, db_sample_ids=db_sample_ids)


@router.post("/manifest/write", response_model=FamilyPackageManifestWriteOut)
async def write_family_import_manifest(
    payload: FamilyPackageManifestWriteRequest,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_superuser),
) -> FamilyPackageManifestWriteOut:
    del user
    fallback_ped_text = await db_pedigree_fallback(session, payload.family_id)
    return write_family_package_manifest(
        folder_path=payload.folder_path,
        manifest_yaml=payload.manifest_yaml,
        overwrite=payload.overwrite,
        fallback_ped_text=fallback_ped_text,
    )


@router.post("", response_model=FamilyPackageImportJobOut)
async def start_family_package_import(
    payload: FamilyPackageImportCreate,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_superuser),
) -> FamilyPackageImportJobOut:
    if payload.conflict_mode == "overwrite" and not payload.dry_run and not is_admin_user(user):
        raise HTTPException(status_code=403, detail=OVERWRITE_ADMIN_ONLY_DETAIL)
    return await queue_family_import_job(
        session,
        folder_path=payload.folder_path,
        project_id=payload.project_id,
        dry_run=payload.dry_run,
        requested_family_id=payload.family_id,
        conflict_mode=payload.conflict_mode,
        requested_by=user,
        manifest_text=payload.manifest_yaml,
        expected_package_fingerprint=payload.expected_package_fingerprint,
    )


@router.post("/validate", response_model=FamilyPackageValidationOut)
async def validate_family_import_package(
    payload: FamilyPackageImportCreate,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_superuser),
) -> FamilyPackageValidationOut:
    del user
    # When targeting an existing family, fall back to its database-configured
    # pedigree so a PED file is not required to validate the package.
    fallback_ped_text = await db_pedigree_fallback(session, payload.family_id)
    # Offloaded: validating an s3:// source stages (downloads) the package, which
    # would otherwise block the event loop. A manifest the request gives is read instead of
    # the folder's own, as the import reads the one its job keeps.
    validation = await asyncio.to_thread(
        partial(
            validate_family_package,
            payload.folder_path,
            fallback_ped_text=fallback_ped_text,
            manifest_text=payload.manifest_yaml,
        )
    )
    # The manifest's region of interest, looked up as the import will (DATA-10).
    roi_issue = await manifest_roi_issue(
        session,
        roi_query=validation.metadata.get("roi"),
        project_id=payload.project_id,
        family_id=validation.family_id or payload.family_id,
    )
    if roi_issue is not None:
        validation = validation.model_copy(update={"warnings": [*validation.warnings, roi_issue]})
    return validation


@router.get("/packages", response_model=list[FamilyPackageCandidateOut])
async def list_available_family_packages(
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_superuser),
) -> list[FamilyPackageCandidateOut]:
    # A superuser and an administrator see every project's families, so the families imported
    # from a folder tell the caller nothing they may not know.
    del user
    # Offloaded: scanning an s3:// root makes blocking network calls.
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
    """Correct the package of a delivery that is ready or needs attention, with a reason the audit
    trail keeps; the delivery is checked again at once (import_delivery_revisions.py)."""
    reason = payload.reason.strip()
    if not reason or manifest_text_problem(reason) is not None:
        raise HTTPException(status_code=400, detail="Give a reason in plain text to correct the delivery.")
    if manifest_text_problem(json.dumps([change.model_dump() for change in payload.changes], ensure_ascii=False)) is not None:
        raise HTTPException(status_code=400, detail="A correction holds a NUL or a lone surrogate, which CoGA cannot keep.")
    return await revise_delivery(
        session, delivery_id=delivery_id, user=user, revision=payload.revision, changes=payload.changes, reason=reason
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
