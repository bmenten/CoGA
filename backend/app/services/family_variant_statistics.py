"""The variant summary's statistics of a family's small variants (PROD-1).

Descriptive counts of what the family's callsets hold, for the *Variant summary* page; no
clinical rule reads them. They cover the family's nuclear small variants from its directly
called callsets:

* chrM is left out: its calls are heteroplasmy levels, summarised by the mtDNA analysis.
* Imputed callsets (glimpse2, shapeit) are left out, as the family's small-variant counts
  leave them out (``refresh_family_small_variant_summaries``); the response names those the
  family holds.

What is counted:

* Per sample, each variant once by its key, whatever callsets hold it: its genotype class
  (the one classification of ``genotypes.py``), its type (SNV, insertion, deletion, other),
  the SNVs' transitions and transversions, whether a call passed its caller's FILTER, its
  most severe consequence across all its transcripts (VEP's order), and its chromosome.
* Per sample, histograms of the calls' genotype quality, depth and (heterozygous calls)
  allele balance: one value per call, so a variant two callsets hold counts twice.
* A monogenic NIPT family's plasma and father are named (``nipt_samples``): their GT is not
  their genotype (the plasma is a mixture; Mutect2 in tumour-only mode writes 0/1 whatever
  the allele fraction), so their het and hom-alt counts say nothing. The NIPT-M statistics
  class their sites by allele fraction (``nipt_variant_statistics``).
* Per child with both parents in the family, per callset and autosome, the sites where all
  three have a biallelic diploid call, and those whose genotypes no transmission explains:
  the child lacks the father's allele, the mother's, or neither parent explains it. A
  chromosome whose inconsistencies lack one parent's allele can point to uniparental disomy
  or a deletion; the counts are not a UPD call.

Every statement reads the family's rows only (``family_guid``, ``sign = 1``, its projects),
with bound parameters.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Mapping, Sequence

from ..schemas import (
    FamilySmallVariantStatisticsOut,
    HistogramBinOut,
    MendelianChromosomeOut,
    MendelianTrioOut,
    SmallVariantChromosomeCountsOut,
    SmallVariantConsequenceCountOut,
    SmallVariantSampleStatisticsOut,
)
from .clickhouse_family_variants import _execute_clickhouse
from .clickhouse_variant_ids import _small_table_name
from .clickhouse_variant_queries import (
    IMPUTED_SMALL_VARIANT_SOURCES,
    _PASSING_FILTER_VALUES,
    _display_sample_name,
    _visible_clickhouse_sample_ids,
)
from .data_scope import chromosome_aliases, normalize_chromosome
from .family_metadata_context import FamilyMetadataContext
from .genotypes import ALT_CLASSES, HET, HOM_ALT, clickhouse_genotype_condition
from .haplotype_lineage_service import build_pedigree

# VEP's consequence terms, most severe first (Ensembl's order), in the lower case the
# annotation tables store them in (``clickhouse_variant_records._annotation_terms``).
CONSEQUENCE_ORDER: tuple[str, ...] = (
    "transcript_ablation",
    "splice_acceptor_variant",
    "splice_donor_variant",
    "stop_gained",
    "frameshift_variant",
    "stop_lost",
    "start_lost",
    "transcript_amplification",
    "feature_elongation",
    "feature_truncation",
    "inframe_insertion",
    "inframe_deletion",
    "missense_variant",
    "protein_altering_variant",
    "splice_donor_5th_base_variant",
    "splice_region_variant",
    "splice_donor_region_variant",
    "splice_polypyrimidine_tract_variant",
    "extended_intronic_splice_region_variant",
    "incomplete_terminal_codon_variant",
    "start_retained_variant",
    "stop_retained_variant",
    "synonymous_variant",
    "coding_sequence_variant",
    "mature_mirna_variant",
    "5_prime_utr_variant",
    "3_prime_utr_variant",
    "non_coding_transcript_exon_variant",
    "intron_variant",
    "nmd_transcript_variant",
    "non_coding_transcript_variant",
    "coding_transcript_variant",
    "upstream_gene_variant",
    "downstream_gene_variant",
    "tfbs_ablation",
    "tfbs_amplification",
    "tf_binding_site_variant",
    "regulatory_region_ablation",
    "regulatory_region_amplification",
    "regulatory_region_variant",
    "intergenic_variant",
    "sequence_variant",
)
# The ranks the consequence query gives what is not a term of CONSEQUENCE_ORDER (whose
# terms rank 1 to its length): a term outside it, and no term. A variant written without
# annotations has an index row without terms; a row without an index row (the 0 a LEFT JOIN
# leaves) is read the same, ranked last, so a variant whose other callset's row names a
# term reads as that term.
_OTHER_TERM_RANK = len(CONSEQUENCE_ORDER) + 1
_NO_TERM_RANK = len(CONSEQUENCE_ORDER) + 2
OTHER_CONSEQUENCE = "other"
NO_CONSEQUENCE = "none"

# The histograms' bins, by their lower edges; a value above the last edge falls in the last
# bin. Genotype quality tops out at 99 for most callers: bins of 5. Depth is binned by 2 up to
# 60, where a whole genome's calls mostly fall, by 5 up to 100, then coarser.
GENOTYPE_QUALITY_EDGES: tuple[float, ...] = tuple(float(step * 5) for step in range(20))
DEPTH_EDGES: tuple[float, ...] = (
    *(float(edge) for edge in range(0, 60, 2)),
    *(float(edge) for edge in range(60, 100, 5)),
    100.0,
    125.0,
    150.0,
    200.0,
)
# Allele balance in hundredths of the reads (the query rounds the fraction to them), binned
# by five hundredths; the bins' edges are given back as fractions.
ALLELE_BALANCE_EDGES: tuple[float, ...] = tuple(float(step * 5) for step in range(20))
_ALLELE_BALANCE_SCALE = 100.0
# The cap the query puts on a depth before grouping (the last bin's edge), so the groups
# stay few: every depth from it up is the last bin's anyway.
_DEPTH_CAP = int(DEPTH_EDGES[-1])
_GENOTYPE_QUALITY_CAP = int(GENOTYPE_QUALITY_EDGES[-1])

_AUTOSOMES: tuple[str, ...] = tuple(str(number) for number in range(1, 23))
_MITOCHONDRIAL_ALIASES: tuple[str, ...] = tuple(chromosome_aliases("MT"))
_CHROMOSOME_ORDER: dict[str, int] = {
    name: index for index, name in enumerate([*_AUTOSOMES, "X", "Y"])
}

# Biallelic diploid genotypes, as the Mendelian query reads them: the ALT allele's dosage.
_DOSAGE_GENOTYPES: dict[int, tuple[str, ...]] = {
    0: ("0/0", "0|0"),
    1: ("0/1", "1/0", "0|1", "1|0"),
    2: ("1/1", "1|1"),
}


def consequence_for_rank(rank: int) -> str:
    """The consequence a rank of the consequence query stands for."""
    if 1 <= rank <= len(CONSEQUENCE_ORDER):
        return CONSEQUENCE_ORDER[rank - 1]
    if rank == _OTHER_TERM_RANK:
        return OTHER_CONSEQUENCE
    return NO_CONSEQUENCE


def histogram_bins(
    values: Mapping[float, int], edges: Sequence[float], *, scale: float = 1.0
) -> tuple[list[HistogramBinOut], int]:
    """``values`` (a value and how many calls have it; a negative value is a call without
    one) binned by ``edges``, and the count of calls without a value. Each bin's ``low`` is
    its edge divided by ``scale``."""
    counts = [0] * len(edges)
    missing = 0
    for value, count in values.items():
        if value < 0:
            missing += int(count)
            continue
        index = 0
        for position, edge in enumerate(edges):
            if value >= edge:
                index = position
            else:
                break
        counts[index] += int(count)
    return [HistogramBinOut(low=float(edge) / scale, count=count) for edge, count in zip(edges, counts)], missing


def mendelian_class(child: int, father: int, mother: int) -> str | None:
    """What the trio's ALT dosages (0, 1 or 2) say: None when a transmission explains the
    child's genotype, else ``not_from_father``, ``not_from_mother`` or ``from_neither``.
    The SQL of ``_mendelian_query`` is this rule; a test holds them to each other."""
    opposite_father = (father == 0 and child == 2) or (father == 2 and child == 0)
    opposite_mother = (mother == 0 and child == 2) or (mother == 2 and child == 0)
    neither_het = child == 1 and ((father == 0 and mother == 0) or (father == 2 and mother == 2))
    if (opposite_father and opposite_mother) or neither_het:
        return "from_neither"
    if opposite_father:
        return "not_from_father"
    if opposite_mother:
        return "not_from_mother"
    return None


def _family_where(context: FamilyMetadataContext, params: dict[str, Any], *, alias: str = "") -> list[str]:
    """The family's rows of its directly called nuclear callsets."""
    prefix = f"{alias}." if alias else ""
    params["family_guid"] = context.family_uuid
    params["imputed_sources"] = tuple(IMPUTED_SMALL_VARIANT_SOURCES)
    params["mitochondrial_chromosomes"] = _MITOCHONDRIAL_ALIASES
    clauses = [
        f"{prefix}family_guid = %(family_guid)s",
        f"{prefix}sign = 1",
        f"lowerUTF8({prefix}source) NOT IN %(imputed_sources)s",
        f"{prefix}chrom NOT IN %(mitochondrial_chromosomes)s",
    ]
    if context.project_ids:
        params["project_ids"] = tuple(context.project_ids)
        clauses.append(f"{prefix}project_guid IN %(project_ids)s")
    return clauses


