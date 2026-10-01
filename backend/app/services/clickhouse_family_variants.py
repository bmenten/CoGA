from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from uuid import UUID

from fastapi import HTTPException
from clickhouse_connect.driver.exceptions import ClickHouseError
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.clickhouse import execute_clickhouse
from ..core.csv_export import TRUNCATED_BY_CANDIDATE_LIMIT, TRUNCATED_BY_ROW_LIMIT
from ..schemas import (
    MonarchPhenotypeMatchOut,
    SmallVariantGroupOut,
    SmallVariantSampleSummaryOut,
    SmallVariantSummaryOut,
    VariantInternalCohortOut,
    SvSecondHitOut,
    VariantOut,
    VariantPage,
    VariantPriorityOut,
)
from .data_scope import normalize_chromosome
from .family_metadata_context import FamilyMetadataContext
from .family_variant_filters import (
    SmallVariantQueryFilters,
    StructuralVariantQueryFilters,
)
from .small_variant_review_pg import (
    get_small_variant_review_map,
    list_matching_small_variant_review_ids,
)
from .monarch_phenotype_score import score_genes_for_hpo
from .sv_gene_index_service import (
    get_sv_hit_genes,
    get_sv_second_hits,
    is_index_current,
    store_sv_gene_index,
    summarize_second_hit,
)
from .variant_ranking_cache import (
    canonical_filters,
    compute_ranking_hashes,
    find_superset_candidates,
    get_cached_ranking,
    store_ranking,
)
from .variant_prioritization import (
    clinvar_may_assert_pathogenic,
    score_structural_variant,
    score_variant,
)
from .structural_variant_review_pg import (
    get_structural_variant_review_map,
    list_matching_structural_variant_review_ids,
)

from .clickhouse_variant_records import (
    PanelFilterConstraints,
    Region,
    SmallVariantCall,
    SmallVariantRecord,
    StructuralVariantCall,
    StructuralVariantRecord,
    _coerce_float,
    _coerce_int,
)

from .clickhouse_variant_queries import (
    IMPUTED_SMALL_VARIANT_SOURCES,
    _PANEL_REGION_INLINE_LIMIT,
    _SMALL_COUNT_LIMIT,
    _SMALL_INHERITANCE_MAX_CANDIDATE_ROWS,
    _append_limit_offset,
    _append_unique,
    _band_name_for_position,
    _can_use_small_native_page,
    _can_use_structural_native_page,
    _clamp_small_variant_page,
    _clickhouse_ids_for_sample,
    _collect_annotations,
    _compound_het_partner_map,
    _decode_json_payload,
    _dedupe_regions,
    _display_sample_name,
    _extract_gene_constraint_metrics,
    _family_affected_unaffected_sample_names,
    _family_pedigree,
    _filter_expanded_carrier_screening,
    _float_list,
    _format_cytoband_label,
    _group_review_for_pair,
    _indexed,
    _inheritance_result_items,
    _int_list,
    _listify,
    _normalize_gt,
    _normalize_small_variant_inheritance,
    _page_offset,
    _parse_interval_regions,
    _primary_gene_keys,
    _sample_small_track_records,
    _segregation_modes_by_variant,
    _selected_structural_samples,
    _small_pair_inheritance_candidate_limit,
    _small_query_filter_parts,
    _small_record_matches,
    _small_summary_table_name,
    _small_track_limit_response,
    _small_variant_out,
    _split_gene_terms,
    _string_list,
    _structural_annotation_extra,
    _structural_record_matches,
    _structural_segregation_modes,
    _structural_variant_out,
    _structural_variant_where_clauses,
    _variant_gene_keys,
    _visible_clickhouse_sample_ids,
)
from .clickhouse_variant_ids import _small_table_name, _structural_table_name
from .clickhouse_variant_storage import (
    ensure_clickhouse_variant_tables,
    get_family_small_variant_data_version,
    get_family_structural_variant_data_version,
)
from .genotypes import ALT_CLASSES, HET, HOM_ALT, clickhouse_genotype_condition

logger = logging.getLogger(__name__)


# ClickHouse error substrings that mean "this query was too broad/expensive to
# run", not "the server is broken". We turn these into an actionable 422 so the
# user can narrow their filters instead of seeing an opaque 500.
_CLICKHOUSE_QUERY_TOO_HEAVY_MARKERS = (
    "memory_limit_exceeded",
    "memory limit",
    "timeout_exceeded",
    "timeout exceeded",
    "max_execution_time",
    "too_many_rows",
    "too_many_bytes",
    "too_many_parts",
    "set_size_limit",
)

# Safety cap on the non-native structural-variant page, where every meaningful
# filter forces a fetch-all-then-filter-in-Python path. Far above any realistic
# per-family SV count, so results are identical below the cap; above it the total
# is reported as estimated.
_SV_NON_NATIVE_STRUCTURAL_CANDIDATE_CAP = 50000
_SMALL_TRACK_RESULT_LIMIT = 10000
# Upper bound on a single variant page. Set to the track-result ceiling because the
# variant endpoints are shared with genome-track rendering, which legitimately fetches
# up to _SMALL_TRACK_RESULT_LIMIT rows; ordinary paginated UI requests are far smaller
# (<=500). Caps unbounded scan/hydration from an oversized page_size without truncating
# any real request. Mirrors the existing _SMALL_COUNT_LIMIT ceiling.
MAX_VARIANT_PAGE_SIZE = _SMALL_TRACK_RESULT_LIMIT


async def _fetch_cytoband_label_map(
    session: AsyncSession,
    *,
    assembly_id: str | None,
    loci: Sequence[tuple[str, str, int, int]],
) -> dict[str, str]:
    """Cytoband label per locus, keyed by the caller's id.

    ``loci`` is ``(key, chromosome, start, end)``. Shared by structural and small
    variants: both need the band the call sits in, and neither carries it in the
    payload — it comes from the assembly's cytoband track.
    """
    if not assembly_id or not loci:
        return {}
    chromosomes = list(
        dict.fromkeys(
            alias
            for _key, chrom, _start, _end in loci
            for alias in (normalize_chromosome(chrom), f"chr{normalize_chromosome(chrom)}")
        )
    )
    result = await session.execute(
        text(
            """
            SELECT chr, bands
            FROM chromosomes
            WHERE assembly_id = CAST(:assembly_id AS uuid)
              AND chr IN :chromosomes
            """
        ).bindparams(bindparam("chromosomes", expanding=True)),
        {
            "assembly_id": assembly_id,
            "chromosomes": chromosomes or [""],
        },
    )
    band_map: dict[str, list[dict[str, Any]]] = {}
    for row in result.mappings().all():
        bands = row.get("bands") or []
        if isinstance(bands, str):
            try:
                bands = json.loads(bands)
            except json.JSONDecodeError:
                bands = []
        if not isinstance(bands, list):
            continue
        normalized = normalize_chromosome(str(row["chr"]))
        band_map[normalized] = [band for band in bands if isinstance(band, dict)]

    cytobands: dict[str, str] = {}
    for key, chrom, start, end in loci:
        bands = band_map.get(normalize_chromosome(chrom))
        if not bands:
            continue
        start_band = _band_name_for_position(bands, start)
        end_band = _band_name_for_position(bands, end)
        label = _format_cytoband_label(chrom, start_band, end_band)
        if label:
            cytobands[key] = label
    return cytobands


async def _fetch_structural_cytoband_map(
    session: AsyncSession,
    *,
    assembly_id: str | None,
    records: Sequence[StructuralVariantRecord],
) -> dict[str, str]:
    return await _fetch_cytoband_label_map(
        session,
        assembly_id=assembly_id,
        loci=[(record.variant_id, record.chr, record.start, record.end) for record in records],
    )


async def _fetch_gene_regions(
    session: AsyncSession,
    *,
    gene_query: str,
    assembly_id: str | None,
) -> list[Region]:
    return await _fetch_gene_regions_for_terms(
        session,
        terms=_split_gene_terms(gene_query),
        assembly_id=assembly_id,
    )


async def _fetch_gene_regions_for_terms(
    session: AsyncSession,
    *,
    terms: Sequence[str],
    assembly_id: str | None,
) -> list[Region]:
    terms = [str(term).strip() for term in terms if str(term).strip()]
    if not terms:
        return []
    clauses = ["(upper(hgnc_symbol) IN :terms OR upper(gene_id) IN :terms)"]
    params: dict[str, Any] = {"terms": [term.upper() for term in terms]}
    bind_params = [bindparam("terms", expanding=True)]
    if assembly_id:
        clauses.append("assembly_id = CAST(:assembly_id AS uuid)")
        params["assembly_id"] = assembly_id
    result = await session.execute(
        text(
            f"""
            SELECT chr, start, "end"
            FROM genes
            WHERE {' AND '.join(clauses)}
            """
        ).bindparams(*bind_params),
        params,
    )
    return [
        Region(chr=row["chr"], start=int(row["start"]), end=int(row["end"]))
        for row in result.mappings().all()
    ]


