from __future__ import annotations

from datetime import datetime, timezone
import gzip
import json
from typing import Any

from fastapi import HTTPException, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import HaplotypeResponse
from .clickhouse_interval_tracks import (
    count_interval_track_source_rows,
    delete_interval_track_sources,
    delete_interval_tracks,
    fetch_interval_track_rows,
    get_interval_track_presence_by_sample,
    insert_interval_track_rows,
    upsert_interval_track_source,
)
from .data_scope import normalize_chromosome
from .family_metadata_context import FamilyMetadataContext, SampleMetadataContext

VALID_BED_TYPES = {"coverage", "apcad", "segments"}


def validate_bed_type(bed_type: str, *, allow_haplotype: bool = False) -> None:
    valid_types = VALID_BED_TYPES | ({"haplotype"} if allow_haplotype else set())
    if bed_type not in valid_types:
        raise HTTPException(status_code=400, detail="Invalid bed type")


async def _decode_bed_upload(file: UploadFile) -> str:
    contents = await file.read()
    try:
        return contents.decode()
    except UnicodeDecodeError:
        try:
            return gzip.decompress(contents).decode()
        except OSError as exc:
            raise HTTPException(
                status_code=400,
                detail="BED file must be plain text or gzipped",
            ) from exc


def _upload_metadata(track_type: str, file: UploadFile) -> str:
    return json.dumps(
        {
            "track_type": track_type,
            "source": "web",
            "filename": file.filename,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
        }
    )


def _build_apcad_row(
    sample_context: SampleMetadataContext,
    parts: list[str],
    *,
    metadata_json: str,
) -> dict[str, Any] | None:
    if len(parts) < 6:
        return None
    chrom, start_str, end_str, record_id, value_str, origin_raw = parts[:6]
    try:
        start = int(start_str)
        end = int(end_str)
        value = float(value_str)
    except ValueError:
        return None
    origin = {
        "paternal": "paternal",
        "maternal": "maternal",
        "pat": "paternal",
        "mat": "maternal",
        "und": "und",
    }.get(origin_raw.lower(), "und")
    return {
        "sample_id": sample_context.sample_uuid,
        "family_id": sample_context.family_uuid,
        "assembly_id": sample_context.assembly_id,
        "track_type": "apcad",
        "source": "web",
        "chr": normalize_chromosome(chrom),
        "start": start,
        "end": end,
        "record_id": record_id or None,
        "value": value,
        "origin": origin,
        "metadata_json": metadata_json,
    }


def _build_numeric_row(
    sample_context: SampleMetadataContext,
    track_type: str,
    parts: list[str],
    *,
    metadata_json: str,
) -> dict[str, Any] | None:
    if len(parts) < 4:
        return None
    chrom, start_str, end_str = parts[:3]
    try:
        start = int(start_str)
        end = int(end_str)
    except ValueError:
        return None

    value = None
    for part in parts[3:]:
        try:
            value = float(part)
            break
        except ValueError:
            continue
    if value is None:
        return None

    return {
        "sample_id": sample_context.sample_uuid,
        "family_id": sample_context.family_uuid,
        "assembly_id": sample_context.assembly_id,
        "track_type": track_type,
        "source": "web",
        "chr": normalize_chromosome(chrom),
        "start": start,
        "end": end,
        "record_id": None,
        "value": value,
        "origin": None,
        "metadata_json": metadata_json,
    }