def _capped_value(column: str, cap: int) -> str:
    """``column`` capped at ``cap``, or -1 without a value. ``least`` ignores a NULL, so the
    NULL is tested first."""
    return f"if(isNull({column}), toInt64(-1), least(toInt64(assumeNotNull({column})), toInt64({cap})))"


async def _source_names(context: FamilyMetadataContext, entries: str) -> list[str]:
    params: dict[str, Any] = {"family_guid": context.family_uuid}
    clauses = ["family_guid = %(family_guid)s", "sign = 1"]
    if context.project_ids:
        params["project_ids"] = tuple(context.project_ids)
        clauses.append("project_guid IN %(project_ids)s")
    rows = await _execute_clickhouse(
        f"SELECT DISTINCT source FROM {entries} WHERE {' AND '.join(clauses)} ORDER BY source LIMIT 50",
        params,
    )
    return [str(row[0]) for row in rows if row and row[0]]


def _chromosome_sort_key(chrom: str) -> tuple[int, str]:
    return (_CHROMOSOME_ORDER.get(chrom, len(_CHROMOSOME_ORDER)), chrom)


# What the per-variant pass keeps of each member's calls of a variant, as bits OR-ed over the
# rows (callsets) that hold it: a call with the ALT allele, heterozygous, homozygous, and one
# that passed its caller's FILTER.
_ALT_BIT, _HET_BIT, _HOM_BIT, _PASS_BIT = 1, 2, 4, 8
# A variant's type: an SNV (a transition or a transversion), an insertion, a deletion, other.
_TRANSITION, _TRANSVERSION, _INSERTION, _DELETION, _OTHER_TYPE = 1, 2, 3, 4, 5


