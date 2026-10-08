"""Wire the monogenic NIPT analysis core to family data (Phase 5).

Resolves the NIPT trio for a family, loads the father + cfDNA calls from ClickHouse and the
plasma's (and father's) per-target coverage, turns them into ``NiptSiteObservation`` rows,
and runs the pure analysis core (nipt_analysis) and the de novo triage and recessive risk
(nipt_triage). The site-building helpers are pure (and unit-tested); the coroutines do I/O.

Two kinds of callset reach here:

- a joint VCF of the father and the plasma (source ``clair3``): every row holds both
  calls, a reference call reading ``0/0``;
- the per-sample ``nipt`` callset, one VCF per sample (family_package_nipt): a sample
  without a call at a variant had no alt read there the caller reported. The father's
  missing call reads as ``absent`` (reference), and a variant only the father has is a
  paternal allele the plasma did not show, its plasma depth taken from the plasma's
  per-target coverage.

See docs/monogenic-nipt.md.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import bindparam, text

from typing import TYPE_CHECKING, Any, Iterable, Sequence

from .clickhouse_family_variants import (
    _fetch_gene_regions,
    _fetch_panel_constraints,
    _fetch_small_variant_rows,
    _hydrate_small_variant_outs,
    count_cfdna_carriers,
)
from .clickhouse_variant_queries import _parse_interval_regions, _small_variant_out, _split_gene_terms
from .clickhouse_variant_records import (
    PanelFilterConstraints,
    SmallVariantCall,
    SmallVariantRecord,
    _annotation_clinvar,
    _annotation_population_frequencies,
    _coerce_float,
)

if TYPE_CHECKING:
    from ..schemas import VariantOut
from .clickhouse_interval_tracks import fetch_interval_track_rows
from .data_scope import normalize_chromosome
from .family_metadata_context import FamilyMetadataContext, build_family_metadata_context
from .family_variant_filters import SmallVariantQueryFilters
from .genotypes import classify_genotype
from .metadata_service import get_family_record
from .access_control import CurrentUser
from .nipt import NiptTrio, nipt_assay_key, resolve_nipt_trio
from .nipt_artifact_pg import assay_cfdna_carrier_samples, load_nipt_artifact_ids
from .nipt_coverage import (
    DEFAULT_MIN_DEPTH,
    CoverageInterval,
    NiptCoverageSummary,
    TargetRegion,
    summarize_on_target_coverage,
)
from .nipt_analysis import (
    FATHER_CALL_OTHER_REPRESENTATION,
    FATHER_CALL_OVERLAPS,
    MATERNAL_ALLELE_VAF_BOUNDARY,
    PLASMA_CALL_OTHER_REPRESENTATION,
    PLASMA_CALL_OVERLAPS,
    FetalFractionEstimate,
    NiptAnalysisResult,
    NiptClassification,
    NiptQualityThresholds,
    NiptSiteObservation,
    trusted_father_state,
    classify_site,
    filter_sites_and_estimate_ff,
    run_nipt_analysis,
)
from .nipt_target_coverage import (
    CRITICAL_MEAN_DEPTH,
    TARGET_COVERAGE_TRACK_TYPE,
    SexChromosomeCoverage,
    TargetCoverageRow,
    TargetCoverageSummary,
    TargetDepthLookup,
    sex_chromosome_coverage,
    summarize_target_coverage,
    target_row_from_track,
)
from .nipt_triage import (
    DeNovoAnnotation,
    DeNovoTriage,
    RecessiveCandidate,
    RecessiveGeneRisk,
    de_novo_window,
    is_de_novo_candidate,
    recessive_gene_risks,
    triage_de_novo,
)
from .variant_prioritization import clinvar_may_assert_pathogenic
from .variant_upload_service import NIPT_SMALL_VARIANT_SOURCE

# Bounds the in-memory classification for the variant list: the first variants of the
# scope, in genomic order. Clinical use narrows by gene/panel/region, so this is a safety
# cap, not a normal limit; a list cut by it says so (total_is_estimated, count_limit).
_NIPT_VARIANT_FETCH_LIMIT = 5000

# The inheritance views of the variant list (the "Inheritance" control of the NIPT page).
DE_NOVO = "de_novo"
PATERNAL_INHERITED = "paternal_dominant"
MATERNAL_INHERITED = "maternal_dominant"
RECESSIVE_AT_RISK = "recessive_at_risk"
_INHERITANCE_VIEWS = (DE_NOVO, PATERNAL_INHERITED, MATERNAL_INHERITED, RECESSIVE_AT_RISK)
# The de novo labels each minimum priority keeps.
_DE_NOVO_PRIORITY_LABELS: dict[str, frozenset[str]] = {
    "high": frozenset({"high"}),
    "medium": frozenset({"high", "medium"}),
    "low": frozenset({"high", "medium", "low"}),
}

_AUTOSOMES = {str(index) for index in range(1, 23)}
_IMPACT_RANK = {"HIGH": 4, "MODERATE": 3, "LOW": 2, "MODIFIER": 1}
_POPULATION_AF_KEYS = ("gnomad_af", "gnomad_exomes_af", "gnomad_genomes_af", "gnomad_popmax_af", "topmed_af")
_SPLICEAI_KEYS = ("spliceai_ds_ag", "spliceai_ds_al", "spliceai_ds_dg", "spliceai_ds_dl")


def _is_autosomal(chrom: str) -> bool:
    return chrom.lower().removeprefix("chr") in _AUTOSOMES


# --------------------------------------------------------------------------- #
# A call's reads and the father's genotype class
# --------------------------------------------------------------------------- #


def _alt_reads(call: SmallVariantCall) -> int | None:
    if call.ad and len(call.ad) > 1:
        return call.ad[1]
    return None


def _ad_depth(call: SmallVariantCall) -> int | None:
    """Every read at the record: the sum of its allele depths, else DP."""
    if call.ad:
        total = sum(depth for depth in call.ad if depth is not None)
        if total > 0:
            return total
    return call.dp


def _call_vaf(call: SmallVariantCall, depth: int | None) -> float | None:
    """The consensus-read allele fraction, alt reads over every read at the site (never
    the caller's model-corrected AF, which the R validation does not use); AF when there is
    no AD."""
    alt = _alt_reads(call)
    if alt is not None and depth:
        return alt / depth
    if call.af:
        return call.af[0]
    return None


def _call_quality(call: SmallVariantCall) -> float | None:
    metrics = call.metrics or {}
    for key in ("TLOD", "QUAL"):
        if metrics.get(key) is not None:
            return metrics[key]
    return None


def derive_father_state(
    call: SmallVariantCall | None,
    qc: NiptQualityThresholds | None = None,
    *,
    depth: int | None = None,
    uncalled: str = "missing",
) -> str:
    """The father's genotype class (``nipt_analysis.FATHER_STATES``).

    From his allele depths, as the R NIPT-M paternal classes read a paternal sample: a call
    with fewer than 20 reads, fewer than 5 alt reads or a quality (TLOD, else QUAL) below 20
    is ``low_support``; then a VAF of 0.80 or more is ``hom_alt``, 0.20 or more ``het``,
    less ``low_vaf``. A call without an alt read is ``hom_ref`` at 20 reads or more. A call
    without allele depths falls back on its GT (the shared genotype classes, #511: a haploid
    ``1`` is ``hom_alt``, ``./0`` no call). No call is ``uncalled``: ``missing`` in a joint
    VCF, ``absent`` in a per-sample callset.

    ``depth`` overrides the call's own read count: the site depth of a record split from a
    multi-allelic one.
    """
    if call is None:
        return uncalled
    qc = qc or NiptQualityThresholds()
    reads = depth if depth is not None else _ad_depth(call)
    vaf = _call_vaf(call, reads)
    if vaf is None or not call.ad:
        genotype_class = classify_genotype(call.gt)
        return "missing" if genotype_class == "no_call" else genotype_class
    alt = _alt_reads(call) or 0
    if alt == 0:
        return "hom_ref" if (reads or 0) >= qc.min_father_dp else "low_support"
    quality = _call_quality(call)
    if (
        (reads is not None and reads < qc.min_father_dp)
        or alt < qc.min_father_alt_reads
        or (quality is not None and quality < qc.min_father_qual)
    ):
        return "low_support"
    if vaf >= qc.father_hom_alt_min_vaf:
        return "hom_alt"
    if vaf >= qc.father_het_min_vaf:
        return "het"
    return "low_vaf"


def restored_site_depths(records: Iterable[SmallVariantRecord], sample_id: str) -> dict[str, int]:
    """The site depth of each record a VCF split from a multi-allelic one (``bcftools norm
    -m-``), for one sample: ``variant_id -> AD[ref] + the alt reads of every sibling``.

    A split record keeps only its own allele's reads, so its alt fraction leaves out the
    reads of the other alleles. Siblings share the position, the reference depth and DP
    (the R NIPT-M adapter's rule). Records that are not split are not listed.
    """
    groups: dict[tuple[str, int, int, int | None], list[tuple[str, int]]] = {}
    for record in records:
        call = next((item for item in record.calls if item.sample == sample_id), None)
        if call is None or len(call.ad) != 2 or call.ad[0] is None or call.ad[1] is None:
            continue
        key = (record.chr, record.start, int(call.ad[0]), call.dp)
        groups.setdefault(key, []).append((record.variant_id, int(call.ad[1])))
    depths: dict[str, int] = {}
    for (_chrom, _pos, ref_depth, _dp), members in groups.items():
        if len(members) < 2:
            continue
        site_depth = ref_depth + sum(alt for _variant, alt in members)
        for variant_id, _alt in members:
            depths[variant_id] = site_depth
    return depths


def _lookup_depth(lookup: TargetDepthLookup | None, chrom: str, position: int) -> int | None:
    if lookup is None:
        return None
    depth = lookup.depth_at(chrom, position)
    return int(round(depth)) if depth is not None else None


def _allele_bases(chrom: str, pos: int, ref: str, alt: str) -> list[tuple[str, int, str, str]]:
    """The single-base substitutions an SNV or an MNV (REF and ALT of one length) is made
    of; [] for an indel."""
    if not ref or len(ref) != len(alt):
        return []
    return [
        (chrom, pos + offset, ref_base, alt_base)
        for offset, (ref_base, alt_base) in enumerate(zip(ref.upper(), alt.upper()))
        if ref_base != alt_base
    ]


@dataclass(slots=True)
class _BaseCall:
    record: SmallVariantRecord
    call: SmallVariantCall
    alt_reads: int


class _BaseCallIndex:
    """One sample's calls by the single-base substitutions they carry: where its VCF wrote
    an allele in another representation than the other sample's (an MNV for SNVs, or the
    reverse), the call is found base by base."""

    def __init__(self, records: Iterable[SmallVariantRecord], sample_id: str) -> None:
        self._calls: dict[tuple[str, int, str, str], list[_BaseCall]] = {}
        for record in records:
            call = next((item for item in record.calls if item.sample == sample_id), None)
            alt_reads = _alt_reads(call) if call is not None else None
            if call is None or not alt_reads:
                continue
            for base in _allele_bases(record.chr, record.start, record.ref, record.alt):
                self._calls.setdefault(base, []).append(_BaseCall(record, call, alt_reads))

    def cover(self, record: SmallVariantRecord) -> tuple[_BaseCall | None, bool]:
        """The sample's calls of other records over ``record``'s bases: the call that best
        carries its weakest base (None when no base is carried), and whether every base is."""
        bases = _allele_bases(record.chr, record.start, record.ref, record.alt)
        weakest: _BaseCall | None = None
        covered = 0
        for base in bases:
            calls = [item for item in self._calls.get(base, ()) if item.record.variant_id != record.variant_id]
            if not calls:
                continue
            covered += 1
            best = max(calls, key=lambda item: item.alt_reads)
            if weakest is None or best.alt_reads < weakest.alt_reads:
                weakest = best
        return weakest, bool(bases) and covered == len(bases)


def build_nipt_observations(
    records: list[SmallVariantRecord],
    *,
    father_sample_id: str,
    cfdna_sample_id: str,
    qc: NiptQualityThresholds | None = None,
    plasma_depth: TargetDepthLookup | None = None,
    father_depth: TargetDepthLookup | None = None,
    counterpart_records: Sequence[SmallVariantRecord] | None = None,
) -> list[NiptSiteObservation]:
    """Turn family variant records into per-site NIPT observations.

    A joint VCF's record without a cfDNA call is skipped. In the per-sample ``nipt``
    callset, a record with only the father's call is a paternal allele the plasma did not
    show: it is kept, with the plasma's depth there from its per-target coverage
    (``plasma_depth``; None outside every target), and a record without the father's call
    reads his genotype as ``absent``, with his depth there from his coverage when known.

    The two per-sample VCFs can write one allele in two representations (an MNV in one,
    its SNVs in the other), so a call missing from a record is first looked up base by
    base among the sample's other calls (``nipt_analysis.REPRESENTATION_FLAGS``).
    ``counterpart_records`` are the records to look in, and to restore split records'
    depths from: the family's every record when ``records`` is a filtered subset.
    """
    qc = qc or NiptQualityThresholds()
    counterparts = records if counterpart_records is None else counterpart_records
    plasma_site_depths = restored_site_depths(counterparts, cfdna_sample_id)
    father_site_depths = restored_site_depths(counterparts, father_sample_id)
    plasma_bases: _BaseCallIndex | None = None
    father_bases: _BaseCallIndex | None = None
    sites: list[NiptSiteObservation] = []
    for record in records:
        calls = {call.sample: call for call in record.calls}
        cf_call = calls.get(cfdna_sample_id)
        father_call = calls.get(father_sample_id)
        per_sample = record.source == NIPT_SMALL_VARIANT_SOURCE
        if cf_call is None and (not per_sample or father_call is None):
            continue
        representation: str | None = None
        cf_record_id = record.variant_id
        father_record_id = record.variant_id
        if per_sample and father_call is None:
            if father_bases is None:
                father_bases = _BaseCallIndex(counterparts, father_sample_id)
            borrowed, complete = father_bases.cover(record)
            if borrowed is not None and complete:
                father_call, father_record_id = borrowed.call, borrowed.record.variant_id
                representation = FATHER_CALL_OTHER_REPRESENTATION
            elif borrowed is not None:
                representation = FATHER_CALL_OVERLAPS
        elif per_sample and cf_call is None:
            if plasma_bases is None:
                plasma_bases = _BaseCallIndex(counterparts, cfdna_sample_id)
            borrowed, complete = plasma_bases.cover(record)
            if borrowed is not None and complete:
                cf_call, cf_record_id = borrowed.call, borrowed.record.variant_id
                representation = PLASMA_CALL_OTHER_REPRESENTATION
            elif borrowed is not None:
                representation = PLASMA_CALL_OVERLAPS
        father_reads = father_site_depths.get(father_record_id)
        if father_call is not None:
            father_dp = father_reads if father_reads is not None else _ad_depth(father_call)
        else:
            father_dp = _lookup_depth(father_depth, record.chr, record.start)
        father_state = derive_father_state(
            father_call, qc, depth=father_reads, uncalled="absent" if per_sample else "missing"
        )
        father_vaf = _call_vaf(father_call, father_dp) if father_call is not None else None
        father_alt = _alt_reads(father_call) if father_call is not None else None
        if cf_call is None:
            sites.append(
                NiptSiteObservation(
                    variant_id=record.variant_id,
                    chrom=record.chr,
                    pos=record.start,
                    is_autosomal=_is_autosomal(record.chr),
                    cf_present=False,
                    cf_dp=_lookup_depth(plasma_depth, record.chr, record.start),
                    cf_alt_reads=0,
                    cf_vaf=None,
                    cf_qual=None,
                    father_state=father_state,
                    father_dp=father_dp,
                    father_qual=_call_quality(father_call) if father_call is not None else None,
                    father_vaf=father_vaf,
                    father_alt_reads=father_alt,
                    ref=record.ref,
                    alt=record.alt,
                    cf_depth_estimated=True,
                    representation=representation,
                )
            )
            continue
        alt_reads = _alt_reads(cf_call)
        depth = plasma_site_depths.get(cf_record_id) or _ad_depth(cf_call)
        if alt_reads is not None:
            present = alt_reads > 0
        else:
            present = classify_genotype(cf_call.gt) in ("het", "hom_alt")
        sites.append(
            NiptSiteObservation(
                variant_id=record.variant_id,
                chrom=record.chr,
                pos=record.start,
                is_autosomal=_is_autosomal(record.chr),
                cf_present=present,
                cf_dp=depth,
                cf_alt_reads=alt_reads,
                cf_vaf=_call_vaf(cf_call, depth),
                cf_qual=record.qual if not per_sample else None,
                father_state=father_state,
                father_dp=father_dp,
                father_qual=_call_quality(father_call) if father_call is not None else None,
                father_vaf=father_vaf,
                father_alt_reads=father_alt,
                ref=record.ref,
                alt=record.alt,
                cf_fs=(cf_call.metrics or {}).get("FS"),
                cf_filters=list(cf_call.filters or []),
                cf_metrics=dict(cf_call.metrics or {}),
                representation=representation,
            )
        )
    return sites


def triage_annotation(record: SmallVariantRecord) -> DeNovoAnnotation:
    """What the de novo triage reads of a record's annotation: its most severe impact,
    its highest SpliceAI delta score and population frequency (the parser keeps all but
    ``gnomad_af`` under ``population_frequencies``; VEP MAX_AF is ``gnomad_popmax_af``),
    whether it is known (an rsID), and whether a ClinVar record may assert it pathogenic."""
    impact: str | None = None
    spliceai: float | None = None
    population_af: float | None = None
    known = bool(record.rsid)
    clinvar_pathogenic = False
    for annotation in record.annotations or []:
        if clinvar_may_assert_pathogenic(_annotation_clinvar(annotation)):
            clinvar_pathogenic = True
        value = str(annotation.get("impact") or "").upper()
        if value in _IMPACT_RANK and (impact is None or _IMPACT_RANK[value] > _IMPACT_RANK[impact]):
            impact = value
        for key in _SPLICEAI_KEYS:
            score = _coerce_float(annotation.get(key))
            if score is not None and (spliceai is None or score > spliceai):
                spliceai = score
        frequencies = _annotation_population_frequencies(annotation)
        for key in _POPULATION_AF_KEYS:
            frequency = frequencies.get(key, _coerce_float(annotation.get(key)))
            if frequency is not None and (population_af is None or frequency > population_af):
                population_af = frequency
        if annotation.get("rsid"):
            known = True
    return DeNovoAnnotation(
        impact=impact,
        spliceai_max=spliceai,
        max_population_af=population_af,
        is_novel=not known,
        clinvar_pathogenic=clinvar_pathogenic,
    )


# The artifact list's protections (nipt_artifact_pg): an allele common in the population
# or with a ClinVar record that may assert it pathogenic is never an artefact. They are
# checked on the assembly's annotation when an allele is listed, but an allele no family
# carried then went unchecked, and an annotation can change: each family's analysis checks
# its listed alleles again with its own annotation.
ARTIFACT_PROTECTION_COMMON_AF = 0.05
ARTIFACT_LIST_PROTECTED_FLAG = "artifact_list_protected"


def artifact_protection(record: SmallVariantRecord) -> str | None:
    """Why the artifact list may not remove ``record`` from the family's analysis:
    ``clinvar`` or ``common`` (a population frequency above 5%); None when nothing does."""
    annotation = triage_annotation(record)
    if annotation.clinvar_pathogenic:
        return "clinvar"
    if annotation.max_population_af is not None and annotation.max_population_af > ARTIFACT_PROTECTION_COMMON_AF:
        return "common"
    return None


def family_artifact_ids(
    records: Iterable[SmallVariantRecord], listed: set[str]
) -> tuple[set[str], dict[str, str]]:
    """The listed artefacts of the family's records its analysis removes, and those the
    family's own annotation protects (``variant_id -> reason``), which stay in."""
    removed: set[str] = set()
    protected: dict[str, str] = {}
    if not listed:
        return removed, protected
    for record in records:
        if record.variant_id not in listed:
            continue
        reason = artifact_protection(record)
        if reason is None:
            removed.add(record.variant_id)
        else:
            protected[record.variant_id] = reason
    return removed, protected


# --------------------------------------------------------------------------- #
# Loading a family
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class NiptClassifiedVariant:
    """A family variant record paired with its NIPT classification.

    ``variant_out`` is the full small-variant serialization (with review/cohort
    hydrated), attached for the page slice so the variant list carries the same
    payload as the small-variant view."""

    record: SmallVariantRecord
    classification: NiptClassification
    variant_out: "VariantOut | None" = None
    site: NiptSiteObservation | None = None
    de_novo: DeNovoTriage | None = None


@dataclass(slots=True)
class NiptVariantsResult:
    fetal_fraction: FetalFractionEstimate
    total: int
    variants: list[NiptClassifiedVariant]
    # True when more variants matched the scope than the list classifies
    # (count_limit): the total is then a lower bound, and the list stops part-way
    # through the genome.
    total_is_estimated: bool = False
    count_limit: int | None = None
    recessive_genes: list[RecessiveGeneRisk] = field(default_factory=list)


@dataclass(slots=True)
class NiptFamilySummary:
    """The NIPT summary: the analysis, and the plasma's coverage QC and sex profile from its
    per-target coverage (None without one)."""

    analysis: NiptAnalysisResult
    target_coverage: TargetCoverageSummary | None = None
    sex_chromosomes: SexChromosomeCoverage | None = None


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


async def _load_listed_artifacts(
    session: AsyncSession, context: FamilyMetadataContext, assay_key: str
) -> set[str]:
    return await load_nipt_artifact_ids(
        session, assembly_id=context.assembly_id, assay_key=assay_key
    )


async def _load_target_rows(
    context: FamilyMetadataContext,
    sample_id: str,
    *,
    with_metadata: bool,
    genes: Sequence[str] | None = None,
) -> list[TargetCoverageRow]:
    """A sample's per-target coverage (its ``target_coverage`` track), or only the targets of
    ``genes`` (case-insensitive); [] without one."""
    sample_uuid = context.sample_name_to_uuid.get(sample_id)
    if not sample_uuid or not context.assembly_name:
        return []
    rows = await fetch_interval_track_rows(
        context.assembly_name,
        sample_uuid=sample_uuid,
        track_type=TARGET_COVERAGE_TRACK_TYPE,
        chromosomes=[],
        include_metadata=with_metadata,
        record_ids=genes,
    )
    return [target_row_from_track(row) for row in rows]


@dataclass(slots=True)
class _FamilyData:
    records: list[SmallVariantRecord]
    plasma_targets: list[TargetCoverageRow]
    father_targets: list[TargetCoverageRow]


async def _load_family_data(context: FamilyMetadataContext, trio: NiptTrio) -> _FamilyData:
    records, plasma_targets, father_targets = await asyncio.gather(
        _load_family_records(context),
        _load_target_rows(context, trio.cfdna_sample_id, with_metadata=False),
        _load_target_rows(context, trio.father_sample_id, with_metadata=False),
    )
    return _FamilyData(records=records, plasma_targets=plasma_targets, father_targets=father_targets)


def _family_sites(
    records: list[SmallVariantRecord],
    trio: NiptTrio,
    *,
    plasma_targets: list[TargetCoverageRow] | None = None,
    father_targets: list[TargetCoverageRow] | None = None,
    qc: NiptQualityThresholds | None = None,
    counterpart_records: list[SmallVariantRecord] | None = None,
) -> list[NiptSiteObservation]:
    return build_nipt_observations(
        records,
        father_sample_id=trio.father_sample_id,
        cfdna_sample_id=trio.cfdna_sample_id,
        qc=qc,
        plasma_depth=TargetDepthLookup(plasma_targets) if plasma_targets else None,
        father_depth=TargetDepthLookup(father_targets) if father_targets else None,
        counterpart_records=counterpart_records,
    )


async def run_family_nipt_analysis(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    project_id: str | None = None,
    qc: NiptQualityThresholds | None = None,
) -> NiptAnalysisResult:
    summary = await get_family_nipt_summary(
        session, family_id=family_id, user=user, project_id=project_id, qc=qc
    )
    return summary.analysis


async def get_family_nipt_summary(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    project_id: str | None = None,
    qc: NiptQualityThresholds | None = None,
) -> NiptFamilySummary:
    """The NIPT analysis of the whole family callset, with the plasma's target coverage QC
    and sex-chromosome profile."""
    trio, context, assay_key = await _resolve_trio_and_context(
        session, family_id=family_id, user=user, project_id=project_id
    )
    listed = await _load_listed_artifacts(session, context, assay_key)
    data = await _load_family_data(context, trio)
    removed, _protected = family_artifact_ids(data.records, listed)
    # The whole family callset goes through the analysis: pure CPU work, so it runs in a
    # worker thread and other requests are served meanwhile (#527).
    analysis = await asyncio.to_thread(
        _analyse_family_sites, data, trio, qc or NiptQualityThresholds(), removed.__contains__
    )
    target_coverage = summarize_target_coverage(data.plasma_targets) if data.plasma_targets else None
    sex_chromosomes = sex_chromosome_coverage(data.plasma_targets) if data.plasma_targets else None
    return NiptFamilySummary(
        analysis=analysis, target_coverage=target_coverage, sex_chromosomes=sex_chromosomes
    )


def _analyse_family_sites(
    data: _FamilyData,
    trio: NiptTrio,
    qc: NiptQualityThresholds,
    artifact_lookup: Any,
) -> NiptAnalysisResult:
    sites = _family_sites(
        data.records,
        trio,
        plasma_targets=data.plasma_targets,
        father_targets=data.father_targets,
        qc=qc,
    )
    return run_nipt_analysis(sites, qc, artifact_lookup=artifact_lookup)


def _estimate_cohort_fetal_fraction(
    data: _FamilyData,
    trio: NiptTrio,
    qc: NiptQualityThresholds,
    artifact_ids: set[str],
) -> FetalFractionEstimate:
    # The summary's computation, over the same filtered sites, so the variant list
    # reports -- and classifies against -- the fetal fraction the summary shows. It used
    # to estimate over every family site, listed artifacts included.
    return filter_sites_and_estimate_ff(
        _family_sites(
            data.records,
            trio,
            plasma_targets=data.plasma_targets,
            father_targets=data.father_targets,
            qc=qc,
        ),
        qc,
        artifact_lookup=artifact_ids.__contains__,
    ).fetal_fraction


def _classify_candidates(
    records: list[SmallVariantRecord],
    trio: NiptTrio,
    listed_artifacts: set[str],
    ff_estimate: FetalFractionEstimate,
    qc: NiptQualityThresholds,
    min_confidence: float | None,
    *,
    plasma_targets: list[TargetCoverageRow] | None = None,
    father_targets: list[TargetCoverageRow] | None = None,
    family_records: list[SmallVariantRecord] | None = None,
) -> list[NiptClassifiedVariant]:
    # The filtered records' calls are looked up in the family's every record: the other
    # representation of an allele, or a split record's sibling, need not pass the filter.
    observations = {
        observation.variant_id: observation
        for observation in _family_sites(
            records,
            trio,
            plasma_targets=plasma_targets,
            father_targets=father_targets,
            qc=qc,
            counterpart_records=family_records,
        )
    }
    classified: list[NiptClassifiedVariant] = []
    for record in records:
        protection = artifact_protection(record) if record.variant_id in listed_artifacts else None
        if record.variant_id in listed_artifacts and protection is None:
            continue
        observation = observations.get(record.variant_id)
        if observation is None:
            continue
        classification = classify_site(observation, ff_estimate, qc)
        if protection is not None:
            # Listed, but the family's annotation says it is no artefact.
            classification.flags.append(ARTIFACT_LIST_PROTECTED_FLAG)
        if min_confidence is not None and (
            classification.category is None or classification.confidence < min_confidence
        ):
            continue
        classified.append(
            NiptClassifiedVariant(record=record, classification=classification, site=observation)
        )
    return classified


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


def _trusted_father(item: NiptClassifiedVariant, qc: NiptQualityThresholds) -> str:
    return trusted_father_state(item.site, qc) if item.site is not None else "missing"


def _is_paternal_allele(item: NiptClassifiedVariant, qc: NiptQualityThresholds) -> bool:
    """The father carries the allele and the mother does not (the fetal band, or no plasma
    call): what a paternal-dominant variant looks like in the plasma."""
    if _trusted_father(item, qc) not in ("het", "hom_alt") or item.site is None:
        return False
    site = item.site
    if site.cf_depth_estimated or not site.cf_present:
        return True
    vaf = site.cf_vaf if site.cf_vaf is not None else 0.0
    return vaf <= MATERNAL_ALLELE_VAF_BOUNDARY


def _paternal_view(
    classified: list[NiptClassifiedVariant], qc: NiptQualityThresholds, *, include_not_inherited: bool
) -> list[NiptClassifiedVariant]:
    """The paternal alleles, with whether the fetus inherited each: by default only those it
    did (transmission probability 0.5 or more), and those the plasma calls in part in
    another representation, where whether it did is uncertain."""
    kept: list[NiptClassifiedVariant] = []
    for item in classified:
        if not _is_paternal_allele(item, qc):
            continue
        probability = item.classification.paternal_transmission_probability
        uncertain = item.site is not None and item.site.representation == PLASMA_CALL_OVERLAPS
        if include_not_inherited or uncertain or (probability is not None and probability >= 0.5):
            kept.append(item)
    return kept


def _maternal_view(
    classified: list[NiptClassifiedVariant], *, include_not_inherited: bool
) -> list[NiptClassifiedVariant]:
    """The mother's alleles (categories 2-6), with whether the fetus inherited each: by
    default only those it did (probability 0.5 or more)."""
    kept: list[NiptClassifiedVariant] = []
    for item in classified:
        classification = item.classification
        if classification.maternal_state not in ("het", "hom"):
            continue
        probability = classification.maternal_allele_probability
        if include_not_inherited or (probability is not None and probability >= 0.5):
            kept.append(item)
    return kept


def _recessive_view(
    classified: list[NiptClassifiedVariant], qc: NiptQualityThresholds
) -> tuple[list[NiptClassifiedVariant], list[RecessiveGeneRisk]]:
    """The genes where both parents carry an allele, each with the fetal risk, and every
    carrier variant in them, highest-risk gene first.

    A variant marks the mother a carrier when her inferred genotype is het (categories
    2-4), and the father a carrier when his genotype is het (nipt_triage: a homozygous
    parent is not a carrier). Run the gene, consequence and frequency filters first so the
    carriers are plausibly causal.
    """
    candidates: list[RecessiveCandidate] = []
    by_variant: dict[str, NiptClassifiedVariant] = {}
    for item in classified:
        father = _trusted_father(item, qc)
        if item.classification.maternal_state != "het" and father != "het":
            continue
        candidates.append(
            RecessiveCandidate(
                variant_id=item.record.variant_id,
                genes=sorted(_record_genes(item.record)),
                father_state=father,
                classification=item.classification,
                variant_class=item.site.variant_class if item.site is not None else "SNV",
                has_population_af=triage_annotation(item.record).max_population_af is not None,
            )
        )
        by_variant[item.record.variant_id] = item
    genes = recessive_gene_risks(candidates)
    order: list[str] = []
    for gene in genes:
        for allele in [*gene.maternal, *gene.paternal]:
            if allele.variant_id not in order:
                order.append(allele.variant_id)
    # The carrier variants of the genes at risk, in genomic order.
    wanted = set(order)
    kept = [item for item in classified if item.record.variant_id in wanted]
    return kept, genes


async def _de_novo_view(
    classified: list[NiptClassifiedVariant],
    ff_estimate: FetalFractionEstimate,
    *,
    assembly_name: str | None,
    carrier_samples: dict[str, str],
    family_uuid: str,
    min_priority: str,
) -> list[NiptClassifiedVariant]:
    """The de novo candidates in the family's fetal window, triaged (nipt_triage) and
    ranked by score, from ``min_priority`` up."""
    window = de_novo_window(ff_estimate)
    candidates = [
        item
        for item in classified
        if item.site is not None and is_de_novo_candidate(item.site, item.classification)
    ]
    if window is None or not candidates:
        return []
    others: dict[str, int] = {}
    if assembly_name and carrier_samples:
        others = await count_cfdna_carriers(
            assembly_name,
            [item.record.variant_id for item in candidates],
            carrier_samples=carrier_samples,
            exclude_family_uuid=family_uuid,
        )
    wanted = _DE_NOVO_PRIORITY_LABELS.get(min_priority, _DE_NOVO_PRIORITY_LABELS["low"])
    kept: list[NiptClassifiedVariant] = []
    for item in candidates:
        assert item.site is not None
        triage = triage_de_novo(
            item.site,
            window,
            triage_annotation(item.record),
            other_cfdna_carriers=others.get(item.record.variant_id, 0),
        )
        if triage is None or triage.label not in wanted:
            continue
        item.de_novo = triage
        kept.append(item)
    kept.sort(key=lambda item: -(item.de_novo.score if item.de_novo else 0))
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
    include_not_inherited: bool = False,
    de_novo_priority: str = "low",
    page: int = 1,
    page_size: int = 100,
    qc: NiptQualityThresholds | None = None,
) -> NiptVariantsResult:
    """The family's cfDNA variants in a search, classified, in an inheritance view:

    - ``de_novo``: the triaged de novo candidates in the fetal window, highest score first;
    - ``paternal_dominant``: the father's alleles the fetus inherited (with
      ``include_not_inherited``, also those it did not);
    - ``maternal_dominant``: the mother's alleles the fetus inherited (idem);
    - ``recessive_at_risk``: the carrier variants of the genes where both parents carry
      one, with each gene's fetal risk (``recessive_genes``).

    ``categories`` keeps the given maternal/fetal categories (within a view, both apply).
    """
    qc = qc or NiptQualityThresholds()
    if inheritance and inheritance not in _INHERITANCE_VIEWS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported NIPT inheritance preset: {inheritance}",
        )
    if de_novo_priority not in _DE_NOVO_PRIORITY_LABELS:
        raise HTTPException(status_code=400, detail=f"Unsupported de novo priority: {de_novo_priority}")
    wanted: set[int] = set(categories or [])

    trio, context, assay_key = await _resolve_trio_and_context(
        session, family_id=family_id, user=user, project_id=project_id
    )
    listed = await _load_listed_artifacts(session, context, assay_key)

    # The location filters, as the family search applies them: the interval list, the
    # excluded intervals and the excluded genes were sent, chipped and never applied here
    # (#604). Read before the cohort work, so an unreadable interval fails fast.
    filters = _build_nipt_query_filters(query_filters or {})
    include_regions = _parse_interval_regions(filters.intervals)
    exclude_regions = _parse_interval_regions(filters.exclude_intervals, label="Excluded interval")
    exclude_gene_regions = (
        await _fetch_gene_regions(session, gene_query=filters.exclude_gene, assembly_id=context.assembly_id)
        if filters.exclude_gene
        else []
    )

    # Fetal fraction is estimated cohort-wide (the FF/2 category-7 sites are
    # rarely inside the clinical filter), exactly as the summary estimates it, then
    # the filtered subset is classified against that FF. Both steps are CPU work over
    # many sites, so they run in a worker thread (#527).
    data = await _load_family_data(context, trio)
    removed, _protected = family_artifact_ids(data.records, listed)
    ff_estimate = await asyncio.to_thread(
        _estimate_cohort_fetal_fraction, data, trio, qc, removed
    )

    panel_constraints = PanelFilterConstraints()
    if filters.panel_id:
        panel_constraints = await _fetch_panel_constraints(
            session, filters.panel_id, assembly_id=context.assembly_id
        )
        if not panel_constraints.genes and not panel_constraints.regions:
            return NiptVariantsResult(fetal_fraction=ff_estimate, total=0, variants=[])

    # One row past the limit tells a cut list from a complete one. The list used to stop
    # at the limit with a total that looked exact, so the page and the report read as
    # complete while the rest of the genome was never classified.
    fetch_limit = _NIPT_VARIANT_FETCH_LIMIT
    records = await _fetch_small_variant_rows(
        context,
        filters,
        panel_constraints=panel_constraints,
        include_regions=include_regions,
        exclude_regions=exclude_regions,
        exclude_gene_regions=exclude_gene_regions,
        exclude_gene_terms=_split_gene_terms(filters.exclude_gene),
        limit=fetch_limit + 1,
    )
    capped = len(records) > fetch_limit
    if capped:
        records = records[:fetch_limit]
    classified = await asyncio.to_thread(
        _classify_candidates,
        records,
        trio,
        listed,
        ff_estimate,
        qc,
        min_confidence,
        plasma_targets=data.plasma_targets,
        father_targets=data.father_targets,
        family_records=data.records,
    )

    recessive_genes: list[RecessiveGeneRisk] = []
    if inheritance == RECESSIVE_AT_RISK:
        classified, recessive_genes = _recessive_view(classified, qc)
    elif inheritance == PATERNAL_INHERITED:
        classified = _paternal_view(classified, qc, include_not_inherited=include_not_inherited)
    elif inheritance == MATERNAL_INHERITED:
        classified = _maternal_view(classified, include_not_inherited=include_not_inherited)
    elif inheritance == DE_NOVO:
        classified = await _de_novo_view(
            classified,
            ff_estimate,
            assembly_name=context.assembly_name,
            carrier_samples=await assay_cfdna_carrier_samples(session, assay_key=assay_key),
            family_uuid=context.family_uuid,
            min_priority=de_novo_priority,
        )
    if wanted:
        classified = [item for item in classified if item.classification.category in wanted]

    total = len(classified)
    offset = max(0, (page - 1) * page_size)
    page_items = classified[offset : offset + page_size]

    # Serialize the page slice as full small-variant payloads (and hydrate their
    # review / internal-cohort / gene-constraint data) so the NIPT variant list
    # carries the same shape as the small-variant view, classification aside.
    variant_outs = [_small_variant_out(item.record, assembly_name=context.assembly_name) for item in page_items]
    await _hydrate_small_variant_outs(session, context=context, variants=variant_outs)
    for item, variant_out in zip(page_items, variant_outs):
        item.variant_out = variant_out

    return NiptVariantsResult(
        fetal_fraction=ff_estimate,
        total=total,
        variants=page_items,
        total_is_estimated=capped,
        count_limit=fetch_limit if capped else None,
        recessive_genes=recessive_genes,
    )


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