async def upload_bed_data(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    bed_type: str,
    file: UploadFile,
    overwrite: bool,
) -> dict[str, int]:
    validate_bed_type(bed_type)
    if not sample_context.assembly_name:
        raise HTTPException(
            status_code=400,
            detail="Could not resolve a single assembly for this sample",
        )
    existing_count = await count_interval_track_source_rows(
        session,
        sample_uuid=sample_context.sample_uuid,
        track_type=bed_type,
    )
    if existing_count and not overwrite:
        raise HTTPException(
            status_code=409,
            detail="BED data already exists for this sample",
        )
    if overwrite:
        await delete_interval_tracks(
            sample_context.assembly_name,
            sample_uuid=sample_context.sample_uuid,
            track_type=bed_type,
        )
        await delete_interval_track_sources(
            session,
            sample_uuid=sample_context.sample_uuid,
            track_type=bed_type,
        )

    text_value = await _decode_bed_upload(file)
    metadata_json = _upload_metadata(bed_type, file)
    rows: list[dict[str, Any]] = []
    for line in text_value.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.strip().split()
        row = (
            _build_apcad_row(sample_context, parts, metadata_json=metadata_json)
            if bed_type == "apcad"
            else _build_numeric_row(sample_context, bed_type, parts, metadata_json=metadata_json)
        )
        if row is not None:
            rows.append(row)

    if not rows:
        raise HTTPException(status_code=400, detail="No valid BED records found")

    await insert_interval_track_rows(sample_context.assembly_name, rows)
    await upsert_interval_track_source(
        session,
        sample_context=sample_context,
        track_type=bed_type,
        source="web",
        filename=file.filename or "",
        row_count=len(rows),
        metadata=json.loads(metadata_json),
    )
    await session.commit()
    return {"inserted": len(rows)}


def _bed_header(bed_type: str) -> str:
    if bed_type == "coverage":
        return "chr\tstart\tend\tid\tratio"
    if bed_type == "segments":
        return "chr\tstart\tend\tratio"
    return "chr\tstart\tend\tid\tratio\torigin"


def _serialize_bed_record(track_type: str, row: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "chr": row["chr"],
        "start": int(row["start"]),
        "end": int(row["end"]),
        "value": float(row["value"]) if row.get("value") is not None else None,
    }
    if track_type == "apcad":
        payload["origin"] = row.get("origin") or "und"
    return payload


def _bed_record_to_line(track_type: str, record: dict[str, Any]) -> str:
    if track_type == "coverage":
        return f"{record['chr']}\t{record['start']}\t{record['end']}\t.\t{record['value']}"
    if track_type == "segments":
        return f"{record['chr']}\t{record['start']}\t{record['end']}\t{record['value']}"
    return (
        f"{record['chr']}\t{record['start']}\t{record['end']}\t.\t{record['value']}\t"
        f"{record.get('origin', 'und')}"
    )


