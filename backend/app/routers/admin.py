import logging
from pathlib import Path
from typing import Dict, List

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import get_current_admin_user
from ..schemas import (
    AuditLogPageOut,
    ClickHouseVariantAssemblyListOut,
    ClickHouseVariantAssemblyStatusOut,
    ClickHouseVariantIntegrityOut,
    ClinicalCnvKbJobOut,
    ClinicalCnvKbRebuildRequest,
    ClinicalCnvKbStatusOut,
    FamilyInventoryDetailOut,
    FamilyInventoryPageOut,
    FamilyRawFilesOut,
    FamilyStatusCreate,
    FamilyStatusOut,
    FamilyStatusUpdate,
    GeneInfoRefreshJobOut,
    GeneReferenceAdminStatusOut,
    HpoAdminSummaryOut,
    HpoAdminTermOut,
    HpoOntologySyncOut,
    HpoOntologySyncRequest,
    MonarchRefreshSummaryOut,
    MonarchSearchOut,
    MonarchStatusOut,
    NiptArtifactAutoSeed,
    NiptArtifactAutoSeedOut,
    NiptArtifactCreate,
    NiptArtifactOut,
    ProjectsUpdate,
    RawImportFileVerifyOut,
    SmallVariantFilterPresetOut,
    SmallVariantTagDefinitionCreate,
    SmallVariantTagDefinitionOut,
    SmallVariantTagDefinitionUpdate,
    UiEventPageOut,
)
from ..services.audit_log_pg import list_audit_log_events
from ..services.ui_event_pg import list_ui_events
from ..services.admin_service import (
    delete_family_data_by_type,
    delete_family_with_data,
    delete_sample_data_by_type,
    delete_sample_with_data,
    ensure_clickhouse_variant_status,
    get_family_data_inventory_detail,
    get_family_raw_files,
    get_raw_import_file_record,
    list_data_inventory_page,
    list_clickhouse_variant_status,
    check_clickhouse_variant_integrity_status,
    optimize_clickhouse_variant_status,
    rebuild_clickhouse_small_variant_gene_index_status,
    update_sample_projects_data,
    verify_raw_import_file_by_id,
)
from ..services.family_status_service import (
    create_family_status,
    delete_family_status,
    list_family_statuses,
    update_family_status,
)
from ..services.clinical_cnv_kb_jobs import (
    get_clinical_cnv_kb_status,
    queue_clinical_cnv_kb_rebuild,
)
from ..services.ped_service import build_pedigree_text
from ..services.panel_metadata_service import regenerate_mendeliome
from ..services.monarch_ingest import (
    monarch_status,
    refresh_monarch,
    search_monarch_associations,
)
from ..services.gene_info_jobs_pg import (
    list_gene_reference_admin_status,
    queue_gene_reference_refresh_job,
)
from ..services.hpo_service import (
    get_hpo_admin_summary,
    list_hpo_admin_terms,
    sync_hpo_ontology,
)
from ..services.metadata_service import (
    CurrentUser,
    list_family_project_assignments,
    update_family_project_assignments,
)
from ..services.nipt_artifact_pg import (
    add_nipt_artifact,
    auto_seed_nipt_artifacts,
    delete_nipt_artifact,
    list_nipt_artifacts,
)
from ..services.small_variant_review_pg import (
    create_small_variant_tag_definition,
    delete_small_variant_tag_definition,
    list_small_variant_filter_presets_for_admin,
    list_small_variant_tag_definitions,
    update_small_variant_tag_definition,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/nipt/artifacts", response_model=List[NiptArtifactOut])
async def list_nipt_artifacts_endpoint(
    assembly_id: str,
    assay_key: str | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> List[NiptArtifactOut]:
    rows = await list_nipt_artifacts(session, assembly_id=assembly_id, assay_key=assay_key)
    return [NiptArtifactOut(**row) for row in rows]


@router.post("/nipt/artifacts", response_model=NiptArtifactOut)
async def add_nipt_artifact_endpoint(
    payload: NiptArtifactCreate,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> NiptArtifactOut:
    row = await add_nipt_artifact(
        session,
        assembly_id=payload.assembly_id,
        assay_key=payload.assay_key,
        variant_id=payload.variant_id,
        label=payload.label,
        source=payload.source,
        recurrence_count=payload.recurrence_count,
        created_by=user.id,
    )
    return NiptArtifactOut(**row)


@router.delete("/nipt/artifacts/{artifact_id}")
async def delete_nipt_artifact_endpoint(
    artifact_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> Dict[str, bool]:
    deleted = await delete_nipt_artifact(session, artifact_id=artifact_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return {"deleted": True}


@router.post("/nipt/artifacts/auto-seed", response_model=NiptArtifactAutoSeedOut)
async def auto_seed_nipt_artifacts_endpoint(
    payload: NiptArtifactAutoSeed,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> NiptArtifactAutoSeedOut:
    result = await auto_seed_nipt_artifacts(
        session,
        assembly_id=payload.assembly_id,
        assay_key=payload.assay_key,
        min_carrier_samples=payload.min_carrier_samples,
        created_by=user.id,
    )
    return NiptArtifactAutoSeedOut(**result)


@router.get("/projects")
async def list_project_assignments(
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> List[Dict]:
    return await list_family_project_assignments(session)


@router.put("/families/{family_id}/projects")
async def update_family_projects(
    family_id: str,
    update: ProjectsUpdate,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> Dict:
    return await update_family_project_assignments(session, family_id, update.project_ids)


@router.put("/samples/{sample_id}/projects")
async def update_sample_projects(
    sample_id: str,
    update: ProjectsUpdate,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> Dict:
    return await update_sample_projects_data(session, sample_id, update)


@router.get("/data", response_model=FamilyInventoryPageOut)
async def list_data(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    search: str | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> FamilyInventoryPageOut:
    return await list_data_inventory_page(
        session,
        page=page,
        page_size=page_size,
        search=search,
    )


@router.get("/clickhouse/variants", response_model=ClickHouseVariantAssemblyListOut)
async def list_clickhouse_variants(
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> ClickHouseVariantAssemblyListOut:
    return await list_clickhouse_variant_status(session)


@router.post(
    "/clickhouse/variants/{assembly_name}/ensure",
    response_model=ClickHouseVariantAssemblyStatusOut,
)
async def ensure_clickhouse_variants(
    assembly_name: str,
    user: CurrentUser = Depends(get_current_admin_user),
) -> ClickHouseVariantAssemblyStatusOut:
    return await ensure_clickhouse_variant_status(assembly_name)


@router.post(
    "/clickhouse/variants/{assembly_name}/optimize",
    response_model=ClickHouseVariantAssemblyStatusOut,
)
async def optimize_clickhouse_variants(
    assembly_name: str,
    final: bool = Query(False),
    user: CurrentUser = Depends(get_current_admin_user),
) -> ClickHouseVariantAssemblyStatusOut:
    return await optimize_clickhouse_variant_status(assembly_name, final=final)


@router.post(
    "/clickhouse/variants/{assembly_name}/rebuild-small-variant-gene-index",
    response_model=ClickHouseVariantAssemblyStatusOut,
)
async def rebuild_clickhouse_small_variant_gene_index(
    assembly_name: str,
    user: CurrentUser = Depends(get_current_admin_user),
) -> ClickHouseVariantAssemblyStatusOut:
    return await rebuild_clickhouse_small_variant_gene_index_status(assembly_name)


@router.get(
    "/clickhouse/variants/{assembly_name}/integrity",
    response_model=ClickHouseVariantIntegrityOut,
)
async def check_clickhouse_variant_integrity_endpoint(
    assembly_name: str,
    user: CurrentUser = Depends(get_current_admin_user),
) -> ClickHouseVariantIntegrityOut:
    return await check_clickhouse_variant_integrity_status(assembly_name)


@router.get("/data/families/{family_id}", response_model=FamilyInventoryDetailOut)
async def get_family_data(
    family_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> FamilyInventoryDetailOut:
    return await get_family_data_inventory_detail(
        session,
        family_id=family_id,
    )


@router.get("/families/{family_id}/ped")
async def download_family_pedigree(
    family_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> Response:
    ped_text = await build_pedigree_text(session, family_id=family_id)
    return Response(
        content=ped_text,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{family_id}.ped"',
        },
    )


@router.get("/data/families/{family_id}/files", response_model=FamilyRawFilesOut)
async def list_family_raw_files(
    family_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> FamilyRawFilesOut:
    return await get_family_raw_files(session, family_id=family_id)


@router.get("/data/files/{file_id}/download")
async def download_raw_import_file(
    file_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> FileResponse:
    record = await get_raw_import_file_record(session, file_id=file_id)
    storage_path = record.get("storage_path") or ""
    if not storage_path or not Path(storage_path).is_file():
        raise HTTPException(
            status_code=410,
            detail="The source file is no longer available at its storage path.",
        )
    return FileResponse(
        path=storage_path,
        filename=record["file_name"],
        media_type="application/octet-stream",
    )


@router.post("/data/files/{file_id}/verify", response_model=RawImportFileVerifyOut)
async def verify_raw_import_file(
    file_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> RawImportFileVerifyOut:
    return await verify_raw_import_file_by_id(session, file_id=file_id)


@router.get(
    "/small-variant-filter-presets",
    response_model=List[SmallVariantFilterPresetOut],
)
async def list_small_variant_filter_presets(
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> List[SmallVariantFilterPresetOut]:
    return await list_small_variant_filter_presets_for_admin(
        session=session,
    )


@router.get(
    "/variant-tags",
    response_model=List[SmallVariantTagDefinitionOut],
)
async def list_variant_tags(
    project_id: str | None = Query(default=None),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> List[SmallVariantTagDefinitionOut]:
    del user
    return await list_small_variant_tag_definitions(
        session,
        family_uuid="",
        project_ids=[],
        project_id=project_id,
        include_all_project_tags=True,
    )


@router.post(
    "/variant-tags",
    response_model=SmallVariantTagDefinitionOut,
)
async def create_variant_tag(
    payload: SmallVariantTagDefinitionCreate,
    project_id: str | None = Query(default=None),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> SmallVariantTagDefinitionOut:
    return await create_small_variant_tag_definition(
        session,
        family_uuid="",
        payload=payload,
        user=user,
        default_project_id=project_id,
    )


@router.put(
    "/variant-tags/{tag_key}",
    response_model=SmallVariantTagDefinitionOut,
)
async def update_variant_tag(
    tag_key: str,
    payload: SmallVariantTagDefinitionUpdate,
    project_id: str | None = Query(default=None),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> SmallVariantTagDefinitionOut:
    return await update_small_variant_tag_definition(
        session,
        family_uuid="",
        tag_key=tag_key,
        payload=payload,
        user=user,
        default_project_id=project_id,
    )


@router.delete("/variant-tags/{tag_key}", status_code=204)
async def delete_variant_tag(
    tag_key: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> None:
    await delete_small_variant_tag_definition(
        session,
        family_uuid="",
        tag_key=tag_key,
        user=user,
    )


@router.get("/family-statuses", response_model=List[FamilyStatusOut])
async def list_family_statuses_admin(
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> List[FamilyStatusOut]:
    del user
    return await list_family_statuses(session, include_inactive=True)


@router.post("/family-statuses", response_model=FamilyStatusOut, status_code=201)
async def create_family_status_admin(
    payload: FamilyStatusCreate,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> FamilyStatusOut:
    return await create_family_status(session, payload=payload, user=user)


@router.put("/family-statuses/{status_key}", response_model=FamilyStatusOut)
async def update_family_status_admin(
    status_key: str,
    payload: FamilyStatusUpdate,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> FamilyStatusOut:
    del user
    return await update_family_status(session, key=status_key, payload=payload)


@router.delete("/family-statuses/{status_key}", status_code=204)
async def delete_family_status_admin(
    status_key: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> None:
    del user
    await delete_family_status(session, key=status_key)


@router.delete("/data/samples/{sample_id}/{data_type}")
async def delete_sample_data(
    sample_id: str,
    data_type: str,
    confirm: bool = False,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> Dict:
    return await delete_sample_data_by_type(
        session,
        sample_id,
        data_type,
        confirm,
    )


@router.delete("/data/families/{family_id}/{data_type}")
async def delete_family_data(
    family_id: str,
    data_type: str,
    confirm: bool = False,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> Dict:
    return await delete_family_data_by_type(
        session,
        family_id,
        data_type,
        confirm,
    )


@router.delete("/samples/{sample_id}")
async def delete_sample(
    sample_id: str,
    confirm: bool = False,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> Dict:
    return await delete_sample_with_data(
        session,
        sample_id,
        confirm,
    )


@router.delete("/families/{family_id}")
async def delete_family(
    family_id: str,
    confirm: bool = False,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> Dict:
    return await delete_family_with_data(
        session,
        family_id,
        confirm,
    )


@router.get("/gene-reference/status", response_model=GeneReferenceAdminStatusOut)
async def get_gene_reference_status(
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> GeneReferenceAdminStatusOut:
    return await list_gene_reference_admin_status(
        session=session,
    )


@router.get("/audit-logs", response_model=AuditLogPageOut)
async def list_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    method: str | None = Query(default=None),
    status_code: int | None = Query(default=None, ge=100, le=599),
    user_email: str | None = Query(default=None),
    path_contains: str | None = Query(default=None),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> AuditLogPageOut:
    return await list_audit_log_events(
        session=session,
        page=page,
        page_size=page_size,
        method=method,
        status_code=status_code,
        user_email=user_email,
        path_contains=path_contains,
    )


@router.get("/ui-events", response_model=UiEventPageOut)
async def list_ui_event_log(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    event_type: str | None = Query(default=None),
    user_email: str | None = Query(default=None),
    path_contains: str | None = Query(default=None),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> UiEventPageOut:
    del user
    return await list_ui_events(
        session=session,
        page=page,
        page_size=page_size,
        event_type=event_type,
        user_email=user_email,
        path_contains=path_contains,
    )


@router.get("/hpo/summary", response_model=HpoAdminSummaryOut)
async def get_hpo_summary(
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> HpoAdminSummaryOut:
    del user
    return await get_hpo_admin_summary(session)


@router.get("/hpo/terms", response_model=List[HpoAdminTermOut])
async def list_hpo_terms_for_admin(
    q: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> List[HpoAdminTermOut]:
    del user
    return await list_hpo_admin_terms(session, query=q, limit=limit)


@router.post("/hpo/sync", response_model=HpoOntologySyncOut)
async def sync_hpo_terms(
    payload: HpoOntologySyncRequest,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> HpoOntologySyncOut:
    del user
    try:
        return await sync_hpo_ontology(
            session,
            path=payload.path,
            release_version=payload.release_version,
            release_date=payload.release_date,
            preview_only=payload.preview_only,
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/gene-reference/refresh-all", response_model=GeneInfoRefreshJobOut)
async def refresh_all_gene_reference(
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> GeneInfoRefreshJobOut:
    return await queue_gene_reference_refresh_job(
        session=session,
        scope="all_human",
        requested_by=user.email,
    )


@router.post("/gene-reference/refresh-gene", response_model=GeneInfoRefreshJobOut)
async def refresh_gene_reference(
    symbol: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> GeneInfoRefreshJobOut:
    return await queue_gene_reference_refresh_job(
        session=session,
        scope="symbol",
        symbol=symbol,
        requested_by=user.email,
    )


@router.get("/monarch/status", response_model=MonarchStatusOut)
async def get_monarch_status(
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> MonarchStatusOut:
    """Return the currently loaded Monarch release and table sizes."""
    del user
    return MonarchStatusOut(**await monarch_status(session))


@router.get("/monarch/search", response_model=MonarchSearchOut)
async def search_monarch(
    q: str = Query(default="", description="Disease name, MONDO id, phenotype name, or HP id"),
    limit: int = Query(default=25, ge=1, le=100),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> MonarchSearchOut:
    """Search Monarch diseases/phenotypes and return their linked genes and phenotypes."""
    del user
    return MonarchSearchOut(
        **await search_monarch_associations(session, query=q, limit=limit)
    )


@router.post("/monarch/refresh", response_model=MonarchRefreshSummaryOut)
async def refresh_monarch_associations(
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> MonarchRefreshSummaryOut:
    """Download the latest Monarch associations and replace the Monarch tables.

    Refreshes both gene -> disease and disease -> phenotype. Runs inline: the
    source files are small (a few MB gzipped) so the full refresh completes in
    seconds, unlike the per-gene gene-reference sync.
    """
    try:
        summary = await refresh_monarch(session)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to download Monarch data: {exc}",
        ) from exc
    # A new Monarch release means a new Mendeliome: re-version the generated panel
    # (a no-op if the gene set is unchanged). Best-effort — never fail the refresh.
    try:
        await regenerate_mendeliome(session, user)
    except Exception:  # noqa: BLE001
        logger.warning("Mendeliome regeneration after Monarch refresh failed", exc_info=True)
    return MonarchRefreshSummaryOut(**summary)


@router.get("/clinical-cnv-kb/status", response_model=ClinicalCnvKbStatusOut)
async def get_clinical_cnv_kb_rebuild_status(
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> ClinicalCnvKbStatusOut:
    return await get_clinical_cnv_kb_status(session)


@router.post("/clinical-cnv-kb/rebuild", response_model=ClinicalCnvKbJobOut)
async def rebuild_clinical_cnv_kb(
    request: ClinicalCnvKbRebuildRequest,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> ClinicalCnvKbJobOut:
    return await queue_clinical_cnv_kb_rebuild(
        session,
        assembly=request.assembly,
        skip_clinvar=request.skip_clinvar,
        requested_by=user.email,
    )
