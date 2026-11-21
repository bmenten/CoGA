
from functools import partial
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.csv_export import TRUNCATED_BY_CANDIDATE_LIMIT, csv_document, export_response_headers
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
    NiptNotClassifiedOut,
    NiptPaternityOut,
    NiptQcOut,
    NiptRecessiveAlleleOut,
    NiptRecessiveGeneOut,
    NiptRecurrenceOut,
    NiptSummaryOut,
    NiptClassificationOut,
    NiptVariantStatisticsOut,
    NiptArtifactCountsOut,
    NiptTargetCoverageGeneOut,
    NiptTargetCoverageOut,
    NiptTargetDetailOut,
    NiptTargetOut,
    NiptTrioOut,
    NiptVariantOut,
    NiptVariantPage,
)
from ..services.clickhouse_family_variants import (
    MAX_VARIANT_PAGE_SIZE,
)
from ..services.access_control import CurrentUser
from ..services.nipt_analysis import (
    FATHER_CALL_OTHER_REPRESENTATION,
    MATERNAL_HET_BIAS,
    NIPT_MODEL_REFERENCE,
    OVERDISPERSION,
    PLASMA_CALL_OTHER_REPRESENTATION,
    NiptQualityThresholds,
    NiptSiteObservation,
)
from ..services.family_analysis_types import pregnancy_of
from ..services.nipt import multiple_pregnancy_note
from ..services.nipt_coverage import DEFAULT_MIN_DEPTH
from ..services.nipt_quality_checks import nipt_quality_checks
from ..services.nipt_service import (
    NIPT_VARIANT_FETCH_LIMIT,
    NiptClassifiedVariant,
    NiptFamilySummary,
    NiptRecurrence,
    get_family_nipt_coverage,
    get_family_nipt_gene_targets,
    get_family_nipt_summary as load_family_nipt_summary,
    get_family_nipt_summary_with_artifacts,
    get_family_nipt_variants,
)
from ..services.nipt_target_coverage import TargetCoverageRow, TargetCoverageSummary
from ..services.nipt_triage import RecessiveGeneRisk
from ..services.nipt_variant_statistics import (
    CHROMOSOME_MARK_MIN_SITES,
    VAF_BIN_WIDTH,
    nipt_variant_statistics,
)
from .families_small_variants import _FAMILY_EXPORT_COLUMNS, _MEMBERS_WITHOUT_CALLS_FIELD, _family_export_cell
from .query_params import FiniteThreshold, NiptAnalysisParam


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


def _qc_out(summary: NiptFamilySummary) -> NiptQcOut:
    # The verdicts are made once, in nipt_quality_checks: the signed record freezes the same.
    checks = nipt_quality_checks(summary)
    window = checks.de_novo_window
    evidence = checks.paternal_transmission
    paternity = checks.paternity
    fetal_sex = checks.fetal_sex
    sex = summary.sex_chromosomes
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
        fetal_fraction_status=checks.fetal_fraction_check.status,
        fetal_sex=NiptFetalSexOut(
            call=fetal_sex.call,  # type: ignore[arg-type]
            status=fetal_sex.status,
            paternal_x=fetal_sex.paternal_x.inferred,
            x_transmitted=fetal_sex.paternal_x.x_transmitted,
            x_not_transmitted=fetal_sex.paternal_x.x_not_transmitted,
            informative_sites=fetal_sex.paternal_x.informative_sites,
            chry_profile=fetal_sex.chry_profile,
            y_ratio=sex.y_ratio if sex is not None else None,
            x_ratio=sex.x_ratio if sex is not None else None,
            chry_fetal_fraction=sex.fetal_fraction_estimate if sex is not None else None,
        ),
        plasma_profile_status=checks.plasma_sample.status,
        plasma_profile_message=checks.plasma_sample.message,
        target_coverage=(
            _target_coverage_out(summary.target_coverage) if summary.target_coverage is not None else None
        ),
        quality_failures=checks.quality_failures,
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