@dataclass(slots=True)
class NiptCoverageResult:
    """The coverage QC: the target table's when the plasma has one (``targets``), else the
    coverage track's over the gene or panel spans (``regions``)."""

    regions: NiptCoverageSummary
    targets: TargetCoverageSummary | None = None


async def get_family_nipt_coverage(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    project_id: str | None = None,
    gene: str | None = None,
    panel_id: str | None = None,
    min_depth: float = DEFAULT_MIN_DEPTH,
    all_genes: bool = False,
) -> NiptCoverageResult:
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
    target_rows = await _load_target_rows(context, trio.cfdna_sample_id, with_metadata=True)
    if target_rows:
        # The capture targets themselves: the panel's or the queried genes', else all.
        genes: list[str] = list(_split_gene_terms(gene)) if gene else []
        if panel_id:
            constraints = await _fetch_panel_constraints(
                session, panel_id, assembly_id=context.assembly_id
            )
            genes.extend(constraints.genes)
        return NiptCoverageResult(
            regions=NiptCoverageSummary(overall_median_on_target=None, target_region_count=0, min_depth=min_depth),
            targets=summarize_target_coverage(target_rows, genes=genes or None, include_passing=all_genes),
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
    return NiptCoverageResult(
        regions=summarize_on_target_coverage(target_regions, coverage, min_depth=min_depth)
    )


@dataclass(slots=True)
class NiptGeneTargets:
    """Every capture target of one gene in the plasma's target table, in genomic order."""

    gene: str
    critical_mean_depth: float
    rows: list[TargetCoverageRow]


async def get_family_nipt_gene_targets(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    gene: str,
    project_id: str | None = None,
) -> NiptGeneTargets:
    """One gene's targets with their depth, for the coverage page; none without a target
    table, or for a gene the panel does not capture."""
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
    # This gene's rows alone: a panel's table holds tens of thousands of targets.
    target_rows = await _load_target_rows(
        context, trio.cfdna_sample_id, with_metadata=True, genes=[gene.strip()]
    )
    rows = sorted(target_rows, key=lambda row: (row.chrom, row.start, row.end))
    return NiptGeneTargets(gene=gene.strip(), critical_mean_depth=CRITICAL_MEAN_DEPTH, rows=rows)
