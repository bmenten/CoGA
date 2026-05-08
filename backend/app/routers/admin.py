from typing import Dict, List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import get_current_admin_user
from ..schemas import (
    AuditLogPageOut,
    ClickHouseVariantAssemblyListOut,
    ClickHouseVariantAssemblyStatusOut,
    FamilyInventoryDetailOut,
    FamilyInventoryPageOut,
    GeneInfoRefreshJobOut,
    GeneReferenceAdminStatusOut,
    ProjectsUpdate,
    SmallVariantFilterPresetOut,
    SmallVariantTagDefinitionCreate,
    SmallVariantTagDefinitionOut,
    SmallVariantTagDefinitionUpdate,
)
from ..services.audit_log_pg import list_audit_log_events
from ..services.admin_service import (
    delete_family_data_by_type,
    delete_family_with_data,
    delete_sample_data_by_type,
    delete_sample_with_data,
    ensure_clickhouse_variant_status,
    get_family_data_inventory_detail,
    list_data_inventory_page,
    list_clickhouse_variant_status,
    optimize_clickhouse_variant_status,
    update_sample_projects_data,
)
from ..services.gene_info_jobs_pg import (
    list_gene_reference_admin_status,
    queue_gene_reference_refresh_job,
)
from ..services.metadata_service import (
    CurrentUser,
    list_family_project_assignments,
    update_family_project_assignments,
)
from ..services.small_variant_review_pg import (
    create_small_variant_tag_definition,
    delete_small_variant_tag_definition,
    list_small_variant_filter_presets_for_admin,
    list_small_variant_tag_definitions,
    update_small_variant_tag_definition,
)

router = APIRouter(prefix="/admin", tags=["admin"])


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