def _type_code_sql(prefix: str) -> str:
    snv = f"length({prefix}ref) = 1 AND length({prefix}alt) = 1"
    transition = (
        f"(upperUTF8({prefix}ref), upperUTF8({prefix}alt)) IN (('A', 'G'), ('G', 'A'), ('C', 'T'), ('T', 'C'))"
    )
    return (
        f"multiIf({snv}, if({transition}, {_TRANSITION}, {_TRANSVERSION}), "
        f"length({prefix}ref) = 1 AND length({prefix}alt) > 1, {_INSERTION}, "
        f"length({prefix}ref) > 1 AND length({prefix}alt) = 1, {_DELETION}, {_OTHER_TYPE})"
    )


def _members(context: FamilyMetadataContext) -> list[tuple[str, tuple[str, ...]]]:
    """The family's members, in its order, with each identifier ClickHouse may store for them."""
    return [
        (name, _stored_ids(context, name))
        for name in (str(row.get("sample_id") or "") for row in context.sample_rows)
        if name
    ]


class _VariantCounts:
    """What the per-variant pass counted: per member (and the family, any member) the variants
    by chromosome, most severe consequence and type, with the heterozygous, homozygous and
    passing ones among them."""

    def __init__(self) -> None:
        self.variants: dict[str, int] = defaultdict(int)
        self.het: dict[str, int] = defaultdict(int)
        self.hom: dict[str, int] = defaultdict(int)
        self.passing: dict[str, int] = defaultdict(int)
        self.types: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
        self.chromosomes: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
        self.consequences: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))


