"""Wire the monogenic NIPT analysis core to family data (Phase 5).

Resolves the NIPT trio for a family, loads the father + cfDNA calls from ClickHouse and the
plasma's (and father's) per-target coverage, turns them into ``NiptSiteObservation`` rows,
and runs the pure analysis core (nipt_analysis) and the de novo triage and recessive risk
(nipt_triage). The site-building helpers are pure (and unit-tested); the coroutines do I/O.

Two kinds of callset reach here:

- a joint VCF of the father and the plasma (stored under its caller's name, ``mutect2``): every row holds both
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
from dataclasses import astuple, dataclass, field
from datetime import date

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import text

from typing import TYPE_CHECKING, Callable, Iterable, Iterator, Mapping, Sequence

from ..core.sql import require_uuid
from ..schemas import ClinvarCogaOut

from .clickhouse_family_variants import (
    _family_small_variant_data_version,
    _fetch_gene_regions,
    _fetch_panel_constraints,
    _fetch_small_variant_rows,
    _hydrate_small_variant_outs,
    _small_variant_outs,
    CarrierCallRows,
    CfdnaRecurrence,
    CfdnaRecurrenceRules,
    annotate_small_variant_records,
    count_cfdna_recurrence,
    count_other_cfdna_samples,
    resolve_review_selection,
)
from .clickhouse_variant_queries import (
    _gene_match_keys,
    _joined_genes,
    _parse_interval_regions,
    _primary_gene_keys,
    _split_gene_terms,
)
from .clickhouse_variant_records import (
    PanelFilterConstraints,
    SmallVariantCall,
    SmallVariantRecord,
    _annotation_clinvar,
    _annotation_population_frequencies,
    _coerce_float,
)

if TYPE_CHECKING:
    from ..schemas import FamilyOut, VariantOut
from .clickhouse_interval_tracks import fetch_interval_track_rows
from .coverage_read_depth import (
    COVERAGE_TRACK_TYPE,
    SampleCoverageTracks,
    coverage_tracks_by_sample,
    not_read_as_depth_note,
)
from .data_scope import normalize_chromosome
from .family_metadata_context import FamilyMetadataContext, build_family_metadata_context
from .family_variant_filters import SmallVariantQueryFilters
from .genotypes import classify_genotype
from .metadata_service import get_family_record
from .access_control import CurrentUser
from .family_analysis_types import pregnancy_of
from .family_package_records import analysis_label, suffixed_source
from .nipt import (
    MONOGENIC_NIPT_ANALYSIS_TYPE,
    NIPT_TRIO_UNRESOLVED,
    NiptTrio,
    is_monogenic_nipt_family,
    nipt_analysis_of,
    nipt_assay_key,
    resolve_nipt_trio,
)
from .nipt_analysis_cache import NiptAnalysisCache, NiptAnalysisInputs, NiptAnalysisKey
from .nipt_artifact_pg import assay_cfdna_carrier_samples, load_nipt_artifacts, recurrence_rules
from .nipt_qc_gate import NiptGateBound, fetal_read_depth, load_nipt_gate_bound
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
    FATHER_X_HET_FLAG,
    PATERNAL_X_IN_SON_FLAG,
    classify_site,
    is_genotyped_call,
    model_fetal_sex,
    run_nipt_analysis,
)
from .nipt_target_coverage import (
    CRITICAL_MEAN_DEPTH,
    TARGET_COVERAGE_SOURCE,
    TARGET_COVERAGE_TRACK_TYPE,
    SexChromosomeCoverage,
    TargetCoverageRow,
    TargetCoverageSummary,
    TargetDepthLookup,
    sex_chromosome_coverage,
    summarize_target_coverage,
    target_row_from_track,
)
from .panel_use_audit import record_family_panel_use
from .analysis_gene_panel import (
    AnalysisGenePanel,
    load_analysis_gene_panel,
    panel_gene_symbols,
    set_analysis_gene_panel,
)
from .nipt_diagnostic_filters import (
    DiagnosticAnnotation,
    NiptDiagnosticCriteria,
    clinvar_override,
    clinvar_strictly_pathogenic,
    dominant_failures,
    is_cohort_common,
    is_repeat_context_indel,
    recessive_failures,
)
from .nipt_triage import (
    DeNovoAnnotation,
    DeNovoTriage,
    RecessiveCandidate,
    RecessiveGeneRisk,
    de_novo_window,
    is_de_novo_candidate,
    mother_carries,
    recessive_gene_risks,
    recessive_listing_order,
    recessive_mode,
    repeat_units,
    triage_de_novo,
)
from .variant_prioritization import clinvar_may_assert_pathogenic
from .annotation_manifest_service import analysis_clinvar_release
from .clinvar_coga import (
    ClinvarCogaRead,
    attach_clinvar_coga,
    clinvar_coga_by_variant,
    clinvar_coga_held,
    clinvar_coga_read,
)
from .small_variant_callsets import is_nipt_callset

# Bounds the in-memory classification for the variant list: the first variants of the
# scope, in genomic order. Clinical use narrows by gene/panel/region, so this is a safety
# cap, not a normal limit; a list cut by it says so (total_is_estimated, count_limit).
NIPT_VARIANT_FETCH_LIMIT = 5000

# The inheritance views of the variant list (the "Inheritance" control of the NIPT page).
DE_NOVO = "de_novo"
PATERNAL_INHERITED = "paternal_dominant"
MATERNAL_INHERITED = "maternal_dominant"
RECESSIVE_AT_RISK = "recessive_at_risk"
_INHERITANCE_VIEWS = (DE_NOVO, PATERNAL_INHERITED, MATERNAL_INHERITED, RECESSIVE_AT_RISK)
# The views that read only the mother's alleles (``mother_carries``) or the parents' alleles:
# their variant load keeps only the rows such an allele can be read from (``CarrierCallRows``).
# The plasma's low-level calls, most of a cfDNA callset, would otherwise fill the load's limit
# before the rest of the genome.
_CARRIER_ROW_VIEWS = frozenset({MATERNAL_INHERITED, RECESSIVE_AT_RISK})
# The de novo labels each minimum priority keeps.
_DE_NOVO_PRIORITY_LABELS: dict[str, frozenset[str]] = {
    "high": frozenset({"high"}),
    "medium": frozenset({"high", "medium"}),
    "low": frozenset({"high", "medium", "low"}),
}

_AUTOSOMES = {str(index) for index in range(1, 23)}
_IMPACT_RANK = {"HIGH": 4, "MODERATE": 3, "LOW": 2, "MODIFIER": 1}
# VEP's MAX_AF (``max_af``, its highest frequency over every population it reads) is the
# frequency the R validation's rarity reads; gnomAD's own and TOPMed's are read beside it.
_POPULATION_AF_KEYS = (
    "max_af",
    "gnomad_af",
    "gnomad_exomes_af",
    "gnomad_genomes_af",
    "gnomad_popmax_af",
    "topmed_af",
)
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
    """The father's genotype class (``nipt_analysis.FATHER_STATES``), as the R NIPT-M
    paternal classes read a paternal sample.

    A call its caller genotyped (``is_genotyped_call``: HaplotypeCaller, the lab's paternal
    caller) is read from its GT: ``het`` (one copy of the allele), ``hom_alt`` (every copy,
    a haploid ``1`` on a male's X too) or ``hom_ref`` (at 20 reads or more); ``low_support``
    when it has fewer than 20 reads, fewer than 5 alt reads, a QUAL below 50 or a GQ below
    20, or no called genotype.

    Any other call (Mutect2, VarDict) is read from its allele depths: a call with fewer
    than 20 reads, fewer than 5 alt reads or a quality (TLOD, else QUAL) below 20 is
    ``low_support``; then a VAF of 0.80 or more is ``hom_alt``, 0.20 or more ``het``,
    less ``low_vaf``. A call without an alt read is ``hom_ref`` at 20 reads or more. A call
    without allele depths falls back on its GT (the shared genotype classes: a haploid
    ``1`` is ``hom_alt``, ``./0`` no call). No call is ``uncalled``: ``missing`` in a joint
    VCF, ``absent`` in a per-sample callset.

    ``depth`` overrides the call's own read count: the site depth of a record split from a
    multi-allelic one.
    """
    if call is None:
        return uncalled
    qc = qc or NiptQualityThresholds()
    reads = depth if depth is not None else _ad_depth(call)
    if is_genotyped_call(call.gq, call.metrics):
        return _genotyped_father_state(call, reads, qc)
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


def _genotyped_father_state(call: SmallVariantCall, reads: int | None, qc: NiptQualityThresholds) -> str:
    """The father's class from the genotype his caller called (``derive_father_state``). A
    missing read count, alt-read count, QUAL or GQ passes, as in the R pipeline."""
    genotype_class = classify_genotype(call.gt)
    if genotype_class == "no_call":
        return "low_support"
    if genotype_class == "hom_ref":
        return "hom_ref" if (reads or 0) >= qc.min_father_dp else "low_support"
    alt = _alt_reads(call)
    quality = (call.metrics or {}).get("QUAL")
    if (
        (reads is not None and reads < qc.min_father_dp)
        or (alt is not None and alt < qc.min_father_alt_reads)
        or (quality is not None and quality < qc.min_father_genotyped_qual)
        or (call.gq is not None and call.gq < qc.min_father_gq)
    ):
        return "low_support"
    return genotype_class


def restored_site_depths(records: Iterable[SmallVariantRecord], sample_id: str) -> dict[str, int]:
    """The site depth of each record a VCF split from a multi-allelic one (``bcftools norm
    -m-``), for one sample: ``variant_id -> AD[ref] + the alt reads of every sibling``.

    A split record keeps only its own allele's reads, so its alt fraction leaves out the
    reads of the other alleles. Siblings share the position, the reference depth and DP
    (the R NIPT-M adapter's rule). Records that are not split are not listed.
    """
    # Each record's site, and the sites of two records or more: a list only for a split site,
    # as a whole callset is read through here.
    first: dict[tuple[str, int, int, int | None], tuple[str, int]] = {}
    split: dict[tuple[str, int, int, int | None], list[tuple[str, int]]] = {}
    for record in records:
        call = _sample_call(record, sample_id)
        if call is None or len(call.ad) != 2 or call.ad[0] is None or call.ad[1] is None:
            continue
        site = (record.chr, record.start, int(call.ad[0]), call.dp)
        member = (record.variant_id, int(call.ad[1]))
        if site not in first:
            first[site] = member
        else:
            split.setdefault(site, [first[site]]).append(member)
    depths: dict[str, int] = {}
    for (_chrom, _pos, ref_depth, _dp), members in split.items():
        site_depth = ref_depth + sum(alt for _variant, alt in members)
        for variant_id, _alt in members:
            depths[variant_id] = site_depth
    return depths


def _sample_call(record: SmallVariantRecord, sample_id: str) -> SmallVariantCall | None:
    """The record's call of ``sample_id`` (its first), None without one."""
    for call in record.calls:
        if call.sample == sample_id:
            return call
    return None


# The longest REF span the capture-target check walks base by base; a longer deletion is in
# the target when one of its first bases is.
_MAX_TARGET_SPAN = 1000


def _in_capture_target(lookup: TargetDepthLookup, record: SmallVariantRecord) -> bool:
    """Whether the record's REF span overlaps a capture target of the plasma's coverage table
    (the R pipeline's rule: a variant is in the target when its REF span overlaps an interval)."""
    span = min(max(len(record.ref or ""), 1), _MAX_TARGET_SPAN)
    return any(lookup.target_at(record.chr, record.start + offset) is not None for offset in range(span))


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
    reverse), the call is found base by base. Only the calls over ``bases`` are indexed: the
    bases of the records ``cover`` is asked about."""

    def __init__(
        self, records: Iterable[SmallVariantRecord], sample_id: str, bases: set[tuple[str, int, str, str]]
    ) -> None:
        self._calls: dict[tuple[str, int, str, str], list[_BaseCall]] = {}
        for record in records:
            call = _sample_call(record, sample_id)
            alt_reads = _alt_reads(call) if call is not None else None
            if call is None or not alt_reads:
                continue
            for base in _allele_bases(record.chr, record.start, record.ref, record.alt):
                if base in bases:
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


@dataclass(frozen=True, slots=True)
class _BorrowedCall:
    """A sample's call of another record over a record's bases (``_BaseCallIndex.cover``): the
    call, the record it is read off, and whether it carries every base of the record."""

    call: SmallVariantCall
    variant_id: str
    complete: bool


@dataclass(frozen=True, slots=True)
class NiptCounterparts:
    """What a site's observation reads of the family's other records, kept without them: each
    sample's site depth of the records split from a multi-allelic one (``restored_site_depths``),
    and, for each per-sample record without one sample's call, that sample's call of another
    record over its bases (``_BorrowedCall``), where one carries a base of it. Read from the
    family's every record (``nipt_counterparts``), they observe any of its records as the whole
    family would."""

    plasma_depths: Mapping[str, int]
    father_depths: Mapping[str, int]
    plasma_covers: Mapping[str, _BorrowedCall]
    father_covers: Mapping[str, _BorrowedCall]


def nipt_counterparts(
    records: Sequence[SmallVariantRecord], *, father_sample_id: str, cfdna_sample_id: str
) -> NiptCounterparts:
    """The counterparts ``records`` give each other (``NiptCounterparts``)."""
    without_father: list[SmallVariantRecord] = []
    without_plasma: list[SmallVariantRecord] = []
    for record in records:
        if not is_nipt_callset(record.source):
            continue
        plasma_call = _sample_call(record, cfdna_sample_id)
        father_call = _sample_call(record, father_sample_id)
        if plasma_call is not None and father_call is None:
            without_father.append(record)
        elif father_call is not None and plasma_call is None:
            without_plasma.append(record)
    return NiptCounterparts(
        plasma_depths=restored_site_depths(records, cfdna_sample_id),
        father_depths=restored_site_depths(records, father_sample_id),
        plasma_covers=_covers(records, cfdna_sample_id, without_plasma),
        father_covers=_covers(records, father_sample_id, without_father),
    )


def _covers(
    records: Sequence[SmallVariantRecord], sample_id: str, without: Sequence[SmallVariantRecord]
) -> dict[str, _BorrowedCall]:
    """Each of ``without`` (records without ``sample_id``'s call) whose bases a call of
    ``sample_id``'s in another of ``records`` carries: the call that best carries its weakest
    base, and whether every base is carried (``_BaseCallIndex.cover``)."""
    if not without:
        return {}
    bases = {base for record in without for base in _allele_bases(record.chr, record.start, record.ref, record.alt)}
    index = _BaseCallIndex(records, sample_id, bases)
    covers: dict[str, _BorrowedCall] = {}
    for record in without:
        borrowed, complete = index.cover(record)
        if borrowed is not None:
            covers[record.variant_id] = _BorrowedCall(borrowed.call, borrowed.record.variant_id, complete)
    return covers


def build_nipt_observations(
    records: Sequence[SmallVariantRecord],
    *,
    father_sample_id: str,
    cfdna_sample_id: str,
    qc: NiptQualityThresholds | None = None,
    plasma_depth: TargetDepthLookup | None = None,
    father_depth: TargetDepthLookup | None = None,
    counterparts: NiptCounterparts | None = None,
) -> Iterator[NiptSiteObservation]:
    """Each record's per-site NIPT observation, one at a time: the family's whole callset is
    observed without its observations being held together.

    A joint VCF's record without a cfDNA call is skipped. In the per-sample ``nipt``
    callset, a record with only the father's call is a paternal allele the plasma did not
    show: it is kept, with the plasma's depth there from its per-target coverage
    (``plasma_depth``; None outside every target), and a record without the father's call
    reads his genotype as ``absent``, with his depth there from his coverage when known.
    Every per-sample site, called by the plasma or not, also carries that coverage depth
    as ``cf_coverage_dp``, the depth paternity and the fetal sex judge a site at; a joint
    VCF's site carries its plasma call's depth.

    The two per-sample VCFs can write one allele in two representations (an MNV in one,
    its SNVs in the other), so a call missing from a record is first looked up base by
    base among the sample's other calls (``nipt_analysis.REPRESENTATION_FLAGS``), and a split
    record's depth is restored from its siblings: both from ``counterparts``, those ``records``
    give each other unless given (the family's every record's when ``records`` is a filtered
    subset).
    """
    qc = qc or NiptQualityThresholds()
    counterparts = counterparts or nipt_counterparts(
        records, father_sample_id=father_sample_id, cfdna_sample_id=cfdna_sample_id
    )
    for record in records:
        calls = {call.sample: call for call in record.calls}
        cf_call = calls.get(cfdna_sample_id)
        father_call = calls.get(father_sample_id)
        per_sample = is_nipt_callset(record.source)
        if cf_call is None and (not per_sample or father_call is None):
            continue
        representation: str | None = None
        represented_by: str | None = None
        cf_record_id = record.variant_id
        father_record_id = record.variant_id
        if per_sample and father_call is None:
            borrowed = counterparts.father_covers.get(record.variant_id)
            if borrowed is not None and borrowed.complete:
                father_call, father_record_id = borrowed.call, borrowed.variant_id
                representation, represented_by = FATHER_CALL_OTHER_REPRESENTATION, father_record_id
            elif borrowed is not None:
                representation = FATHER_CALL_OVERLAPS
        elif per_sample and cf_call is None:
            borrowed = counterparts.plasma_covers.get(record.variant_id)
            if borrowed is not None and borrowed.complete:
                cf_call, cf_record_id = borrowed.call, borrowed.variant_id
                representation, represented_by = PLASMA_CALL_OTHER_REPRESENTATION, cf_record_id
            elif borrowed is not None:
                representation = PLASMA_CALL_OVERLAPS
        father_reads = counterparts.father_depths.get(father_record_id)
        if father_call is not None:
            father_dp = father_reads if father_reads is not None else _ad_depth(father_call)
        else:
            father_dp = _lookup_depth(father_depth, record.chr, record.start)
        father_state = derive_father_state(
            father_call, qc, depth=father_reads, uncalled="absent" if per_sample else "missing"
        )
        father_vaf = _call_vaf(father_call, father_dp) if father_call is not None else None
        father_alt = _alt_reads(father_call) if father_call is not None else None
        plasma_coverage = _lookup_depth(plasma_depth, record.chr, record.start) if per_sample else None
        in_target = _in_capture_target(plasma_depth, record) if per_sample and plasma_depth is not None else None
        if cf_call is None:
            yield NiptSiteObservation(
                variant_id=record.variant_id,
                chrom=record.chr,
                pos=record.start,
                is_autosomal=_is_autosomal(record.chr),
                cf_present=False,
                cf_dp=plasma_coverage,
                cf_alt_reads=0,
                cf_vaf=None,
                cf_qual=None,
                cf_coverage_dp=plasma_coverage,
                father_state=father_state,
                father_dp=father_dp,
                father_qual=_call_quality(father_call) if father_call is not None else None,
                father_vaf=father_vaf,
                father_alt_reads=father_alt,
                ref=record.ref,
                alt=record.alt,
                cf_depth_estimated=True,
                representation=representation,
                represented_by=represented_by,
                in_target=in_target,
            )
            continue
        alt_reads = _alt_reads(cf_call)
        depth = counterparts.plasma_depths.get(cf_record_id) or _ad_depth(cf_call)
        if alt_reads is not None:
            present = alt_reads > 0
        else:
            present = classify_genotype(cf_call.gt) in ("het", "hom_alt")
        yield NiptSiteObservation(
            variant_id=record.variant_id,
            chrom=record.chr,
            pos=record.start,
            is_autosomal=_is_autosomal(record.chr),
            cf_present=present,
            cf_dp=depth,
            cf_alt_reads=alt_reads,
            cf_vaf=_call_vaf(cf_call, depth),
            cf_qual=record.qual if not per_sample else None,
            cf_coverage_dp=plasma_coverage if per_sample else depth,
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
            represented_by=represented_by,
            in_target=in_target,
        )


def clinvar_read(record: SmallVariantRecord, clinvar_coga: ClinvarCogaOut | None) -> list[str]:
    """The record's ClinVar as the NIPT readers read it, by the ClinVar design's one rule (B9,
    B11): the classification in CoGA's release where it holds the allele, else each
    annotation's; none where there is none."""
    if clinvar_coga is not None and clinvar_coga_held(clinvar_coga):
        values = [clinvar_coga.classification]
    else:
        values = [_annotation_clinvar(annotation) for annotation in record.annotations or []]
    return [value for value in values if value]


def triage_annotation(record: SmallVariantRecord, clinvar_coga: ClinvarCogaOut | None) -> DeNovoAnnotation:
    """What the de novo triage reads of a record's annotation: its most severe impact,
    its highest SpliceAI delta score and population frequency (the parser keeps all but
    ``gnomad_af`` under ``population_frequencies``; VEP MAX_AF is ``gnomad_popmax_af``),
    whether it is known (an rsID), and whether a ClinVar record may assert it pathogenic,
    read by the one rule (``clinvar_read``; ``clinvar_coga`` the value of its ALT)."""
    impact: str | None = None
    spliceai: float | None = None
    population_af: float | None = None
    known = bool(record.rsid)
    clinvar_pathogenic = clinvar_may_assert_pathogenic(clinvar_read(record, clinvar_coga))
    for annotation in record.annotations or []:
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
# A listed allele the family's father carries is his allele, not an artefact, when the
# plasma shows it at most at a fetal-like allele fraction and it recurs in few cfDNA
# samples (the R NIPT-M v0.7.3 diagnostic rule: a founder allele, where an allele in many
# plasmas is a paralogue or an unannotated common variant and stays an artefact).
PATERNAL_CARRIER_PROTECTION_MAX_VAF = 0.30
PATERNAL_CARRIER_PROTECTION_MAX_RECURRENCE = 3


def artifact_protection(record: SmallVariantRecord, clinvar_coga: ClinvarCogaOut | None) -> str | None:
    """Why the artifact list may not remove ``record`` from the family's analysis:
    ``clinvar`` or ``common`` (a population frequency above 5%); None when nothing does."""
    annotation = triage_annotation(record, clinvar_coga)
    if annotation.clinvar_pathogenic:
        return "clinvar"
    if annotation.max_population_af is not None and annotation.max_population_af > ARTIFACT_PROTECTION_COMMON_AF:
        return "common"
    return None


def paternal_carrier_protection(
    record: SmallVariantRecord,
    recurrence: int,
    *,
    father_sample_id: str,
    cfdna_sample_id: str,
    qc: NiptQualityThresholds,
) -> bool:
    """Whether a listed allele is the family's father's own: his call of it is het or
    hom-alt (``derive_father_state``), the plasma has no call of it or one at an allele
    fraction of at most ``PATERNAL_CARRIER_PROTECTION_MAX_VAF``, and the list records it in
    at most ``PATERNAL_CARRIER_PROTECTION_MAX_RECURRENCE`` cfDNA samples (``recurrence``)."""
    if recurrence > PATERNAL_CARRIER_PROTECTION_MAX_RECURRENCE:
        return False
    calls = {call.sample: call for call in record.calls}
    if derive_father_state(calls.get(father_sample_id), qc) not in ("het", "hom_alt"):
        return False
    plasma = calls.get(cfdna_sample_id)
    if plasma is None or not _alt_reads(plasma):
        return True
    vaf = _call_vaf(plasma, _ad_depth(plasma))
    return vaf is not None and vaf <= PATERNAL_CARRIER_PROTECTION_MAX_VAF


def family_artifact_ids(
    records: Iterable[SmallVariantRecord],
    listed: Mapping[str, int],
    *,
    clinvar_coga: Mapping[str, ClinvarCogaOut | None],
    father_sample_id: str,
    cfdna_sample_id: str,
    qc: NiptQualityThresholds,
) -> tuple[set[str], dict[str, str]]:
    """The listed artefacts of the family's records its analysis removes, and those that
    stay in (``variant_id -> reason``): the family's own annotation protects them
    (``artifact_protection``: ``clinvar``, ``common``), or the family's father carries them
    (``paternal_carrier_protection``: ``paternal_carrier``). ``listed`` maps each listed
    allele to the number of cfDNA samples the list records it in."""
    removed: set[str] = set()
    protected: dict[str, str] = {}
    if not listed:
        return removed, protected
    for record in records:
        recurrence = listed.get(record.variant_id)
        if recurrence is None:
            continue
        reason = listed_artifact_protection(
            record,
            recurrence,
            clinvar_coga=clinvar_coga.get(record.variant_id),
            father_sample_id=father_sample_id,
            cfdna_sample_id=cfdna_sample_id,
            qc=qc,
        )
        if reason is None:
            removed.add(record.variant_id)
        else:
            protected[record.variant_id] = reason
    return removed, protected


def listed_artifact_protection(
    record: SmallVariantRecord,
    recurrence: int,
    *,
    clinvar_coga: ClinvarCogaOut | None,
    father_sample_id: str,
    cfdna_sample_id: str,
    qc: NiptQualityThresholds,
) -> str | None:
    """Why a listed allele stays in the family's analysis: ``clinvar`` or ``common``
    (``artifact_protection``), or ``paternal_carrier`` (``paternal_carrier_protection``);
    None when the list removes it."""
    reason = artifact_protection(record, clinvar_coga)
    if reason is None and paternal_carrier_protection(
        record, recurrence, father_sample_id=father_sample_id, cfdna_sample_id=cfdna_sample_id, qc=qc
    ):
        reason = "paternal_carrier"
    return reason


# --------------------------------------------------------------------------- #
# Loading a family
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class NiptRecurrence:
    """How a variant's allele recurs in the other cfDNA samples of the family's assay
    (``count_cfdna_recurrence``): ``other_samples`` is how many of them have a call on the
    assembly, and ``protection`` why the recurrence filter keeps the variant whatever its
    count (``artifact_protection``: ``clinvar`` or ``common``)."""

    other_samples: int
    counts: CfdnaRecurrence
    protection: str | None


@dataclass(slots=True)
class NiptClassifiedVariant:
    """A family variant record paired with its NIPT classification.

    ``variant_out`` is the full small-variant serialization (with review/cohort
    hydrated), attached for the page slice so the variant list carries the same
    payload as the small-variant view."""

    record: SmallVariantRecord
    classification: NiptClassification
    # The value of its ALT in CoGA's ClinVar release, which the NIPT readers read where the
    # release holds it (``clinvar_read``); None where none is.
    clinvar_coga: ClinvarCogaOut | None
    variant_out: "VariantOut | None" = None
    site: NiptSiteObservation | None = None
    de_novo: DeNovoTriage | None = None
    recurrence: NiptRecurrence | None = None


@dataclass(slots=True)
class NiptVariantsResult:
    fetal_fraction: FetalFractionEstimate
    total: int
    variants: list[NiptClassifiedVariant]
    # The samples the analysis read as the plasma and the father, and the fetus's placeholder
    # (no data): the list shows each sample's reads by its part in the analysis.
    trio: NiptTrio
    # True when more variants matched the scope than the list classifies
    # (count_limit): the total is then a lower bound, and the list stops part-way
    # through the genome.
    total_is_estimated: bool = False
    count_limit: int | None = None
    # The recessive view's results are its genes, each with its carrier variants (``variants``
    # holds none): ``total`` counts the genes, ``gene_total`` and ``variant_total`` the genes
    # and their variants on every page (None in the other views).
    recessive_genes: list[NiptRecessiveGene] = field(default_factory=list)
    gene_total: int | None = None
    variant_total: int | None = None
    # The variants the review-tag filter selects that the analysis does not classify, so
    # that no list shows them, whatever the other filters, and why (variant id -> reason,
    # as the signed record names a reported one); empty without a review-tag filter.
    not_classified: dict[str, str] = field(default_factory=dict)
    # The two ClinVar releases the list names (design B7): CoGA's release the analysis read, of
    # each listed variant's ClinVar (CoGA), and the release of the ClinVar file the analysis's
    # annotation names. None where the search ends before its variants are read (a review-tag
    # filter or a gene panel that selects none).
    clinvar_coga_release: date | None = None
    clinvar_annotation_release: date | None = None


@dataclass(slots=True)
class NiptFamilySummary:
    """The NIPT summary: the analysis, the QC gate's bound, and the plasma's coverage QC and
    sex profile from its per-target coverage (None without one)."""

    analysis: NiptAnalysisResult
    # The trio of the pregnancy the analysis is of: its monogenic NIPT, samples and fetuses.
    trio: NiptTrio
    # The bound of the QC gate on the expected fetal reads (nipt_qc_gate): QA's or provisional.
    gate_bound: NiptGateBound
    target_coverage: TargetCoverageSummary | None = None
    sex_chromosomes: SexChromosomeCoverage | None = None


async def _load_family_records(context: FamilyMetadataContext) -> list[SmallVariantRecord]:
    # No filters: the summary/FF estimation spans the whole family cohort (FF
    # needs every category-7 site and the category counts are cohort-wide). Without their
    # annotation: the analysis reads the calls, and a plasma callset holds every low-level
    # call, a million rows or more. A record whose annotation is read is annotated apart.
    filters = SmallVariantQueryFilters(page=1, page_size=100)
    return await _fetch_small_variant_rows(context, filters, limit=None, annotated=False)


async def _resolve_family_trio_context(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    project_id: str | None,
    analysis: str | None,
) -> tuple[FamilyOut, NiptTrio, FamilyMetadataContext]:
    """The family, the trio of its monogenic NIPT ``analysis`` (one pregnancy's, by its key;
    without one the family's only monogenic NIPT, ``nipt_analysis_of``) and its metadata
    context; 400 when the family is not configured for monogenic NIPT."""
    family = await get_family_record(session, family_id, user)
    if not is_monogenic_nipt_family(family):
        raise HTTPException(
            status_code=400,
            detail="Family is not configured for monogenic NIPT analysis",
        )
    own = nipt_analysis_of(family.metadata, MONOGENIC_NIPT_ANALYSIS_TYPE, analysis)
    trio = resolve_nipt_trio(family, own)
    if trio is None:
        raise HTTPException(status_code=400, detail=NIPT_TRIO_UNRESOLVED)
    # The monogenic NIPT is read from its own analysis's callset: a family that also holds
    # another analysis with small variants of its own (another pregnancy's, the couple's carrier
    # screening) never lends it its rows (analysis_scope.py).
    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        project_id=project_id,
        analysis=own,
    )
    return family, trio, context


async def _resolve_trio_and_context(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    project_id: str | None,
    analysis: str | None,
) -> tuple[NiptTrio, FamilyMetadataContext, str]:
    family, trio, context = await _resolve_family_trio_context(
        session, family_id=family_id, user=user, project_id=project_id, analysis=analysis
    )
    return trio, context, nipt_assay_key(family, trio)


async def _load_listed_artifacts(
    session: AsyncSession, context: FamilyMetadataContext, assay_key: str
) -> dict[str, int]:
    return await load_nipt_artifacts(
        session, assembly_id=context.assembly_id, assay_key=assay_key
    )


def nipt_coverage_source(trio: NiptTrio) -> str:
    """The source the per-target coverage tables of the trio's pregnancy are stored under: the
    pregnancy's own (``family_package_records.suffixed_source``), so a father sampled for each
    pregnancy is read with the table its pregnancy's package gave."""
    return suffixed_source(TARGET_COVERAGE_SOURCE, pregnancy_of(trio.analysis))


async def _load_target_rows(
    context: FamilyMetadataContext,
    trio: NiptTrio,
    sample_id: str,
    *,
    with_metadata: bool,
    genes: Sequence[str] | None = None,
) -> list[TargetCoverageRow]:
    """A sample's per-target coverage (its ``target_coverage`` track) of the trio's pregnancy,
    or only the targets of ``genes`` (case-insensitive); [] without one."""
    sample_uuid = context.sample_name_to_uuid.get(sample_id)
    if not sample_uuid or not context.assembly_name:
        return []
    rows = await fetch_interval_track_rows(
        context.assembly_name,
        sample_uuid=sample_uuid,
        track_type=TARGET_COVERAGE_TRACK_TYPE,
        chromosomes=[],
        source=nipt_coverage_source(trio),
        include_metadata=with_metadata,
        record_ids=genes,
    )
    return [target_row_from_track(row) for row in rows]


async def _load_family_inputs(
    session: AsyncSession, context: FamilyMetadataContext, trio: NiptTrio, assay_key: str
) -> NiptAnalysisInputs:
    """What the family's analysis is computed from besides its records (``NiptAnalysisInputs``),
    read for each request."""
    # The data version first: a write of the family's variants after it moves it.
    data_version = await _family_small_variant_data_version(context)
    listed = await _load_listed_artifacts(session, context, assay_key)
    gate_bound = await load_nipt_gate_bound(session)
    plasma_targets, father_targets = await asyncio.gather(
        # With each target's covered share: a target with bases without coverage is weak, in
        # the summary and the record as on the coverage page.
        _load_target_rows(context, trio, trio.cfdna_sample_id, with_metadata=True),
        _load_target_rows(context, trio, trio.father_sample_id, with_metadata=False),
    )
    return NiptAnalysisInputs(
        data_version=data_version,
        scope=context.scope,
        samples=dict(context.sample_name_to_uuid),
        listed=listed,
        gate_bound=gate_bound,
        plasma_targets=plasma_targets,
        father_targets=father_targets,
        clinvar_coga=await clinvar_coga_read(session, context.assembly_name),
    )


def _family_sites(
    records: Sequence[SmallVariantRecord],
    trio: NiptTrio,
    qc: NiptQualityThresholds,
    *,
    plasma_targets: list[TargetCoverageRow],
    father_targets: list[TargetCoverageRow],
    counterparts: NiptCounterparts,
) -> Iterator[NiptSiteObservation]:
    return build_nipt_observations(
        records,
        father_sample_id=trio.father_sample_id,
        cfdna_sample_id=trio.cfdna_sample_id,
        qc=qc,
        plasma_depth=TargetDepthLookup(plasma_targets) if plasma_targets else None,
        father_depth=TargetDepthLookup(father_targets) if father_targets else None,
        counterparts=counterparts,
    )


@dataclass(slots=True)
class _FamilyAnalysis:
    """A family's NIPT callset analysed (``_analyse_family``), kept without its records
    (``nipt_analysis_cache``): the inputs it was computed from (the assay's artifact list, each
    allele with its recurrence on it, and the per-target coverage among them), what the list
    removed from the analysis and what stayed in (variant id -> reason), what the family's
    records give each other's observations, and the summary."""

    inputs: NiptAnalysisInputs
    removed: set[str]
    protected: dict[str, str]
    counterparts: NiptCounterparts
    summary: NiptFamilySummary

    @property
    def fetal_sex(self) -> str:
        """The fetal sex chrX is classified with, as the summary classifies its sites: the
        summary's call in a singleton pregnancy, ``indeterminate`` in a multiple one
        (``model_fetal_sex``)."""
        return model_fetal_sex(self.summary.analysis.fetal_sex_call, len(self.summary.trio.fetus_sample_ids))

    async def classify(
        self,
        records: list[SmallVariantRecord],
        trio: NiptTrio,
        qc: NiptQualityThresholds,
        *,
        min_confidence: float | None = None,
    ) -> list[NiptClassifiedVariant]:
        """``records``, some of the family's, classified as the summary classifies its sites
        (``_classify_candidates``): against its fetal fraction and fetal sex, with the artifact
        list, the per-target coverage and CoGA's ClinVar release the analysis read, each
        observed as the family's every record observes it. The values of the release are read
        once for them all; the classification, pure CPU work, runs in a worker thread."""
        clinvar_coga = await clinvar_coga_by_variant(self.inputs.clinvar_coga, records)
        return await asyncio.to_thread(
            _classify_candidates,
            records,
            trio,
            self.inputs.listed,
            self.summary.analysis.fetal_fraction,
            qc,
            min_confidence,
            plasma_targets=self.inputs.plasma_targets,
            father_targets=self.inputs.father_targets,
            counterparts=self.counterparts,
            fetal_sex=self.fetal_sex,
            clinvar_coga=clinvar_coga,
        )


async def _analyse_family(
    context: FamilyMetadataContext,
    trio: NiptTrio,
    qc: NiptQualityThresholds,
    inputs: NiptAnalysisInputs,
) -> _FamilyAnalysis:
    """The family's analysis of its every record, read without its annotation
    (``_load_family_records``); the listed records' protection reads theirs."""
    records = await _load_family_records(context)
    listed_records = await annotate_small_variant_records(
        context, [record for record in records if record.variant_id in inputs.listed]
    )
    removed, protected = family_artifact_ids(
        listed_records,
        inputs.listed,
        clinvar_coga=await clinvar_coga_by_variant(inputs.clinvar_coga, listed_records),
        father_sample_id=trio.father_sample_id,
        cfdna_sample_id=trio.cfdna_sample_id,
        qc=qc,
    )
    # The whole family callset goes through the analysis: pure CPU work, so it runs in a
    # worker thread and other requests are served meanwhile.
    counterparts, analysis = await asyncio.to_thread(
        _analyse_family_sites, records, trio, qc, inputs, removed.__contains__
    )
    return _FamilyAnalysis(
        inputs=inputs,
        removed=removed,
        protected=protected,
        counterparts=counterparts,
        summary=_summarize_family(analysis, trio, inputs),
    )


def _analysis_key(
    context: FamilyMetadataContext, trio: NiptTrio, assay_key: str, qc: NiptQualityThresholds
) -> NiptAnalysisKey:
    return NiptAnalysisKey(
        family_uuid=context.family_uuid,
        assembly_name=context.assembly_name,
        project_ids=tuple(sorted(context.project_ids)),
        analysis=trio.analysis,
        father_sample_id=trio.father_sample_id,
        cfdna_sample_id=trio.cfdna_sample_id,
        fetus_sample_ids=tuple(trio.fetus_sample_ids),
        assay_key=assay_key,
        thresholds=astuple(qc),
    )


# The analyses this worker process keeps (nipt_analysis_cache).
_KEPT_ANALYSES: NiptAnalysisCache[_FamilyAnalysis] = NiptAnalysisCache()


async def _kept_family_analysis(
    session: AsyncSession,
    context: FamilyMetadataContext,
    trio: NiptTrio,
    assay_key: str,
    qc: NiptQualityThresholds,
) -> _FamilyAnalysis:
    """The family's analysis, kept by this worker process from equal inputs or computed."""
    inputs = await _load_family_inputs(session, context, trio, assay_key)
    return await _KEPT_ANALYSES.analysis(
        _analysis_key(context, trio, assay_key, qc),
        inputs,
        lambda: _analyse_family(context, trio, qc, inputs),
        lambda: _family_small_variant_data_version(context),
    )


class SharedNiptAnalysis:
    """A family's NIPT analysis read once for everything one sign-out builds: the Sample
    QC's NIPT checks and the signed record's NIPT section.

    Read for each, its inputs would be read twice per sign-out, and the two could read
    different inputs: the assay's artifact list is curated apart from the family's data, and
    a change between the two reads would make the Sample QC and the NIPT section of one record
    disagree. Shared, they read the same values. An analysis is shared only with a caller
    asking for the same one (``NiptAnalysisKey``); one that failed fails for both, so neither
    reports what the other could not read.
    """

    def __init__(self) -> None:
        self._outcomes: dict[NiptAnalysisKey, _FamilyAnalysis | Exception] = {}

    async def analysis(
        self,
        session: AsyncSession,
        context: FamilyMetadataContext,
        trio: NiptTrio,
        assay_key: str,
        qc: NiptQualityThresholds,
    ) -> _FamilyAnalysis:
        key = _analysis_key(context, trio, assay_key, qc)
        outcome = self._outcomes.get(key)
        if outcome is None:
            try:
                outcome = await _kept_family_analysis(session, context, trio, assay_key, qc)
            except Exception as exc:  # noqa: BLE001 — kept and raised again, to each caller
                outcome = exc
            self._outcomes[key] = outcome
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


async def _family_analysis(
    session: AsyncSession,
    context: FamilyMetadataContext,
    trio: NiptTrio,
    assay_key: str,
    qc: NiptQualityThresholds,
    shared: SharedNiptAnalysis | None,
) -> _FamilyAnalysis:
    if shared is None:
        return await _kept_family_analysis(session, context, trio, assay_key, qc)
    return await shared.analysis(session, context, trio, assay_key, qc)


async def run_family_nipt_analysis(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    project_id: str | None = None,
    qc: NiptQualityThresholds | None = None,
    shared_nipt: SharedNiptAnalysis | None = None,
    analysis: str | None = None,
) -> NiptAnalysisResult:
    summary = await get_family_nipt_summary(
        session,
        family_id=family_id,
        user=user,
        project_id=project_id,
        qc=qc,
        shared_nipt=shared_nipt,
        analysis=analysis,
    )
    return summary.analysis


async def get_family_nipt_summary(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    project_id: str | None = None,
    qc: NiptQualityThresholds | None = None,
    shared_nipt: SharedNiptAnalysis | None = None,
    analysis: str | None = None,
) -> NiptFamilySummary:
    """The NIPT analysis of the whole callset of the monogenic NIPT ``analysis`` (one
    pregnancy's), with the plasma's target coverage QC and sex-chromosome profile;
    ``shared_nipt``'s, when it holds this analysis."""
    trio, context, assay_key = await _resolve_trio_and_context(
        session, family_id=family_id, user=user, project_id=project_id, analysis=analysis
    )
    computed = await _family_analysis(
        session, context, trio, assay_key, qc or NiptQualityThresholds(), shared_nipt
    )
    return computed.summary


@dataclass(slots=True)
class NiptArtifactCounts:
    """What the assay's recurrent-artifact list did to one family's analysis: the alleles it
    lists, the family's alleles among them, those removed from the analysis and those the
    family's annotation kept in, by reason (``clinvar``, ``common``)."""

    listed: int
    in_family: int
    removed: int
    protected: dict[str, int]


async def get_family_nipt_summary_with_artifacts(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    project_id: str | None = None,
    qc: NiptQualityThresholds | None = None,
    analysis: str | None = None,
) -> tuple[NiptFamilySummary, NiptArtifactCounts]:
    """The NIPT summary of the family's monogenic NIPT ``analysis`` (``get_family_nipt_summary``)
    and what the artifact list did to it, for the variant summary's NIPT-M tab."""
    trio, context, assay_key = await _resolve_trio_and_context(
        session, family_id=family_id, user=user, project_id=project_id, analysis=analysis
    )
    computed = await _kept_family_analysis(session, context, trio, assay_key, qc or NiptQualityThresholds())
    protected: dict[str, int] = {}
    for reason in computed.protected.values():
        protected[reason] = protected.get(reason, 0) + 1
    counts = NiptArtifactCounts(
        listed=len(computed.inputs.listed),
        in_family=len(computed.removed) + len(computed.protected),
        removed=len(computed.removed),
        protected=protected,
    )
    return computed.summary, counts


def _summarize_family(
    analysis: NiptAnalysisResult, trio: NiptTrio, inputs: NiptAnalysisInputs
) -> NiptFamilySummary:
    """The summary of the family's analysis, the NIPT page's and the signed record's. The
    coverage QC counts the targets too thin for the QC gate's fetal reads at the estimated
    fetal fraction."""
    plasma_targets = inputs.plasma_targets
    target_coverage = (
        summarize_target_coverage(
            plasma_targets,
            fetal_read_depth=fetal_read_depth(analysis.fetal_fraction.ff_computed, inputs.gate_bound),
        )
        if plasma_targets
        else None
    )
    return NiptFamilySummary(
        analysis=analysis,
        trio=trio,
        gate_bound=inputs.gate_bound,
        target_coverage=target_coverage,
        sex_chromosomes=sex_chromosome_coverage(plasma_targets) if plasma_targets else None,
    )


def _analyse_family_sites(
    records: Sequence[SmallVariantRecord],
    trio: NiptTrio,
    qc: NiptQualityThresholds,
    inputs: NiptAnalysisInputs,
    artifact_lookup: Callable[[str], bool],
) -> tuple[NiptCounterparts, NiptAnalysisResult]:
    """What the family's records give each other's observations, and the analysis of their
    observations, read one at a time."""
    counterparts = nipt_counterparts(
        records, father_sample_id=trio.father_sample_id, cfdna_sample_id=trio.cfdna_sample_id
    )
    plasma_targets = inputs.plasma_targets
    return counterparts, run_nipt_analysis(
        _family_sites(
            records,
            trio,
            qc,
            plasma_targets=plasma_targets,
            father_targets=inputs.father_targets,
            counterparts=counterparts,
        ),
        qc,
        artifact_lookup=artifact_lookup,
        # The plasma's sex-chromosome profile: with the father's X alleles it reads the fetal
        # sex chrX is classified with.
        chry_profile=sex_chromosome_coverage(plasma_targets).profile if plasma_targets else None,
        fetuses=len(trio.fetus_sample_ids),
    )


def _classify_candidates(
    records: list[SmallVariantRecord],
    trio: NiptTrio,
    listed_artifacts: Mapping[str, int],
    ff_estimate: FetalFractionEstimate,
    qc: NiptQualityThresholds,
    min_confidence: float | None,
    *,
    plasma_targets: list[TargetCoverageRow],
    father_targets: list[TargetCoverageRow],
    counterparts: NiptCounterparts,
    clinvar_coga: Mapping[str, ClinvarCogaOut | None],
    fetal_sex: str | None = None,
) -> list[NiptClassifiedVariant]:
    # The filtered records are observed with the family's every record's ``counterparts``:
    # the other representation of an allele, or a split record's sibling, need not pass the
    # filter.
    observations = {
        observation.variant_id: observation
        for observation in _family_sites(
            records,
            trio,
            qc,
            plasma_targets=plasma_targets,
            father_targets=father_targets,
            counterparts=counterparts,
        )
    }
    classified: list[NiptClassifiedVariant] = []
    for record in records:
        recurrence = listed_artifacts.get(record.variant_id)
        protection = (
            listed_artifact_protection(
                record,
                recurrence,
                clinvar_coga=clinvar_coga.get(record.variant_id),
                father_sample_id=trio.father_sample_id,
                cfdna_sample_id=trio.cfdna_sample_id,
                qc=qc,
            )
            if recurrence is not None
            else None
        )
        if recurrence is not None and protection is None:
            continue
        observation = observations.get(record.variant_id)
        if observation is None:
            continue
        classification = classify_site(observation, ff_estimate, qc, fetal_sex=fetal_sex)
        if protection is not None:
            # Listed, but the family's annotation or father says it is no artefact.
            classification.flags.append(ARTIFACT_LIST_PROTECTED_FLAG)
        if min_confidence is not None and (
            classification.category is None or classification.confidence < min_confidence
        ):
            continue
        classified.append(
            NiptClassifiedVariant(
                record=record,
                classification=classification,
                clinvar_coga=clinvar_coga.get(record.variant_id),
                site=observation,
            )
        )
    return classified


def _build_nipt_query_filters(query_filters: dict) -> SmallVariantQueryFilters:
    """Build a SmallVariantQueryFilters from the NIPT filter set.

    NIPT reuses the full small-variant filter form, so the same annotation /
    frequency / in-silico / pathogenicity / location filters apply. Per-sample
    genotype and phenotype-prioritisation filters do not apply to cfDNA (the
    maternal/fetal call is inferred) and are not forwarded, nor are the review
    classification and saved-notes filters; the review tags are applied by variant id in
    the variant load (``get_family_nipt_variants``), as the family search applies them.
    The maternal/fetal `category` filter and `inheritance` preset are applied on the
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
        clinvar_pathogenic_override=bool(query_filters.get("clinvar_pathogenic_override", False)),
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
    """The mother's alleles (categories 2-6 on a plasma call of 25% or more, ``mother_carries``),
    with whether the fetus inherited each: by default only those it did (probability 0.5 or
    more)."""
    kept: list[NiptClassifiedVariant] = []
    for item in classified:
        classification = item.classification
        if not mother_carries(classification):
            continue
        probability = classification.maternal_allele_probability
        if include_not_inherited or (probability is not None and probability >= 0.5):
            kept.append(item)
    return kept


def _technically_retained(item: NiptClassifiedVariant) -> bool:
    """The R validation's technical filter: the site's plasma call passes the cfDNA quality
    filter inside a capture target. A father's allele the plasma does not call has no call to
    fail; whether the fetus inherited it is the classification's."""
    if item.classification.quality_failures:
        return False
    return item.site is None or item.site.in_target is not False


@dataclass(slots=True)
class NiptRecessiveGene:
    """A gene of the recessive view (``_recessive_view``): the fetal status and its
    probabilities (``RecessiveGeneRisk``), the card gene's id, and the gene's carrier variants
    in genomic order."""

    risk: RecessiveGeneRisk
    gene_id: str | None
    variants: list[NiptClassifiedVariant]


def _recessive_view(
    classified: list[NiptClassifiedVariant], qc: NiptQualityThresholds, *, technical: bool = False
) -> list[NiptRecessiveGene]:
    """The genes in which both parents carry an allele, and on chrX outside the
    pseudo-autosomal regions those in which a son's mother carries one, one parent of a
    daughter, or either parent of a fetus whose sex is not known, each with the fetal status
    and its probabilities (``nipt_triage.recessive_gene_risks``) and its carrier variants, in
    the order of ``nipt_triage.recessive_listing_order``.

    A variant is read in its card's gene, as the expanded carrier screen reads it: the gene id
    and symbol of its primary annotation (``_gene_match_keys``), the variants that share either
    one gene (``_joined_genes``). Another gene its annotation names (one it lies up- or
    downstream of) pairs nothing, and a variant without a gene is in none.

    A variant marks the mother a carrier when her inferred genotype is het or hom (categories
    2-6) on a plasma call of 25% or more (``nipt_triage.mother_carries``), and the father when
    his genotype is het or hom-alt (a homozygous or hemizygous parent always passes the
    allele). Run the gene, consequence and frequency filters first so the carriers are
    plausibly causal. ``technical`` applies the technical filter (``_technically_retained``),
    as the locked diagnostic candidates do.
    """
    keyed = [(item, keys) for item in classified if (keys := _gene_match_keys(item.record))]
    genes: dict[tuple[str, str], list[NiptClassifiedVariant]] = {}
    for (item, _keys), gene in zip(keyed, _joined_genes([keys for _item, keys in keyed]), strict=True):
        genes.setdefault(gene, []).append(item)
    recessive: list[NiptRecessiveGene] = []
    for items in genes.values():
        symbols = [_primary_gene_keys(item.record) for item in items]
        gene_id = next((gene_id for _symbol, gene_id in symbols if gene_id), None)
        name = next((symbol for symbol, _gene_id in symbols if symbol), None) or gene_id
        assert name is not None  # _gene_match_keys keys a variant by its symbol or its gene id
        candidates = [
            RecessiveCandidate(
                variant_id=item.record.variant_id,
                # A het call of a man's X is an artefact, read as no call (classify_site).
                father_state="missing"
                if FATHER_X_HET_FLAG in item.classification.flags
                else _trusted_father(item, qc),
                classification=item.classification,
                mode=mode,
                variant_class=item.site.variant_class if item.site is not None else "SNV",
                has_population_af=triage_annotation(item.record, item.clinvar_coga).max_population_af is not None,
                plasma_call_retained=not technical or _technically_retained(item),
            )
            for item in items
            if (mode := recessive_mode(item.classification, item.record.chr)) is not None
        ]
        for risk in recessive_gene_risks(name, candidates):
            carried = {allele.variant_id for allele in [*risk.maternal, *risk.paternal]}
            variants = [item for item in items if item.record.variant_id in carried]
            recessive.append(NiptRecessiveGene(risk=risk, gene_id=gene_id, variants=variants))
    recessive.sort(key=lambda gene: recessive_listing_order(gene.risk))
    return recessive


@dataclass(slots=True)
class _RecurrenceScope:
    """The other cfDNA samples of the family's assay that a recurrence count reads; how
    many of them have a call on the assembly is read once, on the first count."""

    assembly_name: str | None
    carrier_samples: Sequence[str]
    family_uuid: str
    rules: CfdnaRecurrenceRules
    other_samples: int | None = None


async def _recurrence_scope(
    session: AsyncSession, context: FamilyMetadataContext, assay_key: str, qc: NiptQualityThresholds
) -> _RecurrenceScope:
    return _RecurrenceScope(
        assembly_name=context.assembly_name,
        carrier_samples=await assay_cfdna_carrier_samples(session, assay_key=assay_key),
        family_uuid=context.family_uuid,
        rules=recurrence_rules(qc),
    )


async def _attach_recurrence(items: Sequence[NiptClassifiedVariant], scope: _RecurrenceScope) -> None:
    """Give each of ``items`` that has none its recurrence in the other cfDNA samples of the
    family's assay, in one count."""
    pending = [item for item in items if item.recurrence is None]
    if not pending:
        return
    counts: dict[str, CfdnaRecurrence] = {}
    if scope.assembly_name and scope.carrier_samples:
        counts = await count_cfdna_recurrence(
            scope.assembly_name,
            [item.record.variant_id for item in pending],
            carrier_samples=scope.carrier_samples,
            exclude_family_uuid=scope.family_uuid,
            rules=scope.rules,
        )
        if scope.other_samples is None:
            scope.other_samples = await count_other_cfdna_samples(
                scope.assembly_name,
                carrier_samples=scope.carrier_samples,
                exclude_family_uuid=scope.family_uuid,
            )
    for item in pending:
        item.recurrence = NiptRecurrence(
            other_samples=scope.other_samples or 0,
            counts=counts.get(item.record.variant_id, CfdnaRecurrence()),
            protection=artifact_protection(item.record, item.clinvar_coga),
        )


async def _serve_variant_outs(
    session: AsyncSession,
    context: FamilyMetadataContext,
    filters: SmallVariantQueryFilters,
    items: Sequence[NiptClassifiedVariant],
    clinvar_coga: ClinvarCogaRead,
) -> None:
    """Give each of ``items`` its full small-variant payload (``variant_out``), hydrated as the
    small-variant list hydrates it (review, internal cohort, gene constraint), so a NIPT list
    shows a variant as the small-variant view does, classification aside, each naming the
    transcript the annotation filters held on when its primary one does not. Its ClinVar (CoGA)
    per ALT is read in ``clinvar_coga``, the release the analysis read: the page, its export and
    the ACMG evaluation show the value the analysis's decisions read (``clinvar_read``)."""
    variant_outs = await _small_variant_outs(context, filters, [item.record for item in items])
    await _hydrate_small_variant_outs(session, context=context, variants=variant_outs)
    await attach_clinvar_coga(clinvar_coga, variant_outs)
    for item, variant_out in zip(items, variant_outs):
        item.variant_out = variant_out


def hidden_as_recurrent(item: NiptClassifiedVariant, max_unexplained: int) -> bool:
    """Whether the recurrence filter leaves ``item`` out: an unexplained low-VAF call in
    more than ``max_unexplained`` other cfDNA samples of the assay, and nothing protects it
    (``artifact_protection``: a ClinVar record that may call it pathogenic, such as a de
    novo hotspot, or a common allele)."""
    recurrence = item.recurrence
    if recurrence is None:
        raise RuntimeError("NIPT variant was filtered on recurrence before its count")
    return recurrence.protection is None and recurrence.counts.unexplained_low_vaf > max_unexplained


async def _triage_de_novo_candidates(
    classified: list[NiptClassifiedVariant],
    ff_estimate: FetalFractionEstimate,
    *,
    scope: _RecurrenceScope,
) -> list[NiptClassifiedVariant]:
    """The de novo candidates among ``classified``, each given its triage (nipt_triage) in
    the family's fetal window, whatever its label; [] without a window. The triage reads how
    many other cfDNA samples of the assay carry the allele from the candidate's recurrence.
    The de novo view lists them from a priority up; the signed record freezes the triage of
    each reported one."""
    window = de_novo_window(ff_estimate)
    candidates = [
        item
        for item in classified
        if item.site is not None and is_de_novo_candidate(item.site, item.classification)
    ]
    if window is None or not candidates:
        return []
    await _attach_recurrence(candidates, scope)
    triaged: list[NiptClassifiedVariant] = []
    for item in candidates:
        assert item.site is not None and item.recurrence is not None
        triage = triage_de_novo(
            item.site,
            window,
            triage_annotation(item.record, item.clinvar_coga),
            other_cfdna_carriers=item.recurrence.counts.carriers,
        )
        if triage is None:  # pragma: no cover - a window always gives a triage
            continue
        item.de_novo = triage
        triaged.append(item)
    return triaged


async def _de_novo_view(
    classified: list[NiptClassifiedVariant],
    ff_estimate: FetalFractionEstimate,
    *,
    scope: _RecurrenceScope,
    min_priority: str,
) -> list[NiptClassifiedVariant]:
    """The de novo candidates in the family's fetal window, triaged (nipt_triage) and
    ranked by score, from ``min_priority`` up."""
    wanted = _DE_NOVO_PRIORITY_LABELS.get(min_priority, _DE_NOVO_PRIORITY_LABELS["low"])
    triaged = await _triage_de_novo_candidates(classified, ff_estimate, scope=scope)
    kept: list[NiptClassifiedVariant] = []
    for item in triaged:
        if item.de_novo is None or item.de_novo.label not in wanted:
            item.de_novo = None
            continue
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
    page_size: int | None = 100,
    qc: NiptQualityThresholds | None = None,
    review_tags: list[str] | None = None,
    exclude_review_tags: list[str] | None = None,
    max_unexplained_recurrence: int | None = None,
    exclude_cohort_common: bool = False,
    panel_search: str = "nipt_variants",
    analysis: str | None = None,
) -> NiptVariantsResult:
    """The family's cfDNA variants in a search, classified, in an inheritance view:

    - ``de_novo``: the triaged de novo candidates in the fetal window, highest score first;
    - ``paternal_dominant``: the father's alleles the fetus inherited (with
      ``include_not_inherited``, also those it did not);
    - ``maternal_dominant``: the mother's alleles the fetus inherited (idem);
    - ``recessive_at_risk``: the genes of ``_recessive_view`` (``recessive_genes``), each with
      its fetal status and its carrier variants; a page holds genes.

    ``categories`` keeps the given maternal/fetal categories (within a view, both apply); in
    the recessive view it keeps the genes with a carrier variant in one of them, the pairing
    reading every carrier variant.
    ``review_tags`` keeps the variants with one of the review tags, and
    ``exclude_review_tags`` leaves out those with one of these, as the small-variant list
    reads them (``resolve_review_selection``); a selected variant the analysis does not
    classify is named in ``not_classified``, with why. With ``review_tags=["report"]`` and
    no other filter, the list holds the variants a sign-out freezes, as it classifies them
    (``get_family_nipt_record``).

    Each listed variant carries its recurrence in the other cfDNA samples of the assay
    (``NiptRecurrence``). ``max_unexplained_recurrence`` leaves out, after the view and the
    categories, the variants that are an unexplained low-VAF call in more of them
    (``hidden_as_recurrent``), as recurrent artefacts; in the recessive view, the genes with no
    other carrier variant. ``exclude_cohort_common`` leaves out, before the view, the alleles the
    locked criteria read as an unannotated common allele (``_not_cohort_common``), so that the
    recessive view pairs none of them.

    ``page_size`` None gives every classified variant of the search, as the CSV export
    writes them; ``panel_search`` names the search on the family's audit trail when it
    applies a gene panel (``PANEL_SEARCHES``).
    """
    qc = qc or NiptQualityThresholds()
    if max_unexplained_recurrence is not None and max_unexplained_recurrence < 0:
        raise HTTPException(status_code=400, detail="max_unexplained_recurrence must be 0 or more")
    if inheritance and inheritance not in _INHERITANCE_VIEWS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported NIPT inheritance preset: {inheritance}",
        )
    if de_novo_priority not in _DE_NOVO_PRIORITY_LABELS:
        raise HTTPException(status_code=400, detail=f"Unsupported de novo priority: {de_novo_priority}")
    wanted: set[int] = set(categories or [])

    trio, context, assay_key = await _resolve_trio_and_context(
        session, family_id=family_id, user=user, project_id=project_id, analysis=analysis
    )

    # The location filters, as the family search applies them: the interval list, the
    # excluded intervals and the excluded genes. Read before the cohort work, so an
    # unreadable interval fails fast.
    filters = _build_nipt_query_filters(query_filters or {})
    include_regions = _parse_interval_regions(filters.intervals)
    exclude_regions = _parse_interval_regions(filters.exclude_intervals, label="Excluded interval")
    exclude_gene_regions = (
        await _fetch_gene_regions(session, gene_query=filters.exclude_gene, assembly_id=context.assembly_id)
        if filters.exclude_gene
        else []
    )
    # The review tags, as the family search selects by them: the ids go into the variant
    # load itself, so a tagged variant is never one the classification limit cut off.
    review = await resolve_review_selection(
        session, context.family_uuid, scope=context.scope, tags=review_tags, exclude_tags=exclude_review_tags
    )

    # Fetal fraction is estimated cohort-wide (the FF/2 category-7 sites are rarely inside
    # the clinical filter): the summary's, and the fetal sex it classifies chrX with in this
    # pregnancy. The filtered subset is classified against them.
    family = await _kept_family_analysis(session, context, trio, assay_key, qc)
    ff_estimate = family.summary.analysis.fetal_fraction
    # The ClinVar filters, their exclusion and the P/LP override read CoGA's release the analysis
    # reads, where it holds the allele (the ClinVar design's one rule, B11), as on the family page.
    filters.clinvar_coga_release = family.inputs.clinvar_coga.release

    # The selected variants no list can show, whatever the other filters: the analysis
    # does not classify them. Read as the signed record reads the reported ones.
    not_classified: dict[str, str] = {}
    if review.include:
        _selected, not_classified = await _classify_selected(
            context, review.include - review.exclude, family, trio, qc
        )
    if review.matches_nothing:
        return NiptVariantsResult(fetal_fraction=ff_estimate, total=0, variants=[], trio=trio)

    panel_constraints = PanelFilterConstraints()
    if filters.panel_id:
        # On the family's clinical audit trail before it is applied (REQ-CARR-003).
        await record_family_panel_use(
            session, context=context, panel_id=filters.panel_id, user=user, search=panel_search
        )
        panel_constraints = await _fetch_panel_constraints(
            session, filters.panel_id, assembly_id=context.assembly_id
        )
        if not panel_constraints.genes and not panel_constraints.regions:
            return NiptVariantsResult(
                fetal_fraction=ff_estimate, total=0, variants=[], trio=trio, not_classified=not_classified
            )

    # One row past the limit tells a cut list from a complete one: a list stopped at the
    # limit with a total that looked exact would have the page and the report read as
    # complete while the rest of the genome was never classified.
    fetch_limit = NIPT_VARIANT_FETCH_LIMIT
    carrier_calls = (
        CarrierCallRows(
            plasma_sample_id=trio.cfdna_sample_id,
            father_sample_id=trio.father_sample_id,
            maternal_vaf=MATERNAL_ALLELE_VAF_BOUNDARY,
        )
        if inheritance in _CARRIER_ROW_VIEWS
        else None
    )
    records = await _fetch_small_variant_rows(
        context,
        filters,
        panel_constraints=panel_constraints,
        include_variant_ids=sorted(review.include) if review.include is not None else None,
        exclude_variant_ids=sorted(review.exclude),
        include_regions=include_regions,
        exclude_regions=exclude_regions,
        exclude_gene_regions=exclude_gene_regions,
        exclude_gene_terms=_split_gene_terms(filters.exclude_gene),
        limit=fetch_limit + 1,
        carrier_calls=carrier_calls,
    )
    capped = len(records) > fetch_limit
    if capped:
        records = records[:fetch_limit]
    classified = await family.classify(records, trio, qc, min_confidence=min_confidence)

    recurrence_scope = await _recurrence_scope(session, context, assay_key, qc)
    if exclude_cohort_common:
        classified = await _not_cohort_common(classified, recurrence_scope)
    gene_total: int | None = None
    variant_total: int | None = None
    if inheritance == RECESSIVE_AT_RISK:
        # The pairing reads every carrier variant of the search, as the report and the signed
        # record pair them; the categories and the recurrence filter choose the genes listed,
        # each with every carrier variant of its own. A page holds genes.
        genes = _recessive_view(classified, qc)
        carriers = [item for gene in genes for item in gene.variants]
        shown = {
            item.record.variant_id
            for item in await _shown(carriers, wanted, max_unexplained_recurrence, recurrence_scope)
        }
        genes = [gene for gene in genes if any(item.record.variant_id in shown for item in gene.variants)]
        page_genes = _page_of(genes, page, page_size)
        listed: list[NiptClassifiedVariant] = []
        page_items = [item for gene in page_genes for item in gene.variants]
        total = gene_total = len(genes)
        variant_total = sum(len(gene.variants) for gene in genes)
    else:
        if inheritance == PATERNAL_INHERITED:
            classified = _paternal_view(classified, qc, include_not_inherited=include_not_inherited)
        elif inheritance == MATERNAL_INHERITED:
            classified = _maternal_view(classified, include_not_inherited=include_not_inherited)
        elif inheritance == DE_NOVO:
            classified = await _de_novo_view(
                classified, ff_estimate, scope=recurrence_scope, min_priority=de_novo_priority
            )
        classified = await _shown(classified, wanted, max_unexplained_recurrence, recurrence_scope)
        page_genes = []
        listed = page_items = _page_of(classified, page, page_size)
        total = len(classified)
    await _attach_recurrence(page_items, recurrence_scope)
    await _serve_variant_outs(session, context, filters, page_items, family.inputs.clinvar_coga)

    return NiptVariantsResult(
        fetal_fraction=ff_estimate,
        total=total,
        variants=listed,
        trio=trio,
        total_is_estimated=capped,
        count_limit=fetch_limit if capped else None,
        recessive_genes=page_genes,
        gene_total=gene_total,
        variant_total=variant_total,
        not_classified=not_classified,
        clinvar_coga_release=family.inputs.clinvar_coga.release,
        clinvar_annotation_release=await analysis_clinvar_release(session, context),
    )


