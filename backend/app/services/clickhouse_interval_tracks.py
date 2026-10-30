from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.clickhouse import execute_clickhouse
from ..core.config import settings
from ..core.sql import uuid_list_bindparam, uuid_values
from .clickhouse_column_bounds import UINT64, column_int
from .clickhouse_variant_ids import _require_clickhouse_identifier
from .coverage_content import COVERAGE_CONTENT_KEY
from .data_scope import chromosome_aliases, normalize_chromosome
from .family_metadata_context import SampleMetadataContext

# Memoize which interval tables have been ensured this process so the DDL runs
# once per assembly instead of before every read/presence call (mirrors
# clickhouse_variant_storage._ensured_variant_table_assemblies).
_ensured_interval_table_assemblies: set[str] = set()
_ensure_interval_table_lock = asyncio.Lock()

logger = logging.getLogger(__name__)


def _interval_table_name(assembly_name: str) -> str:
    dataset = _require_clickhouse_identifier(assembly_name)
    return f"{settings.clickhouse_database}.`{dataset}/INTERVAL/entries`"


async def _execute(
    query: str,
    params: dict[str, Any] | None = None,
    data: Sequence[tuple[Any, ...]] | None = None,
) -> Any:
    if data is not None:
        if not data:
            return None
        return await execute_clickhouse(query, list(data))
    return await execute_clickhouse(query, params or {})


async def ensure_clickhouse_interval_table(assembly_name: str) -> None:
    table_name = _interval_table_name(assembly_name)
    if table_name in _ensured_interval_table_assemblies:
        return
    async with _ensure_interval_table_lock:
        if table_name in _ensured_interval_table_assemblies:
            return
        await _ensure_clickhouse_interval_table_ddl(table_name)
        _ensured_interval_table_assemblies.add(table_name)


