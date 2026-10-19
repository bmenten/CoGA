from dataclasses import replace
from typing import List

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.csv_export import csv_document, export_response_headers
from ..core.postgres import get_postgres_session
from ..dependencies import get_current_user
from ..schemas import (
    SmallVariantReviewOut,
    SmallVariantReviewSummaryOut,
    StructuralVariantFilterPresetCreate,
    StructuralVariantFilterPresetOut,
    StructuralVariantReviewUpdate,
    VariantOut,
    VariantPage,
)
from ..services.clickhouse_family_variants import (
    MAX_VARIANT_PAGE_SIZE,
    export_family_structural_variants,
    get_family_structural_variants_page as get_family_structural_variants_clickhouse,
)
from ..services.family_variant_filters import StructuralVariantQueryFilters
from ..services.family_metadata_context import build_family_metadata_context
from ..services.access_control import CurrentUser
from ..services.panel_use_audit import record_family_panel_use
from ..services.structural_variant_review_pg import (
    delete_structural_variant_filter_preset as delete_structural_variant_filter_preset_record,
    get_structural_variant_review_summary,
    list_structural_variant_filter_presets as list_structural_variant_filter_preset_records,
    save_structural_variant_filter_preset as save_structural_variant_filter_preset_record,
    upsert_structural_variant_review as upsert_structural_variant_review_record,
)
from .families_small_variants import _family_export_cell


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


async def _family_structural_variant_filters(
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
) -> StructuralVariantQueryFilters:
    """The SV filters of a request to the family SV list or its CSV export.

    Both routes read their filters here, and only here, so the file holds the SVs the list
    shows: a filter declared on one route alone would make the two differ (ENG-2). The page
    is no filter: the search sets it (the list's page, the export's single page), so the
    ``page`` and ``page_size`` given here only hold their places.
    """

    return StructuralVariantQueryFilters(
        page=1,
        page_size=0,
        chromosome=chr,
        start=start,
        end=end,
        length=length,
        min_length=min_length,
        variant_type=type,
        source=source,
        sample_filters=sample_filters,
        selected_samples=samples,
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


@router.get("/{family_id}/structural-variants", response_model=VariantPage)
async def get_family_structural_variants(
    family_id: str,
    page: int = 1,
    page_size: int = Query(default=100, ge=0, le=MAX_VARIANT_PAGE_SIZE),
    project_id: str | None = None,
    overlap: bool = False,
    prioritize: bool = False,
    track_mode: bool = False,
    count_only: bool = False,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
    # After the user: the caller is signed in, and the request's values are held to the
    # control-character rule, before a filter is read.
    filters: StructuralVariantQueryFilters = Depends(_family_structural_variant_filters),
) -> VariantPage:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        project_id=project_id,
    )
    # A panel the search applies goes on the family's clinical audit trail first (REQ-CARR-003).
    await record_family_panel_use(
        session, context=context, panel_id=filters.panel_id, user=user, search="structural_variants"
    )
    result = await get_family_structural_variants_clickhouse(
        session,
        context=context,
        page=page,
        page_size=page_size,
        # The genome track's region rule (an SV overlapping the window, not only one inside
        # it): the list's alone, as the SV page never sends it and the export never took it.
        filters=replace(filters, overlap=overlap),
        prioritize=prioritize,
        track_mode=track_mode,
        count_only=count_only,
    )
    if track_mode:
        # The genome SV track reads only chr/start/end/type/source/copy_change + per-sample
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
# table plus the annotation fields that don't fit in the UI. A breakend's mate, under
# the SV list's names (Remote chr, Remote start) and empty for an SV without one, stands
# beside the locus, where the reader of a translocation looks for its partner (DATA-5).
# The file is read by its header, not by position: the ranked export already shifts
# every column by its Priority and Rank.
_FAMILY_SV_EXPORT_COLUMNS: list[tuple[str, str]] = [
    ("chr", "Chromosome"),
    ("start", "Start"),
    ("end", "End"),
    ("remote_chr", "Remote chr"),
    ("remote_start", "Remote start"),
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
    """The SV-only columns, read from ``annotation_extra``; every other column as in the
    small-variant export (priority, review, genotypes, plain attributes)."""
    extra = variant.annotation_extra or {}
    if field in {"cytoband", "inheritance", "control_af", "population_af"}:
        value = extra.get(field)
        return "" if value is None else str(value)
    if field == "region_flags":
        flags = extra.get("region_flags") or []
        return "; ".join(str(flag) for flag in flags) if isinstance(flags, list) else str(flags)
    return _family_export_cell(variant, field)


@router.get("/{family_id}/structural-variants/export")
async def export_family_structural_variants_csv(
    family_id: str,
    project_id: str | None = None,
    prioritize: bool = False,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
    # The list's filters, read by the same dependency (ENG-2), after the user as there.
    filters: StructuralVariantQueryFilters = Depends(_family_structural_variant_filters),
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
    await record_family_panel_use(
        session,
        context=context,
        panel_id=filters.panel_id,
        user=user,
        search="structural_variants_export",
    )
    export = await export_family_structural_variants(
        session,
        context=context,
        filters=filters,
        prioritize=prioritize,
    )

    columns = (
        [("priority_score", "Priority"), ("priority_rank", "Rank"), *_FAMILY_SV_EXPORT_COLUMNS]
        if prioritize
        else _FAMILY_SV_EXPORT_COLUMNS
    )
    return StreamingResponse(
        iter([csv_document(columns, export.rows, _family_sv_export_cell)]),
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
    payload: StructuralVariantReviewUpdate,
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