async def _not_cohort_common(
    items: list[NiptClassifiedVariant], scope: _RecurrenceScope
) -> list[NiptClassifiedVariant]:
    """The ``items`` less those the locked criteria read as an unannotated common allele
    (``nipt_diagnostic_filters.is_cohort_common``: no population frequency, called in
    ``cohort_common_min_samples`` cfDNA samples of the assay or more, this one included: a
    paralogue, a repeat-length allele, an MNV gnomAD lists as its SNVs), unless ClinVar calls
    them pathogenic (``clinvar_override``), as the report's recessive pairing reads them."""
    criteria = NiptDiagnosticCriteria()
    unannotated = [
        item for item in items if triage_annotation(item.record, item.clinvar_coga).max_population_af is None
    ]
    await _attach_recurrence(unannotated, scope)
    common = {
        item.record.variant_id
        for item in unannotated
        if is_cohort_common(annotation := diagnostic_annotation(item), criteria)
        and not clinvar_override(annotation, criteria)
    }
    return [item for item in items if item.record.variant_id not in common]


async def _shown(
    items: list[NiptClassifiedVariant],
    wanted: set[int],
    max_unexplained_recurrence: int | None,
    scope: _RecurrenceScope,
) -> list[NiptClassifiedVariant]:
    """The ``items`` in one of the ``wanted`` categories (every item without), less those the
    recurrence filter leaves out (``hidden_as_recurrent``)."""
    shown = [item for item in items if item.classification.category in wanted] if wanted else items
    if max_unexplained_recurrence is None:
        return shown
    await _attach_recurrence(shown, scope)
    return [item for item in shown if not hidden_as_recurrent(item, max_unexplained_recurrence)]