_FAMILY = ""


async def _variant_counts(
    context: FamilyMetadataContext, entries: str, annotation_index: str
) -> _VariantCounts:
    """One read of the family's rows, grouped by variant first: each variant once, whatever
    callsets hold it, with each member's calls OR-ed over its rows (``_ALT_BIT`` …) and its most
    severe consequence over every transcript of each row's own annotation (its key and its
    annotation set under any version, as the family search matches them: ``_OWN_ANNOTATION``;
    a row without one ranks last, so the variant reads as another callset's annotation). The groups
    are the family's variants, whatever its size, each holding a byte per member: a count per
    member (``uniqExact`` per member and figure) held a set of keys per member and figure."""
    members = _members(context)
    params: dict[str, Any] = {
        "visible_sample_ids": tuple(_visible_clickhouse_sample_ids(context)),
        "passing_filter_values": _PASSING_FILTER_VALUES,
        "consequence_terms": list(CONSEQUENCE_ORDER),
        "other_term_rank": _OTHER_TERM_RANK,
        "no_term_rank": _NO_TERM_RANK,
    }
    for index, (_name, ids) in enumerate(members):
        params[f"member_ids_{index}"] = ids
    carries_alt = clickhouse_genotype_condition("g", ALT_CLASSES, param="gt_alt", params=params)
    is_het = clickhouse_genotype_condition("g", {HET}, param="gt_het", params=params)
    is_hom = clickhouse_genotype_condition("g", {HOM_ALT}, param="gt_hom", params=params)
    where = _family_where(context, params, alias="e")
    # The annotation rows read are the family's keys only: the index is shared by every
    # family on the assembly (``_small_family_key_condition``).
    key_where = _family_where(context, params, alias="fk")
    positions = ", ".join(
        f"arrayFirstIndex(stored -> stored IN %(member_ids_{index})s, e.calls.sampleId)"
        for index in range(len(members))
    )
    # A call passed when its own FILTER does (a callset of one file per sample) or, when no
    # call of the row has one, the row's (``_small_pass_only_condition``'s reading).
    passing = (
        "if(own_filtered, arrayAll(value -> value IN %(passing_filter_values)s, own), "
        "arrayAll(value -> value IN %(passing_filter_values)s, e.filters))"
    )
    code = (
        f"toUInt8(if({carries_alt}, {_ALT_BIT} + if({is_het}, {_HET_BIT}, 0) + if({is_hom}, {_HOM_BIT}, 0) "
        f"+ if({passing}, {_PASS_BIT}, 0), 0))"
    )
    severity = (
        "if(empty(ai.effects), %(no_term_rank)s, arrayMin(arrayMap(term -> "
        "if(has(%(consequence_terms)s, term), indexOf(%(consequence_terms)s, term), %(other_term_rank)s), "
        "ai.effects)))"
    )
    with_family = "arrayPushBack(member_codes, arrayReduce('groupBitOr', member_codes))"
    rows = await _execute_clickhouse(
        f"""
        SELECT
            member,
            chrom,
            best_rank,
            type_code,
            count(),
            countIf(bitAnd(code, {_HET_BIT}) > 0),
            countIf(bitAnd(code, {_HOM_BIT}) > 0),
            countIf(bitAnd(code, {_PASS_BIT}) > 0)
        FROM (
            SELECT
                key,
                any(chrom) AS chrom,
                any(type_code) AS type_code,
                min(if(rank = 0, %(no_term_rank)s, rank)) AS best_rank,
                groupBitOrForEach(codes) AS member_codes
            FROM (
                SELECT
                    e.key AS key,
                    e.chrom AS chrom,
                    {_type_code_sql('e.')} AS type_code,
                    annotation.rank AS rank,
                    [{positions}] AS positions,
                    arrayMap(position -> arrayElement(e.calls.gt, position), positions) AS gts,
                    arrayMap(position -> arrayElement(e.calls.filters, position), positions) AS owns,
                    arrayExists(
                        (call_sample, call_filters) -> call_sample IN %(visible_sample_ids)s AND notEmpty(call_filters),
                        e.calls.sampleId, e.calls.filters
                    ) AS own_filtered,
                    arrayMap((g, own) -> {code}, gts, owns) AS codes
                FROM {entries} AS e
                LEFT JOIN (
                    SELECT ai.key AS key, ai.annotationSetHash AS annotationSetHash,
                           toUInt64(min({severity})) AS rank
                    FROM {annotation_index} AS ai
                    WHERE identity(ai.key) IN (
                        SELECT fk.key FROM {entries} AS fk WHERE {' AND '.join(key_where)}
                    )
                    GROUP BY ai.key, ai.annotationSetHash
                ) AS annotation
                  ON annotation.key = e.key
                 AND annotation.annotationSetHash = e.annotationSetHash
                WHERE {' AND '.join(where)}
            )
            GROUP BY key
        )
        ARRAY JOIN {with_family} AS code, arrayEnumerate({with_family}) AS member
        WHERE bitAnd(code, {_ALT_BIT}) > 0
        GROUP BY member, chrom, best_rank, type_code
        """,
        params,
    )
    names = [name for name, _ids in members] + [_FAMILY]
    counts = _VariantCounts()
    for member, chrom, rank, type_code, variants, het, hom, passed in rows:
        index = int(member or 0) - 1
        if not 0 <= index < len(names):
            continue
        name = names[index]
        counts.variants[name] += int(variants or 0)
        counts.het[name] += int(het or 0)
        counts.hom[name] += int(hom or 0)
        counts.passing[name] += int(passed or 0)
        counts.types[name][int(type_code or 0)] += int(variants or 0)
        chromosome = counts.chromosomes[name][normalize_chromosome(str(chrom or ""))]
        chromosome[0] += int(het or 0)
        chromosome[1] += int(hom or 0)
        counts.consequences[name][consequence_for_rank(int(rank or 0))] += int(variants or 0)
    return counts