async def _ensure_clickhouse_interval_table_ddl(table_name: str) -> None:
    await _execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name}
        (
            `family_guid` String,
            `sample_guid` String,
            `track_type` LowCardinality(String),
            `source` LowCardinality(String),
            `filename` String,
            `chrom` LowCardinality(String),
            `start` UInt64,
            `end` UInt64,
            `record_id` Nullable(String),
            `value` Nullable(Float64),
            `origin` Nullable(String),
            `hap1` Nullable(String),
            `hap2` Nullable(String),
            `ps` Nullable(UInt64),
            `metadata_json` String,
            `uploaded_at` DateTime DEFAULT now()
        )
        ENGINE = MergeTree
        PARTITION BY track_type
        ORDER BY (family_guid, sample_guid, track_type, chrom, start, end, source)
        """
    )


def _metadata_filename(row: dict[str, Any]) -> str:
    filename = str(row.get("filename") or "").strip()
    if filename:
        return filename
    try:
        metadata = json.loads(str(row.get("metadata_json") or "{}"))
    except json.JSONDecodeError:
        return ""
    return str(metadata.get("filename") or "")


def _interval_row_tuple(row: dict[str, Any]) -> tuple[Any, ...] | None:
    """The row as the table stores it, or None when the ``UInt64`` columns cannot hold its
    start or end. Its other values are bound to their columns (SAFE-30): a phase set no
    ``UInt64`` holds is stored as missing."""
    start = column_int(row["start"], UINT64)
    end = column_int(row["end"], UINT64)
    if start is None or end is None:
        return None
    return (
        str(row["family_id"]),
        str(row["sample_id"]),
        str(row.get("track_type") or ""),
        str(row.get("source") or "web"),
        _metadata_filename(row),
        normalize_chromosome(str(row["chr"])),
        start,
        end,
        None if row.get("record_id") in (None, "") else str(row.get("record_id")),
        None if row.get("value") is None else float(row["value"]),
        None if row.get("origin") in (None, "") else str(row.get("origin")),
        None if row.get("hap1") in (None, "") else str(row.get("hap1")),
        None if row.get("hap2") in (None, "") else str(row.get("hap2")),
        column_int(row.get("ps"), UINT64),
        str(row.get("metadata_json") or "{}"),
    )


async def insert_interval_track_rows(assembly_name: str, rows: Sequence[dict[str, Any]]) -> None:
    """Write the track rows. A row whose start or end no column holds is left out: the
    loaders that read files leave such a row out as malformed, and count it, before they
    get here, so this only guards one that did not."""
    if not rows:
        return
    stored = [row for row in map(_interval_row_tuple, rows) if row is not None]
    if len(stored) < len(rows):
        logger.warning(
            "Left out %d interval-track row(s) whose start or end no ClickHouse column holds",
            len(rows) - len(stored),
        )
    await ensure_clickhouse_interval_table(assembly_name)
    await _execute(
        f"""
        INSERT INTO {_interval_table_name(assembly_name)} (
            family_guid,
            sample_guid,
            track_type,
            source,
            filename,
            chrom,
            start,
            end,
            record_id,
            value,
            origin,
            hap1,
            hap2,
            ps,
            metadata_json
        ) VALUES
        """,
        data=stored,
    )


async def delete_interval_tracks(
    assembly_name: str,
    *,
    family_uuid: str | None = None,
    sample_uuid: str | None = None,
    track_type: str | None = None,
    source: str | None = None,
    filename: str | None = None,
) -> None:
    await ensure_clickhouse_interval_table(assembly_name)
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if family_uuid is not None:
        clauses.append("family_guid = %(family_uuid)s")
        params["family_uuid"] = str(family_uuid)
    if sample_uuid is not None:
        clauses.append("sample_guid = %(sample_uuid)s")
        params["sample_uuid"] = str(sample_uuid)
    if track_type is not None:
        clauses.append("track_type = %(track_type)s")
        params["track_type"] = track_type
    if source is not None:
        clauses.append("source = %(source)s")
        params["source"] = source
    if filename is not None:
        clauses.append("(filename = %(filename)s OR JSONExtractString(metadata_json, 'filename') = %(filename)s)")
        params["filename"] = filename
    if not clauses:
        raise ValueError("At least one interval-track delete filter is required")
    await _execute(
        f"""
        ALTER TABLE {_interval_table_name(assembly_name)}
        DELETE WHERE {' AND '.join(clauses)}
        SETTINGS mutations_sync = 1
        """,
        params,
    )


def _chrom_values(chromosomes: Sequence[str]) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for chrom in chromosomes:
        for candidate in chromosome_aliases(chrom):
            normalized = normalize_chromosome(candidate)
            if normalized not in seen:
                seen.add(normalized)
                values.append(normalized)
    return values


async def fetch_interval_track_rows(
    assembly_name: str,
    *,
    sample_uuid: str | None = None,
    family_uuid: str | None = None,
    sample_uuids: Sequence[str] | None = None,
    track_type: str,
    chromosomes: Sequence[str],
    origins: Sequence[str] | None = None,
    source: str | None = None,
    start: int | None = None,
    end: int | None = None,
    limit: int | None = None,
    include_metadata: bool = False,
    record_ids: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    await ensure_clickhouse_interval_table(assembly_name)
    clauses = ["track_type = %(track_type)s"]
    params: dict[str, Any] = {"track_type": track_type}
    if source is not None:
        # Without this every caller's rows for a track type come back together. A
        # sample can carry HiFiCNV, WisecondorX and QDNAseq coverage at once, and
        # merging them is not a display quirk -- the windowed average downstream
        # would blend three independent measurements into one meaningless line.
        clauses.append("source = %(source)s")
        params["source"] = source
    if sample_uuid is not None:
        clauses.append("sample_guid = %(sample_uuid)s")
        params["sample_uuid"] = str(sample_uuid)
    if family_uuid is not None:
        clauses.append("family_guid = %(family_uuid)s")
        params["family_uuid"] = str(family_uuid)
    if sample_uuids:
        clauses.append("sample_guid IN %(sample_uuids)s")
        params["sample_uuids"] = tuple(str(value) for value in sample_uuids)
    chrom_values = _chrom_values(chromosomes)
    if chrom_values:
        clauses.append("chrom IN %(chromosomes)s")
        params["chromosomes"] = tuple(chrom_values)
    if origins:
        clauses.append("origin IN %(origins)s")
        params["origins"] = tuple(str(value) for value in origins)
    if record_ids:
        # Case-insensitive, as a gene symbol is typed: a per-target coverage table's
        # record_id is the target's gene.
        clauses.append("upper(record_id) IN %(record_ids)s")
        params["record_ids"] = tuple(str(value).upper() for value in record_ids)
    if start is not None and end is not None:
        clauses.append("start <= %(window_end)s AND end >= %(window_start)s")
        params["window_start"] = int(start)
        params["window_end"] = int(end)
    # `metadata_json` (per-row provenance: filename, line_no, …) is NOT selected unless
    # asked for. It is large (a JSON blob per row) and unused by every track reader but
    # the per-target coverage table's (whose rows keep their median and covered share
    # there), so streaming it for a whole-genome APCAD track meant moving megabytes of
    # dead weight per request — slow, and the prime trigger for stream corruption under
    # the genome view's concurrent fan-out.
    metadata_column = ", metadata_json" if include_metadata else ""
    query = f"""
        SELECT
            sample_guid,
            chrom AS chr,
            start,
            end,
            record_id,
            value,
            origin,
            hap1,
            hap2,
            ps{metadata_column}
        FROM {_interval_table_name(assembly_name)}
        WHERE {' AND '.join(clauses)}
        ORDER BY chrom, start
    """
    if limit is not None:
        query += " LIMIT %(limit)s"
        params["limit"] = int(limit)
    rows = await _execute(query, params)
    result: list[dict[str, Any]] = []
    for row in rows:
        (
            sample_guid,
            chrom,
            row_start,
            row_end,
            record_id,
            value,
            origin,
            hap1,
            hap2,
            ps,
        ) = row[:10]
        item = {
            "sample_uuid": sample_guid,
            "chr": chrom,
            "start": row_start,
            "end": row_end,
            "record_id": record_id,
            "value": value,
            "origin": origin,
            "hap1": hap1,
            "hap2": hap2,
            "ps": ps,
        }
        if include_metadata:
            item["metadata_json"] = row[10]
        result.append(item)
    return result


def _apcad_band_targets(het_count: int, homo_count: int, budget: int) -> tuple[int, int]:
    """How many het / homozygous APCAD points to keep for a ~``budget`` total.

    Reserves up to 40% of the budget for the homozygous bands so they stay visible
    even when het markers are plentiful; the rest goes to het (the phasing signal),
    so rare het — the autozygosity-break signal — is kept in full. Returns
    ``(het_target, homo_target)``.
    """
    budget = max(1, int(budget))
    homo_reserve = min(homo_count, budget * 2 // 5)
    het_target = min(het_count, budget - homo_reserve)
    homo_target = min(homo_count, budget - het_target)
    return het_target, homo_target


def _apcad_chromosome_budgets(site_counts: Mapping[str, int], budget: int) -> dict[str, int]:
    """Split a ~``budget`` APCAD point budget over the chromosomes in proportion to the
    sites each one holds, so every chromosome keeps its own density of points per Mb.

    One quality-ranked budget across the genome does not: joint calling gives a
    chromosome's markers higher ``qual`` wherever any sample of the cohort carries extra
    reads there (one embryo's trisomy raised a whole chromosome's ``qual`` for every
    embryo), and a genome-wide ranking then draws that chromosome denser than the others
    while its stored sites are not. A track that fits the budget keeps every site; else
    each chromosome gets its floor share and the points left over go to the largest
    remainders (in the order given on a tie), so the shares add up to ``budget`` exactly.
    A chromosome whose share rounds to nothing is left out.
    """
    budget = max(1, int(budget))
    counts = {chrom: int(count) for chrom, count in site_counts.items() if int(count) > 0}
    total = sum(counts.values())
    if total <= budget:
        return counts
    shares = {chrom: budget * count // total for chrom, count in counts.items()}
    remainders = {chrom: budget * count % total for chrom, count in counts.items()}
    left_over = budget - sum(shares.values())
    for chrom in sorted(counts, key=lambda name: remainders[name], reverse=True)[:left_over]:
        shares[chrom] += 1
    return {chrom: share for chrom, share in shares.items() if share > 0}


async def fetch_apcad_downsampled(
    assembly_name: str,
    *,
    sample_uuid: str,
    family_uuid: str | None = None,
    chromosomes: Sequence[str],
    origins: Sequence[str] | None = None,
    budget: int,
    start: int | None = None,
    end: int | None = None,
) -> list[dict[str, Any]]:
    """Heterozygous-preserving, quality-filtered downsample of an APCAD track,
    entirely server-side, to at most ~``budget`` points.

    APCAD is stored at SNV resolution (millions of markers/sample). Pulling them all
    into Python and thinning there moved megabytes and blocked the event loop for
    seconds. Instead ClickHouse does the selection:

    - Informative markers only: ``origin IN ('paternal','maternal')`` — these are the
      SNVs that distinguish the parental haplotypes (``und`` markers are not phasing-
      informative). The caller passes the origins; this keeps the informative set.
      ``origins`` is a *preference*, not a hard filter: a track with no parent-of-origin
      calls at all falls back to its unphased markers rather than rendering empty. That
      is the HiFiCNV minor-allele-fraction track — bigWig has nowhere to record a
      parental origin, so every one of its points is ``und``, and filtering them out
      would silently blank the whole track. Where phased markers do exist they still
      win, so a trio's APCAD track is unaffected.
    - Quality gate: keep VCF ``filter = PASS`` (plus markers with no recorded filter,
      which a source without VCF provenance, such as the bigWig track, never has); drop the low-quality
      VQSR-tranche markers. The per-marker ``qual`` score is also available in
      ``metadata_json`` if a stricter numeric threshold is ever wanted.
    - Per-chromosome, band-aware, quality-ranked budget: split the budget over the
      chromosomes in proportion to the sites each holds, so every chromosome keeps its
      own density; within a chromosome keep the heterozygous (BAF mid-band) markers —
      the phasing signal — up to its share, while reserving part of it for the
      homozygous bands so they stay visible; within each band keep the highest VCF
      ``qual`` markers (not a spatial sample), so the points shown are the most
      confident ones.
    """
    await ensure_clickhouse_interval_table(assembly_name)
    base_clauses = ["track_type = 'apcad'", "sample_guid = %(sample_uuid)s"]
    params: dict[str, Any] = {"sample_uuid": str(sample_uuid)}
    if family_uuid is not None:
        base_clauses.append("family_guid = %(family_uuid)s")
        params["family_uuid"] = str(family_uuid)
    chrom_values = _chrom_values(chromosomes)
    if chrom_values:
        base_clauses.append("chrom IN %(chromosomes)s")
        params["chromosomes"] = tuple(chrom_values)
    # The origin preference is applied per-band below, not here, so one counts query
    # can measure the track both with and without it.
    origin_clause = ""
    if origins:
        origin_clause = "origin IN %(origins)s"
        params["origins"] = tuple(str(value) for value in origins)
    if start is not None and end is not None:
        base_clauses.append("start <= %(window_end)s AND end >= %(window_start)s")
        params["window_start"] = int(start)
        params["window_end"] = int(end)
    # Quality gate: PASS, or no recorded filter (uploads without VCF provenance).
    base_clauses.append("JSONExtractString(metadata_json, 'filter') IN ('PASS', '')")
    where = " AND ".join(base_clauses)
    table = _interval_table_name(assembly_name)

    het_expr = "value >= 0.05 AND value <= 0.95"
    homo_expr = "value IS NOT NULL AND (value < 0.05 OR value > 0.95)"

    # Whether to fall back to unphased markers is a property of the *track*, not of the
    # window being drawn. Deciding it per window would be wrong in the direction that
    # matters: on a genuinely phased track a stretch with no informative markers
    # currently renders empty, and that emptiness is the autozygosity signal -- filling
    # it with `und` points would mask exactly what the view exists to show. So probe the
    # whole track once (LIMIT 1 on the primary key: family, sample, track_type).
    if origin_clause:
        probe_clauses = ["track_type = 'apcad'", "sample_guid = %(sample_uuid)s", origin_clause]
        if family_uuid is not None:
            probe_clauses.append("family_guid = %(family_uuid)s")
        probe = await _execute(
            f"SELECT 1 FROM {table} WHERE {' AND '.join(probe_clauses)} LIMIT 1",
            params,
        )
        if not probe:
            origin_clause = ""

    band_where = f"{where} AND ({origin_clause})" if origin_clause else where
    counts = await _execute(
        f"SELECT chrom, countIf({het_expr}) AS het, countIf({homo_expr}) AS homo "
        f"FROM {table} WHERE {band_where} GROUP BY chrom ORDER BY chrom",
        params,
    )
    band_counts = {str(chrom): (int(het), int(homo)) for chrom, het, homo in counts}
    # The budget is split over the chromosomes first, in proportion to their sites, and
    # only then over the bands and by quality within each chromosome: ranked across the
    # genome, a chromosome joint calling scored higher would be drawn denser than its
    # sites are (see `_apcad_chromosome_budgets`).
    chromosome_budgets = _apcad_chromosome_budgets(
        {chrom: het + homo for chrom, (het, homo) in band_counts.items()}, budget
    )
    if not chromosome_budgets:
        return []

    # Keep the highest-quality markers in each band (qual = per-marker VCF confidence)
    # rather than a spatial sample. One ranked, LIMITed subquery per chromosome and
    # band, unioned; the chromosome leads the sort key, so each reads only its own.
    qual_expr = "JSONExtractFloat(metadata_json, 'qual')"
    # Deterministic spatial tiebreaker. Ranking by quality alone is only a ranking
    # while there *is* a quality to rank by: a track whose markers carry no `qual`
    # -- HiFiCNV's minor-allele-fraction bigWig, which records a value per site and
    # nothing else -- extracts 0.0 for every row, making the ORDER BY a constant. The
    # LIMIT then keeps whatever ClickHouse read first, which in primary-key order is
    # the start of the chromosome: 2000 points landed in 2.1 Mb of chr1's 249 Mb and
    # the rest of the track was blank.
    #
    # Hashing the position spreads those ties uniformly across the requested range.
    # Where `qual` does exist it still decides the ranking outright and this only
    # makes the previously arbitrary tie order reproducible.
    order_expr = f"{qual_expr} DESC, cityHash64(chrom, start)"

    def _band_query(chrom_param: str, band_expr: str, target: int) -> str:
        return (
            f"SELECT chrom AS chr, start, end, value, origin FROM {table} "
            f"WHERE {band_where} AND chrom = %({chrom_param})s AND ({band_expr}) "
            f"ORDER BY {order_expr} LIMIT {int(target)}"
        )

    subqueries: list[str] = []
    for index, (chrom, chrom_budget) in enumerate(chromosome_budgets.items()):
        chrom_param = f"apcad_chrom_{index}"
        params[chrom_param] = chrom
        het_target, homo_target = _apcad_band_targets(*band_counts[chrom], chrom_budget)
        if het_target > 0:
            subqueries.append(_band_query(chrom_param, het_expr, het_target))
        if homo_target > 0:
            subqueries.append(_band_query(chrom_param, homo_expr, homo_target))
    if not subqueries:
        return []

    rows = await _execute(" UNION ALL ".join(subqueries), params)
    return [
        {
            "chr": chrom,
            "start": int(row_start),
            "end": int(row_end),
            "value": None if value is None else float(value),
            "origin": origin or "und",
        }
        for (chrom, row_start, row_end, value, origin) in rows
    ]


def _samples_track_scope(
    *,
    family_uuid: str,
    sample_uuid_to_name: dict[str, str],
    track_type: str,
    chromosomes: Sequence[str],
    start: int | None,
    end: int | None,
) -> tuple[list[str], dict[str, Any]]:
    """The conditions (and their bound values) that pick these samples' rows of one track
    type of the family, on ``chromosomes`` (all when none) and in the window when given."""
    clauses = [
        "family_guid = %(family_uuid)s",
        "sample_guid IN %(sample_uuids)s",
        "track_type = %(track_type)s",
    ]
    params: dict[str, Any] = {
        "family_uuid": family_uuid,
        "sample_uuids": tuple(sample_uuid_to_name),
        "track_type": track_type,
    }
    chrom_values = _chrom_values(chromosomes)
    if chrom_values:
        clauses.append("chrom IN %(chromosomes)s")
        params["chromosomes"] = tuple(chrom_values)
    if start is not None and end is not None:
        clauses.append("start <= %(window_end)s AND end >= %(window_start)s")
        params["window_start"] = int(start)
        params["window_end"] = int(end)
    return clauses, params


async def get_interval_track_sources_by_sample(
    assembly_name: str,
    *,
    family_uuid: str,
    sample_uuid_to_name: dict[str, str],
    track_type: str,
    chromosomes: Sequence[str],
    start: int | None = None,
    end: int | None = None,
) -> dict[str, list[str]]:
    """Sample name → the sources that actually have rows for ``track_type``.

    Presence alone ("this sample has coverage") is not enough once a sample can
    carry three callers' coverage at once: the view has to know *which* callers,
    so it can draw one track per caller rather than one track of everything
    averaged together.
    """

    if not sample_uuid_to_name:
        return {}
    await ensure_clickhouse_interval_table(assembly_name)
    clauses, params = _samples_track_scope(
        family_uuid=family_uuid,
        sample_uuid_to_name=sample_uuid_to_name,
        track_type=track_type,
        chromosomes=chromosomes,
        start=start,
        end=end,
    )
    rows = await _execute(
        f"""
        SELECT DISTINCT sample_guid, source
        FROM {_interval_table_name(assembly_name)}
        WHERE {' AND '.join(clauses)}
        """,
        params,
    )
    by_sample: dict[str, list[str]] = {}
    for sample_guid, source in rows:
        name = sample_uuid_to_name.get(str(sample_guid))
        if name is None or not source:
            continue
        by_sample.setdefault(name, []).append(str(source))
    return {name: sorted(set(sources)) for name, sources in by_sample.items()}


async def get_interval_track_presence_by_sample(
    assembly_name: str,
    *,
    family_uuid: str,
    sample_uuid_to_name: dict[str, str],
    track_type: str,
    chromosomes: Sequence[str],
    start: int | None = None,
    end: int | None = None,
) -> set[str]:
    if not sample_uuid_to_name:
        return set()
    await ensure_clickhouse_interval_table(assembly_name)
    clauses, params = _samples_track_scope(
        family_uuid=family_uuid,
        sample_uuid_to_name=sample_uuid_to_name,
        track_type=track_type,
        chromosomes=chromosomes,
        start=start,
        end=end,
    )
    rows = await _execute(
        f"""
        SELECT DISTINCT sample_guid
        FROM {_interval_table_name(assembly_name)}
        WHERE {' AND '.join(clauses)}
        """,
        params,
    )
    present = {str(row[0]) for row in rows}
    return {sample_uuid_to_name[sample_uuid] for sample_uuid in present if sample_uuid in sample_uuid_to_name}


async def get_interval_track_lineage_hash(
    assembly_name: str,
    *,
    family_uuid: str,
    track_type: str,
) -> str | None:
    """Return the ``metadata_json.lineage_hash`` stored on a family's precomputed
    lineage track, or None if the track is absent. One cheap query so the genome
    overview can confirm a precompute still matches the current pedigree before
    serving its colours (and otherwise fall back to the fast grey path)."""
    await ensure_clickhouse_interval_table(assembly_name)
    rows = await _execute(
        f"""
        SELECT JSONExtractString(metadata_json, 'lineage_hash') AS lineage_hash
        FROM {_interval_table_name(assembly_name)}
        WHERE family_guid = %(family_uuid)s AND track_type = %(track_type)s
        LIMIT 1
        """,
        {"family_uuid": str(family_uuid), "track_type": track_type},
    )
    if not rows:
        return None
    value = str(rows[0][0] or "").strip()
    return value or None


async def count_interval_track_rows(
    assembly_name: str,
    *,
    family_uuid: str,
    sample_uuid: str | None,
    track_type: str,
    source: str | None = None,
) -> int:
    """How many rows the interval table holds of one sample's ``track_type`` track, or with
    ``sample_uuid`` None of every sample's of the family, of ``source`` or of every source:
    what is stored. ``count_interval_track_source_rows`` reads instead what the loaders
    recorded in Postgres, which a failed overwrite's restore does not rewrite with these
    rows: one that fails part-way can leave the record of a track whose rows it deleted
    (SAFE-20)."""
    await ensure_clickhouse_interval_table(assembly_name)
    clauses = ["family_guid = %(family_uuid)s", "track_type = %(track_type)s"]
    params: dict[str, Any] = {"family_uuid": str(family_uuid), "track_type": track_type}
    if sample_uuid is not None:
        clauses.append("sample_guid = %(sample_uuid)s")
        params["sample_uuid"] = str(sample_uuid)
    if source is not None:
        clauses.append("source = %(source)s")
        params["source"] = source
    rows = await _execute(
        f"""
        SELECT count()
        FROM {_interval_table_name(assembly_name)}
        WHERE {' AND '.join(clauses)}
        """,
        params,
    )
    return int(rows[0][0]) if rows else 0


async def interval_track_rows_digest(
    assembly_name: str,
    *,
    family_uuid: str,
    track_type: str,
) -> str:
    """An order-independent digest of a family's ``track_type`` rows as stored: their number
    and the XOR of each row's hash over its sample, place, haplotypes, metadata and load time.
    It changes when a row is added, removed or written again, so the lineage precompute's
    fingerprint covers the haplotype blocks it was computed from (CLIN-66)."""
    await ensure_clickhouse_interval_table(assembly_name)
    rows = await _execute(
        f"""
        SELECT
            count(),
            groupBitXor(
                cityHash64(
                    sample_guid, chrom, start, `end`, ifNull(hap1, ''), ifNull(hap2, ''),
                    ifNull(ps, 0), metadata_json, toUnixTimestamp(uploaded_at)
                )
            )
        FROM {_interval_table_name(assembly_name)}
        WHERE family_guid = %(family_uuid)s AND track_type = %(track_type)s
        """,
        {"family_uuid": str(family_uuid), "track_type": track_type},
    )
    count, digest = rows[0] if rows else (0, 0)
    return f"{int(count)}:{int(digest)}"


def _track_source_filters(
    *,
    sample_uuid: str | None,
    family_uuid: str | None,
    track_type: str | None,
    source: str | None,
    filename: str | None,
) -> tuple[list[str], dict[str, Any]]:
    """The conditions (and their bound values) that pick ``sample_interval_track_sources``
    rows by whichever of these are given."""
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if sample_uuid is not None:
        clauses.append("sample_id = CAST(:sample_uuid AS uuid)")
        params["sample_uuid"] = sample_uuid
    if family_uuid is not None:
        clauses.append("family_id = CAST(:family_uuid AS uuid)")
        params["family_uuid"] = family_uuid
    if track_type is not None:
        clauses.append("track_type = :track_type")
        params["track_type"] = track_type
    if source is not None:
        clauses.append("source = :source")
        params["source"] = source
    if filename is not None:
        clauses.append("filename = :filename")
        params["filename"] = filename
    return clauses, params


async def count_interval_track_source_rows(
    session: AsyncSession,
    *,
    sample_uuid: str | None = None,
    family_uuid: str | None = None,
    track_type: str | None = None,
    source: str | None = None,
    filename: str | None = None,
) -> int:
    clauses, params = _track_source_filters(
        sample_uuid=sample_uuid,
        family_uuid=family_uuid,
        track_type=track_type,
        source=source,
        filename=filename,
    )
    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    result = await session.execute(
        text(
            f"""
            SELECT COALESCE(SUM(row_count), 0)
            FROM sample_interval_track_sources
            {where_clause}
            """
        ),
        params,
    )
    return int(result.scalar_one() or 0)


async def upsert_interval_track_source(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    track_type: str,
    source: str,
    filename: str,
    row_count: int,
    metadata: dict[str, Any] | None = None,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO sample_interval_track_sources (
                sample_id,
                family_id,
                assembly_id,
                track_type,
                source,
                filename,
                row_count,
                metadata,
                uploaded_at
            )
            VALUES (
                CAST(:sample_id AS uuid),
                CAST(:family_id AS uuid),
                CAST(NULLIF(:assembly_id, '') AS uuid),
                :track_type,
                :source,
                :filename,
                :row_count,
                CAST(:metadata_json AS jsonb),
                timezone('utc', now())
            )
            ON CONFLICT (sample_id, track_type, source, filename)
            DO UPDATE SET
                family_id = EXCLUDED.family_id,
                assembly_id = EXCLUDED.assembly_id,
                row_count = EXCLUDED.row_count,
                metadata = EXCLUDED.metadata,
                uploaded_at = EXCLUDED.uploaded_at
            """
        ),
        {
            "sample_id": sample_context.sample_uuid,
            "family_id": sample_context.family_uuid,
            "assembly_id": sample_context.assembly_id or "",
            "track_type": track_type,
            "source": source,
            "filename": filename,
            "row_count": int(row_count),
            "metadata_json": json.dumps(
                {
                    **(metadata or {}),
                    "registered_at": datetime.now(timezone.utc).isoformat(),
                }
            ),
        },
    )


