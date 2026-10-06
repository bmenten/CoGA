
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import get_current_user
from ..schemas import (
    NiptCoverageLowRegionOut,
    NiptCoverageRegionOut,
    NiptCoverageSummaryOut,
    NiptDeNovoTriageOut,
    NiptDeNovoWindowOut,
    NiptFetalFractionOut,
    NiptFetalSexOut,
    NiptGeneTargetsOut,
    NiptModelOut,
    NiptPaternityOut,
    NiptQcOut,
    NiptRecessiveAlleleOut,
    NiptRecessiveGeneOut,
    NiptSummaryOut,
    NiptClassificationOut,
    NiptTargetCoverageGeneOut,
    NiptTargetCoverageOut,
    NiptTargetDetailOut,
    NiptTargetOut,
    NiptVariantOut,
    NiptVariantPage,
)
from ..services.clickhouse_family_variants import (
    MAX_VARIANT_PAGE_SIZE,
)
from ..services.access_control import CurrentUser
from ..services.nipt_analysis import (
    MATERNAL_HET_BIAS,
    NIPT_MODEL_REFERENCE,
    OVERDISPERSION,
    NiptQualityThresholds,
    combined_fetal_sex,
)
from ..services.nipt_coverage import DEFAULT_MIN_DEPTH
from ..services.nipt_service import (
    NiptClassifiedVariant,
    NiptFamilySummary,
    get_family_nipt_coverage,
    get_family_nipt_gene_targets,
    get_family_nipt_summary as load_family_nipt_summary,
    get_family_nipt_variants,
)
from ..services.nipt_target_coverage import TargetCoverageRow, TargetCoverageSummary
from ..services.nipt_triage import RecessiveGeneRisk, de_novo_window
from ..services.sample_integrity_qc import evaluate_paternity


router = APIRouter()


def _nipt_fetal_fraction_out(ff) -> NiptFetalFractionOut:
    return NiptFetalFractionOut(
        ff=ff.ff,
        ff_computed=ff.ff_computed,
        ff_median=ff.ff_median,
        ci_low=ff.ci_low,
        ci_high=ff.ci_high,
        n_sites=ff.n_sites,
        method=ff.method,
        low_confidence=ff.low_confidence,
        vaf_q05=ff.vaf_q05,
        vaf_q95=ff.vaf_q95,
    )


def _target_out(row: TargetCoverageRow) -> NiptTargetOut:
    return NiptTargetOut(
        chr=row.chrom,
        start=row.start,
        end=row.end,
        gene=row.gene,
        attribute=row.attribute,
        mean=row.mean,
        median=row.median,
        min=row.min,
        proportion_covered=row.proportion_covered,
    )


def _target_coverage_out(summary: TargetCoverageSummary) -> NiptTargetCoverageOut:
    return NiptTargetCoverageOut(
        targets=summary.targets,
        median_mean=summary.median_mean,
        q05_mean=summary.q05_mean,
        below_critical=summary.below_critical,
        below_advisory=summary.below_advisory,
        zero_mean=summary.zero_mean,
        incomplete=summary.incomplete,
        critical_mean_depth=summary.critical_mean_depth,
        advisory_mean_depth=summary.advisory_mean_depth,
        genes=[
            NiptTargetCoverageGeneOut(
                gene=gene.gene,
                targets=gene.targets,
                weak_targets=gene.weak_targets,
                min_mean=gene.min_mean,
                mean_of_means=gene.mean_of_means,
                weak=[_target_out(row) for row in gene.weak],
            )
            for gene in summary.genes
        ],
    )


# The plasma's sex profile (nipt_target_coverage.sex_chromosome_coverage) as a QC verdict:
# male DNA cannot be maternal plasma.
_PLASMA_PROFILE_VERDICTS: dict[str, tuple[str, str]] = {
    "female_no_chrY_signal": ("pass", "Female plasma; no chrY signal (a female fetus, or a low fetal fraction)."),
    "female_with_male_fetal_signal": ("pass", "Female plasma with a male fetal chrY signal."),
    "high_chrY_review": ("warn", "chrY coverage near the autosomal level: check that this is maternal plasma."),
    "male_like_not_maternal_plasma": (
        "fail",
        "Male DNA (chrY at the autosomal level, chrX low): this sample cannot be maternal plasma.",
    ),
    "no_chrY_targets": ("unknown", "The panel has no chrY target: no sex profile."),
}


