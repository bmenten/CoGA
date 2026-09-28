from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.sql import is_missing_postgres_schema_error
from ..core.postgres import get_postgres_session
from ..dependencies import get_current_admin_user, get_current_user
from ..schemas import (
    FamilyOut,
    FamilyMemberBatchUpdate,
    FamilyMemberBatchUpdateOut,
    FamilyMemberDeleteOut,
    FamilyMemberDetailOut,
    FamilyMemberImpactOut,
    FamilyMemberUpdate,
    FamilyMemberUpdateOut,
    FamilyMetadataUpdate,
    FamilyRegionOfInterestUpdate,
    FamilyStructureUpdate,
    FamilyStructureUpdateOut,
    FamilyPhenotypeMatchOut,
    HpoAnnotationCreate,
    HpoAnnotationOut,
    HpoAnnotationUpdate,
    HpoFamilyQueryOut,
    PhenotypeMatchResultOut,
)
from ..services.family_metadata_context import build_family_metadata_context
from ..services.family_service import (
    get_family_for_user,
    list_families_for_user,
    update_family_roi_for_admin,
)
from ..services.family_member_management_service import (
    delete_family_member_for_admin,
    get_family_member_detail_for_user,
    get_family_member_impact_for_user,
    update_family_members_batch_for_admin,
    update_family_member_for_admin,
)
from ..services.family_status_service import update_family_metadata_for_user
from ..services.family_structure_service import update_family_structure_for_admin
from ..services.hpo_service import (
    create_individual_hpo_annotation,
    delete_individual_hpo_annotation,
    list_family_hpo_annotations,
    query_family_hpo_annotations,
    update_individual_hpo_annotation,
)
from ..services.monarch_ingest import gene_phenotype_breakdown, phenotype_closure
from ..services.monarch_semsim import (
    DEFAULT_GROUP,
    DEFAULT_LIMIT,
    MAX_LIMIT,
    SEMSIM_GROUPS,
    MonarchSemsimError,
    semsim_search,
)
from ..services.access_control import CurrentUser
from ..services.bed_service import precompute_family_lineage_safe
from ..services.clickhouse_family_variants import precompute_family_ranking_safe

router = APIRouter(prefix="/families", tags=["families"])


async def _raise_metadata_schema_error_if_needed(
    session: AsyncSession,
    exc: DBAPIError,
) -> None:
    if not is_missing_postgres_schema_error(exc):
        return
    await session.rollback()
    raise HTTPException(
        status_code=503,
        detail=(
            "Family metadata database schema is not available. Restart the backend or apply "
            "the latest Postgres schema so family relationship and structure-version tables "
            "are created, then retry the metadata update."
        ),
    ) from exc