async def delete_interval_track_sources(
    session: AsyncSession,
    *,
    sample_uuid: str | None = None,
    family_uuid: str | None = None,
    track_type: str | None = None,
    source: str | None = None,
    filename: str | None = None,
) -> int:
    existing = await count_interval_track_source_rows(
        session,
        sample_uuid=sample_uuid,
        family_uuid=family_uuid,
        track_type=track_type,
        source=source,
        filename=filename,
    )
    clauses, params = _track_source_filters(
        sample_uuid=sample_uuid,
        family_uuid=family_uuid,
        track_type=track_type,
        source=source,
        filename=filename,
    )
    if not clauses:
        raise ValueError("At least one interval-track source delete filter is required")
    await session.execute(
        text(
            f"""
            DELETE FROM sample_interval_track_sources
            WHERE {' AND '.join(clauses)}
            """
        ),
        params,
    )
    return existing


async def interval_counts_by_family(
    session: AsyncSession,
    family_uuids: Sequence[str],
    track_types: Sequence[str],
) -> dict[str, dict[str, int]]:
    if not family_uuids:
        return {}
    # One uuid[] parameter, not one per family (an expanding IN): asyncpg takes at most 32,767
    # per statement, and the data inventory counts every one (DATA-6).
    result = await session.execute(
        text(
            """
            SELECT family_id::text AS family_uuid, track_type, COALESCE(SUM(row_count), 0) AS count
            FROM sample_interval_track_sources
            WHERE family_id = ANY(CAST(:family_uuids AS uuid[]))
            GROUP BY family_id, track_type
            """
        ),
        {"family_uuids": uuid_values(list(family_uuids))},
    )
    counts: dict[str, dict[str, int]] = {
        family_uuid: {track_type: 0 for track_type in track_types}
        for family_uuid in family_uuids
    }
    for row in result.mappings().all():
        counts.setdefault(row["family_uuid"], {track_type: 0 for track_type in track_types})
        counts[row["family_uuid"]][row["track_type"]] = int(row["count"])
    return counts