def _qc_out(summary: NiptFamilySummary) -> NiptQcOut:
    analysis = summary.analysis
    ff = analysis.fetal_fraction
    window = de_novo_window(ff)
    evidence = analysis.paternal_transmission
    paternity = evaluate_paternity(
        "father",
        hom_alt_transmitted=evidence.hom_alt_transmitted,
        hom_alt_not_transmitted=evidence.hom_alt_not_transmitted,
        het_transmitted=evidence.het_transmitted,
        het_not_transmitted=evidence.het_not_transmitted,
    )
    sex = summary.sex_chromosomes
    profile = sex.profile if sex is not None else None
    status, message = _PLASMA_PROFILE_VERDICTS.get(
        profile or "", ("unknown", "No per-target coverage: no sex profile.")
    )
    qc = NiptQualityThresholds()
    return NiptQcOut(
        de_novo_window=(
            NiptDeNovoWindowOut(
                strict_min=window.strict_min,
                strict_max=window.strict_max,
                loose_min=window.loose_min,
                loose_max=window.loose_max,
            )
            if window is not None
            else None
        ),
        paternity=NiptPaternityOut(
            hom_alt_transmitted=evidence.hom_alt_transmitted,
            hom_alt_not_transmitted=evidence.hom_alt_not_transmitted,
            het_transmitted=evidence.het_transmitted,
            het_not_transmitted=evidence.het_not_transmitted,
            hom_alt_rate=evidence.hom_alt_rate,
            het_rate=evidence.het_rate,
            status=paternity.status,
            message=paternity.message,
        ),
        fetal_sex=NiptFetalSexOut(
            call=combined_fetal_sex(  # type: ignore[arg-type]
                analysis.fetal_sex.inferred, profile, ff.ff_computed or 0.0
            ),
            paternal_x=analysis.fetal_sex.inferred,
            x_transmitted=analysis.fetal_sex.x_transmitted,
            x_not_transmitted=analysis.fetal_sex.x_not_transmitted,
            informative_sites=analysis.fetal_sex.informative_sites,
            chry_profile=profile,
            y_ratio=sex.y_ratio if sex is not None else None,
            x_ratio=sex.x_ratio if sex is not None else None,
            chry_fetal_fraction=sex.fetal_fraction_estimate if sex is not None else None,
        ),
        plasma_profile_status=status,  # type: ignore[arg-type]
        plasma_profile_message=message,
        target_coverage=(
            _target_coverage_out(summary.target_coverage) if summary.target_coverage is not None else None
        ),
        quality_failures=analysis.quality_failure_counts,
        model=NiptModelOut(
            reference=NIPT_MODEL_REFERENCE,
            overdispersion=OVERDISPERSION,
            maternal_het_bias=MATERNAL_HET_BIAS,
            min_quality=qc.min_qual,
            min_alt_reads=qc.min_cf_alt_reads,
            min_vaf=qc.min_vaf,
            vaf_ff_fraction=qc.vaf_ff_fraction,
            max_strand_bias_fs=qc.max_fs,
            father_het_min_vaf=qc.father_het_min_vaf,
            father_hom_alt_min_vaf=qc.father_hom_alt_min_vaf,
            min_father_depth=qc.min_father_dp,
        ),
    )


def _recessive_gene_out(gene: RecessiveGeneRisk) -> NiptRecessiveGeneOut:
    return NiptRecessiveGeneOut(
        gene=gene.gene,
        maternal=[
            NiptRecessiveAlleleOut(
                variant_id=allele.variant_id,
                inherited_probability=allele.inherited_probability,
                category=allele.category,
                note=allele.note,
            )
            for allele in gene.maternal
        ],
        paternal=[
            NiptRecessiveAlleleOut(
                variant_id=allele.variant_id,
                inherited_probability=allele.inherited_probability,
                category=allele.category,
                note=allele.note,
            )
            for allele in gene.paternal
        ],
        risk=gene.risk,
        maternal_variant_id=gene.maternal_variant_id,
        paternal_variant_id=gene.paternal_variant_id,
        risk_uses_prior=gene.risk_uses_prior,
    )


