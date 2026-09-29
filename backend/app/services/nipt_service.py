"""Wire the monogenic NIPT analysis core to family data (Phase 5).

Resolves the NIPT trio for a family, loads the joined father + cfDNA calls from
ClickHouse, turns them into ``NiptSiteObservation`` rows, and runs the pure
analysis core. The site-building helpers are pure (and unit-tested); only
``run_family_nipt_analysis`` performs I/O.

See docs/monogenic-nipt.md and docs/monogenic-nipt-classification.md.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import bindparam, text

from typing import TYPE_CHECKING, Any

from .clickhouse_family_variants import (
    _fetch_panel_constraints,
    _fetch_small_variant_rows,
    _hydrate_small_variant_outs,
)
from .clickhouse_variant_queries import _small_variant_out, _split_gene_terms
from .clickhouse_variant_records import (
    PanelFilterConstraints,
    SmallVariantCall,
    SmallVariantRecord,
)

if TYPE_CHECKING:
    from ..schemas import VariantOut
from .clickhouse_interval_tracks import fetch_interval_track_rows
from .data_scope import normalize_chromosome
from .family_metadata_context import FamilyMetadataContext, build_family_metadata_context
from .family_variant_filters import SmallVariantQueryFilters
from .metadata_service import get_family_record
from .access_control import CurrentUser
from .nipt import NiptTrio, nipt_assay_key, resolve_nipt_trio
from .nipt_artifact_pg import load_nipt_artifact_ids
from .nipt_coverage import (
    DEFAULT_MIN_DEPTH,
    CoverageInterval,
    NiptCoverageSummary,
    TargetRegion,
    summarize_on_target_coverage,
)
from .nipt_analysis import (
    FetalFractionEstimate,
    NiptAnalysisResult,
    NiptClassification,
    NiptQualityThresholds,
    NiptSiteObservation,
    classify_site,
    estimate_fetal_fraction,
    run_nipt_analysis,
)

# Bounds the in-memory classification for the variant list. Clinical use always
# narrows by gene/panel/region, so this is a safety cap, not a normal limit.
_NIPT_VARIANT_FETCH_LIMIT = 5000

# NIPT inheritance presets that map to a set of categories. recessive_at_risk is
# not here -- it needs cross-variant gene pairing and is handled separately in
# get_family_nipt_variants.
_INHERITANCE_CATEGORIES: dict[str, frozenset[int]] = {
    "de_novo": frozenset({1}),
    "paternal_dominant": frozenset({7}),
    "maternal_dominant": frozenset({3, 4}),
}
_RECESSIVE_AT_RISK = "recessive_at_risk"

_AUTOSOMES = {str(index) for index in range(1, 23)}


def _is_autosomal(chrom: str) -> bool:
    return chrom.lower().removeprefix("chr") in _AUTOSOMES


def _gt_alleles(gt: str | None) -> list[str]:
    if not gt:
        return []
    return [token for token in gt.replace("|", "/").split("/") if token not in ("", ".")]


def derive_father_state(call: SmallVariantCall | None) -> str:
    """Map a germline genotype to hom_ref / het / hom_alt / missing."""
    alleles = _gt_alleles(call.gt) if call is not None else []
    if not alleles:
        return "missing"
    alt_count = sum(1 for allele in alleles if allele != "0")
    if alt_count == 0:
        return "hom_ref"
    if len(alleles) >= 2 and alt_count >= len(alleles):
        return "hom_alt"
    return "het"


def _cfdna_alt_reads(call: SmallVariantCall) -> int | None:
    if call.ad and len(call.ad) > 1:
        return call.ad[1]
    return None


def _cfdna_vaf(call: SmallVariantCall, alt_reads: int | None) -> float | None:
    if call.af:
        return call.af[0]
    if alt_reads is not None and call.dp:
        return alt_reads / call.dp
    return None


def build_nipt_observations(
    records: list[SmallVariantRecord],
    *,
    father_sample_id: str,
    cfdna_sample_id: str,
) -> list[NiptSiteObservation]:
    """Turn family variant records into per-site NIPT observations.

    Each record is expected to carry per-sample calls; the cfDNA sample provides
    the signal we classify and the father sample provides the genotype that
    prunes candidate categories. Sites without a cfDNA call are skipped.
    """
    sites: list[NiptSiteObservation] = []
    for record in records:
        calls = {call.sample: call for call in record.calls}
        cf_call = calls.get(cfdna_sample_id)
        if cf_call is None:
            continue
        father_call = calls.get(father_sample_id)
        alt_reads = _cfdna_alt_reads(cf_call)
        if alt_reads is not None:
            present = alt_reads > 0
        else:
            present = any(allele != "0" for allele in _gt_alleles(cf_call.gt))
        sites.append(
            NiptSiteObservation(
                variant_id=record.variant_id,
                chrom=record.chr,
                pos=record.start,
                is_autosomal=_is_autosomal(record.chr),
                cf_present=present,
                cf_dp=cf_call.dp,
                cf_alt_reads=alt_reads,
                cf_vaf=_cfdna_vaf(cf_call, alt_reads),
                cf_qual=record.qual,
                father_state=derive_father_state(father_call),
                father_dp=father_call.dp if father_call is not None else None,
                father_qual=None,
            )
        )
    return sites


@dataclass(slots=True)
class NiptClassifiedVariant:
    """A family variant record paired with its NIPT classification.

    ``variant_out`` is the full small-variant serialization (with review/cohort
    hydrated), attached for the page slice so the variant list carries the same
    payload as the small-variant view."""

    record: SmallVariantRecord
    classification: NiptClassification
    variant_out: "VariantOut | None" = None


@dataclass(slots=True)
class NiptVariantsResult:
    fetal_fraction: FetalFractionEstimate
    total: int
    variants: list[NiptClassifiedVariant]


async def _load_family_records(context: FamilyMetadataContext) -> list[SmallVariantRecord]:
    # No filters: the summary/FF estimation spans the whole family cohort (FF
    # needs every category-7 site and the category counts are cohort-wide).
    filters = SmallVariantQueryFilters(page=1, page_size=100)
    return await _fetch_small_variant_rows(context, filters, limit=None)


async def _resolve_trio_and_context(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    project_id: str | None,
) -> tuple[NiptTrio, FamilyMetadataContext, str]:
    family = await get_family_record(session, family_id, user)
    trio = resolve_nipt_trio(family)
    if trio is None:
        raise HTTPException(
            status_code=400,
            detail="Family is not configured for monogenic NIPT analysis",
        )
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        project_id=project_id,
    )
    return trio, context, nipt_assay_key(family, trio)


async def _load_artifact_lookup(
    session: AsyncSession, context: FamilyMetadataContext, assay_key: str
):
    artifact_ids = await load_nipt_artifact_ids(
        session, assembly_id=context.assembly_id, assay_key=assay_key
    )
    return artifact_ids.__contains__


def _family_sites(
    records: list[SmallVariantRecord], trio: NiptTrio
) -> list[NiptSiteObservation]:
    return build_nipt_observations(
        records,
        father_sample_id=trio.father_sample_id,
        cfdna_sample_id=trio.cfdna_sample_id,
    )


async def run_family_nipt_analysis(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    project_id: str | None = None,
    qc: NiptQualityThresholds | None = None,
    external_ff: float | None = None,
) -> NiptAnalysisResult:
    trio, context, assay_key = await _resolve_trio_and_context(
        session, family_id=family_id, user=user, project_id=project_id
    )
    artifact_lookup = await _load_artifact_lookup(session, context, assay_key)
    records = await _load_family_records(context)
    # The whole family callset goes through the analysis: pure CPU work, so it runs in a
    # worker thread and other requests are served meanwhile (#527).
    return await asyncio.to_thread(
        _analyse_family_sites, records, trio, qc or NiptQualityThresholds(), artifact_lookup, external_ff
    )


def _analyse_family_sites(
    records: list[SmallVariantRecord],
    trio: NiptTrio,
    qc: NiptQualityThresholds,
    artifact_lookup: Any,
    external_ff: float | None,
) -> NiptAnalysisResult:
    return run_nipt_analysis(
        _family_sites(records, trio),
        qc,
        artifact_lookup=artifact_lookup,
        external_ff=external_ff,
    )


def _estimate_cohort_fetal_fraction(
    records: list[SmallVariantRecord],
    trio: NiptTrio,
    qc: NiptQualityThresholds,
    external_ff: float | None,
) -> FetalFractionEstimate:
    return estimate_fetal_fraction(_family_sites(records, trio), qc, external_ff=external_ff)


def _classify_candidates(
    records: list[SmallVariantRecord],
    trio: NiptTrio,
    artifact_ids: Any,
    ff_estimate: FetalFractionEstimate,
    qc: NiptQualityThresholds,
    min_confidence: float | None,
) -> tuple[list[NiptClassifiedVariant], dict[str, NiptSiteObservation]]:
    observations = {
        observation.variant_id: observation for observation in _family_sites(records, trio)
    }
    classified: list[NiptClassifiedVariant] = []
    for record in records:
        if record.variant_id in artifact_ids:
            continue
        observation = observations.get(record.variant_id)
        if observation is None:
            continue
        classification = classify_site(observation, ff_estimate, qc)
        if min_confidence is not None and (
            classification.category is None or classification.confidence < min_confidence
        ):
            continue
        classified.append(NiptClassifiedVariant(record=record, classification=classification))
    return classified, observations


def _build_nipt_query_filters(query_filters: dict) -> SmallVariantQueryFilters:
    """Build a SmallVariantQueryFilters from the NIPT filter set.

    NIPT reuses the full small-variant filter form, so the same annotation /
    frequency / in-silico / pathogenicity / location filters apply. Per-sample
    genotype, review-state, and phenotype-prioritisation filters do not apply to
    cfDNA (the maternal/fetal call is inferred) and are not forwarded; the
    maternal/fetal `category` filter and `inheritance` preset are applied on the
    classification afterwards rather than here.
    """
    return SmallVariantQueryFilters(
        page=1,
        page_size=100,
        chromosome=query_filters.get("chr"),
        start=query_filters.get("start"),
        end=query_filters.get("end"),
        intervals=query_filters.get("intervals"),
        phase_set=query_filters.get("ps"),
        variant_type=query_filters.get("type"),
        source=query_filters.get("source"),
        gene=query_filters.get("gene"),
        transcript=query_filters.get("transcript"),
        exclude_gene=query_filters.get("exclude_gene"),
        exclude_intervals=query_filters.get("exclude_intervals"),
        rsid=query_filters.get("rsid"),
        hgvsc=query_filters.get("hgvsc"),
        hgvsp=query_filters.get("hgvsp"),
        impact=query_filters.get("impact") or [],
        effect=query_filters.get("effect") or [],
        clinvar=query_filters.get("clinvar") or [],
        exclude_clinvar=query_filters.get("exclude_clinvar") or [],
        clinvar_overrides_frequency=bool(query_filters.get("clinvar_overrides_frequency", False)),
        sift=query_filters.get("sift"),
        polyphen=query_filters.get("polyphen"),
        max_gnomad_af=query_filters.get("max_gnomad_af"),
        max_gnomad_exomes_af=query_filters.get("max_gnomad_exomes_af"),
        max_gnomad_genomes_af=query_filters.get("max_gnomad_genomes_af"),
        max_gnomad_popmax_af=query_filters.get("max_gnomad_popmax_af"),
        max_topmed_af=query_filters.get("max_topmed_af"),
        max_gnomad_ac=query_filters.get("max_gnomad_ac"),
        max_gnomad_hom_count=query_filters.get("max_gnomad_hom_count"),
        max_gnomad_hemi_count=query_filters.get("max_gnomad_hemi_count"),
        min_cadd=query_filters.get("min_cadd"),
        min_revel=query_filters.get("min_revel"),
        min_spliceai=query_filters.get("min_spliceai"),
        canonical_only=bool(query_filters.get("canonical_only", False)),
        mane_only=bool(query_filters.get("mane_only", False)),
        lof_only=bool(query_filters.get("lof_only", False)),
        panel_id=query_filters.get("panel_id"),
    )


def _record_genes(record: SmallVariantRecord) -> set[str]:
    return {gene.upper() for gene in (record.gene_symbols or []) if gene}


def _recessive_at_risk_variants(
    classified: list[NiptClassifiedVariant],
    observations: dict[str, NiptSiteObservation],
) -> list[NiptClassifiedVariant]:
    """Keep variants in genes where *both* parents carry a candidate variant.

    A variant marks the mother a carrier when her inferred genotype is het or hom
    (categories 2-6), and the father a carrier when his observed genotype is het
    or hom-alt. A gene is at recessive risk when it has at least one maternal and
    at least one paternal carrier variant (the same variant counts for both when
    both parents carry it). Every carrier variant in those genes is returned so
    the analyst can read whether the fetus inherited none, one, or both alleles
    off the categories (e.g. cat 4/6 = fetus homozygous; a transmitted maternal
    cat 3 plus a transmitted paternal cat 7 in the same gene = compound het; cat 2
    or cat 8 = a parental allele the fetus did not inherit).

    Run the gene/consequence/frequency filters first so the carriers are
    plausibly causal, and min_confidence so the inferred maternal calls are
    trustworthy.
    """

    def father_state(variant: NiptClassifiedVariant) -> str:
        observation = observations.get(variant.record.variant_id)
        return observation.father_state if observation is not None else "missing"

    def is_maternal_carrier(variant: NiptClassifiedVariant) -> bool:
        return variant.classification.maternal_state in ("het", "hom")

    def is_paternal_carrier(variant: NiptClassifiedVariant) -> bool:
        return father_state(variant) in ("het", "hom_alt")

    maternal_genes: set[str] = set()
    paternal_genes: set[str] = set()
    for variant in classified:
        genes = _record_genes(variant.record)
        if is_maternal_carrier(variant):
            maternal_genes |= genes
        if is_paternal_carrier(variant):
            paternal_genes |= genes
    recessive_genes = maternal_genes & paternal_genes

    kept: list[NiptClassifiedVariant] = []
    for variant in classified:
        if not (_record_genes(variant.record) & recessive_genes):
            continue
        if is_maternal_carrier(variant) or is_paternal_carrier(variant):
            kept.append(variant)
    return kept


async def get_family_nipt_variants(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    project_id: str | None = None,
    query_filters: dict | None = None,
    categories: list[int] | None = None,
    min_confidence: float | None = None,
    inheritance: str | None = None,
    page: int = 1,
    page_size: int = 100,
    qc: NiptQualityThresholds | None = None,
    external_ff: float | None = None,
) -> NiptVariantsResult:
    qc = qc or NiptQualityThresholds()

    recessive_mode = inheritance == _RECESSIVE_AT_RISK
    wanted: set[int] = set(categories or [])
    if inheritance and not recessive_mode:
        preset = _INHERITANCE_CATEGORIES.get(inheritance)
        if preset is None:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported NIPT inheritance preset: {inheritance}",
            )
        wanted = (wanted & set(preset)) if wanted else set(preset)

    trio, context, assay_key = await _resolve_trio_and_context(
        session, family_id=family_id, user=user, project_id=project_id
    )
    artifact_ids = await load_nipt_artifact_ids(
        session, assembly_id=context.assembly_id, assay_key=assay_key
    )

    # Fetal fraction is estimated cohort-wide (the FF/2 category-7 sites are
    # rarely inside the clinical filter), then the filtered subset is classified
    # against that FF. Both steps are CPU work over many sites, so they run in a
    # worker thread (#527).
    cohort_records = await _load_family_records(context)
    ff_estimate = await asyncio.to_thread(
        _estimate_cohort_fetal_fraction, cohort_records, trio, qc, external_ff
    )

    filters = _build_nipt_query_filters(query_filters or {})
    panel_constraints = PanelFilterConstraints()
    if filters.panel_id:
        panel_constraints = await _fetch_panel_constraints(
            session, filters.panel_id, assembly_id=context.assembly_id
        )
        if not panel_constraints.genes and not panel_constraints.regions:
            return NiptVariantsResult(fetal_fraction=ff_estimate, total=0, variants=[])

    records = await _fetch_small_variant_rows(
        context,
        filters,
        panel_constraints=panel_constraints,
        limit=_NIPT_VARIANT_FETCH_LIMIT,
    )
    classified, observations = await asyncio.to_thread(
        _classify_candidates, records, trio, artifact_ids, ff_estimate, qc, min_confidence
    )

    # The category filter is applied after classification so recessive_at_risk
    # can group across the full candidate set by gene.
    if recessive_mode:
        classified = _recessive_at_risk_variants(classified, observations)
    elif wanted:
        classified = [item for item in classified if item.classification.category in wanted]

    total = len(classified)
    offset = max(0, (page - 1) * page_size)
    page_items = classified[offset : offset + page_size]

    # Serialize the page slice as full small-variant payloads (and hydrate their
    # review / internal-cohort / gene-constraint data) so the NIPT variant list
    # carries the same shape as the small-variant view, classification aside.
    variant_outs = [_small_variant_out(item.record) for item in page_items]
    await _hydrate_small_variant_outs(session, context=context, variants=variant_outs)
    for item, variant_out in zip(page_items, variant_outs):
        item.variant_out = variant_out

    return NiptVariantsResult(fetal_fraction=ff_estimate, total=total, variants=page_items)


async def _fetch_labeled_gene_regions(
    session: AsyncSession, *, terms: list[str], assembly_id: str | None
) -> list[TargetRegion]:
    """Resolve gene symbols/ids to regions labelled by the gene symbol."""
    cleaned = [str(term).strip() for term in terms if str(term).strip()]
    if not cleaned:
        return []
    clauses = ["(upper(hgnc_symbol) IN :terms OR upper(gene_id) IN :terms)"]
    params: dict = {"terms": [term.upper() for term in cleaned]}
    bind_params: list[Any] = [bindparam("terms", expanding=True)]
    if assembly_id:
        clauses.append("assembly_id = CAST(:assembly_id AS uuid)")
        params["assembly_id"] = assembly_id
    result = await session.execute(
        text(
            f"""
            SELECT hgnc_symbol, chr, start, "end" AS end
            FROM genes
            WHERE {' AND '.join(clauses)}
            """
        ).bindparams(*bind_params),
        params,
    )
    regions: list[TargetRegion] = []
    for row in result.mappings().all():
        chrom = normalize_chromosome(str(row["chr"]))
        start, end = int(row["start"]), int(row["end"])
        regions.append(
            TargetRegion(
                label=str(row["hgnc_symbol"] or f"{chrom}:{start}-{end}"),
                chrom=chrom,
                start=start,
                end=end,
            )
        )
    return regions


async def _resolve_nipt_target_regions(
    session: AsyncSession,
    *,
    family,
    context: FamilyMetadataContext,
    gene: str | None,
    panel_id: str | None,
) -> list[TargetRegion]:
    """Target = a gene query, a panel, or (fallback) the family ROI.

    Gene-derived regions (from a gene query or a panel's genes) are labelled by
    their gene symbol; a panel's explicit regions are labelled by coordinates.
    """
    regions: list[TargetRegion] = []
    gene_terms: list[str] = list(_split_gene_terms(gene)) if gene else []
    if panel_id:
        constraints = await _fetch_panel_constraints(
            session, panel_id, assembly_id=context.assembly_id
        )
        gene_terms.extend(constraints.genes)
        for region in constraints.regions:
            chrom = normalize_chromosome(region.chr)
            regions.append(
                TargetRegion(
                    label=f"{chrom}:{region.start}-{region.end}",
                    chrom=chrom,
                    start=region.start,
                    end=region.end,
                )
            )
    if gene_terms:
        regions.extend(
            await _fetch_labeled_gene_regions(
                session, terms=gene_terms, assembly_id=context.assembly_id
            )
        )
    if not regions and family.roi is not None:
        roi = family.roi
        regions.append(
            TargetRegion(
                label=roi.label or roi.query,
                chrom=normalize_chromosome(roi.chr),
                start=roi.start,
                end=roi.end,
            )
        )
    return regions


async def _load_coverage_intervals(
    assembly_name: str, sample_uuid: str, target_regions: list[TargetRegion]
) -> list[CoverageInterval]:
    chromosomes = sorted({region.chrom for region in target_regions})
    rows = await fetch_interval_track_rows(
        assembly_name,
        sample_uuid=sample_uuid,
        track_type="coverage",
        chromosomes=chromosomes,
    )
    intervals: list[CoverageInterval] = []
    for row in rows:
        value = row.get("value")
        if value is None:
            continue
        intervals.append(
            CoverageInterval(
                chrom=normalize_chromosome(str(row["chr"])),
                start=int(row["start"]),
                end=int(row["end"]),
                value=float(value),
            )
        )
    return intervals


async def get_family_nipt_coverage(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    project_id: str | None = None,
    gene: str | None = None,
    panel_id: str | None = None,
    min_depth: float = DEFAULT_MIN_DEPTH,
) -> NiptCoverageSummary:
    family = await get_family_record(session, family_id, user)
    trio = resolve_nipt_trio(family)
    if trio is None:
        raise HTTPException(
            status_code=400,
            detail="Family is not configured for monogenic NIPT analysis",
        )
    context = await build_family_metadata_context(
        session, family_identifier=family_id, user=user, project_id=project_id
    )
    target_regions = await _resolve_nipt_target_regions(
        session, family=family, context=context, gene=gene, panel_id=panel_id
    )
    sample_uuid = context.sample_name_to_uuid.get(trio.cfdna_sample_id)
    coverage: list[CoverageInterval] = []
    if sample_uuid and context.assembly_name and target_regions:
        coverage = await _load_coverage_intervals(
            context.assembly_name, sample_uuid, target_regions
        )
    return summarize_on_target_coverage(
        target_regions, coverage, min_depth=min_depth
    )