async def _call_histograms(
    context: FamilyMetadataContext, entries: str
) -> dict[str, dict[str, dict[float, int]]]:
    """Each member's calls with the ALT allele by genotype quality, depth and (heterozygous
    calls) allele balance in hundredths: one value per call, a negative one for a call without."""
    params: dict[str, Any] = {"visible_sample_ids": tuple(_visible_clickhouse_sample_ids(context))}
    carries_alt = clickhouse_genotype_condition("gt", ALT_CLASSES, param="gt_alt", params=params)
    is_het = clickhouse_genotype_condition("gt", {HET}, param="gt_het", params=params)
    where = _family_where(context, params)
    where.extend(["sample_id IN %(visible_sample_ids)s", carries_alt])
    allele_balance = (
        "if(isNull(ab) OR isNaN(assumeNotNull(ab)), toInt64(-1), "
        "toInt64(round(least(greatest(assumeNotNull(ab), 0), 1) * 100)))"
    )
    rows = await _execute_clickhouse(
        f"""
        SELECT
            sample_id,
            sumMap([{_capped_value('gq', _GENOTYPE_QUALITY_CAP)}], [toUInt64(1)]),
            sumMap([{_capped_value('dp', _DEPTH_CAP)}], [toUInt64(1)]),
            sumMapIf([{allele_balance}], [toUInt64(1)], {is_het})
        FROM {entries}
        ARRAY JOIN
            `calls.sampleId` AS sample_id,
            `calls.gt` AS gt,
            `calls.gq` AS gq,
            `calls.dp` AS dp,
            `calls.ab` AS ab
        WHERE {' AND '.join(where)}
        GROUP BY sample_id
        """,
        params,
    )
    histograms: dict[str, dict[str, dict[float, int]]] = defaultdict(
        lambda: {"gq": defaultdict(int), "dp": defaultdict(int), "ab": defaultdict(int)}
    )
    for stored_sample, *maps in rows:
        sample = _display_sample_name(context, stored_sample)
        if not sample or sample not in context.sample_name_to_uuid:
            continue
        for metric, mapped in zip(("gq", "dp", "ab"), maps):
            keys, values = mapped if mapped else ([], [])
            for key, value in zip(keys, values):
                histograms[sample][metric][float(key)] += int(value or 0)
    return histograms