def _recurrence_out(recurrence: NiptRecurrence | None) -> NiptRecurrenceOut:
    if recurrence is None:
        # get_family_nipt_variants always counts the page slice's recurrence.
        raise RuntimeError("NIPT variant was serialized before its recurrence was counted")
    counts = recurrence.counts
    return NiptRecurrenceOut(
        other_samples=recurrence.other_samples,
        carriers=counts.carriers,
        maternal=counts.maternal,
        paternal_low_vaf=counts.paternal_low_vaf,
        unexplained_low_vaf=counts.unexplained_low_vaf,
        unexplained_median_vaf=counts.unexplained_median_vaf,
        protection=recurrence.protection,  # type: ignore[arg-type]
    )


def _represented_by(site: NiptSiteObservation | None, representation: str) -> str | None:
    """The record whose call the analysis read for a sample, where it read it off another
    record (``representation``); None where the variant's own record holds the call."""
    if site is None or site.representation != representation:
        return None
    return site.represented_by


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
        father_alt_reads=site.father_alt_reads if site is not None else None,
        cf_call_variant_id=_represented_by(site, PLASMA_CALL_OTHER_REPRESENTATION),
        father_call_variant_id=_represented_by(site, FATHER_CALL_OTHER_REPRESENTATION),
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
        recurrence=_recurrence_out(item.recurrence),
    )
    if item.variant_out is None:
        # get_family_nipt_variants always hydrates the page slice.
        raise RuntimeError("NIPT variant was serialized before hydration")
    return NiptVariantOut(**item.variant_out.model_dump(by_alias=True), nipt=nipt)