async def interval_counts_by_sample(
    session: AsyncSession,
    sample_uuids: Sequence[str],
    track_types: Sequence[str],
) -> dict[str, dict[str, int]]:
    if not sample_uuids:
        return {}
    # One uuid[] parameter, not one per sample (an expanding IN): asyncpg takes at most 32,767
    # per statement, and the data inventory counts every one (DATA-6).
    result = await session.execute(
        text(
            """
            SELECT sample_id::text AS sample_uuid, track_type, COALESCE(SUM(row_count), 0) AS count
            FROM sample_interval_track_sources
            WHERE sample_id = ANY(CAST(:sample_uuids AS uuid[]))
            GROUP BY sample_id, track_type
            """
        ),
        {"sample_uuids": uuid_values(list(sample_uuids))},
    )
    counts: dict[str, dict[str, int]] = {
        sample_uuid: {track_type: 0 for track_type in track_types}
        for sample_uuid in sample_uuids
    }
    for row in result.mappings().all():
        counts.setdefault(row["sample_uuid"], {track_type: 0 for track_type in track_types})
        counts[row["sample_uuid"]][row["track_type"]] = int(row["count"])
    return counts


async def interval_track_normalizations_by_sample(
    session: AsyncSession,
    *,
    family_uuid: str,
    sample_uuid_to_name: dict[str, str],
    track_type: str,
) -> dict[str, dict[str, list[str]]]:
    """Sample name → source → the transforms its ``track_type`` records name.

    An import that stores a track's values otherwise than as its file holds them records
    the transform in ``metadata.normalization``: ``log2_ratio_to_autosomal_median`` on a
    HiFiCNV depth track, or ``none`` when that sample's depth file gave no usable
    autosomal median and its depths are stored as read. A source whose records name none
    is left out: its values are its file's. Each source's values are distinct and sorted,
    so two records that disagree are both named rather than one picked.
    """

    return await _interval_track_record_values_by_sample(
        session,
        family_uuid=family_uuid,
        sample_uuid_to_name=sample_uuid_to_name,
        track_type=track_type,
        metadata_key="normalization",
    )


