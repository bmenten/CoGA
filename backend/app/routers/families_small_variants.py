from functools import partial
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.csv_export import csv_document, export_response_headers
from ..core.postgres import get_postgres_session
from ..dependencies import get_current_admin_user, get_current_superuser, get_current_user
from ..schemas import (
    FamilySmallVariantStatisticsOut,
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
    family_members_without_calls,
    get_family_small_variants_page as get_family_small_variants_clickhouse,
)
from ..services.family_metadata_context import build_family_metadata_context, family_sample_contexts
from ..services.family_variant_statistics import get_family_small_variant_statistics
from ..services.family_analysis_types import analysis_type_of
from ..services.nipt import MONOGENIC_NIPT_ANALYSIS_TYPE
from ..services.signed_record_nipt import family_nipt_trio
from ..services.access_control import CurrentUser, changes_only_own_data
from ..services.panel_use_audit import record_family_panel_use
from ..services.raw_import_files_pg import record_upload_file_obj
from ..services.review_pg_utils import review_class_label
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
    list_small_variant_filter_presets_for_owner,
    save_small_variant_filter_preset as save_small_variant_filter_preset_record,
)
from ..services.variant_prioritization import RANK_BY_COMBINED_SCORE, RankBy
from ..services.small_variant_callsets import IMPUTED_CALLSETS, is_nipt_callset
from ..services.variant_upload_service import upload_family_small_variant_file
from .query_params import AnalysisParam, FiniteThreshold


router = APIRouter()


@router.get(
    "/{family_id}/small-variant-review-summary",
    response_model=SmallVariantReviewSummaryOut,
)
async def get_family_small_variant_review_summary(
    family_id: str,
    analysis: AnalysisParam = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> SmallVariantReviewSummaryOut:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        analysis=analysis,
    )
    # The reviews of the analysis read (analysis_reviews): named when the family holds several
    # analyses with small variants of their own.
    return await get_small_variant_review_summary(
        session,
        family_uuid=context.family_uuid,
        scope=context.scope.require_named(),
    )