def _sample_statistics(
    name: str, counts: _VariantCounts, histograms: Mapping[str, Mapping[float, int]]
) -> SmallVariantSampleStatisticsOut:
    types = counts.types[name]
    gq_bins, gq_missing = histogram_bins(histograms.get("gq", {}), GENOTYPE_QUALITY_EDGES)
    dp_bins, dp_missing = histogram_bins(histograms.get("dp", {}), DEPTH_EDGES)
    ab_bins, ab_missing = histogram_bins(histograms.get("ab", {}), ALLELE_BALANCE_EDGES, scale=_ALLELE_BALANCE_SCALE)
    order = {term: index for index, term in enumerate([*CONSEQUENCE_ORDER, OTHER_CONSEQUENCE, NO_CONSEQUENCE])}
    return SmallVariantSampleStatisticsOut(
        sample_id=name,
        non_ref_count=counts.variants[name],
        het_count=counts.het[name],
        hom_alt_count=counts.hom[name],
        snv_count=types[_TRANSITION] + types[_TRANSVERSION],
        insertion_count=types[_INSERTION],
        deletion_count=types[_DELETION],
        other_count=types[_OTHER_TYPE],
        transition_count=types[_TRANSITION],
        transversion_count=types[_TRANSVERSION],
        passing_count=counts.passing[name],
        consequences=[
            SmallVariantConsequenceCountOut(consequence=term, count=count)
            for term, count in sorted(
                counts.consequences[name].items(), key=lambda item: order.get(item[0], len(order))
            )
        ],
        chromosomes=[
            SmallVariantChromosomeCountsOut(chrom=chrom, het_count=het, hom_alt_count=hom)
            for chrom, (het, hom) in sorted(
                counts.chromosomes[name].items(), key=lambda item: _chromosome_sort_key(item[0])
            )
        ],
        genotype_quality_histogram=gq_bins,
        genotype_quality_missing=gq_missing,
        depth_histogram=dp_bins,
        depth_missing=dp_missing,
        allele_balance_histogram=ab_bins,
        allele_balance_missing=ab_missing,
    )


def _dosage_sql(sample_ids_param: str) -> str:
    """The ALT dosage of the call of the sample whose stored IDs ``sample_ids_param`` binds
    (0, 1 or 2), or -1 when its call is not biallelic diploid or the row has none."""
    genotype = (
        f"arrayElement(e.calls.gt, arrayFirstIndex(stored -> stored IN %({sample_ids_param})s, e.calls.sampleId))"
    )
    return (
        f"multiIf({genotype} IN %(dosage_0)s, 0, {genotype} IN %(dosage_1)s, 1, "
        f"{genotype} IN %(dosage_2)s, 2, -1)"
    )


def _stored_ids(context: FamilyMetadataContext, sample: str) -> tuple[str, ...]:
    ids = [sample]
    uuid = context.sample_name_to_uuid.get(sample)
    if uuid and uuid not in ids:
        ids.append(uuid)
    return tuple(ids)