@router.get("/{family_id}/nipt/summary", response_model=NiptSummaryOut)
async def get_family_nipt_summary(
    family_id: str,
    project_id: str | None = None,
    analysis: NiptAnalysisParam = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> NiptSummaryOut:
    summary = await load_family_nipt_summary(
        session,
        family_id=family_id,
        user=user,
        project_id=project_id,
        analysis=analysis,
    )
    result = summary.analysis
    return NiptSummaryOut(
        family_id=family_id,
        analysis=summary.trio.analysis,
        fetuses=list(summary.trio.fetus_sample_ids),
        multiple_pregnancy_note=multiple_pregnancy_note(len(summary.trio.fetus_sample_ids)),
        fetal_fraction=_nipt_fetal_fraction_out(result.fetal_fraction),
        category_counts=result.category_counts,
        filter_counts=result.filter_counts,
        qc=_qc_out(summary),
    )


@router.get("/{family_id}/nipt/statistics", response_model=NiptVariantStatisticsOut)
async def get_family_nipt_statistics(
    family_id: str,
    project_id: str | None = None,
    analysis: NiptAnalysisParam = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> NiptVariantStatisticsOut:
    """The variant summary's NIPT-M tab: the plasma's sites by the mother's and the fetus's
    genotype, transmission per autosome, the father's genotypes and the plasma calls' funnel,
    from the NIPT page's analysis, and what the assay's artifact list did to it. Descriptive;
    the signed record does not hold it."""
    summary, artifacts = await get_family_nipt_summary_with_artifacts(
        session,
        family_id=family_id,
        user=user,
        project_id=project_id,
        analysis=analysis,
    )
    result = summary.analysis
    statistics = nipt_variant_statistics(result, NiptQualityThresholds())
    return NiptVariantStatisticsOut(
        family_id=family_id,
        fetal_fraction=_nipt_fetal_fraction_out(result.fetal_fraction),
        qc=_qc_out(summary),
        filter_counts=result.filter_counts,
        caller_filters=result.caller_filters,
        classified_sites=statistics.classified_sites,
        groups=statistics.groups,
        not_classified=statistics.not_classified,
        vaf_bin_width=VAF_BIN_WIDTH,
        chromosomes=statistics.chromosomes,
        chromosome_mark_min_sites=CHROMOSOME_MARK_MIN_SITES,
        father_genotypes=statistics.father_genotypes,
        artifacts=NiptArtifactCountsOut(
            listed=artifacts.listed,
            in_family=artifacts.in_family,
            removed=artifacts.removed,
            protected=artifacts.protected,
        ),
    )


def _nipt_variant_search(
    category: list[int] | None = Query(None),
    min_confidence: FiniteThreshold = None,
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
    canonical_only: bool = False,
    mane_only: bool = False,
    lof_only: bool = False,
    # The review tags, by the family small-variant list's names and rules: the variants
    # with one of these tags (a variant's own or its compound-het pair's), and without
    # one of the excluded ones. review_tag=report with no other filter lists what a
    # sign-out freezes.
    review_tags: list[str] = Query(default_factory=list, alias="review_tag"),
    exclude_review_tags: list[str] = Query(default_factory=list, alias="exclude_review_tag"),
    # The recurrence filter: leave out the variants that are an unexplained low-VAF call in
    # more than this many other cfDNA samples of the assay (unless ClinVar or a population
    # frequency protects them).
    max_unexplained_recurrence: int | None = Query(None, ge=0),
    # The pregnancy whose monogenic NIPT is read, of a family that holds several.
    analysis: NiptAnalysisParam = None,
) -> dict[str, Any]:
    """A NIPT variant search, as ``get_family_nipt_variants`` takes it: the variant list and
    its CSV export read the same parameters, so the file holds the list the page showed."""
    return {
        "query_filters": {
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
        "categories": category,
        "min_confidence": min_confidence,
        "inheritance": inheritance,
        "include_not_inherited": include_not_inherited,
        "de_novo_priority": de_novo_priority,
        "review_tags": review_tags,
        "exclude_review_tags": exclude_review_tags,
        "max_unexplained_recurrence": max_unexplained_recurrence,
        "analysis": analysis,
    }


@router.get("/{family_id}/nipt/variants", response_model=NiptVariantPage)
async def get_family_nipt_variants_page(
    family_id: str,
    page: int = 1,
    page_size: int = Query(default=100, ge=0, le=MAX_VARIANT_PAGE_SIZE),
    project_id: str | None = None,
    search: dict[str, Any] = Depends(_nipt_variant_search),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> NiptVariantPage:
    result = await get_family_nipt_variants(
        session,
        family_id=family_id,
        user=user,
        project_id=project_id,
        page=page,
        page_size=page_size,
        **search,
    )
    trio = result.trio
    return NiptVariantPage(
        family_id=family_id,
        trio=NiptTrioOut(
            cfdna_sample_id=trio.cfdna_sample_id,
            father_sample_id=trio.father_sample_id,
            fetus_sample_ids=list(trio.fetus_sample_ids),
        ),
        total=result.total,
        total_is_estimated=result.total_is_estimated,
        count_limit=result.count_limit,
        fetal_fraction=_nipt_fetal_fraction_out(result.fetal_fraction),
        variants=[_nipt_variant_out(item) for item in result.variants],
        recessive_genes=[_recessive_gene_out(gene) for gene in result.recessive_genes],
        not_classified=[
            NiptNotClassifiedOut(variant_id=variant_id, reason=reason)  # type: ignore[arg-type]
            for variant_id, reason in sorted(result.not_classified.items())
        ],
    )


# The NIPT export's columns: the small-variant export's, but its genotypes and its members
# without calls. The plasma's GT is no genotype (a mixture of the mother's and the fetus's
# DNA), the fetus has no sample of its own, and the NIPT list reads no member's genotype for an
# inheritance filter. The NIPT columns after them hold what the list shows of each variant: the
# fetal fraction it was classified against (the same on every row), its category, the plasma's
# and the father's reads, the fetal-inheritance probabilities, the de novo triage and the
# recurrence in the assay's other cfDNA samples.
_NIPT_SMALL_VARIANT_COLUMNS: list[tuple[str, str]] = [
    (field, label)
    for field, label in _FAMILY_EXPORT_COLUMNS
    if field not in {"genotypes", _MEMBERS_WITHOUT_CALLS_FIELD}
]

_NIPT_FIELD = "nipt."
_FETAL_FRACTION_FIELD = "fetal_fraction"
_FETAL_FRACTION_LOW_CONFIDENCE_FIELD = "fetal_fraction_low_confidence"
_MULTIPLE_PREGNANCY_FIELD = "multiple_pregnancy_note"

_NIPT_COLUMNS: list[tuple[str, str]] = [
    (_FETAL_FRACTION_FIELD, "Fetal fraction"),
    (_FETAL_FRACTION_LOW_CONFIDENCE_FIELD, "Fetal fraction low confidence"),
    (_MULTIPLE_PREGNANCY_FIELD, "Multiple pregnancy note"),
    ("nipt.category", "NIPT category"),
    ("nipt.category_label", "NIPT category label"),
    ("nipt.maternal_state", "Maternal state"),
    ("nipt.fetal_inheritance", "Fetal inheritance"),
    ("nipt.confidence", "NIPT confidence"),
    ("nipt.runner_up_category", "Runner-up category"),
    ("nipt.runner_up_confidence", "Runner-up confidence"),
    ("nipt.observed_vaf", "Observed VAF"),
    ("nipt.expected_vaf", "Expected VAF"),
    ("nipt.cf_alt_reads", "Plasma alt reads"),
    ("nipt.cf_depth", "Plasma depth"),
    # Yes where the plasma has no call there and its depth is read from its target coverage.
    ("nipt.cf_depth_estimated", "Plasma depth estimated"),
    # The record a sample's call was read off, where its VCF writes the allele another way
    # (an MNV for its SNVs, or the reverse); empty where the row's own record holds the call.
    ("nipt.cf_call_variant_id", "Plasma call read from"),
    ("nipt.father_state", "Father state"),
    ("nipt.father_vaf", "Father VAF"),
    ("nipt.father_depth", "Father depth"),
    ("nipt.father_alt_reads", "Father alt reads"),
    ("nipt.father_call_variant_id", "Father call read from"),
    ("nipt.paternal_transmission_probability", "P(paternal allele inherited)"),
    ("nipt.maternal_allele_probability", "P(maternal allele inherited)"),
    ("nipt.fetal_hom_alt_probability", "P(fetus homozygous)"),
    ("nipt.fetal_genotype_posterior", "Fetal genotype posterior"),
    ("nipt.flags", "NIPT flags"),
    ("nipt.quality_failures", "Quality failures"),
    # The de novo view's triage; empty in the other views.
    ("nipt.de_novo.label", "De novo priority"),
    ("nipt.de_novo.score", "De novo score"),
    ("nipt.de_novo.window", "De novo window"),
    ("nipt.de_novo.reasons", "De novo reasons"),
    ("nipt.recurrence.other_samples", "Other cfDNA samples"),
    ("nipt.recurrence.carriers", "Recurrence carriers"),
    ("nipt.recurrence.maternal", "Recurrence maternal"),
    ("nipt.recurrence.paternal_low_vaf", "Recurrence paternal low-VAF"),
    ("nipt.recurrence.unexplained_low_vaf", "Recurrence unexplained low-VAF"),
    ("nipt.recurrence.unexplained_median_vaf", "Recurrence unexplained median VAF"),
    ("nipt.recurrence.protection", "Recurrence protection"),
]

NIPT_EXPORT_COLUMNS: list[tuple[str, str]] = [*_NIPT_SMALL_VARIANT_COLUMNS, *_NIPT_COLUMNS]


def _nipt_value_text(value: Any) -> str:
    """A NIPT value as a cell: nothing for none, yes or no for a flag, a list joined by "; ",
    the fetal genotype posterior as "0/1=0.8; 1/1=0.2"."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    if isinstance(value, dict):
        return "; ".join(f"{key}={item}" for key, item in value.items())
    return str(value)


def _nipt_export_cell(
    row: NiptVariantOut, field: str, fetal_fraction: NiptFetalFractionOut, pregnancy_note: str | None
) -> str:
    """The text of ``row``'s cell of the NIPT export column ``field``; the fetal fraction's
    columns hold ``fetal_fraction``, and the multiple-pregnancy column the note of a pregnancy
    of two or more fetuses (``multiple_pregnancy_note``), each the same on every row."""
    if field == _FETAL_FRACTION_FIELD:
        return _nipt_value_text(fetal_fraction.ff)
    if field == _FETAL_FRACTION_LOW_CONFIDENCE_FIELD:
        return _nipt_value_text(fetal_fraction.low_confidence)
    if field == _MULTIPLE_PREGNANCY_FIELD:
        return _nipt_value_text(pregnancy_note)
    if not field.startswith(_NIPT_FIELD):
        return _family_export_cell(row, field)
    value: Any = row.nipt
    for name in field.removeprefix(_NIPT_FIELD).split("."):
        value = getattr(value, name) if value is not None else None
    return _nipt_value_text(value)


@router.get("/{family_id}/nipt/variants/export")
async def export_family_nipt_variants_csv(
    family_id: str,
    project_id: str | None = None,
    search: dict[str, Any] = Depends(_nipt_variant_search),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> StreamingResponse:
    """Download the NIPT variant list of this search as a CSV file: every variant the list
    classifies, in its order.

    The list classifies the first variants of its search, up to its limit. A search that
    matched more is not cut silently: the file name says TRUNCATED and the X-CoGA-Export-*
    headers report it (X-CoGA-Export-Truncated-Reason: candidate-limit), since a match can be
    missing however few rows the file holds.
    """
    result = await get_family_nipt_variants(
        session,
        family_id=family_id,
        user=user,
        project_id=project_id,
        page=1,
        page_size=None,
        panel_search="nipt_variants_export",
        **search,
    )
    rows = [_nipt_variant_out(item) for item in result.variants]
    cell = partial(
        _nipt_export_cell,
        fetal_fraction=_nipt_fetal_fraction_out(result.fetal_fraction),
        pregnancy_note=multiple_pregnancy_note(len(result.trio.fetus_sample_ids)),
    )
    # The file names the pregnancy whose analysis it holds: a family holds one per pregnancy.
    pregnancy = pregnancy_of(result.trio.analysis)
    return StreamingResponse(
        iter([csv_document(NIPT_EXPORT_COLUMNS, rows, cell)]),
        media_type="text/csv",
        headers=export_response_headers(
            f"family-{family_id}-{pregnancy}-nipt-variants" if pregnancy else f"family-{family_id}-nipt-variants",
            rows=len(rows),
            truncated=result.total_is_estimated,
            limit=result.count_limit if result.count_limit is not None else NIPT_VARIANT_FETCH_LIMIT,
            reason=TRUNCATED_BY_CANDIDATE_LIMIT if result.total_is_estimated else None,
        ),
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
    analysis: NiptAnalysisParam = None,
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
        analysis=analysis,
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
        coverage_notes=list(coverage.notes),
    )


@router.get("/{family_id}/nipt/coverage/targets", response_model=NiptGeneTargetsOut)
async def get_family_nipt_gene_coverage(
    family_id: str,
    gene: str = Query(..., min_length=1, max_length=64),
    project_id: str | None = None,
    analysis: NiptAnalysisParam = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> NiptGeneTargetsOut:
    """One gene's capture targets and their depth: a row of the coverage page, opened."""
    result = await get_family_nipt_gene_targets(
        session, family_id=family_id, user=user, gene=gene, project_id=project_id, analysis=analysis
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