def _page_of[T](items: list[T], page: int, page_size: int | None) -> list[T]:
    """``items`` on ``page`` of ``page_size`` (every item without a page size)."""
    if page_size is None:
        return items
    offset = max(0, (page - 1) * page_size)
    return items[offset : offset + page_size]


# --------------------------------------------------------------------------- #
# The locked diagnostic candidates (REQ-NIPT-020)
# --------------------------------------------------------------------------- #

# The views of the diagnostic candidates, in the report's order.
DIAGNOSTIC_VIEWS = ("de_novo", "paternal", "maternal", "recessive")
REPEAT_CONTEXT_FLAG = "repeat_context"
CLINVAR_PATHOGENIC_FLAG = "clinvar_pathogenic"


@dataclass(slots=True)
class NiptDiagnosticCandidate:
    """A variant the locked criteria leave for further investigation in a view: its
    classification, what the criteria read of it, its confidence (``low`` for a de novo indel
    in a repeat) and its flags (``repeat_context``, ``clinvar_pathogenic``)."""

    item: NiptClassifiedVariant
    annotation: DiagnosticAnnotation
    confidence: str = "standard"
    flags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class NiptDiagnosticCandidates:
    """The diagnostic candidates of each view, by the locked criteria: de novo, paternal and
    maternal alleles the fetus inherited, and the recessive genes with their qualifying alleles.
    ``capped``: the scope held more variants than the analysis classifies at once."""

    criteria: NiptDiagnosticCriteria
    de_novo: list[NiptDiagnosticCandidate]
    paternal: list[NiptDiagnosticCandidate]
    maternal: list[NiptDiagnosticCandidate]
    recessive_variants: list[NiptDiagnosticCandidate]
    recessive_genes: list[RecessiveGeneRisk]
    capped: bool = False

    def all_items(self) -> list[NiptClassifiedVariant]:
        seen: dict[str, NiptClassifiedVariant] = {}
        for candidate in [*self.de_novo, *self.paternal, *self.maternal, *self.recessive_variants]:
            seen.setdefault(candidate.item.record.variant_id, candidate.item)
        return list(seen.values())