async def _fetch_panel_constraints(
    session: AsyncSession,
    panel_id: str,
    *,
    assembly_id: str | None = None,
) -> PanelFilterConstraints:
    try:
        UUID(panel_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid panel id") from exc
    gene_result = await session.execute(
        text(
            """
            SELECT gene_symbol
            FROM gene_panel_genes
            WHERE panel_id = CAST(:panel_id AS uuid)
            ORDER BY gene_symbol
            """
        ),
        {"panel_id": panel_id},
    )
    genes: list[str] = []
    for row in gene_result.mappings().all():
        _append_unique(genes, row.get("gene_symbol"))

    # Stored regions are per assembly: only the family's own assembly's coordinates may
    # constrain its variants. Without a resolved assembly none can be trusted, and the
    # panel narrows by gene symbol alone (#515).
    region_rows: list[dict[str, Any]] = []
    if assembly_id:
        region_result = await session.execute(
            text(
                """
                SELECT gene, chr, start, "end"
                FROM gene_panel_regions
                WHERE panel_id = CAST(:panel_id AS uuid)
                  AND assembly_id = CAST(:assembly_id AS uuid)
                ORDER BY gene, chr, start, "end"
                """
            ),
            {"panel_id": panel_id, "assembly_id": assembly_id},
        )
        region_rows = [dict(row) for row in region_result.mappings().all()]
    regions = [
        Region(chr=row["chr"], start=int(row["start"]), end=int(row["end"]))
        for row in region_rows
    ]
    for row in region_rows:
        _append_unique(genes, row.get("gene"))
    if assembly_id and genes:
        regions.extend(
            await _fetch_gene_regions_for_terms(
                session,
                terms=genes,
                assembly_id=assembly_id,
            )
        )
    if genes or regions:
        return PanelFilterConstraints(genes=tuple(genes), regions=_dedupe_regions(regions))

    exists = await session.execute(
        text("SELECT 1 FROM gene_panels WHERE id = CAST(:panel_id AS uuid)"),
        {"panel_id": panel_id},
    )
    if exists.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Panel not found")
    return PanelFilterConstraints()


async def _fetch_panel_regions(
    session: AsyncSession,
    panel_id: str,
    *,
    assembly_id: str | None = None,
) -> list[Region]:
    constraints = await _fetch_panel_constraints(session, panel_id, assembly_id=assembly_id)
    return list(constraints.regions)


async def _fetch_gene_constraint_metric_map(
    session: AsyncSession,
    variants: Sequence[VariantOut],
) -> dict[str, dict[str, float | None]]:
    if not variants:
        return {}
    gene_symbols = sorted({variant.gene for variant in variants if variant.gene})
    gene_ids = sorted({variant.gene_id for variant in variants if variant.gene_id})
    clauses: list[str] = []
    params: dict[str, Any] = {}
    bind_params = []
    if gene_symbols:
        clauses.append("upper(hgnc_symbol) IN :gene_symbols")
        params["gene_symbols"] = [symbol.upper() for symbol in gene_symbols]
        bind_params.append(bindparam("gene_symbols", expanding=True))
    if gene_ids:
        clauses.append("gene_id IN :gene_ids")
        params["gene_ids"] = gene_ids
        bind_params.append(bindparam("gene_ids", expanding=True))
    if not clauses:
        return {}
    result = await session.execute(
        text(
            f"""
            SELECT hgnc_symbol, gene_id, extra, source_status, updated_at
            FROM gene_info
            WHERE {' OR '.join(clauses)}
            ORDER BY updated_at DESC
            """
        ).bindparams(*bind_params),
        params,
    )
    by_symbol: dict[str, dict[str, float | None]] = {}
    by_gene_id: dict[str, dict[str, float | None]] = {}
    for row in result.mappings().all():
        doc = dict(row)
        metrics = _extract_gene_constraint_metrics(doc)
        symbol = str(doc.get("hgnc_symbol") or "").strip()
        gene_id = str(doc.get("gene_id") or "").strip()
        if symbol and symbol not in by_symbol:
            by_symbol[symbol] = metrics
        if gene_id and gene_id not in by_gene_id:
            by_gene_id[gene_id] = metrics
    result_map: dict[str, dict[str, float | None]] = {}
    for variant in variants:
        if variant.gene_id and variant.gene_id in by_gene_id:
            result_map[str(variant.id)] = by_gene_id[variant.gene_id]
        elif variant.gene and variant.gene in by_symbol:
            result_map[str(variant.id)] = by_symbol[variant.gene]
    return result_map




async def _fetch_internal_cohort_map(
    context: FamilyMetadataContext,
    variant_ids: Sequence[str],
) -> dict[str, VariantInternalCohortOut]:
    """Per-variant occurrence across the family's accessible-project cohort.

    Aggregated directly from ``entries`` (sign = 1) so each carrier sample is
    counted once regardless of how the data was loaded.
    """

    normalized_ids = tuple({str(value).strip() for value in variant_ids if str(value).strip()})
    if not normalized_ids or not context.assembly_name or not context.project_ids:
        return {}
    entries_table = _small_table_name(context.assembly_name, "entries")
    params: dict[str, Any] = {
        "variant_ids": normalized_ids,
        "project_ids": tuple(context.project_ids),
    }
    # Carriers are the calls with an ALT allele (#511): a haploid "0" is reference, not a
    # carrier, and a haploid "1" or a "2/2" is homozygous alt.
    is_hom = clickhouse_genotype_condition("gt", {HOM_ALT}, param="gt_hom", params=params)
    is_carrier = clickhouse_genotype_condition("gt", ALT_CLASSES, param="gt_alt", params=params)
    rows = await _execute_clickhouse(
        f"""
        SELECT
            variantId,
            uniqExactIf(sample_id, {is_hom}) AS hom,
            uniqExactIf(sample_id, NOT {is_hom}) AS het,
            uniqExact(sample_id) AS samples,
            uniqExact(family_guid) AS families
        FROM {entries_table}
        ARRAY JOIN `calls.sampleId` AS sample_id, `calls.gt` AS gt
        WHERE sign = 1
          AND project_guid IN %(project_ids)s
          AND variantId IN %(variant_ids)s
          AND {is_carrier}
        GROUP BY variantId
        """,
        params,
    )
    result: dict[str, VariantInternalCohortOut] = {}
    for variant_id, hom, het, samples, families in rows:
        result[str(variant_id)] = VariantInternalCohortOut(
            samples=int(samples or 0),
            het=int(het or 0),
            hom=int(hom or 0),
            families=int(families or 0),
        )
    return result


async def fetch_recurrent_small_variant_ids(
    assembly_name: str,
    *,
    min_carrier_samples: int,
    carrier_samples: Mapping[str, str] | None = None,
    exclude_common: bool = True,
    exclude_clinvar_pathogenic: bool = True,
    project_ids: Sequence[str] | None = None,
    limit: int = 100_000,
) -> list[tuple[str, int]]:
    """Variant ids carried (non-ref) by >= ``min_carrier_samples`` distinct
    samples -- recurrent-artifact candidates.

    ``carrier_samples`` limits the carriers counted to those samples (``None``: every
    sample on the assembly; empty: none). It maps each identifier ClickHouse may store
    for a sample -- its name or its UUID -- to one name per sample, so a sample counts
    once whichever identifier its calls carry. ``exclude_common`` drops variants flagged
    at import as more than 5% frequent in gnomAD/TopMed (``is_gnomad_gt_5_percent``):
    every common variant recurs, and recurrence is no sign of an artifact.
    ``exclude_clinvar_pathogenic`` drops every variant whose ClinVar record, in any of
    its annotations on the assembly, carries or may carry a pathogenic or likely
    pathogenic assertion (``clinvar_may_assert_pathogenic``: P, LP and every
    conflicting record): a familial founder variant recurs in a disease-focused panel,
    below 5%, and is the last thing an artifact list may hold.

    Returns ``(variant_id, carrier_count)`` pairs ordered by recurrence. Counts
    each carrier sample once via ``sign = 1`` over the ``entries`` table.
    """
    if not assembly_name or min_carrier_samples < 1:
        return []
    if carrier_samples is not None and not carrier_samples:
        return []
    entries_table = _small_table_name(assembly_name, "entries")
    params: dict[str, Any] = {
        "min_samples": int(min_carrier_samples),
        "limit": int(limit),
    }
    clauses = ["sign = 1", clickhouse_genotype_condition("gt", ALT_CLASSES, param="gt_alt", params=params)]
    carrier = "sample_id"
    if carrier_samples is not None:
        stored_ids = sorted(carrier_samples)
        params["carrier_ids"] = tuple(stored_ids)
        # transform() takes arrays: a list is sent as one, a tuple as a tuple.
        params["carrier_from"] = list(stored_ids)
        params["carrier_to"] = [carrier_samples[stored] for stored in stored_ids]
        clauses.append("sample_id IN %(carrier_ids)s")
        carrier = "transform(sample_id, %(carrier_from)s, %(carrier_to)s, sample_id)"
    if exclude_common:
        clauses.append("NOT is_gnomad_gt_5_percent")
    if project_ids:
        clauses.append("project_guid IN %(project_ids)s")
        params["project_ids"] = tuple(project_ids)
    recurrent = f"""
        SELECT variantId, uniqExact({carrier}) AS carriers
        FROM {entries_table}
        ARRAY JOIN `calls.sampleId` AS sample_id, `calls.gt` AS gt
        WHERE {' AND '.join(clauses)}
        GROUP BY variantId
        HAVING carriers >= %(min_samples)s
    """
    if not exclude_clinvar_pathogenic:
        rows = await _execute_clickhouse(
            f"{recurrent} ORDER BY carriers DESC LIMIT %(limit)s", params
        )
        return [(str(variant_id), int(carriers or 0)) for variant_id, carriers in rows]
    # Each candidate's ClinVar terms, pooled across its annotations, read by the one
    # Python ClinVar rule rather than a second copy of it in SQL.
    annotation_index = _small_table_name(assembly_name, "variants/annotation_index")
    rows = await _execute_clickhouse(
        f"""
        SELECT recurrent.variantId, recurrent.carriers, clinvar.terms
        FROM ({recurrent}) AS recurrent
        LEFT JOIN (
            SELECT variantId, groupUniqArrayArray(clinvar_terms) AS terms
            FROM {annotation_index}
            WHERE notEmpty(clinvar_terms)
            GROUP BY variantId
        ) AS clinvar ON clinvar.variantId = recurrent.variantId
        ORDER BY recurrent.carriers DESC
        LIMIT %(limit)s
        """,
        params,
    )
    return [
        (str(variant_id), int(carriers or 0))
        for variant_id, carriers, terms in rows
        if not clinvar_may_assert_pathogenic([str(term) for term in terms or []])
    ]


async def _scan_family_sv_gene_map(
    context: FamilyMetadataContext,
) -> tuple[dict[str, list[dict[str, Any]]], int]:
    """Scan the family's structural variants and group them by overlapped gene."""
    if not context.assembly_name:
        return {}, 0
    entries_table = _structural_table_name(context.assembly_name, "entries")
    rows = await execute_clickhouse(
        f"""
        SELECT e.variantId, e.svType, e.chrom, e.start, e.end, e.gene_symbols,
               e.calls.sampleId, e.calls.gt, e.calls.ps
        FROM {entries_table} AS e
        WHERE e.family_guid = %(family_guid)s AND e.sign = 1 AND length(e.gene_symbols) > 0
        """,
        {"family_guid": context.family_uuid},
    )
    gene_map: dict[str, list[dict[str, Any]]] = {}
    sv_ids: set[str] = set()
    for variant_id, sv_type, chrom, start, end, genes, sample_ids, gts, phase_sets in rows:
        sv_ids.add(str(variant_id))
        gt_map = {
            str(sample): str(gt)
            for sample, gt in zip(sample_ids or [], gts or [])
            if sample not in (None, "")
        }
        ps_map = {
            str(sample): int(ps)
            for sample, ps in zip(sample_ids or [], phase_sets or [])
            if sample not in (None, "") and ps is not None
        }
        sv = {
            "sv_id": str(variant_id),
            "sv_type": str(sv_type or ""),
            "chr": str(chrom or ""),
            "start": int(start) if start is not None else None,
            "end": int(end) if end is not None else None,
            "gt": gt_map,
            "ps": ps_map,
        }
        for gene in genes or []:
            symbol = str(gene).strip()
            if symbol:
                gene_map.setdefault(symbol.upper(), []).append(sv)
    return gene_map, len(sv_ids)


async def _ensure_family_sv_gene_index(
    session: AsyncSession, context: FamilyMetadataContext
) -> None:
    """Build the family's SV→gene index, or rebuild it once the family's SVs have changed.

    The index records the storage-level SV data version it was built from, which every SV
    insert, delete and snapshot restore moves (``clickhouse_variant_storage``), whatever
    code path made the write. Only the package import used to clear the index, so a
    per-sample SV upload or an admin SV delete left the second-hit badge describing SVs
    the family no longer had.

    The version is read before the scan: a write that lands while the index is being
    built leaves it stamped with a version that has already moved on, so the next read
    rebuilds it again rather than keeping a half-updated index.
    """
    sv_data_version: str | None = None
    if context.assembly_name:
        # Ensure the SV tables are current (the calls.ps phase-set column read-based
        # phasing reads, and the data-version table) before reading them.
        await ensure_clickhouse_variant_tables(context.assembly_name)
        sv_data_version = await get_family_structural_variant_data_version(
            context.assembly_name, context.family_uuid
        )
    if await is_index_current(session, context.family_uuid, sv_data_version):
        return
    gene_map, sv_total = await _scan_family_sv_gene_map(context)
    await store_sv_gene_index(
        session,
        family_uuid=context.family_uuid,
        gene_map=gene_map,
        sv_total=sv_total,
        sv_data_version=sv_data_version,
    )


async def _attach_sv_second_hits(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    variants: Sequence[VariantOut],
) -> None:
    """Flag small variants whose gene is also hit by a structural variant (best-effort)."""
    try:
        await _ensure_family_sv_gene_index(session, context)
        genes: set[str] = set()
        for variant in variants:
            genes.update(_variant_gene_keys(variant))
        second_hits = await get_sv_second_hits(
            session, family_uuid=context.family_uuid, gene_symbols=genes
        )
        if not second_hits:
            return
        affected, unaffected = _family_affected_unaffected_sample_names(context)
        pedigree = _family_pedigree(context)
        for variant in variants:
            # A variant can hit several genes and the SV may be on any of them, so record
            # which one matched. The badge's link needs that gene, not the variant's
            # primary symbol — those differ whenever the SV sits on a secondary gene.
            matched_gene = next(
                (gene for gene in _variant_gene_keys(variant) if gene in second_hits),
                None,
            )
            if matched_gene:
                hit = second_hits[matched_gene]
                snv_gt = {gt.sample: gt.gt for gt in (variant.genotypes or []) if gt.sample}
                snv_ps = {
                    gt.sample: int(gt.ps)
                    for gt in (variant.genotypes or [])
                    if gt.sample and gt.ps is not None
                }
                snv_dp = {
                    gt.sample: int(gt.dp)
                    for gt in (variant.genotypes or [])
                    if gt.sample and gt.dp is not None
                }
                variant.sv_second_hit = SvSecondHitOut.model_validate(
                    {
                        **summarize_second_hit(
                            hit["svs"],
                            list(affected),
                            unaffected_samples=list(unaffected),
                            snv_gt_by_sample=snv_gt,
                            snv_ps_by_sample=snv_ps,
                            snv_dp_by_sample=snv_dp,
                            pedigree=pedigree,
                            snv_locus=(variant.chr, variant.start),
                        ),
                        "gene": matched_gene,
                    }
                )
    except Exception:  # the second-hit overlay must never break the page
        logger.warning("SV second-hit overlay failed for family %s", context.family_id, exc_info=True)


async def _hydrate_small_variant_outs(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    variants: Sequence[VariantOut],
) -> None:
    if not variants:
        return
    await _attach_sv_second_hits(session, context=context, variants=variants)

    review_map = await get_small_variant_review_map(
        session,
        family_uuid=context.family_uuid,
        variant_ids=[str(variant.id) for variant in variants],
    )
    for variant in variants:
        variant.review = review_map.get(str(variant.id))

    try:
        internal_map = await _fetch_internal_cohort_map(
            context, [str(variant.id) for variant in variants]
        )
    except Exception:  # noqa: BLE001  # pragma: no cover - internal frequency is best-effort
        internal_map = {}
    for variant in variants:
        variant.internal_cohort = internal_map.get(str(variant.id))

    metric_map = await _fetch_gene_constraint_metric_map(session, variants)
    for variant in variants:
        metrics = metric_map.get(str(variant.id))
        if not metrics:
            continue
        if variant.gene_pli is None:
            variant.gene_pli = metrics.get("gene_pli")
        if variant.gene_missense_z is None:
            variant.gene_missense_z = metrics.get("gene_missense_z")

    try:
        cytoband_map = await _fetch_cytoband_label_map(
            session,
            assembly_id=context.assembly_id,
            loci=[
                (str(variant.id), variant.chr, variant.start, variant.end)
                for variant in variants
            ],
        )
    except Exception:  # noqa: BLE001  # pragma: no cover - the band is decoration, never the page
        cytoband_map = {}
    for variant in variants:
        variant.cytoband = cytoband_map.get(str(variant.id))


async def _execute_clickhouse(query: str, params: dict[str, Any]) -> list[tuple[Any, ...]]:
    try:
        return await execute_clickhouse(query, params)
    except ClickHouseError as exc:
        message = str(exc)
        if "UNKNOWN_TABLE" in message or "doesn't exist" in message:
            return []
        logger.error(
            "ClickHouse variant query failed: %s | params=%s | query=%s",
            message,
            sorted(params.keys()),
            " ".join(query.split()),
        )
        lowered = message.lower()
        if any(marker in lowered for marker in _CLICKHOUSE_QUERY_TOO_HEAVY_MARKERS):
            raise HTTPException(
                status_code=422,
                detail=(
                    "This filter combination matches too many variants to evaluate. "
                    "Narrow the search with a region, gene, or stricter frequency/impact "
                    "thresholds and try again."
                ),
            ) from exc
        raise


async def _count_small_variant_rows_bounded(
    context: FamilyMetadataContext,
    filters: SmallVariantQueryFilters,
    *,
    count_limit: int = _SMALL_COUNT_LIMIT,
    panel_constraints: PanelFilterConstraints | None = None,
    include_variant_ids: Sequence[str] | None = None,
    exclude_variant_ids: Sequence[str] = (),
    include_regions: Sequence[Region] = (),
    exclude_regions: Sequence[Region] = (),
    exclude_gene_regions: Sequence[Region] = (),
    exclude_gene_terms: Sequence[str] = (),
) -> tuple[int, bool]:
    if not context.assembly_name:
        return 0, False
    entries_table = _small_table_name(context.assembly_name, "entries")
    where_clauses, params, _use_detail_join = _small_query_filter_parts(
        context,
        filters,
        panel_constraints=panel_constraints,
        include_variant_ids=include_variant_ids,
        exclude_variant_ids=exclude_variant_ids,
        include_regions=include_regions,
        exclude_regions=exclude_regions,
        exclude_gene_regions=exclude_gene_regions,
        exclude_gene_terms=exclude_gene_terms,
        exclude_imputed=True,
    )
    params["count_limit"] = max(int(count_limit), 1)
    rows = await _execute_clickhouse(
        f"""
        SELECT count()
        FROM (
            SELECT e.key
            FROM {entries_table} AS e
            WHERE {' AND '.join(where_clauses)}
            GROUP BY e.key
            LIMIT %(count_limit)s
        )
        """,
        params,
    )
    count = int(rows[0][0] or 0) if rows else 0
    return count, count >= params["count_limit"]


async def _fetch_small_variant_detail_map(
    assembly_name: str,
    variants: Sequence[tuple[int, str, int]],
) -> dict[tuple[int, str, int], dict[str, Any]]:
    pairs = [
        (int(key), str(annotation_version or "current"), int(annotation_set_hash))
        for key, annotation_version, annotation_set_hash in variants
        if key is not None and annotation_set_hash is not None
    ]
    keys = tuple(dict.fromkeys(key for key, _annotation_version, _annotation_set_hash in pairs))
    if not keys:
        return {}
    annotation_versions = tuple(
        dict.fromkeys(annotation_version for _key, annotation_version, _annotation_set_hash in pairs)
    )
    annotation_set_hashes = tuple(
        dict.fromkeys(annotation_set_hash for _key, _annotation_version, annotation_set_hash in pairs)
    )
    details_table = _small_table_name(assembly_name, "variants/details")
    rows = await _execute_clickhouse(
        f"""
        SELECT
            key,
            annotation_version,
            annotationSetHash,
            any(rsid) AS rsid,
            any(annotationsJson) AS annotations_json
        FROM {details_table}
        WHERE key IN %(variant_keys)s
          AND annotation_version IN %(annotation_versions)s
          AND annotationSetHash IN %(annotation_set_hashes)s
        GROUP BY key, annotation_version, annotationSetHash
        """,
        {
            "variant_keys": keys,
            "annotation_versions": annotation_versions,
            "annotation_set_hashes": annotation_set_hashes,
        },
    )
    details: dict[tuple[int, str, int], dict[str, Any]] = {}
    for key, annotation_version, annotation_set_hash, rsid, annotations_json in rows:
        parsed_key = _coerce_int(key)
        parsed_annotation_set_hash = _coerce_int(annotation_set_hash)
        if parsed_key is None or parsed_annotation_set_hash is None:
            continue
        details[(parsed_key, str(annotation_version or "current"), parsed_annotation_set_hash)] = {
            "rsid": rsid,
            "annotations_json": annotations_json,
        }
    return details


async def _family_has_small_variants(context: FamilyMetadataContext) -> bool:
    """Cheap presence probe for the family dashboard.

    Filters on the indexed ``family_guid`` (and ``project_guid``) column with
    ``LIMIT 1`` — the canonical per-family key, the same one
    ``family_variant_summary`` is keyed by. The paginated query instead filters
    by ``hasAny(calls.sampleId, …)`` membership, which can't use that index and
    scans the table; for a yes/no presence check that scan is wasted. This answers
    "does this family have small-variant data?" in a few ms.
    """
    if not context.assembly_name:
        return False
    entries_table = _small_table_name(context.assembly_name, "entries")
    where_clauses = ["family_guid = %(family_guid)s", "sign = 1"]
    params: dict[str, Any] = {"family_guid": context.family_uuid}
    if context.project_ids:
        where_clauses.append("project_guid IN %(project_ids)s")
        params["project_ids"] = tuple(context.project_ids)
    rows = await _execute_clickhouse(
        f"SELECT 1 FROM {entries_table} WHERE {' AND '.join(where_clauses)} LIMIT 1",
        params,
    )
    return bool(rows)


async def _family_has_structural_variants(context: FamilyMetadataContext) -> bool:
    """Cheap structural-variant presence probe (see :func:`_family_has_small_variants`)."""
    if not context.assembly_name:
        return False
    entries_table = _structural_table_name(context.assembly_name, "entries")
    where_clauses = ["family_guid = %(family_guid)s", "sign = 1"]
    params: dict[str, Any] = {"family_guid": context.family_uuid}
    if context.project_ids:
        where_clauses.append("project_guid IN %(project_ids)s")
        params["project_ids"] = tuple(context.project_ids)
    rows = await _execute_clickhouse(
        f"SELECT 1 FROM {entries_table} WHERE {' AND '.join(where_clauses)} LIMIT 1",
        params,
    )
    return bool(rows)


async def _fetch_structural_variant_summary(
    context: FamilyMetadataContext,
    filters: StructuralVariantQueryFilters,
) -> tuple[int, dict[str, dict[str, int]]]:
    if not context.assembly_name:
        return 0, {}
    entries_table = _structural_table_name(context.assembly_name, "entries")
    where_clauses, params = _structural_variant_where_clauses(context, filters)
    rows = await _execute_clickhouse(
        f"""
        SELECT sv_type, source_value, count()
        FROM (
            SELECT
                any(e.svType) AS sv_type,
                any(e.source) AS source_value
            FROM {entries_table} AS e
            WHERE {' AND '.join(where_clauses)}
            GROUP BY e.key, e.variantId
        )
        GROUP BY sv_type, source_value
        """,
        params,
    )
    summary: dict[str, dict[str, int]] = {}
    total = 0
    for sv_type, source, count in rows:
        count_int = int(count or 0)
        total += count_int
        type_key = str(sv_type or "")
        source_key = str(source or "")
        summary.setdefault(type_key, {})[source_key] = count_int
    return total, summary


async def _fetch_small_variant_summary(
    context: FamilyMetadataContext,
) -> SmallVariantSummaryOut | None:
    if not context.assembly_name:
        return None

    family_variant_summary: SmallVariantSummaryOut | None = None
    family_params: dict[str, Any] = {"family_guid": context.family_uuid}

    if len(context.project_ids) == 1:
        family_params["project_guid"] = context.project_ids[0]
        family_rows = await _execute_clickhouse(
            f"""
            SELECT total_variants, snv_count, indel_count
            FROM {_small_summary_table_name(context.assembly_name, 'family_variant_summary')}
            WHERE family_guid = %(family_guid)s
              AND project_guid = %(project_guid)s
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            family_params,
        )
        if family_rows:
            total_variants, snv_count, indel_count = family_rows[0]
            family_variant_summary = SmallVariantSummaryOut(
                total_variants=int(total_variants or 0),
                snv_count=int(snv_count or 0),
                indel_count=int(indel_count or 0),
            )

    if family_variant_summary is None:
        entries_table = _small_table_name(context.assembly_name, "entries")
        where_clauses = ["family_guid = %(family_guid)s", "sign = 1"]
        params: dict[str, Any] = {"family_guid": context.family_uuid}
        if context.project_ids:
            where_clauses.append("project_guid IN %(project_ids)s")
            params["project_ids"] = tuple(context.project_ids)
        # Exclude imputed callsets so the live-fallback count matches the cached
        # summary (refresh_family_small_variant_summaries also excludes them).
        where_clauses.append("lowerUTF8(source) NOT IN %(imputed_sources)s")
        params["imputed_sources"] = tuple(IMPUTED_SMALL_VARIANT_SOURCES)
        rows = await _execute_clickhouse(
            f"""
            SELECT
                countDistinct(key) AS total_variants,
                countDistinctIf(key, length(ref) = 1 AND length(alt) = 1) AS snv_count,
                countDistinctIf(key, NOT (length(ref) = 1 AND length(alt) = 1)) AS indel_count
            FROM {entries_table}
            WHERE {' AND '.join(where_clauses)}
            """,
            params,
        )
        total_variants, snv_count, indel_count = rows[0] if rows else (0, 0, 0)
        family_variant_summary = SmallVariantSummaryOut(
            total_variants=int(total_variants or 0),
            snv_count=int(snv_count or 0),
            indel_count=int(indel_count or 0),
        )

    sample_rows: list[tuple[Any, ...]] = []
    if len(context.project_ids) == 1:
        sample_rows = await _execute_clickhouse(
            f"""
            SELECT sample_id, non_ref_count, het_count, hom_alt_count
            FROM {_small_summary_table_name(context.assembly_name, 'family_sample_variant_summary')}
            WHERE family_guid = %(family_guid)s
              AND project_guid = %(project_guid)s
            ORDER BY sample_id
            """,
            {
                "family_guid": context.family_uuid,
                "project_guid": context.project_ids[0],
            },
        )

    if not sample_rows:
        entries_table = _small_table_name(context.assembly_name, "entries")
        sample_where_clauses = ["family_guid = %(family_guid)s", "sign = 1"]
        sample_params: dict[str, Any] = {"family_guid": context.family_uuid}
        if context.project_ids:
            sample_where_clauses.append("project_guid IN %(project_ids)s")
            sample_params["project_ids"] = tuple(context.project_ids)
        sample_where_clauses.append("lowerUTF8(source) NOT IN %(imputed_sources)s")
        sample_params["imputed_sources"] = tuple(IMPUTED_SMALL_VARIANT_SOURCES)
        is_alt = clickhouse_genotype_condition("gt", ALT_CLASSES, param="gt_alt", params=sample_params)
        is_het = clickhouse_genotype_condition("gt", {HET}, param="gt_het", params=sample_params)
        is_hom = clickhouse_genotype_condition("gt", {HOM_ALT}, param="gt_hom", params=sample_params)
        sample_rows = await _execute_clickhouse(
            f"""
            SELECT
                sample_id,
                countDistinctIf(key, {is_alt}) AS non_ref_count,
                countDistinctIf(key, {is_het}) AS het_count,
                countDistinctIf(key, {is_hom}) AS hom_alt_count
            FROM {entries_table}
            ARRAY JOIN `calls.sampleId` AS sample_id, `calls.gt` AS gt
            WHERE {' AND '.join(sample_where_clauses)}
            GROUP BY sample_id
            ORDER BY sample_id
            """,
            sample_params,
        )

    sample_counts_by_name: dict[str, SmallVariantSampleSummaryOut] = {}
    for stored_sample_id, non_ref_count, het_count, hom_alt_count in sample_rows:
        sample_name = _display_sample_name(context, stored_sample_id)
        if not sample_name or sample_name not in context.sample_name_to_uuid:
            continue
        sample_counts_by_name[sample_name] = SmallVariantSampleSummaryOut(
            sample_id=sample_name,
            non_ref_count=int(non_ref_count or 0),
            het_count=int(het_count or 0),
            hom_alt_count=int(hom_alt_count or 0),
        )

    ordered_sample_counts: list[SmallVariantSampleSummaryOut] = []
    for row in context.sample_rows:
        sample_name = str(row.get("sample_id") or "").strip()
        sample_summary = sample_counts_by_name.pop(sample_name, None)
        if sample_summary is not None:
            ordered_sample_counts.append(sample_summary)
    ordered_sample_counts.extend(sorted(sample_counts_by_name.values(), key=lambda item: item.sample_id))
    family_variant_summary.sample_counts = ordered_sample_counts
    return family_variant_summary


async def _fetch_small_variant_rows(
    context: FamilyMetadataContext,
    filters: SmallVariantQueryFilters,
    *,
    limit: int | None = None,
    offset: int = 0,
    panel_constraints: PanelFilterConstraints | None = None,
    include_variant_ids: Sequence[str] | None = None,
    exclude_variant_ids: Sequence[str] = (),
    include_regions: Sequence[Region] = (),
    exclude_regions: Sequence[Region] = (),
    exclude_gene_regions: Sequence[Region] = (),
    exclude_gene_terms: Sequence[str] = (),
) -> list[SmallVariantRecord]:
    if not context.assembly_name:
        return []
    entries_table = _small_table_name(context.assembly_name, "entries")
    where_clauses, params, _use_detail_join = _small_query_filter_parts(
        context,
        filters,
        panel_constraints=panel_constraints,
        include_variant_ids=include_variant_ids,
        exclude_variant_ids=exclude_variant_ids,
        include_regions=include_regions,
        exclude_regions=exclude_regions,
        exclude_gene_regions=exclude_gene_regions,
        exclude_gene_terms=exclude_gene_terms,
        exclude_imputed=True,
    )
    # Collapse to one row per key with any() + GROUP BY e.key — exactly the grouping the
    # count path (_count_small_variant_rows_bounded) uses, so the list and the deduped
    # total always agree. The entries table is a CollapsingMergeTree and a lagged/partial
    # ALTER…DELETE mutation or replica lag can transiently leave more than one sign=1 row
    # per key; without the GROUP BY those surfaced as duplicate rows in the page and
    # drifted the OFFSET boundaries. Column order/aliases are unchanged so the row parser
    # below is unaffected. (any() aggregation mirrors _fetch_structural_variant_rows.) (#333)
    query = f"""
        SELECT
            any(e.key) AS key,
            any(e.variantId) AS variant_id,
            any(e.annotation_version) AS annotation_version,
            any(e.annotationSetHash) AS annotation_set_hash,
            any(e.chrom) AS chrom,
            any(e.pos) AS pos,
            any(e.ref) AS ref,
            any(e.alt) AS alt,
            any(e.source) AS source,
            any(e.rsid) AS rsid,
            any(e.filters) AS entry_filters,
            any(e.gene_symbols) AS gene_symbols,
            any(e.calls.sampleId) AS sample_ids,
            any(e.calls.gt) AS sample_gts,
            any(e.calls.gq) AS sample_gqs,
            any(e.calls.dp) AS sample_dps,
            any(e.calls.ab) AS sample_abs,
            any(e.calls.af) AS sample_afs,
            any(e.calls.ad) AS sample_ads,
            any(e.calls.ps) AS sample_phase_sets,
            any(e.qual) AS qual
        FROM {entries_table} AS e
        WHERE {' AND '.join(where_clauses)}
        GROUP BY e.key
        ORDER BY any(e.xpos), key
    """
    query = _append_limit_offset(query, params, limit=limit, offset=offset)
    rows = await _execute_clickhouse(query, params)
    detail_map = await _fetch_small_variant_detail_map(
        context.assembly_name,
        [
            (parsed_key, str(row[2] or "current"), int(row[3]))
            for row in rows
            if (parsed_key := _coerce_int(row[0])) is not None
            and _coerce_int(row[3]) is not None
        ],
    )
    records: list[SmallVariantRecord] = []
    for row in rows:
        (
            variant_key,
            variant_id,
            annotation_version,
            annotation_set_hash,
            chrom,
            pos,
            ref,
            alt,
            source,
            rsid,
            entry_filters,
            gene_symbols,
            sample_ids,
            sample_gts,
            sample_gqs,
            sample_dps,
            sample_abs,
            sample_afs,
            sample_ads,
            sample_phase_sets,
            qual,
        ) = row
        parsed_variant_key = _coerce_int(variant_key)
        parsed_annotation_set_hash = _coerce_int(annotation_set_hash)
        detail = detail_map.get(
            (
                parsed_variant_key or -1,
                str(annotation_version or "current"),
                parsed_annotation_set_hash or -1,
            ),
            {},
        )
        annotations_json = detail.get("annotations_json")
        rsid = rsid if rsid not in (None, "") else detail.get("rsid")
        calls: list[SmallVariantCall] = []
        sample_id_list = _listify(sample_ids)
        for index, sample_id in enumerate(sample_id_list):
            sample_name = _display_sample_name(context, sample_id)
            if not sample_name or sample_name not in context.sample_name_to_uuid:
                continue
            af_values = _float_list(_indexed(sample_afs, index))
            ab_value = _coerce_float(_indexed(sample_abs, index))
            if not af_values and ab_value is not None:
                af_values = [ab_value]
            calls.append(
                SmallVariantCall(
                    sample=sample_name,
                    gt=_normalize_gt(_indexed(sample_gts, index)),
                    gq=_coerce_float(_indexed(sample_gqs, index)),
                    dp=_coerce_int(_indexed(sample_dps, index)),
                    af=af_values,
                    ad=_int_list(_indexed(sample_ads, index)),
                    ps=_coerce_int(_indexed(sample_phase_sets, index)),
                )
            )
        if not calls:
            continue
        start = int(pos)
        ref_text = str(ref or "")
        records.append(
            SmallVariantRecord(
                variant_key=parsed_variant_key,
                variant_id=str(variant_id),
                chr=normalize_chromosome(str(chrom)),
                start=start,
                end=start + max(len(ref_text), 1) - 1,
                ref=ref_text,
                alt=str(alt or ""),
                source=str(source) if source is not None else None,
                rsid=str(rsid) if rsid not in (None, "") else None,
                filters=_string_list(entry_filters),
                gene_symbols=_string_list(gene_symbols),
                annotations=_collect_annotations(_decode_json_payload(annotations_json)),
                calls=calls,
                qual=_coerce_float(qual),
            )
        )
    return records


async def fetch_imputed_phased_genotypes(
    context: FamilyMetadataContext,
    *,
    chrom: str,
    start: int,
    end: int,
    limit: int,
    source: str | None = "glimpse2",
) -> list[tuple[int, str, str, list[str], list[str]]]:
    """Lean, family-scoped fetch of variant positions, the ref/alt alleles, and
    the per-sample genotype strings in a region — no annotation hydration, so it
    stays cheap even for tens of thousands of sites. Used by the phased-marker
    parent-of-origin computation, relative haplotype colouring (``source=glimpse2``,
    phased ``0|1``) and sample-integrity QC (which also reads ``clair3`` SNVs,
    unphased ``0/1``). Pass ``source=None`` to read across all callsets. Each row
    is ``(pos, ref, alt, sample_ids, gts)``."""
    if not context.assembly_name:
        return []
    filters = SmallVariantQueryFilters(
        page=1,
        page_size=1,
        chromosome=chrom,
        start=start,
        end=end,
        source=source,
        overlap=True,
    )
    where_clauses, params, _ = _small_query_filter_parts(context, filters)
    entries_table = _small_table_name(context.assembly_name, "entries")
    query = f"""
        SELECT e.pos AS pos, e.ref AS ref, e.alt AS alt,
               e.calls.sampleId AS sample_ids, e.calls.gt AS sample_gts
        FROM {entries_table} AS e
        WHERE {' AND '.join(where_clauses)}
        -- e.key is a unique per-variant tiebreaker so the LIMIT cutoff selects a fixed
        -- set of sites even when several variants share a position. Without it, ties at
        -- the truncation boundary resolve arbitrarily across executions, which would
        -- make the sample-integrity QC metrics (and the frozen sign-out content hash
        -- that includes them) non-reproducible for identical data.
        ORDER BY e.pos, e.key
        LIMIT %(phased_limit)s
    """
    params["phased_limit"] = int(limit)
    rows = await _execute_clickhouse(query, params)
    return [
        (int(row[0]), str(row[1]), str(row[2]), [str(s) for s in row[3]], [str(g) for g in row[4]])
        for row in rows
    ]


async def fetch_family_variant_sources(context: FamilyMetadataContext) -> list[str]:
    """Distinct small-variant callset ``source`` values for the family (e.g.
    ``glimpse2``, ``clair3``). Lets sample-integrity QC pick the right genotype
    source for the application without hard-coding it."""
    if not context.assembly_name:
        return []
    filters = SmallVariantQueryFilters(page=1, page_size=1)
    where_clauses, params, _ = _small_query_filter_parts(context, filters)
    entries_table = _small_table_name(context.assembly_name, "entries")
    query = f"""
        SELECT DISTINCT e.source AS source
        FROM {entries_table} AS e
        WHERE {' AND '.join(where_clauses)}
        LIMIT 50
    """
    rows = await _execute_clickhouse(query, params)
    return [str(row[0]) for row in rows if row[0]]


async def _small_variant_present_sample_names(
    context: FamilyMetadataContext,
    filters: SmallVariantQueryFilters,
    *,
    include_regions: Sequence[Region] = (),
) -> set[str]:
    """Per-sample small-variant presence for track availability, in one aggregate query
    (plus a cheap base-match probe only when explicit sample-filters are present).

    Equivalent to the old N per-sample ``_fetch_small_variant_rows(limit=1)`` probes (which
    injected ``_small_variant_presence_filters``). A family sample carrying an explicit
    sample-filter has its constraint already in the shared WHERE, so it is present iff the
    base query matches ANY variant — even when it is absent from that variant's calls (the
    ``include_absent`` / reference-parent case, e.g. a de-novo MOTHER:0/0). Every other
    sample is present iff a matching variant carries a non-ref genotype for it. The base
    sample-filters are evaluated on the full call arrays in the inner query (before the
    ARRAY JOIN), so cross-sample constraints are not corrupted.
    """
    if not context.assembly_name:
        return set()
    visible_ids = _visible_clickhouse_sample_ids(context)
    if not visible_ids:
        return set()
    entries_table = _small_table_name(context.assembly_name, "entries")
    where_clauses, params, _use_detail_join = _small_query_filter_parts(
        context, filters, include_regions=include_regions
    )
    inner = (
        f"SELECT e.calls.sampleId AS sids, e.calls.gt AS gts "
        f"FROM {entries_table} AS e WHERE {' AND '.join(where_clauses)}"
    )
    explicit_names = {
        entry.split(":", 1)[0]
        for entry in (filters.sample_filters or [])
        if entry.split(":", 1)[0] in context.sample_name_to_uuid
    }
    params["track_visible_ids"] = tuple(visible_ids)
    # A sample is present when a matching variant carries an ALT allele for it — haploid
    # and multi-allelic calls included, so an all-haploid sample is not "empty" (#511).
    is_alt = clickhouse_genotype_condition("gt", ALT_CLASSES, param="track_nonref_gts", params=params)
    nonref_query = f"""
        SELECT sid
        FROM ({inner})
        ARRAY JOIN sids AS sid, gts AS gt
        WHERE sid IN %(track_visible_ids)s AND {is_alt}
        GROUP BY sid
    """
    present: set[str] = set()
    for (sid,) in await _execute_clickhouse(nonref_query, params):
        name = _display_sample_name(context, sid)
        if name and name in context.sample_name_to_uuid:
            present.add(name)
    # Explicit-filter samples: present iff the base query matches at all (handles the
    # reference-parent / include_absent case the non-ref ARRAY JOIN cannot).
    if explicit_names:
        base_match = await _execute_clickhouse(f"SELECT 1 FROM ({inner}) LIMIT 1", params)
        if base_match:
            present |= explicit_names
    return present


async def _fetch_structural_variant_rows(
    context: FamilyMetadataContext,
    filters: StructuralVariantQueryFilters,
    *,
    limit: int | None = None,
    offset: int = 0,
    track_mode: bool = False,
    include_regions: Sequence[Region] = (),
    call_sample_names: Sequence[str] = (),
) -> list[StructuralVariantRecord]:
    """The family view of its SVs: the visible projects and samples, grouped per SV.

    For display only. A write path that rewrites stored rows reads them with
    ``clickhouse_variant_storage.fetch_family_structural_variant_rows`` instead: this read
    drops what the view does not show (inactive members' calls, phase sets, remote ends).
    """
    if not context.assembly_name:
        return []
    entries_table = _structural_table_name(context.assembly_name, "entries")
    details_table = _structural_table_name(context.assembly_name, "variants/details")
    where_clauses, params = _structural_variant_where_clauses(
        context, filters, include_regions=include_regions
    )
    if call_sample_names:
        # Only the SVs with a call for these samples, the rule the record filter applies
        # afterwards anyway; in SQL, a row limit counts those samples' SVs instead of the
        # whole family's (#585).
        call_sample_ids: list[str] = []
        for sample_name in call_sample_names:
            for sample_id in _clickhouse_ids_for_sample(context, sample_name):
                _append_unique(call_sample_ids, sample_id)
        where_clauses.append("hasAny(e.calls.sampleId, %(call_sample_ids)s)")
        params["call_sample_ids"] = call_sample_ids
    # The genome track never uses the (multi-KB-per-variant) annotation JSON, and we set
    # annotations=[] for it below; not selecting the column means ClickHouse never reads
    # it off disk — the bulk of the remaining per-member fetch time for tens of
    # thousands of SVs. The details join stays so detail-column WHERE filters still work.
    annotations_col = "'' AS annotations_json" if track_mode else "any(d.annotationsJson) AS annotations_json"
    query = f"""
        SELECT
            any(e.key) AS key,
            any(e.variantId) AS variant_id,
            any(e.chrom) AS chrom,
            any(e.start) AS start,
            any(e.end) AS "end",
            any(e.svType) AS sv_type,
            any(e.source) AS source,
            any(d.remoteChrom) AS remote_chr,
            any(d.remoteStart) AS remote_start,
            any(d.svLen) AS sv_len,
            any(d.filters) AS filters,
            {annotations_col},
            any(e.gene_symbols) AS gene_symbols,
            any(e.calls.sampleId) AS sample_ids,
            any(e.calls.gt) AS sample_gts,
            any(e.calls.qual) AS sample_quals,
            any(e.calls.readSupport) AS sample_read_supports,
            any(e.calls.filter) AS sample_filters,
            any(e.calls.cn) AS sample_copy_numbers
        FROM {entries_table} AS e
        LEFT JOIN {details_table} AS d ON d.key = e.key
        WHERE {' AND '.join(where_clauses)}
        GROUP BY e.key, e.variantId
        ORDER BY chrom, start, key
    """
    query = _append_limit_offset(query, params, limit=limit, offset=offset)
    rows = await _execute_clickhouse(query, params)
    records: list[StructuralVariantRecord] = []
    for row in rows:
        (
            variant_key,
            variant_id,
            chrom,
            start,
            end,
            sv_type,
            source,
            remote_chr,
            remote_start,
            sv_len,
            filters_raw,
            annotations_json,
            gene_symbols,
            sample_ids,
            sample_gts,
            sample_quals,
            sample_read_supports,
            sample_filters_raw,
            sample_copy_numbers,
        ) = row
        calls: list[StructuralVariantCall] = []
        sample_id_list = _listify(sample_ids)
        for index, sample_id in enumerate(sample_id_list):
            sample_name = _display_sample_name(context, sample_id)
            if not sample_name or sample_name not in context.sample_name_to_uuid:
                continue
            calls.append(
                StructuralVariantCall(
                    sample=sample_name,
                    gt=_normalize_gt(_indexed(sample_gts, index)),
                    qual=_coerce_float(_indexed(sample_quals, index)),
                    read_support=_coerce_int(_indexed(sample_read_supports, index)),
                    filter=str(_indexed(sample_filters_raw, index) or "").strip() or None,
                    copy_number=_coerce_int(_indexed(sample_copy_numbers, index)),
                )
            )
        if not calls:
            continue
        records.append(
            StructuralVariantRecord(
                variant_key=_coerce_int(variant_key),
                variant_id=str(variant_id),
                chr=normalize_chromosome(str(chrom)),
                start=int(start),
                end=int(end),
                sv_type=str(sv_type or ""),
                source=str(source) if source is not None else None,
                remote_chr=normalize_chromosome(str(remote_chr)) if remote_chr not in (None, "") else None,
                remote_start=_coerce_int(remote_start),
                remote_end=None,
                sv_len=_coerce_int(sv_len),
                filters=_string_list(filters_raw),
                gene_symbols=_string_list(gene_symbols),
                # Track mode never reads annotations downstream (annotation_extra is
                # zeroed); skipping the multi-KB-per-variant JSON decode is the bulk of
                # the speed-up for tens of thousands of SVs.
                annotations=[] if track_mode else _collect_annotations(_decode_json_payload(annotations_json)),
                calls=calls,
            )
        )
    return records


async def _structural_present_sample_names(
    context: FamilyMetadataContext,
    filters: StructuralVariantQueryFilters,
) -> set[str]:
    """Per-sample structural-variant presence (any call) for track availability, in ONE
    aggregate query.

    Valid ONLY when no Python-side SV filter is active (the caller falls back to the full
    row fetch + per-sample matching otherwise — those queries return far less data anyway).
    A sample is present iff it has a call in a variant matching the base WHERE
    (``_structural_variant_where_clauses``: family/sign/project/visibility/chromosome/
    window), regardless of genotype — mirroring the old ``selected_samples=[sample]``
    membership test (SVs have no non-ref requirement). Avoids materialising + JSON-decoding
    the entire family SV set on an unfiltered genome/chromosome workspace load.
    """
    if not context.assembly_name:
        return set()
    visible_ids = _visible_clickhouse_sample_ids(context)
    if not visible_ids:
        return set()
    entries_table = _structural_table_name(context.assembly_name, "entries")
    where_clauses, params = _structural_variant_where_clauses(context, filters)
    params["track_visible_ids"] = tuple(visible_ids)
    query = f"""
        SELECT sid
        FROM (
            SELECT any(e.calls.sampleId) AS sample_ids
            FROM {entries_table} AS e
            WHERE {' AND '.join(where_clauses)}
            GROUP BY e.key, e.variantId
        )
        ARRAY JOIN sample_ids AS sid
        WHERE sid IN %(track_visible_ids)s
        GROUP BY sid
    """
    present: set[str] = set()
    for (sid,) in await _execute_clickhouse(query, params):
        name = _display_sample_name(context, sid)
        if name and name in context.sample_name_to_uuid:
            present.add(name)
    return present


# Upper bound on variants pulled into a single prioritization pass. The Exomiser-style
# preset applies strict frequency/impact filters first, so the candidate set is normally
# far smaller than this; when it is exceeded the ranking covers only this many (the
# highest-priority variant could lie outside the window), so the response flags
# `ranking_truncated` to prompt the user to narrow their filters.
_PRIORITIZE_CANDIDATE_LIMIT = 5000


async def _affected_present_hpo(
    session: AsyncSession, context: FamilyMetadataContext
) -> tuple[list[str], dict[str, str]]:
    """Present HPO terms for affected individuals (all members if none flagged)."""
    affected_names, _unaffected = _family_affected_unaffected_sample_names(context)
    affected_uuids = [
        context.sample_name_to_uuid[name]
        for name in affected_names
        if name in context.sample_name_to_uuid
    ]
    clauses = ["family_id = CAST(:family_uuid AS uuid)", "ih.status = 'present'"]
    params: dict[str, Any] = {"family_uuid": context.family_uuid}
    if affected_uuids:
        clauses.append("ih.sample_id::text = ANY(:affected_uuids)")
        params["affected_uuids"] = affected_uuids
    rows = await session.execute(
        text(
            f"""
            SELECT DISTINCT ih.hpo_id AS hpo_id, t.label AS label
            FROM individual_hpo ih
            LEFT JOIN hpo_term t ON t.hpo_id = ih.hpo_id
            WHERE {' AND '.join(clauses)}
            """
        ),
        params,
    )
    terms: list[str] = []
    labels: dict[str, str] = {}
    for row in rows.mappings().all():
        terms.append(row["hpo_id"])
        if row["label"]:
            labels[row["hpo_id"]] = row["label"]
    return terms, labels


async def _serve_ranking_from_cache(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    cached: dict[str, Any],
    page: int,
    page_size: int,
    small_variant_summary: SmallVariantSummaryOut | None,
) -> VariantPage | None:
    """Rebuild a prioritised page from a cached ranking.

    The cache holds the ranked (variant id + score breakdown) order; the page's variant
    records and review state are fetched fresh so annotations and review status are never
    served stale. Returns None on any reconstruction problem so the caller falls back to a
    live compute.
    """
    ranking = cached.get("ranking") or []
    if isinstance(ranking, str):
        try:
            ranking = json.loads(ranking)
        except json.JSONDecodeError:
            return None
    total = int(cached.get("total") or 0)
    total_is_estimated = bool(cached.get("total_is_estimated"))
    ranking_truncated = bool(cached.get("ranking_truncated"))
    reported_total = min(total, _SMALL_COUNT_LIMIT)
    unfiltered_total = small_variant_summary.total_variants if small_variant_summary else None

    skip = max(page - 1, 0) * page_size if page_size else 0
    page_entries = ranking[skip : skip + page_size] if page_size else ranking[skip:]
    page_ids = [str(entry["variant_id"]) for entry in page_entries if entry.get("variant_id")]

    page_variants: list[VariantOut] = []
    if page_ids:
        fetch_filters = SmallVariantQueryFilters(page=1, page_size=len(page_ids) + 5)
        records = await _fetch_small_variant_rows(
            context,
            fetch_filters,
            include_variant_ids=page_ids,
            limit=len(page_ids) + 5,
        )
        by_id: dict[str, VariantOut] = {}
        for record in records:
            out = _small_variant_out(record, assembly_name=context.assembly_name)
            by_id[str(out.id)] = out
        for entry in page_entries:
            variant_out = by_id.get(str(entry.get("variant_id")))
            if variant_out is None:
                continue
            priority = entry.get("priority")
            if priority:
                try:
                    variant_out.priority = VariantPriorityOut.model_validate(priority)
                except Exception:  # noqa: BLE001 — a bad cached blob should fall back to live
                    return None
            page_variants.append(variant_out)
        await _hydrate_small_variant_outs(session, context=context, variants=page_variants)

    return VariantPage(
        total=reported_total,
        total_is_estimated=total_is_estimated,
        unfiltered_total=unfiltered_total,
        unfiltered_total_is_estimated=False,
        count_limit=_SMALL_COUNT_LIMIT - 1,
        ranking_truncated=ranking_truncated,
        candidate_limit=_PRIORITIZE_CANDIDATE_LIMIT if ranking_truncated else None,
        ranking_cached=True,
        ranking_computed_at=cached.get("computed_at"),
        variants=page_variants,
        small_variant_summary=small_variant_summary,
    )


async def _serve_subpanel_from_superset(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    base_hash: str,
    panel_constraints: PanelFilterConstraints,
    page: int,
    page_size: int,
    small_variant_summary: SmallVariantSummaryOut | None,
) -> VariantPage | None:
    """Serve a gene panel from a broader cached ranking with the same panel-agnostic
    inputs.

    The per-variant scores don't depend on the panel, so a narrower panel's ranking is the
    superset's ranking restricted to the panel's variants — in the same order. Membership
    is re-validated against ClickHouse with the *exact* panel filter, so the result equals
    a direct compute (no missed variants). Only complete (non-truncated) supersets whose
    genes cover the request are used; otherwise returns None for a live compute.
    """
    request_genes = {gene.lower() for gene in (panel_constraints.genes or []) if gene}
    if not request_genes:
        return None  # no gene panel to narrow from a gene-panel superset

    candidates = await find_superset_candidates(
        session, family_uuid=context.family_uuid, base_hash=base_hash
    )
    for candidate in candidates:
        candidate_panel_id = candidate.get("panel_id")
        if candidate_panel_id:
            candidate_constraints = await _fetch_panel_constraints(
                session, candidate_panel_id, assembly_id=context.assembly_id
            )
            candidate_genes = {gene.lower() for gene in (candidate_constraints.genes or []) if gene}
            if not request_genes.issubset(candidate_genes):
                continue  # this superset doesn't cover all the requested genes

        ranking = candidate.get("ranking") or []
        if isinstance(ranking, str):
            try:
                ranking = json.loads(ranking)
            except json.JSONDecodeError:
                continue
        superset_ids = [str(entry["variant_id"]) for entry in ranking if entry.get("variant_id")]
        if not superset_ids:
            continue

        # Re-validate which of the superset's variants are in the requested panel, using
        # the exact panel filter (SQL + the same Python check the live path applies).
        fetch_filters = SmallVariantQueryFilters(page=1, page_size=len(superset_ids) + 5)
        records = await _fetch_small_variant_rows(
            context,
            fetch_filters,
            include_variant_ids=superset_ids,
            panel_constraints=panel_constraints,
            limit=len(superset_ids) + 5,
        )
        by_id: dict[str, Any] = {}
        for record in records:
            if _small_record_matches(
                record, fetch_filters, [], [], [], panel_constraints=panel_constraints
            ):
                by_id[str(record.variant_id)] = record

        subset_ranking = [
            entry for entry in ranking if str(entry.get("variant_id")) in by_id
        ]
        total = len(subset_ranking)
        reported_total = min(total, _SMALL_COUNT_LIMIT)
        skip = max(page - 1, 0) * page_size if page_size else 0
        page_entries = subset_ranking[skip : skip + page_size] if page_size else subset_ranking[skip:]

        page_variants: list[VariantOut] = []
        for entry in page_entries:
            record = by_id.get(str(entry.get("variant_id")))
            if record is None:
                continue
            variant_out = _small_variant_out(record, assembly_name=context.assembly_name)
            priority = entry.get("priority")
            if priority:
                try:
                    variant_out.priority = VariantPriorityOut.model_validate(priority)
                except Exception:  # noqa: BLE001 - fall back to live compute on a bad blob
                    return None
            page_variants.append(variant_out)
        await _hydrate_small_variant_outs(session, context=context, variants=page_variants)

        unfiltered_total = small_variant_summary.total_variants if small_variant_summary else None
        return VariantPage(
            total=reported_total,
            total_is_estimated=False,
            unfiltered_total=unfiltered_total,
            unfiltered_total_is_estimated=False,
            count_limit=_SMALL_COUNT_LIMIT - 1,
            ranking_truncated=False,
            ranking_cached=True,
            ranking_computed_at=candidate.get("computed_at"),
            variants=page_variants,
            small_variant_summary=small_variant_summary,
        )
    return None


async def _family_small_variant_data_version(context: FamilyMetadataContext) -> str | None:
    """The family's storage-level small-variant data version — a ranking-cache input (#509)."""
    if not context.assembly_name:
        return None
    return await get_family_small_variant_data_version(context.assembly_name, context.family_uuid)


async def _prioritized_small_variants_page(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    filters: SmallVariantQueryFilters,
    page: int,
    page_size: int,
    panel_constraints: PanelFilterConstraints,
    review_variant_ids: set[str] | None,
    excluded_review_variant_ids: set[str],
    include_review_filter_active: bool,
    include_regions: list[Region],
    exclude_regions: Sequence[Region],
    exclude_gene_regions: Sequence[Region],
    exclude_gene_terms: Sequence[str],
    small_variant_summary: SmallVariantSummaryOut | None,
) -> VariantPage:
    if filters.gene:
        include_regions = [
            *include_regions,
            *await _fetch_gene_regions(
                session, gene_query=filters.gene, assembly_id=context.assembly_id
            ),
        ]

    # The phenotype-prioritised ranking is expensive (~10s); serve a cached ranking
    # when its inputs are unchanged — filters, HPO, pedigree, panel, reference releases and
    # the family's variant data (the storage-level data version, see #509).
    patient_terms, term_labels = await _affected_present_hpo(session, context)
    variant_data_version = await _family_small_variant_data_version(context)
    inputs_hash, base_hash = await compute_ranking_hashes(
        session,
        context=context,
        filters=filters,
        patient_terms=patient_terms,
        variant_data_version=variant_data_version,
        review_variant_ids=review_variant_ids if include_review_filter_active else None,
        excluded_review_variant_ids=excluded_review_variant_ids,
        include_review_filter_active=include_review_filter_active,
    )
    cached = await get_cached_ranking(
        session, family_uuid=context.family_uuid, inputs_hash=inputs_hash
    )
    if cached is not None:
        served = await _serve_ranking_from_cache(
            session,
            context=context,
            cached=cached,
            page=page,
            page_size=page_size,
            small_variant_summary=small_variant_summary,
        )
        if served is not None:
            return served

    # No exact hit: a narrower panel can be served from a broader cached ranking with the
    # same panel-agnostic inputs (scores are panel-independent), re-validating membership.
    served = await _serve_subpanel_from_superset(
        session,
        context=context,
        base_hash=base_hash,
        panel_constraints=panel_constraints,
        page=page,
        page_size=page_size,
        small_variant_summary=small_variant_summary,
    )
    if served is not None:
        return served

    records = await _fetch_small_variant_rows(
        context,
        filters,
        panel_constraints=panel_constraints,
        include_variant_ids=review_variant_ids if include_review_filter_active else None,
        exclude_variant_ids=excluded_review_variant_ids,
        # Fetch one extra to detect overflow without dropping a genuine candidate when
        # the set is exactly at the limit.
        limit=_PRIORITIZE_CANDIDATE_LIMIT + 1,
        include_regions=include_regions,
        exclude_regions=exclude_regions,
        exclude_gene_regions=exclude_gene_regions,
        exclude_gene_terms=exclude_gene_terms,
    )
    capped = len(records) > _PRIORITIZE_CANDIDATE_LIMIT
    if capped:
        records = records[:_PRIORITIZE_CANDIDATE_LIMIT]

    filtered = [
        record
        for record in records
        if record.variant_id not in excluded_review_variant_ids
        and ((not include_review_filter_active) or record.variant_id in (review_variant_ids or set()))
        and _small_record_matches(
            record,
            filters,
            include_regions,
            exclude_regions,
            exclude_gene_regions,
            panel_constraints=panel_constraints,
        )
    ]
    if not filtered:
        await store_ranking(
            session,
            family_uuid=context.family_uuid,
            inputs_hash=inputs_hash,
            base_hash=base_hash,
            panel_id=filters.panel_id,
            total=0,
            total_is_estimated=capped,
            ranking_truncated=capped,
            ranking=[],
            provenance={
                "hpo_count": len(patient_terms),
                "panel_id": filters.panel_id,
                "filters": canonical_filters(filters),
            },
        )
        # Nothing in the window survived the filters, but when the window overflowed a
        # match may lie beyond it: the empty ranking is as incomplete as a full one.
        return VariantPage(
            total=0,
            total_is_estimated=capped,
            count_limit=_SMALL_COUNT_LIMIT - 1,
            ranking_truncated=capped,
            candidate_limit=_PRIORITIZE_CANDIDATE_LIMIT if capped else None,
            variants=[],
            small_variant_summary=small_variant_summary,
        )

    modes_by_variant = _segregation_modes_by_variant(filtered, context=context)
    affected_names, _unaffected = _family_affected_unaffected_sample_names(context)
    segregation_evaluated = bool(affected_names)

    variants = [_small_variant_out(record, assembly_name=context.assembly_name) for record in filtered]
    gene_symbols = {variant.gene for variant in variants if variant.gene}
    phenotype_scores = (
        await score_genes_for_hpo(
            session, gene_symbols=gene_symbols, patient_hpo_ids=patient_terms
        )
        if patient_terms and gene_symbols
        else {}
    )
    # Gene-level constraint (pLI / missense-Z) from gene_info, for all candidates (the
    # hydrate step only covers the page); also fills variant.gene_pli for display.
    constraint_map = await _fetch_gene_constraint_metric_map(session, variants)

    for variant in variants:
        metrics = constraint_map.get(str(variant.id))
        if metrics:
            if variant.gene_pli is None:
                variant.gene_pli = metrics.get("gene_pli")
            if variant.gene_missense_z is None:
                variant.gene_missense_z = metrics.get("gene_missense_z")

        modes = modes_by_variant.get(str(variant.id), [])
        gene_score = phenotype_scores.get(variant.gene.upper()) if variant.gene else None
        phenotype_value = gene_score.score if gene_score else None
        scored = score_variant(
            impact=variant.impact,
            clinvar=variant.clinvar,
            cadd_phred=variant.cadd_phred,
            revel=variant.revel,
            spliceai_max=variant.spliceai_max,
            lof=variant.lof,
            gnomad_popmax_af=variant.population_frequencies.get("gnomad_popmax_af"),
            gnomad_af=variant.gnomad_af,
            segregation_modes=modes,
            segregation_evaluated=segregation_evaluated,
            phenotype_score=phenotype_value,
            alpha_missense_class=variant.alpha_missense_class,
            alpha_missense_pathogenicity=variant.alpha_missense_pathogenicity,
            gene_pli=variant.gene_pli,
            gene_missense_z=variant.gene_missense_z,
        )
        phenotype_matches = (
            [
                MonarchPhenotypeMatchOut(
                    hpo_id=match["hpo_id"], label=term_labels.get(match["hpo_id"])
                )
                for match in gene_score.matched
            ]
            if gene_score
            else []
        )
        variant.priority = VariantPriorityOut(
            combined_score=round(scored.combined_score, 4),
            variant_score=round(scored.variant_score, 4),
            pathogenicity_score=round(scored.pathogenicity, 4),
            frequency_score=round(scored.frequency, 4),
            segregation_weight=round(scored.segregation_weight, 4),
            phenotype_score=round(phenotype_value, 4) if phenotype_value is not None else None,
            segregation_modes=scored.segregation_modes,
            phenotype_gene=variant.gene if gene_score else None,
            phenotype_matches=phenotype_matches,
        )

    variants.sort(
        key=lambda v: (
            v.priority.combined_score if v.priority else 0.0,
            v.priority.variant_score if v.priority else 0.0,
            # Stable, fully-determined final tiebreaker: equal-score variants are
            # ordered by variant id so rank assignment, the page slice, and the
            # persisted ranking-cache order are reproducible run-to-run.
            str(v.id),
        ),
        reverse=True,
    )
    for index, variant in enumerate(variants, start=1):
        if variant.priority:
            variant.priority.rank = index

    ranked_total = len(variants)
    # When the candidate set overflowed the ranking window, the ranking only covers the
    # window — report the true filtered count (not the window size) and flag the
    # truncation so the UI can prompt the user to narrow filters.
    if capped:
        true_total, true_estimated = await _count_small_variant_rows_bounded(
            context,
            filters,
            panel_constraints=panel_constraints,
            include_variant_ids=review_variant_ids if include_review_filter_active else None,
            exclude_variant_ids=excluded_review_variant_ids,
            include_regions=include_regions,
            exclude_regions=exclude_regions,
            exclude_gene_regions=exclude_gene_regions,
            exclude_gene_terms=exclude_gene_terms,
        )
        total = max(true_total, ranked_total)
        total_is_estimated = True
    else:
        total = ranked_total
        total_is_estimated = ranked_total >= _SMALL_COUNT_LIMIT

    reported_total = min(total, _SMALL_COUNT_LIMIT)

    # Cache the full ranked order (variant id + score breakdown) so the next open of an
    # unchanged view is served without re-scoring. The page records + review state are
    # re-fetched fresh on a cache hit.
    await store_ranking(
        session,
        family_uuid=context.family_uuid,
        inputs_hash=inputs_hash,
        base_hash=base_hash,
        panel_id=filters.panel_id,
        total=reported_total,
        total_is_estimated=total_is_estimated,
        ranking_truncated=capped,
        ranking=[
            {
                "variant_id": str(variant.id),
                "priority": variant.priority.model_dump(mode="json") if variant.priority else None,
            }
            for variant in variants
        ],
        provenance={
                "hpo_count": len(patient_terms),
                "panel_id": filters.panel_id,
                "filters": canonical_filters(filters),
            },
    )

    skip = max(page - 1, 0) * page_size if page_size else 0
    page_variants = variants[skip : skip + page_size] if page_size else variants[skip:]
    await _hydrate_small_variant_outs(session, context=context, variants=page_variants)

    unfiltered_total = small_variant_summary.total_variants if small_variant_summary else None
    return VariantPage(
        total=reported_total,
        total_is_estimated=total_is_estimated,
        unfiltered_total=unfiltered_total,
        unfiltered_total_is_estimated=False,
        count_limit=_SMALL_COUNT_LIMIT - 1,
        ranking_truncated=capped,
        candidate_limit=_PRIORITIZE_CANDIDATE_LIMIT if capped else None,
        ranking_cached=False,
        ranking_computed_at=datetime.now(timezone.utc),
        variants=page_variants,
        small_variant_summary=small_variant_summary,
    )


async def precompute_family_ranking_safe(
    family_identifier: str, user: Any, *, project_id: str | None = None
) -> None:
    """Best-effort background warm of the prioritised-ranking cache after an HPO or
    pedigree edit.

    Replays the family's most recent prioritised query with the now-current inputs, so
    the next open of that view is served from cache instead of recomputing (~10s). Opens
    its own session (the triggering request is long gone); logs and swallows every error.
    Does nothing until a family has opened the prioritised view at least once (there is no
    query to replay yet), so it never guesses filters.
    """
    from ..core.postgres import get_postgres_engine, get_postgres_sessionmaker
    from .family_metadata_context import build_family_metadata_context

    try:
        get_postgres_engine()
        async with get_postgres_sessionmaker()() as session:
            context = await build_family_metadata_context(
                session, family_identifier=family_identifier, user=user, project_id=project_id
            )
            row = (
                await session.execute(
                    text(
                        """
                        SELECT provenance
                        FROM family_variant_ranking_cache
                        WHERE family_id = CAST(:family_uuid AS uuid)
                        ORDER BY computed_at DESC
                        LIMIT 1
                        """
                    ),
                    {"family_uuid": context.family_uuid},
                )
            ).mappings().first()
            provenance = dict(row)["provenance"] if row else None
            stored_filters = provenance.get("filters") if isinstance(provenance, dict) else None
            if not stored_filters:
                return  # nothing to replay yet — leave the first open as a (lazy) miss

            filters = SmallVariantQueryFilters(**{**stored_filters, "page": 1, "page_size": 100})
            panel_constraints = (
                await _fetch_panel_constraints(
                    session, filters.panel_id, assembly_id=context.assembly_id
                )
                if filters.panel_id
                else PanelFilterConstraints()
            )
            # Replay the no-review default view (the high-value cached case). Computing it
            # stores the ranking under the now-current inputs hash.
            await _prioritized_small_variants_page(
                session,
                context=context,
                filters=filters,
                page=1,
                page_size=100,
                panel_constraints=panel_constraints,
                review_variant_ids=None,
                excluded_review_variant_ids=set(),
                include_review_filter_active=False,
                include_regions=[],
                exclude_regions=[],
                exclude_gene_regions=[],
                exclude_gene_terms=[],
                small_variant_summary=None,
            )
            logger.info("Warmed prioritised-ranking cache for family %s", family_identifier)
    except Exception:  # pragma: no cover - background best-effort
        logger.exception(
            "Background ranking-cache warm failed for family %s", family_identifier
        )


@dataclass(slots=True)
class _SmallVariantScope:
    """What a small-variant request is narrowed to besides its filters, resolved once and
    handed to every fetch and count: the panel, the review selections, and the regions and
    genes to include or leave out."""

    panel_constraints: PanelFilterConstraints
    review_variant_ids: set[str] | None
    excluded_review_variant_ids: set[str]
    include_review_filter_active: bool
    include_regions: list[Region]
    exclude_regions: list[Region]
    exclude_gene_regions: list[Region]
    exclude_gene_terms: list[str]

    def row_filters(self) -> dict[str, Any]:
        """The keyword arguments the row fetch and the bounded count both take."""
        return {
            "panel_constraints": self.panel_constraints,
            "include_variant_ids": self.review_variant_ids if self.include_review_filter_active else None,
            "exclude_variant_ids": self.excluded_review_variant_ids,
            "include_regions": self.include_regions,
            "exclude_regions": self.exclude_regions,
            "exclude_gene_regions": self.exclude_gene_regions,
            "exclude_gene_terms": self.exclude_gene_terms,
        }


async def _resolve_small_variant_scope(
    session: AsyncSession,
    context: FamilyMetadataContext,
    filters: SmallVariantQueryFilters,
    *,
    review_classifications: list[str] | None,
    review_tags: list[str] | None,
    exclude_review_tags: list[str] | None,
    has_notes: bool,
) -> _SmallVariantScope | None:
    """The request's scope, or None when it can match nothing: a panel without genes or
    regions, or a review selection no variant has. An interval list that does not parse is
    refused, not answered as empty (#604)."""
    panel_constraints = PanelFilterConstraints()
    if filters.panel_id:
        panel_constraints = await _fetch_panel_constraints(
            session,
            filters.panel_id,
            assembly_id=context.assembly_id,
        )
        if not panel_constraints.genes and not panel_constraints.regions:
            return None

    include_review_filter_active = bool(review_classifications or review_tags or has_notes)
    review_variant_ids: set[str] | None = None
    if include_review_filter_active:
        review_variant_ids = await list_matching_small_variant_review_ids(
            session,
            family_uuid=context.family_uuid,
            classifications=review_classifications,
            tags=review_tags,
            has_notes=has_notes,
        )
        if not review_variant_ids:
            return None
    excluded_review_variant_ids = (
        await list_matching_small_variant_review_ids(
            session,
            family_uuid=context.family_uuid,
            tags=exclude_review_tags,
        )
        if exclude_review_tags
        else set()
    )

    include_regions = _parse_interval_regions(filters.intervals)
    exclude_regions = _parse_interval_regions(filters.exclude_intervals, label="Excluded interval")
    exclude_gene_regions = (
        await _fetch_gene_regions(session, gene_query=filters.exclude_gene, assembly_id=context.assembly_id)
        if filters.exclude_gene
        else []
    )
    return _SmallVariantScope(
        panel_constraints=panel_constraints,
        review_variant_ids=review_variant_ids,
        excluded_review_variant_ids=excluded_review_variant_ids,
        include_review_filter_active=include_review_filter_active,
        include_regions=include_regions,
        exclude_regions=exclude_regions,
        exclude_gene_regions=exclude_gene_regions,
        exclude_gene_terms=_split_gene_terms(filters.exclude_gene),
    )


async def _small_variants_track_page(
    session: AsyncSession,
    context: FamilyMetadataContext,
    filters: SmallVariantQueryFilters,
    scope: _SmallVariantScope,
    *,
    page: int,
    page_size: int,
    track_result_limit: int,
) -> VariantPage:
    """A track's page, served only while the view holds fewer variants than the track's
    limit; past it, the count alone, so the track can say there are too many to draw."""
    safe_track_result_limit = min(
        max(int(track_result_limit), 1),
        _SMALL_TRACK_RESULT_LIMIT,
    )
    if not _can_use_small_native_page(
        filters,
        track_mode=False,
    ):
        return _small_track_limit_response(
            track_result_limit=safe_track_result_limit
        )

    total, total_is_estimated = await _count_small_variant_rows_bounded(
        context,
        filters,
        count_limit=safe_track_result_limit,
        **scope.row_filters(),
    )
    if total_is_estimated or total >= safe_track_result_limit:
        return VariantPage(
            total=total,
            total_is_estimated=True,
            count_limit=safe_track_result_limit,
            variants=[],
        )

    fetch_limit = min(max(page_size, 0), max(safe_track_result_limit - 1, 1))
    if fetch_limit <= 0 or total <= 0:
        return VariantPage(
            total=total,
            total_is_estimated=False,
            count_limit=safe_track_result_limit,
            variants=[],
        )

    fetched_records = await _fetch_small_variant_rows(
        context,
        filters,
        limit=fetch_limit,
        offset=_page_offset(page, page_size),
        **scope.row_filters(),
    )
    variants = [_small_variant_out(record, assembly_name=context.assembly_name) for record in fetched_records]
    await _hydrate_small_variant_outs(
        session,
        context=context,
        variants=variants,
    )
    return VariantPage(
        total=total,
        total_is_estimated=False,
        count_limit=safe_track_result_limit,
        variants=variants,
    )


async def _small_variants_native_page(
    session: AsyncSession,
    context: FamilyMetadataContext,
    filters: SmallVariantQueryFilters,
    scope: _SmallVariantScope,
    *,
    page: int,
    page_size: int,
    small_variant_summary: SmallVariantSummaryOut | None,
) -> VariantPage:
    """A page ClickHouse filters and pages itself, with the bounded count of all matches.
    Not used in track mode (see ``_can_use_small_native_page``)."""
    fetched_records = await _fetch_small_variant_rows(
        context,
        filters,
        limit=page_size + 1,
        offset=_page_offset(page, page_size),
        **scope.row_filters(),
    )
    total, total_is_estimated = await _count_small_variant_rows_bounded(
        context,
        filters,
        **scope.row_filters(),
    )
    unfiltered_total = small_variant_summary.total_variants if small_variant_summary else None
    page_records = fetched_records[:page_size]
    variants = [_small_variant_out(record, assembly_name=context.assembly_name) for record in page_records]
    if variants:
        await _hydrate_small_variant_outs(
            session,
            context=context,
            variants=variants,
        )
    return VariantPage(
        total=total,
        total_is_estimated=total_is_estimated,
        unfiltered_total=unfiltered_total,
        unfiltered_total_is_estimated=False,
        count_limit=_SMALL_COUNT_LIMIT - 1,
        variants=variants,
        small_variant_summary=small_variant_summary,
    )


async def _small_variants_candidate_page(
    session: AsyncSession,
    context: FamilyMetadataContext,
    filters: SmallVariantQueryFilters,
    scope: _SmallVariantScope,
    *,
    page: int,
    page_size: int,
    track_mode: bool,
    small_variant_summary: SmallVariantSummaryOut | None,
) -> VariantPage:
    """The Python-side path: fetch the candidates ClickHouse can narrow, then apply what it
    cannot — gene symbols, inheritance pairing, carrier screening, a track's sampling."""
    if filters.gene:
        gene_regions = await _fetch_gene_regions(
            session,
            gene_query=filters.gene,
            assembly_id=context.assembly_id,
        )
        scope.include_regions.extend(gene_regions)
    inheritance_candidate_limit = _small_pair_inheritance_candidate_limit(filters)
    records = await _fetch_small_variant_rows(
        context,
        filters,
        limit=inheritance_candidate_limit,
        **scope.row_filters(),
    )
    inheritance_candidates_capped = (
        inheritance_candidate_limit is not None
        and len(records) >= inheritance_candidate_limit
    )
    # The window read, reported when it was full: the limit asks for one row more.
    inheritance_candidate_rows = (
        inheritance_candidate_limit - 1
        if inheritance_candidates_capped and inheritance_candidate_limit is not None
        else None
    )
    if inheritance_candidates_capped:
        records = records[: inheritance_candidate_limit - 1]
    filtered = [
        record
        for record in records
        if record.variant_id not in scope.excluded_review_variant_ids
        and (
            (not scope.include_review_filter_active)
            or record.variant_id in (scope.review_variant_ids or set())
        )
        and _small_record_matches(
            record,
            filters,
            scope.include_regions,
            scope.exclude_regions,
            scope.exclude_gene_regions,
            panel_constraints=scope.panel_constraints,
        )
    ]
    if filters.expanded_carrier_screening:
        filtered = _filter_expanded_carrier_screening(
            filtered,
            context.sample_rows,
            context.relationship_rows,
        )
    if track_mode:
        unfiltered_total = None
    else:
        unfiltered_total = small_variant_summary.total_variants if small_variant_summary else None
    if filters.inheritance:
        return await _small_variants_inheritance_page(
            session,
            context,
            filters.inheritance,
            filtered,
            page=page,
            page_size=page_size,
            track_mode=track_mode,
            candidates_capped=inheritance_candidates_capped,
            candidate_limit=inheritance_candidate_rows,
            unfiltered_total=unfiltered_total,
            small_variant_summary=small_variant_summary,
        )
    total = len(filtered)
    reported_total = min(total, _SMALL_COUNT_LIMIT)
    total_is_estimated = inheritance_candidates_capped or total >= _SMALL_COUNT_LIMIT
    if track_mode:
        page_records = _sample_small_track_records(filtered, page_size)
    else:
        skip = max(page - 1, 0) * page_size if page_size else 0
        page_records = filtered[skip: skip + page_size] if page_size else filtered[skip:]
    variants = [_small_variant_out(record, assembly_name=context.assembly_name) for record in page_records]
    await _hydrate_small_variant_outs(
        session,
        context=context,
        variants=variants,
    )
    return VariantPage(
        total=0 if track_mode else reported_total,
        total_is_estimated=total_is_estimated,
        unfiltered_total=unfiltered_total,
        unfiltered_total_is_estimated=False,
        count_limit=_SMALL_COUNT_LIMIT - 1,
        candidates_capped=inheritance_candidates_capped,
        candidate_limit=inheritance_candidate_rows,
        variants=variants,
        small_variant_summary=small_variant_summary,
    )


async def _small_variants_inheritance_page(
    session: AsyncSession,
    context: FamilyMetadataContext,
    inheritance: str,
    filtered: list[SmallVariantRecord],
    *,
    page: int,
    page_size: int,
    track_mode: bool,
    candidates_capped: bool,
    candidate_limit: int | None,
    unfiltered_total: int | None,
    small_variant_summary: SmallVariantSummaryOut | None,
) -> VariantPage:
    """The candidates that fit the inheritance mode, paged by item: a compound-het pair is
    one item, served as a group of its two variants."""
    affected_sample_names, unaffected_sample_names = _family_affected_unaffected_sample_names(context)
    inheritance_items = _inheritance_result_items(
        inheritance=inheritance,
        records=filtered,
        affected_samples=affected_sample_names,
        unaffected_samples=unaffected_sample_names,
        sample_rows=context.sample_rows,
        assembly_name=context.assembly_name,
        pedigree=_family_pedigree(context),
    )
    total = len(inheritance_items)
    reported_total = min(total, _SMALL_COUNT_LIMIT)
    total_is_estimated = candidates_capped or total >= _SMALL_COUNT_LIMIT
    skip = max(page - 1, 0) * page_size if page_size else 0
    page_items = inheritance_items[skip: skip + page_size] if page_size else inheritance_items[skip:]
    page_variant_groups: list[SmallVariantGroupOut] = []
    page_single_variants: list[VariantOut] = []
    group_variant_outs: list[VariantOut] = []
    for item_type, item_value in page_items:
        if item_type == "group":
            pair = item_value
            left_variant = _small_variant_out(pair.left, assembly_name=context.assembly_name)
            right_variant = _small_variant_out(pair.right, assembly_name=context.assembly_name)
            group_variant_outs.extend([left_variant, right_variant])
            page_variant_groups.append(
                SmallVariantGroupOut(
                    group_key=pair.pair_key,
                    gene=pair.gene,
                    gene_id=pair.gene_id,
                    variants=[left_variant, right_variant],
                    phase=pair.phase,
                    phase_evidence=pair.phase_evidence,
                )
            )
        else:
            page_single_variants.append(_small_variant_out(item_value, assembly_name=context.assembly_name))
    await _hydrate_small_variant_outs(
        session,
        context=context,
        variants=[*group_variant_outs, *page_single_variants],
    )
    for group in page_variant_groups:
        if len(group.variants) >= 2:
            group.review = _group_review_for_pair(group.variants[0], group.variants[1])
    return VariantPage(
        total=0 if track_mode else reported_total,
        total_is_estimated=total_is_estimated,
        unfiltered_total=unfiltered_total,
        unfiltered_total_is_estimated=False,
        count_limit=_SMALL_COUNT_LIMIT - 1,
        candidates_capped=candidates_capped,
        candidate_limit=candidate_limit if candidates_capped else None,
        variants=page_single_variants,
        variant_groups=page_variant_groups,
        small_variant_summary=small_variant_summary,
    )


async def get_family_small_variants_page(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    page: int,
    page_size: int,
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
    impact: list[str] | None = None,
    effect: list[str] | None = None,
    clinvar: list[str] | None = None,
    exclude_clinvar: list[str] | None = None,
    clinvar_overrides_frequency: bool = False,
    exclude_gene: str | None = None,
    exclude_intervals: str | None = None,
    rsid: str | None = None,
    hgvsc: str | None = None,
    hgvsp: str | None = None,
    canonical_only: bool = False,
    mane_only: bool = False,
    lof_only: bool = False,
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
    sift: str | None = None,
    polyphen: str | None = None,
    panel_id: str | None = None,
    sample_filters: list[str] | None = None,
    review_classifications: list[str] | None = None,
    review_tags: list[str] | None = None,
    exclude_review_tags: list[str] | None = None,
    has_notes: bool = False,
    overlap: bool = False,
    require_sv_second_hit: bool = False,
    prioritize: bool = False,
    track_mode: bool = False,
    track_result_limit: int | None = None,
    count_only: bool = False,
    max_page_size: int = MAX_VARIANT_PAGE_SIZE,
) -> VariantPage:
    # Defensive clamp for non-HTTP/internal callers; the routers also bound page_size.
    # Only the CSV export lifts it, to its own bounded cap (#512).
    page_size = max(0, min(page_size, max_page_size))
    # ...and clamp page: the routers declare it as an unbounded `int`, so a huge page would
    # otherwise force a deep-OFFSET scan+skip on the native/track_mode list path. Done
    # before filters is built so filters.page is bounded everywhere it flows. (#333)
    page = _clamp_small_variant_page(page, page_size)
    normalized_inheritance = _normalize_small_variant_inheritance(inheritance)
    filters = SmallVariantQueryFilters(
        page=page,
        page_size=page_size,
        chromosome=chr,
        start=start,
        end=end,
        intervals=intervals,
        inheritance=normalized_inheritance,
        expanded_carrier_screening=expanded_carrier_screening,
        phase_set=ps,
        variant_type=type,
        source=source,
        gene=gene,
        transcript=transcript,
        impact=impact or [],
        effect=effect or [],
        clinvar=clinvar or [],
        exclude_clinvar=exclude_clinvar or [],
        clinvar_overrides_frequency=clinvar_overrides_frequency,
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
        sample_filters=sample_filters or [],
        overlap=overlap,
        require_sv_second_hit=require_sv_second_hit,
    )
    if count_only:
        # Presence check only (family dashboard): probe the indexed family_guid
        # column with LIMIT 1 instead of the bounded filtered count + unfiltered
        # summary scan below. The dashboard only needs total > 0 to decide whether
        # to surface the small-variants workspace, so report a presence-bounded
        # total (0 or 1) and no rows.
        exists = await _family_has_small_variants(context)
        return VariantPage(total=1 if exists else 0, variants=[])
    if filters.require_sv_second_hit:
        # Resolve the family's SV-hit gene set once; every query path below reads it off
        # ``filters`` to intersect results with genes that also carry a structural variant.
        await _ensure_family_sv_gene_index(session, context)
        filters.sv_hit_genes = await get_sv_hit_genes(session, family_uuid=context.family_uuid)
    small_variant_summary = None if track_mode else await _fetch_small_variant_summary(context)
    scope = await _resolve_small_variant_scope(
        session,
        context,
        filters,
        review_classifications=review_classifications,
        review_tags=review_tags,
        exclude_review_tags=exclude_review_tags,
        has_notes=has_notes,
    )
    if scope is None:
        return VariantPage(total=0, variants=[], small_variant_summary=small_variant_summary)

    if prioritize and not track_mode:
        return await _prioritized_small_variants_page(
            session,
            context=context,
            filters=filters,
            page=page,
            page_size=page_size,
            panel_constraints=scope.panel_constraints,
            review_variant_ids=scope.review_variant_ids,
            excluded_review_variant_ids=scope.excluded_review_variant_ids,
            include_review_filter_active=scope.include_review_filter_active,
            include_regions=scope.include_regions,
            exclude_regions=scope.exclude_regions,
            exclude_gene_regions=scope.exclude_gene_regions,
            exclude_gene_terms=scope.exclude_gene_terms,
            small_variant_summary=small_variant_summary,
        )
    if track_mode and track_result_limit is not None:
        return await _small_variants_track_page(
            session,
            context,
            filters,
            scope,
            page=page,
            page_size=page_size,
            track_result_limit=track_result_limit,
        )
    if _can_use_small_native_page(filters, track_mode=track_mode):
        return await _small_variants_native_page(
            session,
            context,
            filters,
            scope,
            page=page,
            page_size=page_size,
            small_variant_summary=small_variant_summary,
        )
    return await _small_variants_candidate_page(
        session,
        context,
        filters,
        scope,
        page=page,
        page_size=page_size,
        track_mode=track_mode,
        small_variant_summary=small_variant_summary,
    )


# Hard cap on rows pulled into a single CSV export. Generous enough for any
# realistic filtered result set, but bounds memory/response size.
_MAX_SMALL_VARIANT_EXPORT_ROWS = 50_000


@dataclass(slots=True)
class VariantExport:
    """The rows of a CSV export and why, if at all, they are not the whole filtered result.

    A clinical export must never be silently partial (#512): the caller names the file
    and tells the user when ``truncated`` is set. ``truncated_reason`` is
    ``TRUNCATED_BY_ROW_LIMIT`` when the result was cut at ``limit`` rows, and
    ``TRUNCATED_BY_CANDIDATE_LIMIT`` when the search behind it read a capped candidate
    window (a match beyond it is missing however few rows the file has).
    """

    rows: list[VariantOut]
    truncated_reason: str | None
    limit: int

    @property
    def truncated(self) -> bool:
        return self.truncated_reason is not None


def _export_truncation(page: VariantPage, rows: Sequence[VariantOut], limit: int) -> str | None:
    """Why an export over ``page`` is incomplete, or None when it holds every match.

    The export's own row cap is not the only cut: a prioritised ranking covers only its
    candidate window (``ranking_truncated``), and compound-het / recessive pairing,
    expanded carrier screening and a Python-filtered SV search read a capped candidate
    window (``candidates_capped``). Either way a match can be missing from a file far
    below the row cap, so either marks the export truncated. The candidate reason wins:
    it says the file may lack matches anywhere, not only past row ``limit``.
    """

    if page.ranking_truncated or page.candidates_capped:
        return TRUNCATED_BY_CANDIDATE_LIMIT
    if len(rows) > limit:
        return TRUNCATED_BY_ROW_LIMIT
    return None


async def export_family_small_variants(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    limit: int = _MAX_SMALL_VARIANT_EXPORT_ROWS,
    prioritize: bool = False,
    **filters: Any,
) -> VariantExport:
    """Fetch up to ``limit`` filtered small variants for CSV export.

    Reuses :func:`get_family_small_variants_page` with the same filters as the
    table so the export always matches what the user sees, but requests a single
    large page instead of paginating. Compound-het pair groups are flattened in
    alongside single variants.

    The page function clamps page_size to MAX_VARIANT_PAGE_SIZE (10,000) for HTTP
    callers; before #512 that silently cut this export at 10,000 rows. The export lifts
    the clamp to its own cap and asks for one row more, so a larger result is reported
    as truncated rather than cut without a trace. An export is also truncated when the
    search behind it read a capped candidate window: a prioritised ranking, or the
    compound-het / recessive / carrier-screening candidate read (see
    :func:`_export_truncation`).
    """

    limit = max(1, min(limit, _MAX_SMALL_VARIANT_EXPORT_ROWS))
    page = await get_family_small_variants_page(
        session,
        context=context,
        page=1,
        page_size=limit + 1,
        prioritize=prioritize,
        track_mode=False,
        max_page_size=limit + 1,
        **filters,
    )
    rows: list[VariantOut] = []
    for group in page.variant_groups:
        rows.extend(group.variants)
    rows.extend(page.variants)
    return VariantExport(rows=rows[:limit], truncated_reason=_export_truncation(page, rows, limit), limit=limit)


_MAX_STRUCTURAL_VARIANT_EXPORT_ROWS = 50_000


async def export_family_structural_variants(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    limit: int = _MAX_STRUCTURAL_VARIANT_EXPORT_ROWS,
    prioritize: bool = False,
    **filters: Any,
) -> VariantExport:
    """Fetch up to ``limit`` filtered structural variants for CSV export.

    Reuses :func:`get_family_structural_variants_page` with the same filters as the
    table (so ``review_tag=report`` exports exactly the reported set), but requests a
    single large page instead of paginating. Lifts the page clamp and reports
    truncation like :func:`export_family_small_variants` (#512).
    """

    limit = max(1, min(limit, _MAX_STRUCTURAL_VARIANT_EXPORT_ROWS))
    page = await get_family_structural_variants_page(
        session,
        context=context,
        page=1,
        page_size=limit + 1,
        prioritize=prioritize,
        track_mode=False,
        max_page_size=limit + 1,
        **filters,
    )
    rows = list(page.variants)
    return VariantExport(rows=rows[:limit], truncated_reason=_export_truncation(page, rows, limit), limit=limit)


async def _prioritized_structural_variants_page(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    filters: StructuralVariantQueryFilters,
    page: int,
    page_size: int,
    selected_samples: Sequence[str],
) -> VariantPage:
    """Phenotype-aware ranking of structural variants (Exomiser-style).

    Mirrors ``_prioritized_small_variants_page``: fetch a candidate window, score each
    SV by event class + overlapped-gene constraint + rarity + segregation, blend with
    the best overlapped-gene HPO phenotype match, sort by combined score, then paginate.
    """
    include_regions: list[Region] = []
    panel_gene_terms: set[str] | None = None
    if filters.panel_id:
        panel_constraints = await _fetch_panel_constraints(
            session, filters.panel_id, assembly_id=context.assembly_id
        )
        if not panel_constraints.genes and not panel_constraints.regions:
            return VariantPage(total=0, variants=[], summary={})
        if panel_constraints.genes and len(panel_constraints.regions) > _PANEL_REGION_INLINE_LIMIT:
            panel_gene_terms = {gene.lower() for gene in panel_constraints.genes if gene}
        else:
            include_regions.extend(panel_constraints.regions)
            if not include_regions:
                return VariantPage(total=0, variants=[], summary={})
    if filters.gene:
        gene_regions = await _fetch_gene_regions(
            session, gene_query=filters.gene, assembly_id=context.assembly_id
        )
        if not gene_regions:
            return VariantPage(total=0, variants=[], summary={})
        include_regions.extend(gene_regions)

    review_variant_ids = await list_matching_structural_variant_review_ids(
        session,
        family_uuid=context.family_uuid,
        classifications=filters.review_classifications,
        tags=filters.review_tags,
        has_notes=filters.has_notes,
    )
    include_review_filter_active = bool(
        filters.review_classifications or filters.review_tags or filters.has_notes
    )
    if include_review_filter_active and not review_variant_ids:
        return VariantPage(total=0, variants=[], summary={})
    excluded_review_variant_ids = (
        await list_matching_structural_variant_review_ids(
            session,
            family_uuid=context.family_uuid,
            tags=filters.exclude_review_tags,
        )
        if filters.exclude_review_tags
        else set()
    )

    # Read a candidate window the size the unranked page already uses, with the gene and
    # panel regions pushed into SQL, and only cap once the filters have run. Capping the
    # fetch first decided which rows the filter was allowed to see: a gene-filtered
    # search returned nothing whenever that gene's SV fell outside the first 5,000 rows,
    # which reads as "no such SV" rather than "we only looked at part of the callset".
    records = await _fetch_structural_variant_rows(
        context,
        filters,
        limit=_SV_NON_NATIVE_STRUCTURAL_CANDIDATE_CAP + 1,
        include_regions=include_regions,
    )
    fetch_overflowed = len(records) > _SV_NON_NATIVE_STRUCTURAL_CANDIDATE_CAP
    if fetch_overflowed:
        records = records[:_SV_NON_NATIVE_STRUCTURAL_CANDIDATE_CAP]
    filtered = [
        record
        for record in records
        if record.variant_id not in excluded_review_variant_ids
        and ((not include_review_filter_active) or record.variant_id in review_variant_ids)
        and _structural_record_matches(
            record, filters, include_regions, selected_samples, panel_gene_terms=panel_gene_terms
        )
    ]
    if not filtered:
        # An overflowed read may hold a match beyond the window, even when none of the
        # window survived the filters: flag the empty ranking as truncated.
        return VariantPage(
            total=0,
            total_is_estimated=fetch_overflowed,
            ranking_truncated=fetch_overflowed,
            candidate_limit=_SV_NON_NATIVE_STRUCTURAL_CANDIDATE_CAP if fetch_overflowed else None,
            variants=[],
            summary={},
        )
    # The cap now bounds the ranking work over the filtered set, which is what it was
    # for; scoring every SV in a large callset is the expensive part.
    capped = fetch_overflowed or len(filtered) > _PRIORITIZE_CANDIDATE_LIMIT
    # The window that cut the ranking: the ranked set when more matched than it holds,
    # else the candidate read that overflowed.
    ranked_window = (
        _PRIORITIZE_CANDIDATE_LIMIT
        if len(filtered) > _PRIORITIZE_CANDIDATE_LIMIT
        else _SV_NON_NATIVE_STRUCTURAL_CANDIDATE_CAP
    )
    filtered = filtered[:_PRIORITIZE_CANDIDATE_LIMIT]

    affected_names, _unaffected = _family_affected_unaffected_sample_names(context)
    segregation_evaluated = bool(affected_names)
    patient_terms, term_labels = await _affected_present_hpo(session, context)

    # One phenotype-scoring pass over the union of every overlapped gene; each SV then
    # takes its best-matching overlapped gene.
    all_gene_symbols = {symbol for record in filtered for symbol in record.gene_symbols if symbol}
    phenotype_scores = (
        await score_genes_for_hpo(
            session, gene_symbols=all_gene_symbols, patient_hpo_ids=patient_terms
        )
        if patient_terms and all_gene_symbols
        else {}
    )

    scored_records: list[tuple[StructuralVariantRecord, VariantPriorityOut]] = []
    for record in filtered:
        annotation_extra = _structural_annotation_extra(record)
        gene_pli = annotation_extra.get("pli")
        gene_pli = float(gene_pli) if isinstance(gene_pli, (int, float)) else None

        best_gene: str | None = None
        best_gene_score = None
        for symbol in record.gene_symbols:
            candidate = phenotype_scores.get(symbol.upper()) if symbol else None
            if candidate and (best_gene_score is None or candidate.score > best_gene_score.score):
                best_gene_score = candidate
                best_gene = symbol
        phenotype_value = best_gene_score.score if best_gene_score else None

        scored = score_structural_variant(
            sv_type=record.sv_type,
            gene_count=len([symbol for symbol in record.gene_symbols if symbol]),
            control_af=_coerce_float(annotation_extra.get("control_af")),
            population_af=_coerce_float(annotation_extra.get("population_af")),
            segregation_modes=_structural_segregation_modes(annotation_extra),
            segregation_evaluated=segregation_evaluated,
            phenotype_score=phenotype_value,
            gene_pli=gene_pli,
        )
        phenotype_matches = (
            [
                MonarchPhenotypeMatchOut(
                    hpo_id=match["hpo_id"], label=term_labels.get(match["hpo_id"])
                )
                for match in best_gene_score.matched
            ]
            if best_gene_score
            else []
        )
        priority = VariantPriorityOut(
            combined_score=round(scored.combined_score, 4),
            variant_score=round(scored.variant_score, 4),
            pathogenicity_score=round(scored.pathogenicity, 4),
            frequency_score=round(scored.frequency, 4),
            segregation_weight=round(scored.segregation_weight, 4),
            phenotype_score=round(phenotype_value, 4) if phenotype_value is not None else None,
            segregation_modes=scored.segregation_modes,
            phenotype_gene=best_gene if best_gene_score else None,
            phenotype_matches=phenotype_matches,
        )
        scored_records.append((record, priority))

    scored_records.sort(
        key=lambda item: (
            item[1].combined_score,
            item[1].variant_score,
            # Stable, fully-determined final tiebreaker (see small-variant sort):
            # equal-score SVs are ordered by variant id for reproducible ranks.
            item[0].variant_id,
        ),
        reverse=True,
    )
    for index, (_record, priority) in enumerate(scored_records, start=1):
        priority.rank = index

    summary: dict[str, dict[str, int]] = {}
    for record, _priority in scored_records:
        bucket = summary.setdefault(record.sv_type or "", {})
        source_key = record.source or ""
        bucket[source_key] = bucket.get(source_key, 0) + 1

    total = len(scored_records)
    skip = max(page - 1, 0) * page_size if page_size else 0
    page_items = scored_records[skip : skip + page_size] if page_size else scored_records[skip:]
    page_records = [record for record, _priority in page_items]
    review_map = await get_structural_variant_review_map(
        session,
        family_uuid=context.family_uuid,
        variant_ids=[record.variant_id for record in page_records],
    )
    cytoband_map = await _fetch_structural_cytoband_map(
        session, assembly_id=context.assembly_id, records=page_records
    )
    variants = []
    for record, priority in page_items:
        variant = _structural_variant_out(
            record,
            selected_samples,
            review_map.get(record.variant_id),
            cytoband_map.get(record.variant_id),
        )
        variant.priority = priority
        variants.append(variant)

    return VariantPage(
        total=total,
        total_is_estimated=capped,
        count_limit=_PRIORITIZE_CANDIDATE_LIMIT if capped else None,
        ranking_truncated=capped,
        candidate_limit=ranked_window if capped else None,
        variants=variants,
        summary=summary,
    )


async def get_family_structural_variants_page(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    page: int,
    page_size: int,
    chr: str | None = None,
    start: int | None = None,
    end: int | None = None,
    length: int | None = None,
    min_length: int | None = None,
    type: str | None = None,
    source: str | None = None,
    sample_filters: list[str] | None = None,
    samples: list[str] | None = None,
    remote_chr: str | None = None,
    remote_start: int | None = None,
    gene: str | None = None,
    panel_id: str | None = None,
    inheritance: str | None = None,
    phenotype: str | None = None,
    hpo: str | None = None,
    moi: str | None = None,
    gencc_support: str | None = None,
    region_flags: list[str] | None = None,
    max_control_af: float | None = None,
    max_population_af: float | None = None,
    min_pli: float | None = None,
    review_classifications: list[str] | None = None,
    review_tags: list[str] | None = None,
    exclude_review_tags: list[str] | None = None,
    has_notes: bool = False,
    overlap: bool = False,
    prioritize: bool = False,
    track_mode: bool = False,
    count_only: bool = False,
    max_page_size: int = MAX_VARIANT_PAGE_SIZE,
) -> VariantPage:
    # Defensive clamp for non-HTTP/internal callers; the routers also bound page_size.
    page_size = max(0, min(page_size, max_page_size))
    filters = StructuralVariantQueryFilters(
        page=page,
        page_size=page_size,
        chromosome=chr,
        start=start,
        end=end,
        length=length,
        min_length=min_length,
        variant_type=type,
        source=source,
        sample_filters=sample_filters or [],
        selected_samples=samples or [],
        remote_chr=remote_chr,
        remote_start=remote_start,
        gene=gene,
        panel_id=panel_id,
        inheritance=inheritance,
        phenotype=phenotype,
        hpo=hpo,
        moi=moi,
        gencc_support=gencc_support,
        region_flags=region_flags or [],
        max_control_af=max_control_af,
        max_population_af=max_population_af,
        min_pli=min_pli,
        review_classifications=review_classifications or [],
        review_tags=review_tags or [],
        exclude_review_tags=exclude_review_tags or [],
        has_notes=has_notes,
        overlap=overlap,
    )
    selected_samples = _selected_structural_samples(context, filters.selected_samples)
    if selected_samples is None:
        return VariantPage(total=0, variants=[], summary={})
    if count_only:
        # Presence check only (family dashboard): probe the indexed family_guid
        # column with LIMIT 1 instead of the structural summary GROUP BY scan.
        # The dashboard only needs total > 0 to decide whether to surface the
        # structural-variants workspace.
        exists = await _family_has_structural_variants(context)
        return VariantPage(total=1 if exists else 0, variants=[], summary={})
    if prioritize and not track_mode:
        return await _prioritized_structural_variants_page(
            session,
            context=context,
            filters=filters,
            page=page,
            page_size=page_size,
            selected_samples=selected_samples,
        )
    if _can_use_structural_native_page(filters, track_mode=track_mode):
        total, summary = await _fetch_structural_variant_summary(context, filters)
        if total == 0:
            return VariantPage(total=0, variants=[], summary={})
        page_records = await _fetch_structural_variant_rows(
            context,
            filters,
            limit=page_size,
            offset=_page_offset(page, page_size),
        )
        review_map = await get_structural_variant_review_map(
            session,
            family_uuid=context.family_uuid,
            variant_ids=[record.variant_id for record in page_records],
        )
        cytoband_map = await _fetch_structural_cytoband_map(
            session,
            assembly_id=context.assembly_id,
            records=page_records,
        )
        variants = [
            _structural_variant_out(
                record,
                selected_samples,
                review_map.get(record.variant_id),
                cytoband_map.get(record.variant_id),
            )
            for record in page_records
        ]
        return VariantPage(total=total, variants=variants, summary=summary)

    include_regions: list[Region] = []
    panel_gene_terms: set[str] | None = None
    if filters.panel_id:
        panel_constraints = await _fetch_panel_constraints(
            session, filters.panel_id, assembly_id=context.assembly_id
        )
        if not panel_constraints.genes and not panel_constraints.regions:
            return VariantPage(total=0, variants=[], summary={})
        if panel_constraints.genes and len(panel_constraints.regions) > _PANEL_REGION_INLINE_LIMIT:
            # Big gene panel: match SVs by gene symbol (fast) instead of overlapping each
            # against thousands of regions.
            panel_gene_terms = {gene.lower() for gene in panel_constraints.genes if gene}
        else:
            include_regions.extend(panel_constraints.regions)
            if not include_regions:
                return VariantPage(total=0, variants=[], summary={})
    if filters.gene:
        gene_regions = await _fetch_gene_regions(
            session,
            gene_query=filters.gene,
            assembly_id=context.assembly_id,
        )
        if not gene_regions:
            return VariantPage(total=0, variants=[], summary={})
        include_regions.extend(gene_regions)
    review_variant_ids = await list_matching_structural_variant_review_ids(
        session,
        family_uuid=context.family_uuid,
        classifications=filters.review_classifications,
        tags=filters.review_tags,
        has_notes=filters.has_notes,
    )
    include_review_filter_active = bool(
        filters.review_classifications or filters.review_tags or filters.has_notes
    )
    if include_review_filter_active and not review_variant_ids:
        return VariantPage(total=0, variants=[], summary={})
    excluded_review_variant_ids = (
        await list_matching_structural_variant_review_ids(
            session,
            family_uuid=context.family_uuid,
            tags=filters.exclude_review_tags,
        )
        if filters.exclude_review_tags
        else set()
    )
    records = await _fetch_structural_variant_rows(
        context,
        filters,
        limit=_SV_NON_NATIVE_STRUCTURAL_CANDIDATE_CAP + 1,
        track_mode=track_mode,
        # Same reason as the ranked path: narrowing in SQL means the row cap cannot
        # decide which rows the gene/panel filter sees.
        include_regions=include_regions,
        # A track draws one sample: the cap should count that sample's SVs (#585).
        call_sample_names=selected_samples if track_mode and filters.selected_samples else (),
    )
    total_is_estimated = len(records) > _SV_NON_NATIVE_STRUCTURAL_CANDIDATE_CAP
    if total_is_estimated:
        records = records[:_SV_NON_NATIVE_STRUCTURAL_CANDIDATE_CAP]
    filtered = [
        record
        for record in records
        if record.variant_id not in excluded_review_variant_ids
        and ((not include_review_filter_active) or record.variant_id in review_variant_ids)
        and _structural_record_matches(
            record, filters, include_regions, selected_samples, panel_gene_terms=panel_gene_terms
        )
    ]
    summary: dict[str, dict[str, int]] = {}
    for record in filtered:
        summary.setdefault(record.sv_type or "", {})
        source_key = record.source or ""
        summary[record.sv_type or ""][source_key] = summary[record.sv_type or ""].get(source_key, 0) + 1
    total = len(filtered)
    skip = max(page - 1, 0) * page_size if page_size else 0
    page_records = filtered[skip: skip + page_size] if page_size else filtered[skip:]
    # The genome track shows neither review status nor cytoband, so skip those two
    # Postgres round-trips (over thousands of variant ids) when track_mode is on.
    review_map = (
        {}
        if track_mode
        else await get_structural_variant_review_map(
            session,
            family_uuid=context.family_uuid,
            variant_ids=[record.variant_id for record in page_records],
        )
    )
    cytoband_map = (
        {}
        if track_mode
        else await _fetch_structural_cytoband_map(
            session,
            assembly_id=context.assembly_id,
            records=page_records,
        )
    )
    variants = [
        _structural_variant_out(
            record,
            selected_samples,
            review_map.get(record.variant_id),
            cytoband_map.get(record.variant_id),
            track_mode=track_mode,
        )
        for record in page_records
    ]
    # A track gets the total and the cap as well: without them, a view holding more SVs
    # than one page, or than the cap, looked complete (#585).
    return VariantPage(
        total=total,
        total_is_estimated=total_is_estimated,
        count_limit=_SV_NON_NATIVE_STRUCTURAL_CANDIDATE_CAP if total_is_estimated else None,
        # total_is_estimated here means exactly that the candidate read overflowed.
        candidates_capped=total_is_estimated,
        candidate_limit=_SV_NON_NATIVE_STRUCTURAL_CANDIDATE_CAP if total_is_estimated else None,
        variants=variants,
        summary=None if track_mode else summary,
    )


async def get_family_compound_het_candidates(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    variant_id: str,
    limit: int = 50,
) -> VariantPage:
    # Compound-het partners are always within the source variant's gene, so look
    # up the source variant first to learn its gene, then scope the partner scan
    # to that gene instead of pulling the whole family's small-variant set. The
    # gene filter matches a superset of records sharing the source's primary gene
    # key, and _compound_het_partner_map below recomputes the exact partners, so
    # the result is identical to scanning every variant.
    source_matches = await _fetch_small_variant_rows(
        context,
        SmallVariantQueryFilters(page=1, page_size=1),
        include_variant_ids=[variant_id],
        limit=1,
    )
    source_record = next(
        (record for record in source_matches if record.variant_id == variant_id), None
    )
    if source_record is None:
        return VariantPage(total=0, variants=[])
    source_gene, _source_gene_id = _primary_gene_keys(source_record)
    if source_gene:
        # Gene-scoped scan: a single gene holds at most a few hundred variants, so cap
        # it defensively well above any realistic count (the cap never bites in
        # practice; partners are recomputed identically below it).
        records = await _fetch_small_variant_rows(
            context,
            SmallVariantQueryFilters(page=1, page_size=1, gene=source_gene),
            limit=_SMALL_INHERITANCE_MAX_CANDIDATE_ROWS + 1,
        )
    else:
        # No gene SYMBOL to scope by (the partner key is a bare gene_id, which the fetch
        # cannot filter on), so the whole-family scan is required to find the same-gene_id
        # partner. It is intentionally left UNBOUNDED: a blind row cap here (rows are
        # ORDER BY genomic position) could silently drop a genuine partner that sorts
        # beyond the cap, producing a missed-partner clinical result. Bounding this safely
        # needs gene_id-scoped fetching (a follow-up), not a blind LIMIT.
        records = await _fetch_small_variant_rows(
            context, SmallVariantQueryFilters(page=1, page_size=1)
        )
    affected_sample_names, unaffected_sample_names = _family_affected_unaffected_sample_names(context)
    partner_ids = _compound_het_partner_map(
        records,
        affected_samples=affected_sample_names,
        unaffected_samples=unaffected_sample_names,
        pedigree=_family_pedigree(context),
    ).get(source_record.variant_id, set())
    if not partner_ids:
        return VariantPage(total=0, variants=[])
    candidates = [
        record
        for record in records
        if record.variant_id in partner_ids and record.variant_id != source_record.variant_id
    ][:limit]
    variants = [_small_variant_out(record, assembly_name=context.assembly_name) for record in candidates]
    await _hydrate_small_variant_outs(
        session,
        context=context,
        variants=variants,
    )
    return VariantPage(total=len(variants), variants=variants)