@router.post("/{family_id}/small-variants/upload", response_model=SmallVariantUploadResult)
async def upload_family_small_variants(
    family_id: str,
    file: UploadFile = File(...),
    overwrite: bool = False,
    # The callset the upload stores rows under: a direct callset named by its caller, the
    # imputed genotypes (glimpse2), the mtDNA calls (mito), or auto (the caller the header
    # names). Anything else is refused (422) before a row is written: the value becomes the
    # rows' source, which scopes every later overwrite and delete.
    source_format: str = Query(default="auto", pattern=r"^(auto|[a-z][a-z0-9_]{1,31})$"),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> SmallVariantUploadResult:
    if is_nipt_callset(source_format) or (source_format in IMPUTED_CALLSETS and source_format != "glimpse2"):
        # A monogenic NIPT's per-sample calls only a package import writes; an imputation's
        # genotypes are uploaded with their haplotype blocks, as GLIMPSE2's.
        raise HTTPException(status_code=422, detail="This callset is not uploaded here")
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
    )
    result = await upload_family_small_variant_file(
        session,
        context=context,
        sample_contexts=family_sample_contexts(context),
        file=file,
        overwrite=overwrite,
        format_hint=source_format,
        record_upload=True,
    )
    await record_upload_file_obj(
        session,
        file=file,
        family_uuid=context.family_uuid,
        family_id=context.family_id,
        sample_uuid=None,
        scope="family",
        dataset="small_variants",
        # Which callset the retained file is (direct calls, imputed genotypes, mtDNA, NIPT).
        metadata={"callset": result["source_format"]},
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
    clinvar_pathogenic_override: bool = False,
    exclude_gene: str | None = None,
    exclude_intervals: str | None = None,
    rsid: str | None = None,
    hgvsc: str | None = None,
    hgvsp: str | None = None,
    canonical_only: bool = False,
    mane_only: bool = False,
    lof_only: bool = False,
    max_gnomad_af: FiniteThreshold = None,
    max_gnomad_exomes_af: FiniteThreshold = None,
    max_gnomad_genomes_af: FiniteThreshold = None,
    max_gnomad_popmax_af: FiniteThreshold = None,
    max_topmed_af: FiniteThreshold = None,
    max_gnomad_ac: int | None = None,
    max_gnomad_hom_count: int | None = None,
    max_gnomad_hemi_count: int | None = None,
    min_cadd: FiniteThreshold = None,
    min_revel: FiniteThreshold = None,
    min_spliceai: FiniteThreshold = None,
    sift: str | None = None,
    polyphen: str | None = None,
    panel_id: str | None = None,
    sample_filters: List[str] = Query(default_factory=list, alias="sample_filter"),
    pass_only: bool = False,
    review_classifications: List[str] = Query(default_factory=list, alias="classification"),
    review_tags: List[str] = Query(default_factory=list, alias="review_tag"),
    exclude_review_tags: List[str] = Query(default_factory=list, alias="exclude_review_tag"),
    has_notes: bool = False,
    salient_only: bool = False,
) -> Dict[str, Any]:
    """Parse the shared family small-variant filter query params.

    Returned as kwargs for ``get_family_small_variants_page`` so the paginated
    listing and the CSV export apply identical filtering. ``salient_only`` is the
    chromosome view's small-variant track in a wide window (``SmallVariantQueryFilters``).
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
        clinvar_pathogenic_override=clinvar_pathogenic_override,
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
        pass_only=pass_only,
        review_classifications=review_classifications,
        review_tags=review_tags,
        exclude_review_tags=exclude_review_tags,
        has_notes=has_notes,
        salient_only=salient_only,
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
    # The ranking's order, when prioritized: by the combined score, or by the variant score
    # alone (novel-gene candidates rank with phenotype-matched genes).
    rank_by: RankBy = RANK_BY_COMBINED_SCORE,
    track_mode: bool = False,
    track_result_limit: int | None = None,
    count_only: bool = False,
    analysis: AnalysisParam = None,
    filters: Dict[str, Any] = Depends(_family_small_variant_filters),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> VariantPage:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        project_id=project_id,
        analysis=analysis,
    )
    # A panel the search applies goes on the family's clinical audit trail first (REQ-CARR-003).
    await record_family_panel_use(
        session, context=context, panel_id=filters.get("panel_id"), user=user, search="small_variants"
    )
    result = await get_family_small_variants_clickhouse(
        session,
        context=context,
        page=page,
        page_size=page_size,
        overlap=overlap,
        require_sv_second_hit=require_sv_second_hit,
        prioritize=prioritize,
        rank_by=rank_by,
        track_mode=track_mode,
        track_result_limit=track_result_limit,
        count_only=count_only,
        **filters,
    )
    if not track_mode and not count_only:
        # The list names the members without calls in the callsets it reads, whose missing calls
        # an inheritance filter reads as no ALT allele (the owner's decision of 2026-11-20).
        result.members_without_calls = await family_members_without_calls(context, source=filters.get("source"))
    return result


@router.get("/{family_id}/small-variant-statistics", response_model=FamilySmallVariantStatisticsOut)
async def get_family_small_variant_statistics_route(
    family_id: str,
    project_id: str | None = None,
    analysis: AnalysisParam = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> FamilySmallVariantStatisticsOut:
    """The variant summary's statistics of the family's small variants (PROD-1), of the analysis
    the family is read for."""
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        project_id=project_id,
        analysis=analysis,
    )
    # The plasma and the father of the monogenic NIPT read: their GT is not their genotype.
    trio = (
        await family_nipt_trio(session, context)
        if analysis_type_of(context.scope.analysis) == MONOGENIC_NIPT_ANALYSIS_TYPE
        else None
    )
    nipt_samples = (trio[0].cfdna_sample_id, trio[0].father_sample_id) if trio is not None else ()
    return await get_family_small_variant_statistics(context, nipt_samples=nipt_samples)


# The export's column of the members without calls (family_members_without_calls).
_MEMBERS_WITHOUT_CALLS_FIELD = "members_without_calls"

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
    # VEP's genomic HGVS (its HGVSg); empty where the annotation has none, never a change
    # written from the VCF record, which is in the columns before it.
    ("hgvsg", "HGVSg"),
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
    # The class the review shows, then its classification text as written.
    ("classification", "Classification"),
    ("classification_text", "Classification text"),
    ("tags", "Tags"),
    ("genotypes", "Genotypes"),
    # The transcript the annotation filters held on, when the row's transcript (the
    # variant's primary one, which the columns above describe) does not meet them,
    # with that transcript's values of the columns above; empty otherwise. After the
    # row's own columns, so every column before them keeps its place.
    ("filter_transcript.transcript_id", "Filter-matched transcript"),
    ("filter_transcript.gene", "Filter-matched gene"),
    ("filter_transcript.impact", "Filter-matched impact"),
    ("filter_transcript.effect", "Filter-matched effect"),
    ("filter_transcript.hgvsc", "Filter-matched HGVSc"),
    ("filter_transcript.hgvsp", "Filter-matched HGVSp"),
    ("filter_transcript.cadd_phred", "Filter-matched CADD"),
    ("filter_transcript.revel", "Filter-matched REVEL"),
    ("filter_transcript.spliceai_max", "Filter-matched SpliceAI"),
    ("filter_transcript.sift", "Filter-matched SIFT"),
    ("filter_transcript.polyphen", "Filter-matched PolyPhen"),
    # The members of the analysis read whose samples hold no call in the callsets the file is
    # read from (family_members_without_calls), the same on every row: an inheritance filter
    # read their missing calls as no ALT allele, so the file says so wherever it is sent.
    (_MEMBERS_WITHOUT_CALLS_FIELD, "Members without calls"),
]

_FILTER_TRANSCRIPT_FIELD = "filter_transcript."

# The columns a prioritized export puts first: both scores and the rank, so the file holds
# the score its order follows, whichever ``rank_by`` it was ranked by.
PRIORITY_EXPORT_COLUMNS: list[tuple[str, str]] = [
    ("priority_score", "Priority"),
    ("priority_variant_score", "Variant score"),
    ("priority_rank", "Rank"),
]


def _family_export_cell(variant: VariantOut, field: str, members_without_calls: str = "") -> str:
    """The text of ``variant``'s cell of the export column ``field``; the column of the members
    without calls holds ``members_without_calls``, the same on every row."""
    if field == _MEMBERS_WITHOUT_CALLS_FIELD:
        return members_without_calls
    if field.startswith(_FILTER_TRANSCRIPT_FIELD):
        transcript = variant.filter_transcript
        value = (
            getattr(transcript, field.removeprefix(_FILTER_TRANSCRIPT_FIELD))
            if transcript is not None
            else None
        )
        return "" if value is None else str(value)
    if field == "priority_score":
        return f"{variant.priority.combined_score:.4f}" if variant.priority else ""
    if field == "priority_variant_score":
        return f"{variant.priority.variant_score:.4f}" if variant.priority else ""
    if field == "priority_rank":
        return str(variant.priority.rank) if variant.priority and variant.priority.rank else ""
    if field == "classification":
        # The class the review shows on the family page, as the Variant Explorer's export
        # writes it (review_class_label: a class word in the text, else the most severe class
        # tag, else the text); the review's tags are in their own column.
        if variant.review is None:
            return ""
        return review_class_label(variant.review.classification, variant.review.tags) or ""
    if field == "classification_text":
        # Beside it the classification as the reviewer wrote it, as stored: where the
        # rule shows a class tag's class, the text the review also holds stays in the file.
        return (variant.review.classification or "") if variant.review else ""
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
    rank_by: RankBy = RANK_BY_COMBINED_SCORE,
    analysis: AnalysisParam = None,
    filters: Dict[str, Any] = Depends(_family_small_variant_filters),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> StreamingResponse:
    """Download the filtered small variants for this family as a CSV file.

    Up to the export cap (50,000 rows). A larger result is not cut silently: the
    file name says TRUNCATED and the X-CoGA-Export-* headers report it. So is a
    result drawn from a capped candidate read (X-CoGA-Export-Truncated-Reason:
    candidate-limit), which can miss matches however few rows it holds.
    """

    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        project_id=project_id,
        analysis=analysis,
    )
    await record_family_panel_use(
        session,
        context=context,
        panel_id=filters.get("panel_id"),
        user=user,
        search="small_variants_export",
    )
    export = await export_family_small_variants(
        session,
        context=context,
        prioritize=prioritize,
        rank_by=rank_by,
        **filters,
    )
    # The members without calls in the callsets the file is read from, named on every row.
    without_calls = "; ".join(
        member.sample_id
        for member in await family_members_without_calls(context, source=filters.get("source"))
    )

    # When prioritizing, prepend the scores and rank so the CSV matches the ranked
    # on-screen order and carries the score it follows.
    columns = (
        [*PRIORITY_EXPORT_COLUMNS, *_FAMILY_EXPORT_COLUMNS]
        if prioritize
        else _FAMILY_EXPORT_COLUMNS
    )
    return StreamingResponse(
        iter([csv_document(columns, export.rows, partial(_family_export_cell, members_without_calls=without_calls))]),
        media_type="text/csv",
        headers=export_response_headers(
            f"family-{family_id}-small-variants",
            rows=len(export.rows),
            truncated=export.truncated,
            limit=export.limit,
            reason=export.truncated_reason,
        ),
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
    # The family route checks the user may open the family; the presets are theirs.
    await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
    )
    return await list_small_variant_filter_presets_for_owner(session, user=user)


@router.post(
    "/{family_id}/small-variant-filter-presets",
    response_model=SmallVariantFilterPresetOut,
)
# A preset is its owner's alone: a reader may save theirs.
@changes_only_own_data
async def save_small_variant_filter_preset(
    family_id: str,
    payload: SmallVariantFilterPresetCreate,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> SmallVariantFilterPresetOut:
    await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
    )
    return await save_small_variant_filter_preset_record(session, payload=payload, user=user)


@router.get(
    "/{family_id}/small-variant-tags",
    response_model=List[SmallVariantTagDefinitionOut],
)
async def list_small_variant_tags(
    family_id: str,
    project_id: str | None = None,
    include_inactive: bool = False,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> List[SmallVariantTagDefinitionOut]:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        project_id=project_id,
    )
    # With the inactive tags, also each tag the family's own reviews hold that its projects do
    # not list (a project it left, or a deleted one), inactive, so its reviews show the tag's
    # label rather than its key.
    return await list_small_variant_tag_definitions(
        session,
        family_uuid=context.family_uuid,
        project_ids=context.project_ids,
        project_id=project_id,
        include_inactive=include_inactive,
        include_held_by_family=True,
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
    user: CurrentUser = Depends(get_current_superuser),
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
    user: CurrentUser = Depends(get_current_superuser),
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
    user: CurrentUser = Depends(get_current_superuser),
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
    analysis: AnalysisParam = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> SmallVariantReviewOut:
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        project_id=project_id,
        analysis=analysis,
    )
    return await upsert_small_variant_review_record(
        session,
        context=context,
        variant_id=variant_id,
        payload=payload,
        user=user,
    )