def diagnostic_annotation(item: NiptClassifiedVariant) -> DiagnosticAnnotation:
    """What the locked criteria read of a classified variant: its annotation
    (``triage_annotation``), whether ClinVar strictly calls it pathogenic (read by the one rule,
    ``clinvar_read``), and in how many other cfDNA samples of the assay it is called (its
    recurrence, counted first)."""
    base = triage_annotation(item.record, item.clinvar_coga)
    return DiagnosticAnnotation(
        max_population_af=base.max_population_af,
        impact=base.impact,
        spliceai_max=base.spliceai_max,
        clinvar_pathogenic=clinvar_strictly_pathogenic(clinvar_read(item.record, item.clinvar_coga)),
        other_cfdna_carriers=item.recurrence.counts.carriers if item.recurrence is not None else 0,
    )


async def diagnostic_candidates(
    classified: list[NiptClassifiedVariant],
    ff_estimate: FetalFractionEstimate,
    qc: NiptQualityThresholds,
    *,
    scope: _RecurrenceScope,
    criteria: NiptDiagnosticCriteria | None = None,
) -> NiptDiagnosticCandidates:
    """The locked diagnostic candidates among ``classified`` (``nipt_diagnostic_filters``):

    * de novo: a candidate in the family's fetal window (at most the maternal boundary,
      0.25) that no other cfDNA sample of the assay carries (unless ClinVar calls it
      pathogenic), rare (0.1 %) and protein-altering; a repeat-context indel is low confidence;
    * paternal: an allele the fetus inherited (probability 0.5 or more), rare (0.1 %) and
      protein-altering;
    * maternal: every allele of the mother's, rare (0.1 %) and protein-altering, with the
      probability that the fetus inherited it, as the R validation's candidate table lists
      them (the owner's decision of 2026-11-25: the per-site maternal inference is the least
      certain, so it does not narrow the list);
    * recessive: the genes in which both parents carry a qualifying allele (rare at 1 %, or
      ClinVar pathogenic up to 5 %, and protein-altering), with the fetal status.

    The paternal and maternal lists, and the mother's alleles of the recessive pairing, hold
    only calls past the R validation's technical filter (``_technically_retained``).
    """
    criteria = criteria or NiptDiagnosticCriteria()
    await _attach_recurrence(classified, scope)
    annotations = {item.record.variant_id: diagnostic_annotation(item) for item in classified}

    def flags_of(item: NiptClassifiedVariant, annotation: DiagnosticAnnotation) -> list[str]:
        return [CLINVAR_PATHOGENIC_FLAG] if clinvar_override(annotation, criteria) else []

    de_novo: list[NiptDiagnosticCandidate] = []
    for item in await _triage_de_novo_candidates(list(classified), ff_estimate, scope=scope):
        site = item.site
        annotation = annotations[item.record.variant_id]
        if item.de_novo is None or item.de_novo.window not in _FETAL_WINDOWS or site is None:
            continue
        if site.cf_vaf is not None and site.cf_vaf > MATERNAL_ALLELE_VAF_BOUNDARY:
            continue
        if annotation.other_cfdna_carriers > 0 and not clinvar_override(annotation, criteria):
            continue
        if dominant_failures(annotation, criteria):
            continue
        flags = flags_of(item, annotation)
        if is_repeat_context_indel(item.record.ref, item.record.alt, repeat_units(site), criteria):
            flags.insert(0, REPEAT_CONTEXT_FLAG)
        if PATERNAL_X_IN_SON_FLAG in item.classification.flags:
            # A son never inherits his father's X: the father's call beside it makes the
            # de novo allele doubtful (the R validation's low-confidence rule).
            flags.insert(0, PATERNAL_X_IN_SON_FLAG)
        low = REPEAT_CONTEXT_FLAG in flags or PATERNAL_X_IN_SON_FLAG in flags
        de_novo.append(
            NiptDiagnosticCandidate(
                item=item,
                annotation=annotation,
                confidence="low" if low else "standard",
                flags=flags,
            )
        )

    def dominant(items: list[NiptClassifiedVariant]) -> list[NiptDiagnosticCandidate]:
        kept = []
        for item in items:
            annotation = annotations[item.record.variant_id]
            if not dominant_failures(annotation, criteria):
                kept.append(NiptDiagnosticCandidate(item=item, annotation=annotation, flags=flags_of(item, annotation)))
        return kept

    retained = [item for item in classified if _technically_retained(item)]
    paternal = dominant(_paternal_view(retained, qc, include_not_inherited=False))
    maternal = dominant(_maternal_view(retained, include_not_inherited=True))
    qualifying = [item for item in classified if not recessive_failures(annotations[item.record.variant_id], criteria)]
    recessive_genes = _recessive_view(qualifying, qc, technical=True)
    carried = {item.record.variant_id for gene in recessive_genes for item in gene.variants}
    carriers = [item for item in qualifying if item.record.variant_id in carried]
    genes = [gene.risk for gene in recessive_genes]
    recessive = [
        NiptDiagnosticCandidate(
            item=item,
            annotation=annotations[item.record.variant_id],
            flags=flags_of(item, annotations[item.record.variant_id]),
        )
        for item in carriers
    ]
    return NiptDiagnosticCandidates(
        criteria=criteria,
        de_novo=de_novo,
        paternal=paternal,
        maternal=maternal,
        recessive_variants=recessive,
        recessive_genes=genes,
    )


