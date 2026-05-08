from typing import Dict, List

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import get_current_admin_user, get_current_user
from ..schemas import (
    FamilyOut,
    FamilyParaphaseTableOut,
    FamilyRepeatExpansionTableOut,
    FamilyRegionOfInterestUpdate,
    FamilyTrackAvailabilityOut,
    HaplotypeResponse,
    RepeatExpansionTrackResponse,
    SmallVariantFilterPresetCreate,
    SmallVariantFilterPresetOut,
    SmallVariantReviewOut,
    SmallVariantReviewSummaryOut,
    SmallVariantReviewUpdate,
    SmallVariantTagDefinitionCreate,
    SmallVariantTagDefinitionOut,
    SmallVariantTagDefinitionUpdate,
    VariantLengthOut,
    VariantPage,
)
from ..services.bed_service import validate_bed_type
from ..services.clickhouse_family_variants import (
    get_family_compound_het_candidates as get_family_compound_het_candidates_clickhouse,
    get_family_small_variants_page as get_family_small_variants_clickhouse,
    get_family_structural_variants_page as get_family_structural_variants_clickhouse,
)
from ..services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext, build_family_metadata_context
from ..services.family_service import (
    get_family_for_user,
    get_family_haplotypes_batch_for_user,
    get_family_haplotypes_for_user,
    get_family_structural_variant_lengths_for_user,
    get_family_track_availability_for_user,
    get_shared_family_structural_variant_counts_for_user,
    list_families_for_user,
    update_family_roi_for_admin,
)
from ..services.metadata_service import CurrentUser
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
    return await upload_family_small_variant_file(
        session,
        context=context,
        sample_contexts=_family_sample_contexts(context),
        file=file,
        overwrite=overwrite,
        format_hint=source_format,  # type: ignore[arg-type]
    )


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
    track_mode: bool = False,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> VariantPage:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        project_id=project_id,
    )
    return await get_family_structural_variants_clickhouse(
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
        track_mode=track_mode,
    )


@router.get("/{family_id}/small-variants", response_model=VariantPage)
async def get_family_small_variants(
    family_id: str,
    page: int = 1,
    page_size: int = 100,
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
    project_id: str | None = None,
    overlap: bool = False,
    track_mode: bool = False,
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
        overlap=overlap,
        track_mode=track_mode,
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


@router.get(
    "/{family_id}/structural-variant-tags",
    response_model=List[SmallVariantTagDefinitionOut],
)
async def list_structural_variant_tags(
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
    "/{family_id}/structural-variant-tags",
    response_model=SmallVariantTagDefinitionOut,
)
async def create_structural_variant_tag(
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
    "/{family_id}/structural-variant-tags/{tag_key}",
    response_model=SmallVariantTagDefinitionOut,
)
async def update_structural_variant_tag(
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


@router.delete("/{family_id}/structural-variant-tags/{tag_key}", status_code=204)
async def delete_structural_variant_tag(
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
    )


@router.get("/{family_id}/paraphase", response_model=FamilyParaphaseTableOut)
async def get_family_paraphase(
    family_id: str,
    project_id: str | None = None,
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
        chroms = [str(value) for value in range(1, 23)] + ["X", "Y"]
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
