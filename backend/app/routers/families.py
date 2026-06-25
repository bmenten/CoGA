import csv
import io
from typing import Any, Dict, List

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.sql import is_missing_postgres_schema_error
from ..core.postgres import get_postgres_session
from ..dependencies import get_current_admin_user, get_current_user
from ..schemas import (
    FamilyOut,
    FamilyMitoDNAAnalysisOut,
    FamilyMemberBatchUpdate,
    FamilyMemberBatchUpdateOut,
    FamilyMemberDeleteOut,
    FamilyMemberDetailOut,
    FamilyMemberImpactOut,
    FamilyMemberUpdate,
    FamilyMemberUpdateOut,
    FamilyMetadataUpdate,
    FamilyParaphaseTableOut,
    FamilyRepeatExpansionTableOut,
    FamilyRegionOfInterestUpdate,
    FamilyStructureUpdate,
    FamilyStructureUpdateOut,
    FamilyTrackAvailabilityOut,
    HaplotypeResponse,
    PhasedMarkerResponse,
    FamilyPhenotypeMatchOut,
    HpoAnnotationCreate,
    HpoAnnotationOut,
    HpoAnnotationUpdate,
    HpoFamilyQueryOut,
    PhenotypeMatchResultOut,
    RepeatExpansionTrackResponse,
    SmallVariantFilterPresetCreate,
    SmallVariantFilterPresetOut,
    SmallVariantReviewOut,
    SmallVariantReviewSummaryOut,
    SmallVariantReviewUpdate,
    SmallVariantTagDefinitionCreate,
    SmallVariantTagDefinitionOut,
    SmallVariantTagDefinitionUpdate,
    NiptCoverageLowRegionOut,
    NiptCoverageRegionOut,
    NiptCoverageSummaryOut,
    NiptFetalFractionOut,
    SampleIntegrityCategoryQcOut,
    SampleIntegrityFetalSexCheckOut,
    SampleIntegrityMendelianCheckOut,
    SampleIntegrityPaternityCheckOut,
    AnnotationManifestOut,
    AnnotationManifestUpdate,
    ClassificationDriftOut,
    ClinicalAuditOut,
    ReportSignoutDetail,
    ReportSignoutListOut,
    ReportSignoutRequest,
    SampleIntegrityQcOut,
    SampleIntegrityRelatednessCheckOut,
    SampleIntegritySexCheckOut,
    NiptSummaryOut,
    NiptClassificationOut,
    NiptVariantOut,
    NiptVariantPage,
    VariantLengthOut,
    VariantOut,
    VariantPage,
)
from ..services.clickhouse_family_variants import (
    export_family_small_variants,
    export_family_structural_variants,
    get_family_compound_het_candidates as get_family_compound_het_candidates_clickhouse,
    get_family_small_variants_page as get_family_small_variants_clickhouse,
    get_family_structural_variants_page as get_family_structural_variants_clickhouse,
)
from ..services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext, build_family_metadata_context
from ..services.family_service import (
    get_family_for_user,
    get_family_haplotypes_batch_for_user,
    get_family_haplotypes_for_user,
    get_family_phased_markers_for_user,
    get_family_structural_variant_lengths_for_user,
    get_family_track_availability_for_user,
    get_shared_family_structural_variant_counts_for_user,
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
from ..services.metadata_service import CurrentUser
from ..services.nipt_coverage import DEFAULT_MIN_DEPTH
from ..services.sample_integrity_service import get_family_sample_integrity_qc
from ..services.annotation_manifest_service import (
    get_family_annotation_manifest,
    set_family_annotation_manifest,
)
from ..services.classification_drift_service import evaluate_classification_drift
from ..services.clinical_audit_service import list_clinical_audit
from ..services.report_signout_service import (
    get_report_signout,
    list_report_signouts,
    sign_out_report,
)
from ..services.nipt_service import (
    NiptClassifiedVariant,
    get_family_nipt_coverage,
    get_family_nipt_variants,
    run_family_nipt_analysis,
)
from ..services.bed_service import precompute_family_lineage_safe
from ..services.clickhouse_family_variants import precompute_family_ranking_safe
from ..services.mitochondrial_analysis import get_family_mitochondrial_analysis_response
from ..services.raw_import_files_pg import record_upload_file_obj
from ..services.paraphase_pg import get_family_paraphase_table_response
from ..services.repeat_expansion_pg import (
    get_family_repeat_expansion_table_response,
    get_sample_repeat_expansion_track_response,
)
from ..services.small_variant_review_pg import (
    create_small_variant_tag_definition,
    delete_small_variant_tag_definition,
    delete_small_variant_filter_preset as delete_small_variant_filter_preset_record,
    get_small_variant_review_summary,
    list_small_variant_filter_presets as list_small_variant_filter_preset_records,
    list_small_variant_tag_definitions,
    save_small_variant_filter_preset as save_small_variant_filter_preset_record,
    update_small_variant_tag_definition,
    upsert_small_variant_review as upsert_small_variant_review_record,
)
from ..services.structural_variant_review_pg import (
    delete_structural_variant_filter_preset as delete_structural_variant_filter_preset_record,
    get_structural_variant_review_summary,
    list_structural_variant_filter_presets as list_structural_variant_filter_preset_records,
    save_structural_variant_filter_preset as save_structural_variant_filter_preset_record,
    upsert_structural_variant_review as upsert_structural_variant_review_record,
)
from ..services.variant_upload_service import upload_family_small_variant_file

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


def _family_sample_contexts(context: FamilyMetadataContext) -> dict[str, SampleMetadataContext]:
    return {
        row["sample_id"]: SampleMetadataContext(
            sample_uuid=row["sample_uuid"],
            sample_id=row["sample_id"],
            family_uuid=context.family_uuid,
            family_id=context.family_id,
            sex=row["sex"],
            project_ids=context.project_ids,
            assembly_id=context.assembly_id,
            assembly_name=context.assembly_name,
        )
        for row in context.sample_rows
    }


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


@router.get(
    "/{family_id}/small-variant-review-summary",
    response_model=SmallVariantReviewSummaryOut,
)
async def get_family_small_variant_review_summary(
    family_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> SmallVariantReviewSummaryOut:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
    )
    return await get_small_variant_review_summary(
        session,
        family_uuid=context.family_uuid,
    )


@router.get(
    "/{family_id}/structural-variant-review-summary",
    response_model=SmallVariantReviewSummaryOut,
)
async def get_family_structural_variant_review_summary(
    family_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> SmallVariantReviewSummaryOut:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
    )
    return await get_structural_variant_review_summary(
        session,
        family_uuid=context.family_uuid,
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


@router.post("/{family_id}/small-variants/upload")
async def upload_family_small_variants(
    family_id: str,
    file: UploadFile = File(...),
    overwrite: bool = False,
    source_format: str = "auto",
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> Dict[str, int | str]:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
    )
    result = await upload_family_small_variant_file(
        session,
        context=context,
        sample_contexts=_family_sample_contexts(context),
        file=file,
        overwrite=overwrite,
        format_hint=source_format,  # type: ignore[arg-type]
    )
    await record_upload_file_obj(
        session,
        file=file,
        family_uuid=context.family_uuid,
        family_id=context.family_id,
        sample_uuid=None,
        scope="family",
        dataset="small_variants",
    )
    return result


@router.get("/{family_id}/structural-variants", response_model=VariantPage)
async def get_family_structural_variants(
    family_id: str,
    page: int = 1,
    page_size: int = 100,
    chr: str | None = None,
    start: int | None = None,
    end: int | None = None,
    length: int | None = None,
    min_length: int | None = None,
    type: str | None = None,
    source: str | None = None,
    sample_filters: List[str] = Query(default_factory=list, alias="sample_filter"),
    samples: List[str] = Query(default_factory=list, alias="sample"),
    remote_chr: str | None = None,
    remote_start: int | None = None,
    gene: str | None = None,
    panel_id: str | None = None,
    inheritance: str | None = None,
    phenotype: str | None = None,
    hpo: str | None = None,
    moi: str | None = None,
    gencc_support: str | None = None,
    region_flags: List[str] = Query(default_factory=list, alias="region_flag"),
    max_control_af: float | None = None,
    max_population_af: float | None = None,
    min_pli: float | None = None,
    classifications: List[str] = Query(default_factory=list, alias="classification"),
    review_tags: List[str] = Query(default_factory=list, alias="review_tag"),
    exclude_review_tags: List[str] = Query(default_factory=list, alias="exclude_review_tag"),
    has_notes: bool = False,
    project_id: str | None = None,
    overlap: bool = False,
    prioritize: bool = False,
    track_mode: bool = False,
    count_only: bool = False,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> VariantPage:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        project_id=project_id,
    )
    result = await get_family_structural_variants_clickhouse(
        session,
        context=context,
        page=page,
        page_size=page_size,
        chr=chr,
        start=start,
        end=end,
        length=length,
        min_length=min_length,
        type=type,
        source=source,
        sample_filters=sample_filters,
        samples=samples,
        remote_chr=remote_chr,
        remote_start=remote_start,
        gene=gene,
        panel_id=panel_id,
        inheritance=inheritance,
        phenotype=phenotype,
        hpo=hpo,
        moi=moi,
        gencc_support=gencc_support,
        region_flags=region_flags,
        max_control_af=max_control_af,
        max_population_af=max_population_af,
        min_pli=min_pli,
        review_classifications=classifications,
        review_tags=review_tags,
        exclude_review_tags=exclude_review_tags,
        has_notes=has_notes,
        overlap=overlap,
        prioritize=prioritize,
        track_mode=track_mode,
        count_only=count_only,
    )
    if track_mode:
        # The genome SV track reads only chr/start/end/type/source + per-sample
        # genotype. VariantOut has 59 fields, ~46 of them null for an SV; dropping the
        # nulls (exclude_none) shrinks the per-member payload ~3x more on top of the
        # annotation slim (182 MB -> ~9 MB for this family). Return raw JSON so the
        # VariantPage response_model does not re-inflate the null fields.
        return Response(
            content=result.model_dump_json(exclude_none=True),
            media_type="application/json",
        )
    return result


def _family_small_variant_filters(
    chr: str | None = None,
    start: int | None = None,
    end: int | None = None,
    intervals: str | None = None,
    inheritance: str | None = None,
    expanded_carrier_screening: bool = False,
    ps: int | None = None,
    type: str | None = None,
    source: str | None = None,
    gene: str | None = None,
    transcript: str | None = None,
    impact: List[str] = Query(default_factory=list),
    effect: List[str] = Query(default_factory=list),
    clinvar: List[str] = Query(default_factory=list),
    exclude_clinvar: List[str] = Query(default_factory=list, alias="exclude_clinvar"),
    clinvar_overrides_frequency: bool = False,
    exclude_gene: str | None = None,
    exclude_intervals: str | None = None,
    rsid: str | None = None,
    hgvsc: str | None = None,
    hgvsp: str | None = None,
    canonical_only: bool = False,
    mane_only: bool = False,
    lof_only: bool = False,
    max_gnomad_af: float | None = None,
    max_gnomad_exomes_af: float | None = None,
    max_gnomad_genomes_af: float | None = None,
    max_gnomad_popmax_af: float | None = None,
    max_topmed_af: float | None = None,
    max_gnomad_ac: int | None = None,
    max_gnomad_hom_count: int | None = None,
    max_gnomad_hemi_count: int | None = None,
    min_cadd: float | None = None,
    min_revel: float | None = None,
    min_spliceai: float | None = None,
    sift: str | None = None,
    polyphen: str | None = None,
    panel_id: str | None = None,
    sample_filters: List[str] = Query(default_factory=list, alias="sample_filter"),
    review_classifications: List[str] = Query(default_factory=list, alias="classification"),
    review_tags: List[str] = Query(default_factory=list, alias="review_tag"),
    exclude_review_tags: List[str] = Query(default_factory=list, alias="exclude_review_tag"),
    has_notes: bool = False,
) -> Dict[str, Any]:
    """Parse the shared family small-variant filter query params.

    Returned as kwargs for ``get_family_small_variants_page`` so the paginated
    listing and the CSV export apply identical filtering.
    """

    return dict(
        chr=chr,
        start=start,
        end=end,
        intervals=intervals,
        inheritance=inheritance,
        expanded_carrier_screening=expanded_carrier_screening,
        ps=ps,
        type=type,
        source=source,
        gene=gene,
        transcript=transcript,
        impact=impact,
        effect=effect,
        clinvar=clinvar,
        exclude_clinvar=exclude_clinvar,
        clinvar_overrides_frequency=clinvar_overrides_frequency,
        exclude_gene=exclude_gene,
        exclude_intervals=exclude_intervals,
        rsid=rsid,
        hgvsc=hgvsc,
        hgvsp=hgvsp,
        canonical_only=canonical_only,
        mane_only=mane_only,
        lof_only=lof_only,
        max_gnomad_af=max_gnomad_af,
        max_gnomad_exomes_af=max_gnomad_exomes_af,
        max_gnomad_genomes_af=max_gnomad_genomes_af,
        max_gnomad_popmax_af=max_gnomad_popmax_af,
        max_topmed_af=max_topmed_af,
        max_gnomad_ac=max_gnomad_ac,
        max_gnomad_hom_count=max_gnomad_hom_count,
        max_gnomad_hemi_count=max_gnomad_hemi_count,
        min_cadd=min_cadd,
        min_revel=min_revel,
        min_spliceai=min_spliceai,
        sift=sift,
        polyphen=polyphen,
        panel_id=panel_id,
        sample_filters=sample_filters,
        review_classifications=review_classifications,
        review_tags=review_tags,
        exclude_review_tags=exclude_review_tags,
        has_notes=has_notes,
    )


@router.get("/{family_id}/small-variants", response_model=VariantPage)
async def get_family_small_variants(
    family_id: str,
    page: int = 1,
    page_size: int = 100,
    project_id: str | None = None,
    overlap: bool = False,
    require_sv_second_hit: bool = False,
    prioritize: bool = False,
    track_mode: bool = False,
    track_result_limit: int | None = None,
    count_only: bool = False,
    filters: Dict[str, Any] = Depends(_family_small_variant_filters),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> VariantPage:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        project_id=project_id,
    )
    return await get_family_small_variants_clickhouse(
        session,
        context=context,
        page=page,
        page_size=page_size,
        overlap=overlap,
        require_sv_second_hit=require_sv_second_hit,
        prioritize=prioritize,
        track_mode=track_mode,
        track_result_limit=track_result_limit,
        count_only=count_only,
        **filters,
    )


def _nipt_fetal_fraction_out(ff) -> NiptFetalFractionOut:
    return NiptFetalFractionOut(
        ff=ff.ff,
        ff_computed=ff.ff_computed,
        ff_external=ff.ff_external,
        ff_median=ff.ff_median,
        ci_low=ff.ci_low,
        ci_high=ff.ci_high,
        n_sites=ff.n_sites,
        method=ff.method,
        low_confidence=ff.low_confidence,
        disagreement=ff.disagreement,
    )


def _nipt_variant_out(item: NiptClassifiedVariant) -> NiptVariantOut:
    classification = item.classification
    nipt = NiptClassificationOut(
        category=classification.category,
        category_label=classification.category_label,
        maternal_state=classification.maternal_state,
        fetal_inheritance=classification.fetal_inheritance,
        expected_vaf=classification.expected_vaf,
        observed_vaf=classification.observed_vaf,
        confidence=classification.confidence,
        flags=classification.flags,
    )
    if item.variant_out is None:
        # get_family_nipt_variants always hydrates the page slice.
        raise RuntimeError("NIPT variant was serialized before hydration")
    return NiptVariantOut(**item.variant_out.model_dump(by_alias=True), nipt=nipt)


@router.get("/{family_id}/nipt/summary", response_model=NiptSummaryOut)
async def get_family_nipt_summary(
    family_id: str,
    project_id: str | None = None,
    external_ff: float | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> NiptSummaryOut:
    result = await run_family_nipt_analysis(
        session,
        family_id=family_id,
        user=user,
        project_id=project_id,
        external_ff=external_ff,
    )
    return NiptSummaryOut(
        family_id=family_id,
        fetal_fraction=_nipt_fetal_fraction_out(result.fetal_fraction),
        category_counts=result.category_counts,
        filter_counts=result.filter_counts,
    )


@router.get("/{family_id}/nipt/variants", response_model=NiptVariantPage)
async def get_family_nipt_variants_page(
    family_id: str,
    page: int = 1,
    page_size: int = 100,
    project_id: str | None = None,
    external_ff: float | None = None,
    category: list[int] | None = Query(None),
    min_confidence: float | None = None,
    inheritance: str | None = None,
    gene: str | None = None,
    exclude_gene: str | None = None,
    panel_id: str | None = None,
    chr: str | None = None,
    start: int | None = None,
    end: int | None = None,
    intervals: str | None = None,
    exclude_intervals: str | None = None,
    ps: int | None = None,
    type: str | None = None,
    source: str | None = None,
    transcript: str | None = None,
    rsid: str | None = None,
    hgvsc: str | None = None,
    hgvsp: str | None = None,
    impact: list[str] | None = Query(None),
    effect: list[str] | None = Query(None),
    clinvar: list[str] | None = Query(None),
    exclude_clinvar: list[str] | None = Query(None),
    clinvar_overrides_frequency: bool = False,
    sift: str | None = None,
    polyphen: str | None = None,
    max_gnomad_af: float | None = None,
    max_gnomad_exomes_af: float | None = None,
    max_gnomad_genomes_af: float | None = None,
    max_gnomad_popmax_af: float | None = None,
    max_topmed_af: float | None = None,
    max_gnomad_ac: int | None = None,
    max_gnomad_hom_count: int | None = None,
    max_gnomad_hemi_count: int | None = None,
    min_cadd: float | None = None,
    min_revel: float | None = None,
    min_spliceai: float | None = None,
    canonical_only: bool = False,
    mane_only: bool = False,
    lof_only: bool = False,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> NiptVariantPage:
    result = await get_family_nipt_variants(
        session,
        family_id=family_id,
        user=user,
        project_id=project_id,
        query_filters={
            "gene": gene,
            "exclude_gene": exclude_gene,
            "panel_id": panel_id,
            "chr": chr,
            "start": start,
            "end": end,
            "intervals": intervals,
            "exclude_intervals": exclude_intervals,
            "ps": ps,
            "type": type,
            "source": source,
            "transcript": transcript,
            "rsid": rsid,
            "hgvsc": hgvsc,
            "hgvsp": hgvsp,
            "impact": impact,
            "effect": effect,
            "clinvar": clinvar,
            "exclude_clinvar": exclude_clinvar,
            "clinvar_overrides_frequency": clinvar_overrides_frequency,
            "sift": sift,
            "polyphen": polyphen,
            "max_gnomad_af": max_gnomad_af,
            "max_gnomad_exomes_af": max_gnomad_exomes_af,
            "max_gnomad_genomes_af": max_gnomad_genomes_af,
            "max_gnomad_popmax_af": max_gnomad_popmax_af,
            "max_topmed_af": max_topmed_af,
            "max_gnomad_ac": max_gnomad_ac,
            "max_gnomad_hom_count": max_gnomad_hom_count,
            "max_gnomad_hemi_count": max_gnomad_hemi_count,
            "min_cadd": min_cadd,
            "min_revel": min_revel,
            "min_spliceai": min_spliceai,
            "canonical_only": canonical_only,
            "mane_only": mane_only,
            "lof_only": lof_only,
        },
        categories=category,
        min_confidence=min_confidence,
        inheritance=inheritance,
        page=page,
        page_size=page_size,
        external_ff=external_ff,
    )
    return NiptVariantPage(
        family_id=family_id,
        total=result.total,
        fetal_fraction=_nipt_fetal_fraction_out(result.fetal_fraction),
        variants=[_nipt_variant_out(item) for item in result.variants],
    )


@router.get("/{family_id}/nipt/coverage", response_model=NiptCoverageSummaryOut)
async def get_family_nipt_coverage_summary(
    family_id: str,
    project_id: str | None = None,
    gene: str | None = None,
    panel_id: str | None = None,
    min_depth: float = Query(DEFAULT_MIN_DEPTH, gt=0),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> NiptCoverageSummaryOut:
    summary = await get_family_nipt_coverage(
        session,
        family_id=family_id,
        user=user,
        project_id=project_id,
        gene=gene,
        panel_id=panel_id,
        min_depth=min_depth,
    )
    return NiptCoverageSummaryOut(
        family_id=family_id,
        overall_median_on_target=summary.overall_median_on_target,
        target_region_count=summary.target_region_count,
        per_region=[
            NiptCoverageRegionOut(
                label=region.label,
                chr=region.chrom,
                start=region.start,
                end=region.end,
                median_coverage=region.median_coverage,
                covered_bases=region.covered_bases,
                target_bases=region.target_bases,
            )
            for region in summary.per_region
        ],
        min_depth=summary.min_depth,
        min_covered_fraction=summary.min_covered_fraction,
        low_coverage_regions=[
            NiptCoverageLowRegionOut(
                label=region.label,
                chr=region.chrom,
                median_coverage=region.median_coverage,
                covered_fraction=region.covered_fraction,
                reason=region.reason,
            )
            for region in summary.low_coverage_regions
        ],
    )


@router.get("/{family_id}/annotation-manifest", response_model=AnnotationManifestOut)
async def get_family_annotation_manifest_endpoint(
    family_id: str,
    project_id: str | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> AnnotationManifestOut:
    return AnnotationManifestOut.model_validate(
        await get_family_annotation_manifest(
            session, family_id=family_id, user=user, project_id=project_id
        )
    )


@router.put("/{family_id}/annotation-manifest", response_model=AnnotationManifestOut)
async def set_family_annotation_manifest_endpoint(
    family_id: str,
    payload: AnnotationManifestUpdate,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> AnnotationManifestOut:
    return AnnotationManifestOut.model_validate(
        await set_family_annotation_manifest(
            session,
            family_id=family_id,
            user=user,
            modules=payload.modules,
            source=payload.source or "manual",
        )
    )


@router.get("/{family_id}/classification-drift", response_model=ClassificationDriftOut)
async def get_family_classification_drift_endpoint(
    family_id: str,
    project_id: str | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> ClassificationDriftOut:
    return ClassificationDriftOut.model_validate(
        await evaluate_classification_drift(
            session, family_id=family_id, user=user, project_id=project_id
        )
    )


@router.get("/{family_id}/clinical-audit", response_model=ClinicalAuditOut)
async def get_family_clinical_audit_endpoint(
    family_id: str,
    project_id: str | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> ClinicalAuditOut:
    return ClinicalAuditOut.model_validate(
        await list_clinical_audit(
            session, family_id=family_id, user=user, project_id=project_id
        )
    )


@router.post("/{family_id}/report/sign-out", response_model=ReportSignoutDetail)
async def sign_out_family_report_endpoint(
    family_id: str,
    payload: ReportSignoutRequest | None = None,
    project_id: str | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> ReportSignoutDetail:
    return ReportSignoutDetail.model_validate(
        await sign_out_report(
            session,
            family_id=family_id,
            user=user,
            acknowledge_drift=bool(payload and payload.acknowledge_drift),
            project_id=project_id,
        )
    )


@router.get("/{family_id}/report/sign-outs", response_model=ReportSignoutListOut)
async def list_family_report_signouts_endpoint(
    family_id: str,
    project_id: str | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> ReportSignoutListOut:
    return ReportSignoutListOut.model_validate(
        await list_report_signouts(
            session, family_id=family_id, user=user, project_id=project_id
        )
    )


@router.get("/{family_id}/report/sign-outs/{version}", response_model=ReportSignoutDetail)
async def get_family_report_signout_endpoint(
    family_id: str,
    version: int,
    project_id: str | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> ReportSignoutDetail:
    return ReportSignoutDetail.model_validate(
        await get_report_signout(
            session, family_id=family_id, version=version, user=user, project_id=project_id
        )
    )


@router.get("/{family_id}/qc/sample-integrity", response_model=SampleIntegrityQcOut)
async def get_family_sample_integrity_qc_endpoint(
    family_id: str,
    project_id: str | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> SampleIntegrityQcOut:
    report = await get_family_sample_integrity_qc(
        session, family_id=family_id, user=user, project_id=project_id
    )
    return SampleIntegrityQcOut(
        family_id=family_id,
        overall_status=report.overall_status,
        application=report.application,
        application_label=report.application_label,
        application_summary=report.application_summary,
        genotype_source=report.genotype_source,
        paternity_check=(
            SampleIntegrityPaternityCheckOut(
                father=report.paternity_check.father,
                cat7_transmitted=report.paternity_check.cat7_transmitted,
                cat8_absent=report.paternity_check.cat8_absent,
                informative_sites=report.paternity_check.informative_sites,
                status=report.paternity_check.status,
                message=report.paternity_check.message,
            )
            if report.paternity_check is not None
            else None
        ),
        fetal_sex_check=(
            SampleIntegrityFetalSexCheckOut(
                inferred_sex=report.fetal_sex_check.inferred_sex,
                x_transmitted=report.fetal_sex_check.x_transmitted,
                x_not_transmitted=report.fetal_sex_check.x_not_transmitted,
                informative_sites=report.fetal_sex_check.informative_sites,
                status=report.fetal_sex_check.status,
                message=report.fetal_sex_check.message,
            )
            if report.fetal_sex_check is not None
            else None
        ),
        category_qc_check=(
            SampleIntegrityCategoryQcOut(
                denovo=report.category_qc_check.denovo,
                paternal_absent=report.category_qc_check.paternal_absent,
                maternal_informative=report.category_qc_check.maternal_informative,
                maternal_inherited=report.category_qc_check.maternal_inherited,
                maternal_inherited_rate=report.category_qc_check.maternal_inherited_rate,
                status=report.category_qc_check.status,
                message=report.category_qc_check.message,
            )
            if report.category_qc_check is not None
            else None
        ),
        sex_checks=[
            SampleIntegritySexCheckOut(
                sample_id=check.sample_id,
                recorded_sex=check.recorded_sex,
                inferred_sex=check.inferred_sex,
                x_het_rate=check.x_het_rate,
                x_sites=check.x_sites,
                status=check.status,
                message=check.message,
            )
            for check in report.sex_checks
        ],
        relatedness_checks=[
            SampleIntegrityRelatednessCheckOut(
                sample_a=check.sample_a,
                sample_b=check.sample_b,
                expected_relationship=check.expected_relationship,
                inferred_relationship=check.inferred_relationship,
                kinship=check.kinship,
                ibs0_rate=check.ibs0_rate,
                informative_sites=check.informative_sites,
                status=check.status,
                message=check.message,
            )
            for check in report.relatedness_checks
        ],
        mendelian_checks=[
            SampleIntegrityMendelianCheckOut(
                child=check.child,
                parents=check.parents,
                informative_sites=check.informative_sites,
                mendel_errors=check.mendel_errors,
                mendel_rate=check.mendel_rate,
                status=check.status,
                message=check.message,
            )
            for check in report.mendelian_checks
        ],
        autosomal_sites=report.autosomal_sites,
        notes=report.notes,
    )


# CSV column order for the family small-variant export. Mirrors the on-screen
# table plus the extra annotation fields that don't fit in the UI.
_FAMILY_EXPORT_COLUMNS: list[tuple[str, str]] = [
    ("chr", "Chromosome"),
    ("start", "Start"),
    ("end", "End"),
    ("type", "Type"),
    ("ref", "Ref"),
    ("alt", "Alt"),
    ("rsid", "rsID"),
    ("gene", "Gene"),
    ("gene_id", "Gene ID"),
    ("transcript_id", "Transcript"),
    ("impact", "Impact"),
    ("effect", "Effect"),
    ("hgvsc", "HGVSc"),
    ("hgvsp", "HGVSp"),
    ("clinvar", "ClinVar"),
    ("gnomad_af", "gnomAD AF"),
    ("gnomad_hom_count", "gnomAD hom"),
    ("cadd_phred", "CADD"),
    ("revel", "REVEL"),
    ("spliceai_max", "SpliceAI"),
    ("sift", "SIFT"),
    ("polyphen", "PolyPhen"),
    ("classification", "Classification"),
    ("tags", "Tags"),
    ("genotypes", "Genotypes"),
]


def _family_export_cell(variant: VariantOut, field: str) -> str:
    if field == "priority_score":
        return f"{variant.priority.combined_score:.4f}" if variant.priority else ""
    if field == "priority_rank":
        return str(variant.priority.rank) if variant.priority and variant.priority.rank else ""
    if field == "classification":
        return variant.review.classification or "" if variant.review else ""
    if field == "tags":
        return "; ".join(variant.review.tags) if variant.review else ""
    if field == "genotypes":
        return "; ".join(f"{gt.sample}={gt.gt}" for gt in variant.genotypes)
    value = getattr(variant, field, None)
    if value is None:
        return ""
    return str(value)


@router.get("/{family_id}/small-variants/export")
async def export_family_small_variants_csv(
    family_id: str,
    project_id: str | None = None,
    prioritize: bool = False,
    filters: Dict[str, Any] = Depends(_family_small_variant_filters),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> StreamingResponse:
    """Download every filtered small variant for this family as a CSV file."""

    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        project_id=project_id,
    )
    rows = await export_family_small_variants(
        session,
        context=context,
        prioritize=prioritize,
        **filters,
    )

    # When prioritizing, prepend the priority score/rank so the CSV matches the ranked
    # on-screen order and carries the score.
    columns = (
        [("priority_score", "Priority"), ("priority_rank", "Rank"), *_FAMILY_EXPORT_COLUMNS]
        if prioritize
        else _FAMILY_EXPORT_COLUMNS
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([label for _, label in columns])
    for variant in rows:
        writer.writerow([_family_export_cell(variant, field) for field, _ in columns])

    filename = f"family-{family_id}-small-variants.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# CSV column order for the family structural-variant export. Mirrors the on-screen
# table plus the annotation fields that don't fit in the UI.
_FAMILY_SV_EXPORT_COLUMNS: list[tuple[str, str]] = [
    ("chr", "Chromosome"),
    ("start", "Start"),
    ("end", "End"),
    ("type", "Type"),
    ("length", "Length"),
    ("source", "Source"),
    ("gene", "Gene"),
    ("cytoband", "Cytoband"),
    ("inheritance", "Inheritance"),
    ("control_af", "Control AF"),
    ("population_af", "Population AF"),
    ("gene_pli", "pLI"),
    ("region_flags", "Region flags"),
    ("classification", "Classification"),
    ("tags", "Tags"),
    ("genotypes", "Genotypes"),
]


def _family_sv_export_cell(variant: VariantOut, field: str) -> str:
    extra = variant.annotation_extra or {}
    if field == "priority_score":
        return f"{variant.priority.combined_score:.4f}" if variant.priority else ""
    if field == "priority_rank":
        return str(variant.priority.rank) if variant.priority and variant.priority.rank else ""
    if field == "classification":
        return variant.review.classification or "" if variant.review else ""
    if field == "tags":
        return "; ".join(variant.review.tags) if variant.review else ""
    if field == "genotypes":
        return "; ".join(f"{gt.sample}={gt.gt}" for gt in variant.genotypes)
    if field in {"cytoband", "inheritance", "control_af", "population_af"}:
        value = extra.get(field)
        return "" if value is None else str(value)
    if field == "region_flags":
        flags = extra.get("region_flags") or []
        return "; ".join(str(flag) for flag in flags) if isinstance(flags, list) else str(flags)
    value = getattr(variant, field, None)
    return "" if value is None else str(value)


@router.get("/{family_id}/structural-variants/export")
async def export_family_structural_variants_csv(
    family_id: str,
    project_id: str | None = None,
    prioritize: bool = False,
    chr: str | None = None,
    start: int | None = None,
    end: int | None = None,
    length: int | None = None,
    min_length: int | None = None,
    type: str | None = None,
    source: str | None = None,
    sample_filters: List[str] = Query(default_factory=list, alias="sample_filter"),
    samples: List[str] = Query(default_factory=list, alias="sample"),
    remote_chr: str | None = None,
    remote_start: int | None = None,
    gene: str | None = None,
    panel_id: str | None = None,
    inheritance: str | None = None,
    phenotype: str | None = None,
    hpo: str | None = None,
    moi: str | None = None,
    gencc_support: str | None = None,
    region_flags: List[str] = Query(default_factory=list, alias="region_flag"),
    max_control_af: float | None = None,
    max_population_af: float | None = None,
    min_pli: float | None = None,
    classifications: List[str] = Query(default_factory=list, alias="classification"),
    review_tags: List[str] = Query(default_factory=list, alias="review_tag"),
    exclude_review_tags: List[str] = Query(default_factory=list, alias="exclude_review_tag"),
    has_notes: bool = False,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> StreamingResponse:
    """Download every filtered structural variant for this family as a CSV file."""

    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        project_id=project_id,
    )
    rows = await export_family_structural_variants(
        session,
        context=context,
        prioritize=prioritize,
        chr=chr,
        start=start,
        end=end,
        length=length,
        min_length=min_length,
        type=type,
        source=source,
        sample_filters=sample_filters,
        samples=samples,
        remote_chr=remote_chr,
        remote_start=remote_start,
        gene=gene,
        panel_id=panel_id,
        inheritance=inheritance,
        phenotype=phenotype,
        hpo=hpo,
        moi=moi,
        gencc_support=gencc_support,
        region_flags=region_flags,
        max_control_af=max_control_af,
        max_population_af=max_population_af,
        min_pli=min_pli,
        review_classifications=classifications,
        review_tags=review_tags,
        exclude_review_tags=exclude_review_tags,
        has_notes=has_notes,
    )

    columns = (
        [("priority_score", "Priority"), ("priority_rank", "Rank"), *_FAMILY_SV_EXPORT_COLUMNS]
        if prioritize
        else _FAMILY_SV_EXPORT_COLUMNS
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([label for _, label in columns])
    for variant in rows:
        writer.writerow([_family_sv_export_cell(variant, field) for field, _ in columns])

    filename = f"family-{family_id}-structural-variants.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/{family_id}/small-variants/{variant_id}/compound-het-candidates",
    response_model=VariantPage,
)
async def get_family_small_variant_compound_het_candidates(
    family_id: str,
    variant_id: str,
    limit: int = 50,
    project_id: str | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> VariantPage:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        project_id=project_id,
    )
    return await get_family_compound_het_candidates_clickhouse(
        session,
        context=context,
        variant_id=variant_id,
        limit=max(1, min(limit, 200)),
    )


@router.get(
    "/{family_id}/small-variant-filter-presets",
    response_model=List[SmallVariantFilterPresetOut],
)
async def list_small_variant_filter_presets(
    family_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> List[SmallVariantFilterPresetOut]:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
    )
    return await list_small_variant_filter_preset_records(
        session,
        family_uuid=context.family_uuid,
        user=user,
    )


@router.post(
    "/{family_id}/small-variant-filter-presets",
    response_model=SmallVariantFilterPresetOut,
)
async def save_small_variant_filter_preset(
    family_id: str,
    payload: SmallVariantFilterPresetCreate,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> SmallVariantFilterPresetOut:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
    )
    return await save_small_variant_filter_preset_record(
        session,
        family_uuid=context.family_uuid,
        payload=payload,
        user=user,
    )


@router.delete("/{family_id}/small-variant-filter-presets/{preset_id}", status_code=204)
async def delete_small_variant_filter_preset(
    family_id: str,
    preset_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> Response:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
    )
    await delete_small_variant_filter_preset_record(
        session,
        family_uuid=context.family_uuid,
        preset_id=preset_id,
        user=user,
    )
    return Response(status_code=204)


@router.get(
    "/{family_id}/small-variant-tags",
    response_model=List[SmallVariantTagDefinitionOut],
)
async def list_small_variant_tags(
    family_id: str,
    project_id: str | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> List[SmallVariantTagDefinitionOut]:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        project_id=project_id,
    )
    return await list_small_variant_tag_definitions(
        session,
        family_uuid=context.family_uuid,
        project_ids=context.project_ids,
        project_id=project_id,
    )


@router.post(
    "/{family_id}/small-variant-tags",
    response_model=SmallVariantTagDefinitionOut,
)
async def create_small_variant_tag(
    family_id: str,
    payload: SmallVariantTagDefinitionCreate,
    project_id: str | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> SmallVariantTagDefinitionOut:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        project_id=project_id,
    )
    return await create_small_variant_tag_definition(
        session,
        family_uuid=context.family_uuid,
        payload=payload,
        user=user,
        default_project_id=project_id,
    )


@router.put(
    "/{family_id}/small-variant-tags/{tag_key}",
    response_model=SmallVariantTagDefinitionOut,
)
async def update_small_variant_tag(
    family_id: str,
    tag_key: str,
    payload: SmallVariantTagDefinitionUpdate,
    project_id: str | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> SmallVariantTagDefinitionOut:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        project_id=project_id,
    )
    return await update_small_variant_tag_definition(
        session,
        family_uuid=context.family_uuid,
        tag_key=tag_key,
        payload=payload,
        user=user,
        default_project_id=project_id,
    )


@router.delete("/{family_id}/small-variant-tags/{tag_key}", status_code=204)
async def delete_small_variant_tag(
    family_id: str,
    tag_key: str,
    project_id: str | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> Response:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        project_id=project_id,
    )
    await delete_small_variant_tag_definition(
        session,
        family_uuid=context.family_uuid,
        tag_key=tag_key,
        user=user,
    )
    return Response(status_code=204)


@router.put(
    "/{family_id}/small-variants/{variant_id:path}/review",
    response_model=SmallVariantReviewOut,
)
async def upsert_small_variant_review(
    family_id: str,
    variant_id: str,
    payload: SmallVariantReviewUpdate,
    project_id: str | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> SmallVariantReviewOut:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        project_id=project_id,
    )
    return await upsert_small_variant_review_record(
        session,
        context=context,
        variant_id=variant_id,
        payload=payload,
        user=user,
    )


@router.get(
    "/{family_id}/structural-variant-filter-presets",
    response_model=List[SmallVariantFilterPresetOut],
)
async def list_structural_variant_filter_presets(
    family_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> List[SmallVariantFilterPresetOut]:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
    )
    return await list_structural_variant_filter_preset_records(
        session,
        family_uuid=context.family_uuid,
        user=user,
    )


@router.post(
    "/{family_id}/structural-variant-filter-presets",
    response_model=SmallVariantFilterPresetOut,
)
async def save_structural_variant_filter_preset(
    family_id: str,
    payload: SmallVariantFilterPresetCreate,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> SmallVariantFilterPresetOut:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
    )
    return await save_structural_variant_filter_preset_record(
        session,
        family_uuid=context.family_uuid,
        payload=payload,
        user=user,
    )


@router.delete("/{family_id}/structural-variant-filter-presets/{preset_id}", status_code=204)
async def delete_structural_variant_filter_preset(
    family_id: str,
    preset_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> Response:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
    )
    await delete_structural_variant_filter_preset_record(
        session,
        family_uuid=context.family_uuid,
        preset_id=preset_id,
        user=user,
    )
    return Response(status_code=204)


@router.put(
    "/{family_id}/structural-variants/{variant_id:path}/review",
    response_model=SmallVariantReviewOut,
)
async def upsert_structural_variant_review(
    family_id: str,
    variant_id: str,
    payload: SmallVariantReviewUpdate,
    project_id: str | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> SmallVariantReviewOut:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        project_id=project_id,
    )
    return await upsert_structural_variant_review_record(
        session,
        context=context,
        variant_id=variant_id,
        payload=payload,
        user=user,
    )


@router.get("/{family_id}/haplotypes", response_model=HaplotypeResponse)
async def get_family_haplotypes(
    family_id: str,
    chr: str,
    start: int | None = None,
    end: int | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> HaplotypeResponse:
    return await get_family_haplotypes_for_user(
        session,
        family_id=family_id,
        user=user,
        chr=chr,
        start=start,
        end=end,
    )


@router.get("/{family_id}/phased-markers", response_model=PhasedMarkerResponse)
async def get_family_phased_markers(
    family_id: str,
    chr: str,
    start: int | None = None,
    end: int | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> PhasedMarkerResponse:
    return await get_family_phased_markers_for_user(
        session,
        family_id=family_id,
        user=user,
        chr=chr,
        start=start,
        end=end,
    )


@router.get("/{family_id}/haplotypes/batch", response_model=HaplotypeResponse)
async def get_family_haplotypes_batch(
    family_id: str,
    chroms: List[str] = Query(..., alias="chr"),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> HaplotypeResponse:
    return await get_family_haplotypes_batch_for_user(
        session,
        family_id=family_id,
        user=user,
        chromosomes=chroms,
    )


@router.get("/{family_id}/repeat-expansions", response_model=FamilyRepeatExpansionTableOut)
async def get_family_repeat_expansions(
    family_id: str,
    project_id: str | None = None,
    count_only: bool = False,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> FamilyRepeatExpansionTableOut:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        project_id=project_id,
    )
    return await get_family_repeat_expansion_table_response(
        session,
        context=context,
        count_only=count_only,
    )


@router.get("/{family_id}/paraphase", response_model=FamilyParaphaseTableOut)
async def get_family_paraphase(
    family_id: str,
    project_id: str | None = None,
    count_only: bool = False,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> FamilyParaphaseTableOut:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        project_id=project_id,
    )
    return await get_family_paraphase_table_response(
        session,
        context=context,
        count_only=count_only,
    )


@router.get("/{family_id}/mitochondrial-dna", response_model=FamilyMitoDNAAnalysisOut)
async def get_family_mitochondrial_dna(
    family_id: str,
    project_id: str | None = None,
    count_only: bool = False,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> FamilyMitoDNAAnalysisOut:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        project_id=project_id,
    )
    return await get_family_mitochondrial_analysis_response(
        session,
        context=context,
        count_only=count_only,
    )


@router.get(
    "/{family_id}/repeat-expansions/sample/{sample_id}",
    response_model=RepeatExpansionTrackResponse,
)
async def get_sample_repeat_expansions(
    family_id: str,
    sample_id: str,
    chroms: List[str] = Query(default_factory=list, alias="chr"),
    start: int | None = None,
    end: int | None = None,
    project_id: str | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> RepeatExpansionTrackResponse:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        project_id=project_id,
    )
    return await get_sample_repeat_expansion_track_response(
        session,
        context=context,
        sample_name=sample_id,
        chromosomes=chroms,
        start=start,
        end=end,
    )


@router.get("/{family_id}/track-availability", response_model=FamilyTrackAvailabilityOut)
async def get_family_track_availability(
    family_id: str,
    chroms: List[str] = Query(default_factory=list, alias="chrom"),
    start: int | None = None,
    end: int | None = None,
    type: str | None = None,
    source: str | None = None,
    length: int | None = None,
    min_length: int | None = None,
    remote_chr: str | None = None,
    remote_start: int | None = None,
    panel_id: str | None = None,
    ps: int | None = None,
    sample_filters: List[str] = Query(default_factory=list, alias="sample_filter"),
    project_id: str | None = None,
    include_small_variants: bool = True,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> FamilyTrackAvailabilityOut:
    if not chroms:
        chroms = [str(value) for value in range(1, 23)] + ["X", "Y", "MT"]
    return await get_family_track_availability_for_user(
        session,
        family_id=family_id,
        user=user,
        chromosomes=chroms,
        start=start,
        end=end,
        variant_type=type,
        source=source,
        length=length,
        min_length=min_length,
        remote_chr=remote_chr,
        remote_start=remote_start,
        panel_id=panel_id,
        phase_set=ps,
        sample_filters=sample_filters,
        project_id=project_id,
        include_small_variants=include_small_variants,
    )


@router.get("/{family_id}/structural-variant-lengths", response_model=List[VariantLengthOut])
async def get_structural_variant_lengths(
    family_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
    limit: int = Query(100000, ge=1, le=100000),
) -> List[VariantLengthOut]:
    return await get_family_structural_variant_lengths_for_user(
        session,
        family_id=family_id,
        user=user,
        limit=limit,
    )


@router.get("/{family_id}/shared-structural-variant-counts", response_model=Dict[str, Dict[str, int]])
async def get_shared_structural_variant_counts(
    family_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> Dict[str, Dict[str, int]]:
    return await get_shared_family_structural_variant_counts_for_user(
        session,
        family_id=family_id,
        user=user,
    )