async def _scoped_diagnostic_candidates(
    session: AsyncSession,
    context: FamilyMetadataContext,
    trio: NiptTrio,
    assay_key: str,
    qc: NiptQualityThresholds,
    *,
    family: _FamilyAnalysis,
    panel_id: str | None,
    gene: str | None,
) -> tuple[NiptDiagnosticCandidates, SmallVariantQueryFilters]:
    """The diagnostic candidates of the family's variants in a gene panel and/or gene (every
    variant without either), classified as the variant list classifies them
    (``_FamilyAnalysis.classify``). The NIPT report's list and the signed record's both come
    from here."""
    ff_estimate = family.summary.analysis.fetal_fraction
    filters = _build_nipt_query_filters({"panel_id": panel_id or None, "gene": gene or None})
    panel_constraints = PanelFilterConstraints()
    if panel_id:
        panel_constraints = await _fetch_panel_constraints(session, panel_id, assembly_id=context.assembly_id)
        if not panel_constraints.genes and not panel_constraints.regions:
            empty = await diagnostic_candidates([], ff_estimate, qc, scope=await _recurrence_scope(session, context, assay_key, qc))
            return empty, filters
    records = await _fetch_small_variant_rows(
        context, filters, panel_constraints=panel_constraints, limit=NIPT_VARIANT_FETCH_LIMIT + 1
    )
    capped = len(records) > NIPT_VARIANT_FETCH_LIMIT
    records = records[:NIPT_VARIANT_FETCH_LIMIT]
    classified = await family.classify(records, trio, qc)
    scope = await _recurrence_scope(session, context, assay_key, qc)
    candidates = await diagnostic_candidates(classified, ff_estimate, qc, scope=scope)
    candidates.capped = capped
    return candidates, filters


