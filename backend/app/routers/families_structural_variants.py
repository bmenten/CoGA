import csv
import io
from typing import List

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.csv_export import csv_safe_cell, export_response_headers
from ..core.postgres import get_postgres_session
from ..dependencies import get_current_user
from ..schemas import (
    SmallVariantReviewOut,
    SmallVariantReviewSummaryOut,
    SmallVariantReviewUpdate,
    StructuralVariantFilterPresetCreate,
    StructuralVariantFilterPresetOut,
    VariantOut,
    VariantPage,
)
from ..services.clickhouse_family_variants import (
    MAX_VARIANT_PAGE_SIZE,
    export_family_structural_variants,
    get_family_structural_variants_page as get_family_structural_variants_clickhouse,
)
from ..services.family_metadata_context import build_family_metadata_context
from ..services.access_control import CurrentUser
from ..services.structural_variant_review_pg import (
    delete_structural_variant_filter_preset as delete_structural_variant_filter_preset_record,
    get_structural_variant_review_summary,
    list_structural_variant_filter_presets as list_structural_variant_filter_preset_records,
    save_structural_variant_filter_preset as save_structural_variant_filter_preset_record,
    upsert_structural_variant_review as upsert_structural_variant_review_record,
)


router = APIRouter()


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


@router.get("/{family_id}/structural-variants", response_model=VariantPage)
async def get_family_structural_variants(
    family_id: str,
    page: int = 1,
    page_size: int = Query(default=100, ge=0, le=MAX_VARIANT_PAGE_SIZE),
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
    """Download the filtered structural variants for this family as a CSV file.

    Up to the export cap (50,000 rows). A larger result is not cut silently: the
    file name says TRUNCATED and the X-CoGA-Export-* headers report it (#512). So is a
    result drawn from a capped candidate read (X-CoGA-Export-Truncated-Reason:
    candidate-limit), which can miss matches however few rows it holds.
    """

    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        project_id=project_id,
    )
    export = await export_family_structural_variants(
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
    for variant in export.rows:
        writer.writerow([csv_safe_cell(_family_sv_export_cell(variant, field)) for field, _ in columns])

    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers=export_response_headers(
            f"family-{family_id}-structural-variants",
            rows=len(export.rows),
            truncated=export.truncated,
            limit=export.limit,
            reason=export.truncated_reason,
        ),
    )


@router.get(
    "/{family_id}/structural-variant-filter-presets",
    response_model=List[StructuralVariantFilterPresetOut],
)
async def list_structural_variant_filter_presets(
    family_id: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> List[StructuralVariantFilterPresetOut]:
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
    response_model=StructuralVariantFilterPresetOut,
)
async def save_structural_variant_filter_preset(
    family_id: str,
    payload: StructuralVariantFilterPresetCreate,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> StructuralVariantFilterPresetOut:
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
