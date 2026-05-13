from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.clickhouse import execute_clickhouse
from ..core.config import settings
from ..core.sql import uuid_list_bindparam, uuid_values
from .data_scope import chromosome_aliases, normalize_chromosome
from .family_metadata_context import SampleMetadataContext

VALID_INTERVAL_TRACK_TYPES = {"coverage", "apcad", "segments", "haplotype"}
_VALID_CLICKHOUSE_SEGMENT = re.compile(r"^[A-Za-z0-9._/-]+$")


def _require_clickhouse_identifier(value: str) -> str:
    if not _VALID_CLICKHOUSE_SEGMENT.fullmatch(value):
        raise ValueError("Assembly name is invalid")
    return value


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


def _interval_row_tuple(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(row["family_id"]),
        str(row["sample_id"]),
        str(row.get("track_type") or ""),
        str(row.get("source") or "web"),
        _metadata_filename(row),
        normalize_chromosome(str(row["chr"])),
        int(row["start"]),
        int(row["end"]),
        None if row.get("record_id") in (None, "") else str(row.get("record_id")),
        None if row.get("value") is None else float(row["value"]),
        None if row.get("origin") in (None, "") else str(row.get("origin")),
        None if row.get("hap1") in (None, "") else str(row.get("hap1")),
        None if row.get("hap2") in (None, "") else str(row.get("hap2")),
        None if row.get("ps") is None else int(row["ps"]),
        str(row.get("metadata_json") or "{}"),
    )


async def insert_interval_track_rows(assembly_name: str, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        return
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
        data=[_interval_row_tuple(row) for row in rows],
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
    start: int | None = None,
    end: int | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    await ensure_clickhouse_interval_table(assembly_name)
    clauses = ["track_type = %(track_type)s"]
    params: dict[str, Any] = {"track_type": track_type}
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
    if start is not None and end is not None:
        clauses.append("start <= %(window_end)s AND end >= %(window_start)s")
        params["window_start"] = int(start)
        params["window_end"] = int(end)
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
            ps,
            metadata_json
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
            metadata_json,
        ) = row
        result.append(
            {
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
                "metadata_json": metadata_json,
            }
        )
    return result


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
    if track_type == "haplotype" and start is not None and end is not None:
        clauses.append("start <= %(window_end)s AND end >= %(window_start)s")
        params["window_start"] = int(start)
        params["window_end"] = int(end)
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


async def count_interval_track_source_rows(
    session: AsyncSession,
    *,
    sample_uuid: str | None = None,
    family_uuid: str | None = None,
    track_type: str | None = None,
    source: str | None = None,
    filename: str | None = None,
) -> int:
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
    result = await session.execute(
        text(
            """
            SELECT family_id::text AS family_uuid, track_type, COALESCE(SUM(row_count), 0) AS count
            FROM sample_interval_track_sources
            WHERE family_id IN :family_uuids
            GROUP BY family_id, track_type
            """
        ).bindparams(uuid_list_bindparam("family_uuids")),
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
    result = await session.execute(
        text(
            """
            SELECT sample_id::text AS sample_uuid, track_type, COALESCE(SUM(row_count), 0) AS count
            FROM sample_interval_track_sources
            WHERE sample_id IN :sample_uuids
            GROUP BY sample_id, track_type
            """
        ).bindparams(uuid_list_bindparam("sample_uuids")),
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