async def _mendelian_trio(
    context: FamilyMetadataContext, entries: str, *, child: str, father: str, mother: str
) -> list[MendelianTrioOut]:
    params: dict[str, Any] = {
        "child_ids": _stored_ids(context, child),
        "father_ids": _stored_ids(context, father),
        "mother_ids": _stored_ids(context, mother),
        "dosage_0": _DOSAGE_GENOTYPES[0],
        "dosage_1": _DOSAGE_GENOTYPES[1],
        "dosage_2": _DOSAGE_GENOTYPES[2],
        "autosomes": tuple(alias for chrom in _AUTOSOMES for alias in chromosome_aliases(chrom)),
    }
    where = _family_where(context, params, alias="e")
    where.append("e.chrom IN %(autosomes)s")
    # mendelian_class, in SQL.
    opposite_father = "((f = 0 AND c = 2) OR (f = 2 AND c = 0))"
    opposite_mother = "((m = 0 AND c = 2) OR (m = 2 AND c = 0))"
    neither_het = "(c = 1 AND ((f = 0 AND m = 0) OR (f = 2 AND m = 2)))"
    rows = await _execute_clickhouse(
        f"""
        SELECT
            source,
            chrom,
            uniqExact(key),
            uniqExactIf(key, {opposite_father} AND NOT {opposite_mother}),
            uniqExactIf(key, {opposite_mother} AND NOT {opposite_father}),
            uniqExactIf(key, ({opposite_father} AND {opposite_mother}) OR {neither_het})
        FROM (
            SELECT
                e.key AS key,
                e.source AS source,
                e.chrom AS chrom,
                {_dosage_sql('child_ids')} AS c,
                {_dosage_sql('father_ids')} AS f,
                {_dosage_sql('mother_ids')} AS m
            FROM {entries} AS e
            WHERE {' AND '.join(where)}
        )
        WHERE c >= 0 AND f >= 0 AND m >= 0 AND (c + f + m) > 0
        GROUP BY source, chrom
        """,
        params,
    )
    by_source: dict[str, list[MendelianChromosomeOut]] = defaultdict(list)
    for source, chrom, sites, not_father, not_mother, neither in rows:
        by_source[str(source or "")].append(
            MendelianChromosomeOut(
                chrom=normalize_chromosome(str(chrom or "")),
                sites=int(sites or 0),
                not_from_father=int(not_father or 0),
                not_from_mother=int(not_mother or 0),
                from_neither=int(neither or 0),
            )
        )
    return [
        MendelianTrioOut(
            child=child,
            father=father,
            mother=mother,
            source=source,
            chromosomes=sorted(chromosomes, key=lambda row: _chromosome_sort_key(row.chrom)),
        )
        for source, chromosomes in sorted(by_source.items())
    ]


def family_trios(context: FamilyMetadataContext) -> list[tuple[str, str, str]]:
    """Each active member with both parents among the family's active members, as (child,
    father, mother), in the family's member order."""
    members = [str(row.get("sample_id") or "") for row in context.sample_rows]
    present = set(members)
    pedigree = build_pedigree(context.sample_rows, context.relationship_rows)
    trios: list[tuple[str, str, str]] = []
    for child in members:
        parents = pedigree.parents_of.get(child) or {}
        father, mother = parents.get("father"), parents.get("mother")
        if father and mother and father in present and mother in present and child not in (father, mother):
            trios.append((child, father, mother))
    return trios


async def get_family_small_variant_statistics(
    context: FamilyMetadataContext, *, nipt_samples: Sequence[str] = ()
) -> FamilySmallVariantStatisticsOut:
    """The family's small-variant statistics; ``nipt_samples`` are a monogenic NIPT family's
    plasma and father, named in the response when they have variants."""
    if not context.assembly_name:
        return FamilySmallVariantStatisticsOut()
    entries = _small_table_name(context.assembly_name, "entries")
    annotation_index = _small_table_name(context.assembly_name, "variants/annotation_index")
    all_sources = await _source_names(context, entries)
    imputed = {source.lower() for source in IMPUTED_SMALL_VARIANT_SOURCES}
    sources = [source for source in all_sources if source.lower() not in imputed]
    imputed_sources = [source for source in all_sources if source.lower() in imputed]
    if not sources:
        return FamilySmallVariantStatisticsOut(imputed_sources=imputed_sources)
    # The reads are independent, so they run at once (the client keeps no session).
    counts, histograms, *trio_tables = await asyncio.gather(
        _variant_counts(context, entries, annotation_index),
        _call_histograms(context, entries),
        *(
            _mendelian_trio(context, entries, child=child, father=father, mother=mother)
            for child, father, mother in family_trios(context)
        ),
    )
    samples = {
        name: _sample_statistics(name, counts, histograms[name])
        for name, _ids in _members(context)
        if counts.variants.get(name)
    }
    # A child without a variant of its own has no table: it was not called.
    trios = [trio for table in trio_tables for trio in table if trio.child in samples]
    family_types = counts.types[_FAMILY]
    return FamilySmallVariantStatisticsOut(
        sources=sources,
        imputed_sources=imputed_sources,
        total_variants=counts.variants[_FAMILY],
        snv_count=family_types[_TRANSITION] + family_types[_TRANSVERSION],
        insertion_count=family_types[_INSERTION],
        deletion_count=family_types[_DELETION],
        other_count=family_types[_OTHER_TYPE],
        samples=list(samples.values()),
        trios=trios,
        nipt_samples=[name for name in nipt_samples if name in samples],
    )

