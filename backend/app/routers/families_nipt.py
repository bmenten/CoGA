
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import get_current_user
from ..schemas import (
    NiptCoverageLowRegionOut,
    NiptCoverageRegionOut,
    NiptCoverageSummaryOut,
    NiptFetalFractionOut,
    NiptSummaryOut,
    NiptClassificationOut,
    NiptVariantOut,
    NiptVariantPage,
)
from ..services.clickhouse_family_variants import (
    MAX_VARIANT_PAGE_SIZE,
)
from ..services.access_control import CurrentUser
from ..services.nipt_coverage import DEFAULT_MIN_DEPTH
from ..services.nipt_service import (
    NiptClassifiedVariant,
    get_family_nipt_coverage,
    get_family_nipt_variants,
    run_family_nipt_analysis,
)


router = APIRouter()


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
    page_size: int = Query(default=100, ge=0, le=MAX_VARIANT_PAGE_SIZE),
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