@dataclass(slots=True)
class NiptDiagnosticCandidatesResult:
    """The diagnostic candidates of a NIPT report's scope, with the trio and the fetal fraction
    they were read against."""

    candidates: NiptDiagnosticCandidates
    trio: NiptTrio
    fetal_fraction: FetalFractionEstimate


async def get_family_nipt_diagnostic_candidates(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    project_id: str | None = None,
    analysis: str | None = None,
    panel_id: str | None = None,
    gene: str | None = None,
    qc: NiptQualityThresholds | None = None,
) -> NiptDiagnosticCandidatesResult:
    """The locked diagnostic candidates of the NIPT report's scope (REQ-NIPT-020), each
    serialised as the variant list serialises a variant. A panel is recorded on the family's
    clinical audit trail before it is applied, as every search records it."""
    qc = qc or NiptQualityThresholds()
    trio, context, assay_key = await _resolve_trio_and_context(
        session, family_id=family_id, user=user, project_id=project_id, analysis=analysis
    )
    if panel_id:
        await record_family_panel_use(
            session, context=context, panel_id=panel_id, user=user, search="nipt_diagnostic_candidates"
        )
    family = await _kept_family_analysis(session, context, trio, assay_key, qc)
    candidates, filters = await _scoped_diagnostic_candidates(
        session,
        context,
        trio,
        assay_key,
        qc,
        family=family,
        panel_id=panel_id,
        gene=gene,
    )
    await _serve_variant_outs(session, context, filters, candidates.all_items(), family.inputs.clinvar_coga)
    return NiptDiagnosticCandidatesResult(
        candidates=candidates, trio=trio, fetal_fraction=family.summary.analysis.fetal_fraction
    )


