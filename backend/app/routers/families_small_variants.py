import csv
import io
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.csv_export import csv_safe_cell, export_response_headers
from ..core.postgres import get_postgres_session
from ..dependencies import get_current_admin_user, get_current_user
from ..schemas import (
    SmallVariantFilterPresetCreate,
    SmallVariantFilterPresetOut,
    SmallVariantReviewOut,
    SmallVariantReviewSummaryOut,
    SmallVariantReviewUpdate,
    SmallVariantTagDefinitionCreate,
    SmallVariantTagDefinitionOut,
    SmallVariantTagDefinitionUpdate,
    SmallVariantUploadResult,
    VariantOut,
    VariantPage,
)
from ..services.clickhouse_family_variants import (
    MAX_VARIANT_PAGE_SIZE,
    export_family_small_variants,
    get_family_compound_het_candidates as get_family_compound_het_candidates_clickhouse,
    get_family_small_variants_page as get_family_small_variants_clickhouse,
)
from ..services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext, build_family_metadata_context
from ..services.access_control import CurrentUser
from ..services.raw_import_files_pg import record_upload_file_obj
from ..services.small_variant_review_pg import (
    get_small_variant_review_summary,
    upsert_small_variant_review as upsert_small_variant_review_record,
)
from ..services.small_variant_review_tags import (
    create_small_variant_tag_definition,
    delete_small_variant_tag_definition,
    list_small_variant_tag_definitions,
    update_small_variant_tag_definition,
)
from ..services.small_variant_review_presets import (
    delete_small_variant_filter_preset as delete_small_variant_filter_preset_record,
    list_small_variant_filter_presets as list_small_variant_filter_preset_records,
    save_small_variant_filter_preset as save_small_variant_filter_preset_record,
)
from ..services.variant_upload_service import SmallVariantFormat, upload_family_small_variant_file


router = APIRouter()


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


@router.post("/{family_id}/small-variants/upload", response_model=SmallVariantUploadResult)
async def upload_family_small_variants(
    family_id: str,
    file: UploadFile = File(...),
    overwrite: bool = False,
    # The callsets the upload stores rows under. Anything else is refused (422) before
    # a row is written: the value becomes the rows' source, which scopes every later
    # overwrite and delete.
    source_format: SmallVariantFormat = "auto",
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> SmallVariantUploadResult:
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
        format_hint=source_format,
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
    return SmallVariantUploadResult.model_validate(result)


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
    page_size: int = Query(default=100, ge=0, le=MAX_VARIANT_PAGE_SIZE),
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
    """Download the filtered small variants for this family as a CSV file.

    Up to the export cap (50,000 rows). A larger result is not cut silently: the
    file name says TRUNCATED and the X-CoGA-Export-* headers report it (#512).
    """

    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        project_id=project_id,
    )
    export = await export_family_small_variants(
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
    for variant in export.rows:
        writer.writerow([csv_safe_cell(_family_export_cell(variant, field)) for field, _ in columns])

    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers=export_response_headers(
            f"family-{family_id}-small-variants",
            rows=len(export.rows),
            truncated=export.truncated,
            limit=export.limit,
        ),
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
    user: CurrentUser = Depends(get_current_admin_user),
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
    user: CurrentUser = Depends(get_current_admin_user),
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
    user: CurrentUser = Depends(get_current_admin_user),
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