async def _fetch_raw_track_rows(
    session: AsyncSession,
    *,
    assembly_name: str | None,
    sample_uuid: str,
    track_type: str,
    chrom: str,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    _ = session
    if not assembly_name:
        return []
    return await fetch_interval_track_rows(
        assembly_name,
        sample_uuid=sample_uuid,
        track_type=track_type,
        chromosomes=[chrom],
        limit=limit,
    )


def _windowed_coverage_rows(rows: list[dict[str, Any]], window: int, limit: int) -> list[dict[str, Any]]:
    grouped: dict[int, list[float]] = {}
    for row in rows:
        bin_start = (int(row["start"]) // window) * window
        grouped.setdefault(bin_start, []).append(float(row["value"]))
    records = [
        {
            "chr": row_chr,
            "start": bin_start,
            "end": bin_start + window,
            "value": sum(values) / len(values),
        }
        for row_chr in {str(row["chr"]) for row in rows}
        for bin_start, values in sorted(grouped.items())
    ]
    return records[:limit]


def _windowed_apcad_rows(rows: list[dict[str, Any]], window: int, limit: int) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int], list[float]] = {}
    extremes: list[dict[str, Any]] = []
    chrom = str(rows[0]["chr"]) if rows else ""
    for row in rows:
        value = float(row["value"])
        origin = str(row.get("origin") or "und")
        if 0.05 <= value <= 0.95:
            bin_start = (int(row["start"]) // window) * window
            grouped.setdefault((origin, bin_start), []).append(value)
        else:
            extremes.append(
                {
                    "chr": chrom,
                    "start": int(row["start"]),
                    "end": int(row["end"]),
                    "value": value,
                    "origin": origin,
                }
            )
    binned = [
        {
            "chr": chrom,
            "start": bin_start,
            "end": bin_start + window,
            "value": sum(values) / len(values),
            "origin": origin,
        }
        for (origin, bin_start), values in sorted(grouped.items(), key=lambda item: (item[0][1], item[0][0]))
    ][:limit]
    return sorted([*binned, *extremes], key=lambda item: (item["start"], item.get("origin") or "und"))


async def _fetch_bed_records_for_chrom(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    bed_type: str,
    chrom: str,
    window: int | None,
    limit: int,
) -> list[dict[str, Any]]:
    chrom_clean = normalize_chromosome(chrom)
    if bed_type == "coverage" and window:
        rows = await _fetch_raw_track_rows(
            session,
            assembly_name=sample_context.assembly_name,
            sample_uuid=sample_context.sample_uuid,
            track_type=bed_type,
            chrom=chrom_clean,
        )
        return _windowed_coverage_rows(rows, window, limit)
    if bed_type == "apcad" and window:
        rows = await _fetch_raw_track_rows(
            session,
            assembly_name=sample_context.assembly_name,
            sample_uuid=sample_context.sample_uuid,
            track_type=bed_type,
            chrom=chrom_clean,
        )
        return _windowed_apcad_rows(rows, window, limit)
    rows = await _fetch_raw_track_rows(
        session,
        assembly_name=sample_context.assembly_name,
        sample_uuid=sample_context.sample_uuid,
        track_type=bed_type,
        chrom=chrom_clean,
        limit=limit,
    )
    return [_serialize_bed_record(bed_type, row) for row in rows]


async def fetch_bed_text(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    bed_type: str,
    chrom: str,
    window: int | None,
    limit: int,
) -> PlainTextResponse:
    validate_bed_type(bed_type)
    records = await _fetch_bed_records_for_chrom(
        session,
        sample_context=sample_context,
        bed_type=bed_type,
        chrom=chrom,
        window=window,
        limit=limit,
    )
    if not records:
        raise HTTPException(status_code=404, detail="No BED data found")
    return PlainTextResponse(
        "\n".join([_bed_header(bed_type), *[_bed_record_to_line(bed_type, item) for item in records]])
    )


async def fetch_bed_json(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    bed_type: str,
    chrom: str,
    window: int | None,
    limit: int,
) -> JSONResponse:
    validate_bed_type(bed_type)
    records = await _fetch_bed_records_for_chrom(
        session,
        sample_context=sample_context,
        bed_type=bed_type,
        chrom=chrom,
        window=window,
        limit=limit,
    )
    if not records:
        raise HTTPException(status_code=404, detail="No BED data found")
    return JSONResponse({"bed_type": bed_type, "items": records})


async def fetch_bed_batch_text(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    bed_type: str,
    chroms: list[str],
    window: int | None,
    limit: int,
) -> PlainTextResponse:
    validate_bed_type(bed_type)
    if not chroms:
        raise HTTPException(status_code=400, detail="At least one chromosome is required")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for chrom in chroms:
        chrom_clean = normalize_chromosome(chrom)
        if chrom_clean in seen:
            continue
        seen.add(chrom_clean)
        records.extend(
            await _fetch_bed_records_for_chrom(
                session,
                sample_context=sample_context,
                bed_type=bed_type,
                chrom=chrom_clean,
                window=window,
                limit=limit,
            )
        )
    if not records:
        raise HTTPException(status_code=404, detail="No BED data found")
    return PlainTextResponse(
        "\n".join([_bed_header(bed_type), *[_bed_record_to_line(bed_type, item) for item in records]])
    )


async def fetch_bed_batch_json(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    bed_type: str,
    chroms: list[str],
    window: int | None,
    limit: int,
) -> JSONResponse:
    validate_bed_type(bed_type)
    if not chroms:
        raise HTTPException(status_code=400, detail="At least one chromosome is required")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for chrom in chroms:
        chrom_clean = normalize_chromosome(chrom)
        if chrom_clean in seen:
            continue
        seen.add(chrom_clean)
        records.extend(
            await _fetch_bed_records_for_chrom(
                session,
                sample_context=sample_context,
                bed_type=bed_type,
                chrom=chrom_clean,
                window=window,
                limit=limit,
            )
        )
    if not records:
        raise HTTPException(status_code=404, detail="No BED data found")
    return JSONResponse({"bed_type": bed_type, "items": records})


async def get_family_haplotypes_response(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    chr: str,
    start: int | None = None,
    end: int | None = None,
) -> HaplotypeResponse:
    sample_ids = list(context.sample_uuid_to_name)
    if not sample_ids or not context.assembly_name:
        return HaplotypeResponse(chr=chr, start=start, end=end, samples=[])
    rows = await fetch_interval_track_rows(
        context.assembly_name,
        family_uuid=context.family_uuid,
        sample_uuids=sample_ids,
        track_type="haplotype",
        chromosomes=[chr],
        start=start,
        end=end,
    )
    segments: dict[str, list[dict[str, Any]]] = {sample_uuid: [] for sample_uuid in sample_ids}
    for row in rows:
        segments[row["sample_uuid"]].append(
            {
                "chr": row["chr"],
                "start": int(row["start"]),
                "end": int(row["end"]),
                "hap1": str(row.get("hap1") or ""),
                "hap2": str(row.get("hap2") or ""),
            }
        )
    return HaplotypeResponse(
        chr=chr,
        start=start,
        end=end,
        samples=[
            {
                "sample": context.sample_uuid_to_name[sample_uuid],
                "segments": segments[sample_uuid],
            }
            for sample_uuid in sample_ids
        ],
    )


async def get_family_haplotypes_batch_response(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    chromosomes: list[str],
) -> HaplotypeResponse:
    sample_ids = list(context.sample_uuid_to_name)
    if not sample_ids or not context.assembly_name:
        return HaplotypeResponse(chr="genome", samples=[])
    if not chromosomes:
        raise HTTPException(status_code=400, detail="At least one chromosome is required")
    rows = await fetch_interval_track_rows(
        context.assembly_name,
        family_uuid=context.family_uuid,
        sample_uuids=sample_ids,
        track_type="haplotype",
        chromosomes=chromosomes,
    )
    segments: dict[str, list[dict[str, Any]]] = {sample_uuid: [] for sample_uuid in sample_ids}
    for row in rows:
        segments[row["sample_uuid"]].append(
            {
                "chr": row["chr"],
                "start": int(row["start"]),
                "end": int(row["end"]),
                "hap1": str(row.get("hap1") or ""),
                "hap2": str(row.get("hap2") or ""),
            }
        )
    return HaplotypeResponse(
        chr="genome",
        samples=[
            {
                "sample": context.sample_uuid_to_name[sample_uuid],
                "segments": segments[sample_uuid],
            }
            for sample_uuid in sample_ids
        ],
    )


async def get_track_presence_by_sample(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    track_type: str,
    chromosomes: list[str],
    start: int | None = None,
    end: int | None = None,
) -> set[str]:
    validate_bed_type(track_type, allow_haplotype=True)
    sample_ids = list(context.sample_uuid_to_name)
    if not sample_ids or not context.assembly_name:
        return set()
    _ = session
    return await get_interval_track_presence_by_sample(
        context.assembly_name,
        family_uuid=context.family_uuid,
        sample_uuid_to_name=context.sample_uuid_to_name,
        track_type=track_type,
        chromosomes=chromosomes,
        start=start,
        end=end,
    )