@dataclass(slots=True)
class NiptFamilyRecord:
    """What the signed analysis record freezes of a family's NIPT analysis
    (``signed_record_nipt``): the summary the NIPT page shows, the artifact list it ran
    with, and each reported variant as the NIPT variant list classifies it."""

    summary: NiptFamilySummary
    # The assay's artifact list (each allele id with its recurrence on the list), what it
    # removed from the family's analysis, and what stayed in (variant id -> reason: the
    # family's own annotation, or its father carrying the allele).
    listed_artifacts: dict[str, int]
    removed_artifacts: set[str]
    protected_artifacts: dict[str, str]
    # Each reported variant the analysis classifies, with its de novo triage when it is a
    # candidate in the fetal window (the ones the de novo view leaves out as recurrent too).
    variants: list[NiptClassifiedVariant]
    # Each reported variant it does not classify, and why: not_in_data, artifact_list or
    # no_plasma_call.
    unclassified: dict[str, str]
    # The recessive fetal risk of the genes the reported variants make both parents carriers
    # in, as the recessive view computes it over them.
    recessive_genes: list[RecessiveGeneRisk]
    # The plasma's per-target coverage table ([] without one): the record reads the coverage
    # of the diagnostic panel's targets from it.
    plasma_targets: list[TargetCoverageRow] = field(default_factory=list)
    # The locked diagnostic candidates of the analysis's diagnostic panel (every variant
    # without one), as the NIPT report lists them (REQ-NIPT-020).
    diagnostic_candidates: NiptDiagnosticCandidates | None = None


# Why a reported variant, or one the variant list's review-tag filter selects, has no NIPT
# classification.
NOT_IN_DATA = "not_in_data"
ON_ARTIFACT_LIST = "artifact_list"
NO_PLASMA_CALL = "no_plasma_call"
# The de novo triage's positions inside the family's fetal window.
_FETAL_WINDOWS = frozenset({"strict", "loose"})


async def _classify_selected(
    context: FamilyMetadataContext,
    wanted: set[str],
    family: _FamilyAnalysis,
    trio: NiptTrio,
    qc: NiptQualityThresholds,
) -> tuple[list[NiptClassifiedVariant], dict[str, str]]:
    """``wanted`` among the family's variants, with their annotation, each classified as the
    variant list classifies it (no filter, no minimum confidence), and
    why the analysis classifies none of the others (variant id -> reason): not in the family's
    data, removed by the artifact list, or without a plasma call. The signed record's
    reported variants and the variant list's review-tag selection both come from here."""
    read = await _fetch_small_variant_rows(
        context, SmallVariantQueryFilters(page=1, page_size=100), include_variant_ids=sorted(wanted), limit=None
    )
    # By their exact ids: the query reads an id without its surrounding spaces.
    records = [record for record in read if record.variant_id in wanted]
    classified = await family.classify(records, trio, qc)
    found = {record.variant_id for record in records}
    classified_ids = {item.record.variant_id for item in classified}
    unclassified: dict[str, str] = {}
    for variant_id in sorted(wanted - classified_ids):
        if variant_id not in found:
            unclassified[variant_id] = NOT_IN_DATA
        elif variant_id in family.removed:
            unclassified[variant_id] = ON_ARTIFACT_LIST
        else:
            unclassified[variant_id] = NO_PLASMA_CALL
    return classified, unclassified


async def get_family_nipt_record(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    trio: NiptTrio,
    assay_key: str,
    variant_ids: Iterable[str],
    qc: NiptQualityThresholds | None = None,
    shared_nipt: SharedNiptAnalysis | None = None,
    diagnostic_panel_id: str | None = None,
) -> NiptFamilyRecord:
    """The family's NIPT analysis as the NIPT page computes it, for the signed record: the
    summary (``get_family_nipt_summary``) and each of ``variant_ids`` classified as the
    NIPT variant list classifies it (``get_family_nipt_variants``), against the one fetal
    fraction, with its de novo triage when it is a candidate in the fetal window, and the
    recessive fetal risk of the genes they make both parents carriers in. One load of the
    family's data serves every part: ``shared_nipt``'s, when the sign-out's Sample QC has
    read it."""
    qc = qc or NiptQualityThresholds()
    family = await _family_analysis(session, context, trio, assay_key, qc, shared_nipt)
    summary = family.summary
    wanted = {str(variant_id) for variant_id in variant_ids if variant_id}
    classified, unclassified = await _classify_selected(context, wanted, family, trio, qc)
    # Each reported variant's recurrence, which the record freezes as the list showed it, in
    # one count that the de novo triage then reads.
    scope = await _recurrence_scope(session, context, assay_key, qc)
    await _attach_recurrence(classified, scope)
    candidates = [
        item
        for item in classified
        if item.site is not None and is_de_novo_candidate(item.site, item.classification)
    ]
    if candidates:
        triaged = await _triage_de_novo_candidates(
            candidates, summary.analysis.fetal_fraction, scope=scope
        )
        for item in triaged:
            # A de novo candidate is one in the fetal window (nipt_triage): a maternal
            # allele above it, or a call below it, is none, and the page triages neither.
            if item.de_novo is not None and item.de_novo.window not in _FETAL_WINDOWS:
                item.de_novo = None
    recessive_genes = [gene.risk for gene in _recessive_view(classified, qc)]
    diagnostic, _filters = await _scoped_diagnostic_candidates(
        session,
        context,
        trio,
        assay_key,
        qc,
        family=family,
        panel_id=diagnostic_panel_id,
        gene=None,
    )
    return NiptFamilyRecord(
        summary=summary,
        listed_artifacts=family.inputs.listed,
        removed_artifacts=family.removed,
        protected_artifacts=family.protected,
        variants=classified,
        unclassified=unclassified,
        recessive_genes=recessive_genes,
        plasma_targets=list(family.inputs.plasma_targets),
        diagnostic_candidates=diagnostic,
    )