def _nipt_variant_out(item: NiptClassifiedVariant) -> NiptVariantOut:
    classification = item.classification
    site = item.site
    nipt = NiptClassificationOut(
        category=classification.category,
        category_label=classification.category_label,
        maternal_state=classification.maternal_state,
        fetal_inheritance=classification.fetal_inheritance,
        expected_vaf=classification.expected_vaf,
        observed_vaf=classification.observed_vaf,
        confidence=classification.confidence,
        flags=classification.flags,
        runner_up_category=classification.runner_up_category,
        runner_up_confidence=classification.runner_up_confidence,
        paternal_transmission_probability=classification.paternal_transmission_probability,
        maternal_allele_probability=classification.maternal_allele_probability,
        fetal_hom_alt_probability=classification.fetal_hom_alt_probability,
        fetal_genotype_posterior=classification.fetal_genotype_posterior,
        quality_failures=classification.quality_failures,
        cf_alt_reads=site.cf_alt_reads if site is not None else None,
        cf_depth=site.cf_dp if site is not None else None,
        cf_depth_estimated=site.cf_depth_estimated if site is not None else False,
        father_state=site.father_state if site is not None else None,
        father_vaf=site.father_vaf if site is not None else None,
        father_depth=site.father_dp if site is not None else None,
        de_novo=(
            NiptDeNovoTriageOut(
                window=item.de_novo.window,  # type: ignore[arg-type]
                score=item.de_novo.score,
                label=item.de_novo.label,  # type: ignore[arg-type]
                reasons=item.de_novo.reasons,
                other_cfdna_carriers=item.de_novo.other_cfdna_carriers,
            )
            if item.de_novo is not None
            else None
        ),
    )
    if item.variant_out is None:
        # get_family_nipt_variants always hydrates the page slice.
        raise RuntimeError("NIPT variant was serialized before hydration")
    return NiptVariantOut(**item.variant_out.model_dump(by_alias=True), nipt=nipt)


@router.get("/{family_id}/nipt/summary", response_model=NiptSummaryOut)
async def get_family_nipt_summary(
    family_id: str,
    project_id: str | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> NiptSummaryOut:
    summary = await load_family_nipt_summary(
        session,
        family_id=family_id,
        user=user,
        project_id=project_id,
    )
    result = summary.analysis
    return NiptSummaryOut(
        family_id=family_id,
        fetal_fraction=_nipt_fetal_fraction_out(result.fetal_fraction),
        category_counts=result.category_counts,
        filter_counts=result.filter_counts,
        qc=_qc_out(summary),
    )


@router.get("/{family_id}/nipt/variants", response_model=NiptVariantPage)
async def get_family_nipt_variants_page(
    family_id: str,
    page: int = 1,
    page_size: int = Query(default=100, ge=0, le=MAX_VARIANT_PAGE_SIZE),
    project_id: str | None = None,
    category: list[int] | None = Query(None),
    min_confidence: float | None = None,
    inheritance: str | None = None,
    # The paternal and maternal views: also the alleles the fetus did not inherit.
    include_not_inherited: bool = False,
    # The de novo view: the lowest priority it lists (high, medium or low).
    de_novo_priority: str = "low",
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
        include_not_inherited=include_not_inherited,
        de_novo_priority=de_novo_priority,
        page=page,
        page_size=page_size,
    )
    return NiptVariantPage(
        family_id=family_id,
        total=result.total,
        total_is_estimated=result.total_is_estimated,
        count_limit=result.count_limit,
        fetal_fraction=_nipt_fetal_fraction_out(result.fetal_fraction),
        variants=[_nipt_variant_out(item) for item in result.variants],
        recessive_genes=[_recessive_gene_out(gene) for gene in result.recessive_genes],
    )


@router.get("/{family_id}/nipt/coverage", response_model=NiptCoverageSummaryOut)
async def get_family_nipt_coverage_summary(
    family_id: str,
    project_id: str | None = None,
    gene: str | None = None,
    panel_id: str | None = None,
    min_depth: float = Query(DEFAULT_MIN_DEPTH, gt=0),
    # The coverage page lists every gene in scope, the ones whose targets all pass too;
    # the NIPT page only the weak ones.
    all_genes: bool = False,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> NiptCoverageSummaryOut:
    coverage = await get_family_nipt_coverage(
        session,
        family_id=family_id,
        user=user,
        project_id=project_id,
        gene=gene,
        panel_id=panel_id,
        min_depth=min_depth,
        all_genes=all_genes,
    )
    summary = coverage.regions
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
        targets=_target_coverage_out(coverage.targets) if coverage.targets is not None else None,
    )


@router.get("/{family_id}/nipt/coverage/targets", response_model=NiptGeneTargetsOut)
async def get_family_nipt_gene_coverage(
    family_id: str,
    gene: str = Query(..., min_length=1, max_length=64),
    project_id: str | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> NiptGeneTargetsOut:
    """One gene's capture targets and their depth: a row of the coverage page, opened."""
    result = await get_family_nipt_gene_targets(
        session, family_id=family_id, user=user, gene=gene, project_id=project_id
    )
    return NiptGeneTargetsOut(
        family_id=family_id,
        gene=result.gene,
        critical_mean_depth=result.critical_mean_depth,
        targets=[
            NiptTargetDetailOut(
                **_target_out(row).model_dump(),
                weak=row.is_weak(result.critical_mean_depth),
            )
            for row in result.rows
        ],
    )