async def interval_track_contents_by_sample(
    session: AsyncSession,
    *,
    family_uuid: str,
    sample_uuid_to_name: dict[str, str],
    track_type: str,
) -> dict[str, dict[str, list[str]]]:
    """Sample name → source → what its ``track_type`` records declare the values are
    (``metadata.content``, TRACE-9): a coverage BED's ``read_depth``, ``log2_ratio`` or
    ``unknown`` (``coverage_content``). A source whose records declare nothing is left out:
    a coverage BED stored before BEDs declared what they hold, and every other caller's
    track. Distinct and sorted, as ``interval_track_normalizations_by_sample``."""

    return await _interval_track_record_values_by_sample(
        session,
        family_uuid=family_uuid,
        sample_uuid_to_name=sample_uuid_to_name,
        track_type=track_type,
        metadata_key=COVERAGE_CONTENT_KEY,
    )


async def _interval_track_record_values_by_sample(
    session: AsyncSession,
    *,
    family_uuid: str,
    sample_uuid_to_name: dict[str, str],
    track_type: str,
    metadata_key: str,
) -> dict[str, dict[str, list[str]]]:
    """Sample name → source → the distinct values, sorted, that its ``track_type`` records
    hold under ``metadata_key`` in their metadata; a source whose records hold none is left
    out."""

    if not sample_uuid_to_name:
        return {}
    result = await session.execute(
        text(
            """
            SELECT DISTINCT
                sample_id::text AS sample_uuid,
                source,
                metadata ->> CAST(:metadata_key AS text) AS value
            FROM sample_interval_track_sources
            WHERE family_id = CAST(:family_uuid AS uuid)
              AND sample_id IN :sample_uuids
              AND track_type = :track_type
              AND metadata ->> CAST(:metadata_key AS text) IS NOT NULL
            """
        ).bindparams(uuid_list_bindparam("sample_uuids")),
        {
            "family_uuid": family_uuid,
            "sample_uuids": uuid_values(list(sample_uuid_to_name)),
            "track_type": track_type,
            "metadata_key": metadata_key,
        },
    )
    by_sample: dict[str, dict[str, set[str]]] = {}
    for row in result.mappings().all():
        name = sample_uuid_to_name.get(str(row["sample_uuid"]))
        if name is None:
            continue
        by_sample.setdefault(name, {}).setdefault(str(row["source"]), set()).add(str(row["value"]))
    return {
        name: {source: sorted(values) for source, values in sources.items()}
        for name, sources in by_sample.items()
    }