async def _fetch_labeled_gene_regions(
    session: AsyncSession, *, terms: list[str], assembly_id: str | None
) -> list[TargetRegion]:
    """Resolve gene symbols/ids to regions labelled by the gene symbol."""
    cleaned = [str(term).strip() for term in terms if str(term).strip()]
    if not cleaned:
        return []
    # One text[] parameter, not one per term (an expanding IN): asyncpg takes at most 32,767
    # per statement, and a gene list names any number of genes.
    clauses = ["(upper(hgnc_symbol) = ANY(CAST(:terms AS text[])) OR upper(gene_id) = ANY(CAST(:terms AS text[])))"]
    params: dict = {"terms": [term.upper() for term in cleaned]}
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
        ),
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
        panel_genes, panel_regions = await _panel_genes_and_regions(session, context, panel_id)
        gene_terms.extend(panel_genes)
        regions.extend(panel_regions)
    if gene_terms:
        regions.extend(
            await _fetch_labeled_gene_regions(
                session, terms=gene_terms, assembly_id=context.assembly_id
            )
        )
    if not regions and family.roi is not None:
        roi = family.roi
        regions.append(
            roi_target_region(query=roi.query, label=roi.label, chrom=roi.chr, start=roi.start, end=roi.end)
        )
    return regions


async def _panel_genes_and_regions(
    session: AsyncSession, context: FamilyMetadataContext, panel_id: str
) -> tuple[list[str], list[TargetRegion]]:
    """A panel's genes, and its explicit regions on the family's assembly labelled by their
    coordinates."""
    constraints = await _fetch_panel_constraints(session, panel_id, assembly_id=context.assembly_id)
    regions = []
    for region in constraints.regions:
        chrom = normalize_chromosome(region.chr)
        regions.append(
            TargetRegion(label=f"{chrom}:{region.start}-{region.end}", chrom=chrom, start=region.start, end=region.end)
        )
    return list(constraints.genes), regions


async def nipt_panel_target_regions(
    session: AsyncSession, context: FamilyMetadataContext, panel_id: str
) -> list[TargetRegion]:
    """The coverage check's target regions of a gene panel: its genes' loci, labelled by their
    symbols, and its explicit regions (as ``get_family_nipt_coverage`` reads a panel)."""
    genes, regions = await _panel_genes_and_regions(session, context, panel_id)
    if genes:
        regions.extend(await _fetch_labeled_gene_regions(session, terms=genes, assembly_id=context.assembly_id))
    return regions


def roi_target_region(*, query: str, label: str | None, chrom: str, start: int, end: int) -> TargetRegion:
    """The family's region of interest as the coverage check's target region when no gene or
    panel is given, labelled as the NIPT page labels it: by its label, else its query."""
    return TargetRegion(label=label or query, chrom=normalize_chromosome(chrom), start=start, end=end)


async def _coverage_tracks_over(
    session: AsyncSession,
    context: FamilyMetadataContext,
    sample_id: str,
    target_regions: list[TargetRegion],
) -> tuple[SampleCoverageTracks | None, list[str]]:
    """The sample's coverage tracks on the target regions' chromosomes, and a note for each of
    them that is not read as depth, with the reason (``not_read_as_depth_note``); (None, [])
    without a region or a track."""
    sample_uuid = context.sample_name_to_uuid.get(sample_id)
    if not sample_uuid or not context.assembly_name or not target_regions:
        return None, []
    tracks = (
        await coverage_tracks_by_sample(
            session,
            assembly_name=context.assembly_name,
            family_uuid=context.family_uuid,
            sample_uuid_to_name={sample_uuid: sample_id},
            chromosomes=sorted({region.chrom for region in target_regions}),
        )
    ).get(sample_id)
    if tracks is None:
        return None, []
    notes = [
        not_read_as_depth_note(sample_id, track_source, content)
        for track_source, content in tracks.not_read_as_depth()
    ]
    return tracks, notes


async def nipt_coverage_notes(
    session: AsyncSession,
    context: FamilyMetadataContext,
    sample_id: str,
    target_regions: list[TargetRegion],
) -> list[str]:
    """The notes of the coverage check over ``sample_id``'s coverage tracks for
    ``target_regions``, word for word as the NIPT page shows them (``get_family_nipt_coverage``,
    from the same code): the signed record freezes them. The check's coverage itself
    is not read."""
    _tracks, notes = await _coverage_tracks_over(session, context, sample_id, target_regions)
    return notes


async def _load_coverage_intervals(
    session: AsyncSession,
    context: FamilyMetadataContext,
    sample_id: str,
    target_regions: list[TargetRegion],
) -> tuple[list[CoverageInterval], list[str]]:
    """The sample's coverage on the target regions' chromosomes, from its one coverage track
    whose values are read depth: a coverage BED declared as read depth, or a depth
    track stored as read (``coverage_read_depth``). WisecondorX's and QDNAseq's bins and
    HiFiCNV's normalised depth are log2 ratios: merged with the BED's depths they would pull
    its median down, and their rows over a region count its bases again, which can hide an
    uncovered stretch from the completeness check. A coverage BED that does not declare what
    it holds is of unknown kind, and is not read as depth either. Without a depth
    track, [], which reads as no coverage.

    With the intervals, a note for each of the sample's coverage tracks there that is not
    read as depth, with the reason (``not_read_as_depth_note``): the check shows them, so a
    region without coverage says why."""
    tracks, notes = await _coverage_tracks_over(session, context, sample_id, target_regions)
    source = tracks.depth_source if tracks is not None else None
    # Tracks are only read for a sample with a uuid, on an assembly (_coverage_tracks_over).
    sample_uuid = context.sample_name_to_uuid.get(sample_id)
    if source is None or not sample_uuid or not context.assembly_name:
        return [], notes
    rows = await fetch_interval_track_rows(
        context.assembly_name,
        sample_uuid=sample_uuid,
        family_uuid=context.family_uuid,
        track_type=COVERAGE_TRACK_TYPE,
        chromosomes=sorted({region.chrom for region in target_regions}),
        source=source,
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
    return intervals, notes


@dataclass(slots=True)
class NiptCoverageResult:
    """The coverage QC: the target table's when the plasma has one (``targets``), else the
    coverage track's over the gene or panel spans (``regions``), with a note for each of the
    plasma's coverage tracks not read as depth (``notes``)."""

    regions: NiptCoverageSummary
    targets: TargetCoverageSummary | None = None
    notes: list[str] = field(default_factory=list)


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
    thin_below: float | None = None,
    analysis: str | None = None,
) -> NiptCoverageResult:
    family, trio, context = await _resolve_family_trio_context(
        session, family_id=family_id, user=user, project_id=project_id, analysis=analysis
    )
    # A panel the coverage is read for goes on the family's clinical audit trail (REQ-CARR-003).
    await record_family_panel_use(
        session, context=context, panel_id=panel_id, user=user, search="nipt_coverage"
    )
    target_rows = await _load_target_rows(context, trio, trio.cfdna_sample_id, with_metadata=True)
    if target_rows:
        # The capture targets themselves: the panel's or the queried genes', else all.
        genes: list[str] = list(_split_gene_terms(gene)) if gene else []
        if panel_id:
            # By symbol, as the targets are named and the record reads the panel: an Ensembl
            # gene id is never listed as a gene without a target beside its own symbol.
            symbols = await panel_gene_symbols(session, require_uuid(panel_id, "Invalid panel id"))
            genes.extend(symbols or ())
        return NiptCoverageResult(
            regions=NiptCoverageSummary(overall_median_on_target=None, target_region_count=0, min_depth=min_depth),
            targets=summarize_target_coverage(
                target_rows, genes=genes or None, include_passing=all_genes, fetal_read_depth=thin_below
            ),
        )
    target_regions = await _resolve_nipt_target_regions(
        session, family=family, context=context, gene=gene, panel_id=panel_id
    )
    coverage, notes = await _load_coverage_intervals(
        session, context, trio.cfdna_sample_id, target_regions
    )
    return NiptCoverageResult(
        regions=summarize_on_target_coverage(target_regions, coverage, min_depth=min_depth),
        notes=notes,
    )


@dataclass(slots=True)
class NiptGeneTargets:
    """Every capture target of one gene in the plasma's target table, in genomic order."""

    gene: str
    critical_mean_depth: float
    rows: list[TargetCoverageRow]


def nipt_analysis_key(trio: NiptTrio) -> str:
    """The key the trio's analysis is stored and audited under: its pregnancy's key, or the
    analysis type for a family that names no pregnancy (one made in the Family Builder)."""
    return trio.analysis or MONOGENIC_NIPT_ANALYSIS_TYPE


async def get_family_nipt_diagnostic_panel(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    project_id: str | None = None,
    analysis: str | None = None,
) -> tuple[str, AnalysisGenePanel | None]:
    """The monogenic NIPT's analysis key and its diagnostic gene panel (REQ-NIPT-019), None when
    it has none."""
    trio, context, _assay_key = await _resolve_trio_and_context(
        session, family_id=family_id, user=user, project_id=project_id, analysis=analysis
    )
    key = nipt_analysis_key(trio)
    return key, await load_analysis_gene_panel(session, family_uuid=context.family_uuid, analysis=key)


async def set_family_nipt_diagnostic_panel(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    panel_id: str | None,
    project_id: str | None = None,
    analysis: str | None = None,
) -> tuple[str, AnalysisGenePanel | None]:
    """Set or clear the monogenic NIPT's diagnostic gene panel, on the family's clinical audit
    trail (``analysis_gene_panel.set_analysis_gene_panel``). A reader of the family is refused
    by the family's checkpoint, as every change is."""
    trio, context, _assay_key = await _resolve_trio_and_context(
        session, family_id=family_id, user=user, project_id=project_id, analysis=analysis
    )
    key = nipt_analysis_key(trio)
    panel = await set_analysis_gene_panel(
        session,
        family_uuid=context.family_uuid,
        family_identifier=context.family_id,
        analysis=key,
        analysis_label=analysis_label(key) or "monogenic NIPT",
        panel_id=panel_id,
        user=user,
    )
    return key, panel


async def get_family_nipt_gene_targets(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    gene: str,
    project_id: str | None = None,
    analysis: str | None = None,
) -> NiptGeneTargets:
    """One gene's targets with their depth, for the coverage page; none without a target
    table, or for a gene the panel does not capture."""
    _family, trio, context = await _resolve_family_trio_context(
        session, family_id=family_id, user=user, project_id=project_id, analysis=analysis
    )
    # This gene's rows alone: a panel's table holds tens of thousands of targets.
    target_rows = await _load_target_rows(
        context, trio, trio.cfdna_sample_id, with_metadata=True, genes=[gene.strip()]
    )
    rows = sorted(target_rows, key=lambda row: (row.chrom, row.start, row.end))
    return NiptGeneTargets(gene=gene.strip(), critical_mean_depth=CRITICAL_MEAN_DEPTH, rows=rows)