@router.get("/", response_model=List[FamilyOut])
async def list_families(
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> List[FamilyOut]:
    return await list_families_for_user(session, user)


@router.get("/{family_id}", response_model=FamilyOut)
async def get_family(
    family_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> FamilyOut:
    return await get_family_for_user(session, family_id, user)


@router.put("/{family_id}/metadata", response_model=FamilyOut)
async def update_family_metadata(
    family_id: str,
    update: FamilyMetadataUpdate,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> FamilyOut:
    return await update_family_metadata_for_user(
        session,
        family_id=family_id,
        update=update,
        user=user,
    )


@router.put("/{family_id}/roi", response_model=FamilyOut)
async def update_family_roi(
    family_id: str,
    update: FamilyRegionOfInterestUpdate,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> FamilyOut:
    return await update_family_roi_for_admin(
        session,
        family_id=family_id,
        update=update,
        user=user,
    )


@router.put("/{family_id}/structure", response_model=FamilyStructureUpdateOut)
async def update_family_structure(
    family_id: str,
    update: FamilyStructureUpdate,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> FamilyStructureUpdateOut:
    try:
        return await update_family_structure_for_admin(
            session,
            family_id=family_id,
            update=update,
            user=user,
        )
    except DBAPIError as exc:
        await _raise_metadata_schema_error_if_needed(session, exc)
        raise


@router.put("/{family_id}/members/batch", response_model=FamilyMemberBatchUpdateOut)
async def update_family_members_batch(
    family_id: str,
    update: FamilyMemberBatchUpdate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> FamilyMemberBatchUpdateOut:
    try:
        result = await update_family_members_batch_for_admin(
            session,
            family_id=family_id,
            update=update,
            user=user,
        )
        # Roles / affected status drive the haplotype lineage colours; refresh the
        # precomputed genome-overview lineage in the background (the hash guard keeps
        # the overview safe-but-grey until the new precompute lands).
        background_tasks.add_task(precompute_family_lineage_safe, family_id, user)
        background_tasks.add_task(precompute_family_ranking_safe, family_id, user)
        return result
    except DBAPIError as exc:
        await _raise_metadata_schema_error_if_needed(session, exc)
        raise


@router.get("/{family_id}/members/{sample_id}", response_model=FamilyMemberDetailOut)
async def get_family_member_detail(
    family_id: str,
    sample_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> FamilyMemberDetailOut:
    return await get_family_member_detail_for_user(
        session,
        family_id=family_id,
        sample_id=sample_id,
        user=user,
    )


@router.get("/{family_id}/members/{sample_id}/impact", response_model=FamilyMemberImpactOut)
async def get_family_member_impact(
    family_id: str,
    sample_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> FamilyMemberImpactOut:
    return await get_family_member_impact_for_user(
        session,
        family_id=family_id,
        sample_id=sample_id,
        user=user,
    )


@router.put("/{family_id}/members/{sample_id}", response_model=FamilyMemberUpdateOut)
async def update_family_member(
    family_id: str,
    sample_id: str,
    update: FamilyMemberUpdate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> FamilyMemberUpdateOut:
    try:
        result = await update_family_member_for_admin(
            session,
            family_id=family_id,
            sample_id=sample_id,
            update=update,
            user=user,
        )
        # Role / affected status feed the haplotype lineage colours — refresh the
        # precomputed genome-overview lineage in the background.
        background_tasks.add_task(precompute_family_lineage_safe, family_id, user)
        background_tasks.add_task(precompute_family_ranking_safe, family_id, user)
        return result
    except DBAPIError as exc:
        await _raise_metadata_schema_error_if_needed(session, exc)
        raise


@router.delete("/{family_id}/members/{sample_id}", response_model=FamilyMemberDeleteOut)
async def delete_family_member(
    family_id: str,
    sample_id: str,
    background_tasks: BackgroundTasks,
    confirm: bool = Query(False),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> FamilyMemberDeleteOut:
    try:
        result = await delete_family_member_for_admin(
            session,
            family_id=family_id,
            sample_id=sample_id,
            confirmed=confirm,
            user=user,
        )
        # Removing a member changes the pedigree — refresh the precomputed
        # genome-overview lineage in the background.
        background_tasks.add_task(precompute_family_lineage_safe, family_id, user)
        background_tasks.add_task(precompute_family_ranking_safe, family_id, user)
        return result
    except DBAPIError as exc:
        await _raise_metadata_schema_error_if_needed(session, exc)
        raise


@router.get("/{family_id}/hpo", response_model=List[HpoAnnotationOut])
async def list_family_hpo(
    family_id: str,
    sample_id: str | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> List[HpoAnnotationOut]:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
    )
    return await list_family_hpo_annotations(
        session,
        family_uuid=context.family_uuid,
        sample_id=sample_id,
    )


@router.get("/{family_id}/hpo/query", response_model=HpoFamilyQueryOut)
async def query_family_hpo(
    family_id: str,
    hpo_id: str = Query(min_length=1),
    include_descendants: bool = True,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> HpoFamilyQueryOut:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
    )
    return await query_family_hpo_annotations(
        session,
        family_uuid=context.family_uuid,
        hpo_id=hpo_id,
        include_descendants=include_descendants,
    )


@router.get("/{family_id}/phenotype-match", response_model=FamilyPhenotypeMatchOut)
async def family_phenotype_match(
    family_id: str,
    sample_id: str | None = None,
    group: str = Query(default=DEFAULT_GROUP),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> FamilyPhenotypeMatchOut:
    """Rank candidate genes/diseases by phenotypic similarity to observed HPO terms.

    Uses the Monarch Initiative semsim service against the family's (or a single
    member's) `present` HPO annotations. Genes are linkable to the gene profile,
    closing the loop with the gene->disease and disease->phenotype views.
    """
    if group not in SEMSIM_GROUPS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported group. Choose one of: {', '.join(SEMSIM_GROUPS)}",
        )
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
    )
    annotations = await list_family_hpo_annotations(
        session,
        family_uuid=context.family_uuid,
        sample_id=sample_id,
    )
    present = sorted(
        {row["hpo_id"] for row in annotations if row.get("status") == "present"}
    )
    if not present:
        return FamilyPhenotypeMatchOut(
            group=group, sample_id=sample_id, query_hpo_ids=[], results=[]
        )

    try:
        raw_results = await semsim_search(present, group=group, limit=limit)
    except MonarchSemsimError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Monarch phenotype matching is unavailable: {exc}",
        ) from exc

    # For gene results, flag which symbols exist in this platform so the UI can link
    # straight to the gene profile (where Monarch gene->disease + overlap render).
    gene_symbols = {
        str(r.get("name"))
        for r in raw_results
        if (r.get("category") or "").endswith("Gene") and r.get("name")
    }
    in_platform: set[str] = set()
    if gene_symbols:
        platform_result = await session.execute(
            text(
                """
                SELECT DISTINCT hgnc_symbol
                FROM genes
                WHERE hgnc_symbol = ANY(:symbols)
                """
            ),
            {"symbols": list(gene_symbols)},
        )
        in_platform = {row["hgnc_symbol"] for row in platform_result.mappings().all()}

    # Enrich each gene with which of its Monarch phenotypes the family exhibits
    # (matching) versus the rest (extra), using the local Monarch tables. The match
    # is against the ancestor closure of the family's present terms, so a general
    # gene phenotype counts when the family has it or a more specific descendant.
    observed_closure = await phenotype_closure(session, present)
    breakdown = await gene_phenotype_breakdown(
        session, symbols=gene_symbols, observed_closure=observed_closure
    )

    results = []
    for entry in raw_results:
        is_gene = (entry.get("category") or "").endswith("Gene")
        symbol = entry.get("name") if is_gene else None
        gene_terms = breakdown.get(symbol.upper(), {}) if symbol else {}
        results.append(
            PhenotypeMatchResultOut(
                rank=entry["rank"],
                score=entry.get("score"),
                id=entry["id"],
                name=entry["name"],
                category=entry.get("category"),
                symbol=symbol,
                gene_in_platform=bool(symbol and symbol in in_platform),
                matching_phenotypes=gene_terms.get("matching", []),
                extra_phenotypes=gene_terms.get("extra", []),
            )
        )

    return FamilyPhenotypeMatchOut(
        group=group,
        sample_id=sample_id,
        query_hpo_ids=present,
        results=results,
    )


@router.post("/{family_id}/members/{sample_id}/hpo", response_model=HpoAnnotationOut)
async def create_family_member_hpo(
    family_id: str,
    sample_id: str,
    annotation: HpoAnnotationCreate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> HpoAnnotationOut:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
    )
    result = await create_individual_hpo_annotation(
        session,
        family_uuid=context.family_uuid,
        sample_id=sample_id,
        payload=annotation,
    )
    # Phenotypes changed: warm the prioritised-ranking cache so the next open is fast.
    background_tasks.add_task(precompute_family_ranking_safe, family_id, user)
    return result


@router.put("/{family_id}/hpo/{annotation_id}", response_model=HpoAnnotationOut)
async def update_family_hpo(
    family_id: str,
    annotation_id: str,
    annotation: HpoAnnotationUpdate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> HpoAnnotationOut:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
    )
    result = await update_individual_hpo_annotation(
        session,
        family_uuid=context.family_uuid,
        annotation_id=annotation_id,
        payload=annotation,
    )
    background_tasks.add_task(precompute_family_ranking_safe, family_id, user)
    return result


@router.delete("/{family_id}/hpo/{annotation_id}", status_code=204)
async def delete_family_hpo(
    family_id: str,
    annotation_id: str,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> Response:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
    )
    await delete_individual_hpo_annotation(
        session,
        family_uuid=context.family_uuid,
        annotation_id=annotation_id,
    )
    background_tasks.add_task(precompute_family_ranking_safe, family_id, user)
    return Response(status_code=204)


# --- Sub-resource routers -----------------------------------------------------
# The small-variant / structural-variant / NIPT / reports / track endpoints were
# extracted into sibling modules for maintainability. They mount here under the
# same prefix ("/families") so every route path is unchanged.
from . import (  # noqa: E402
    families_nipt,
    families_reports,
    families_small_variants,
    families_structural_variants,
    families_tracks,
)

# Re-exported so `from ...routers.families import _FAMILY_EXPORT_COLUMNS` (a test) keeps resolving.
from .families_small_variants import _FAMILY_EXPORT_COLUMNS, _family_export_cell  # noqa: E402,F401

for _sub_router in (
    families_small_variants.router,
    families_structural_variants.router,
    families_nipt.router,
    families_reports.router,
    families_tracks.router,
):
    router.include_router(_sub_router, tags=["families"])
