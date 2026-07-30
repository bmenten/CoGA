from __future__ import annotations

import hashlib
from datetime import datetime, timezone
import json
import logging
from typing import Any

from fastapi import HTTPException, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import HaplotypeResponse
from .upload_safety import decode_upload_text
from .clickhouse_interval_tracks import (
    count_interval_track_source_rows,
    delete_interval_track_sources,
    delete_interval_tracks,
    fetch_apcad_downsampled,
    fetch_interval_track_rows,
    get_interval_track_lineage_hash,
    get_interval_track_presence_by_sample,
    get_interval_track_sources_by_sample,
    insert_interval_track_rows,
    upsert_interval_track_source,
)
from .data_scope import chromosome_aliases, normalize_chromosome
from .family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from .haplotype_lineage_service import annotate_lineage

logger = logging.getLogger(__name__)

# Ceiling for the on-demand phased-genotype fetch that drives relative haplotype
# colouring. Generous for a region-of-interest; whole-chromosome batch views fetch
# per chromosome.
LINEAGE_PHASED_FETCH_LIMIT = 500_000
# Effectively-uncapped fetch for the one-time, background UPLOAD-TIME precompute of
# the genome-wide lineage (no human chromosome has this many imputed sites), so the
# stored overview lineage is coloured genome-wide with no truncated q-arm.
LINEAGE_PRECOMPUTE_FETCH_LIMIT = 5_000_000
# Interval track-type holding the precomputed genome-wide lineage. The two per-lane
# lineage tags are packed into the existing `origin` column as "hap1l|hap2l" so no
# ClickHouse schema change is needed; `metadata_json.lineage_hash` records the
# pedigree/status fingerprint the colours were computed for (staleness guard).
LINEAGE_TRACK_TYPE = "haplotype_lineage"

VALID_BED_TYPES = {"coverage", "apcad", "apcad_pcf", "segments"}


def validate_bed_type(bed_type: str, *, allow_haplotype: bool = False) -> None:
    valid_types = VALID_BED_TYPES | ({"haplotype"} if allow_haplotype else set())
    if bed_type not in valid_types:
        raise HTTPException(status_code=400, detail="Invalid bed type")


async def _decode_bed_upload(file: UploadFile) -> str:
    return await decode_upload_text(file, kind="BED")


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
    track_type: str,
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
        "track_type": track_type,
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
            _build_apcad_row(sample_context, parts, track_type=bed_type, metadata_json=metadata_json)
            if bed_type in {"apcad", "apcad_pcf"}
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
    if track_type in {"apcad", "apcad_pcf"}:
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
    family_uuid: str | None = None,
    track_type: str,
    chrom: str,
    origins: list[str] | None = None,
    source: str | None = None,
    start: int | None = None,
    end: int | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    _ = session
    if not assembly_name:
        return []
    return await fetch_interval_track_rows(
        assembly_name,
        sample_uuid=sample_uuid,
        # The interval table is ordered by (family_guid, sample_guid, …); filtering
        # on the family too lets ClickHouse use the primary key instead of scanning
        # the whole track partition across every family.
        family_uuid=family_uuid,
        track_type=track_type,
        chromosomes=[chrom],
        origins=origins,
        source=source,
        start=start,
        end=end,
        limit=limit,
    )


def _windowed_coverage_rows(rows: list[dict[str, Any]], window: int, limit: int) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int], list[float]] = {}
    for row in rows:
        chrom = str(row["chr"])
        bin_start = (int(row["start"]) // window) * window
        grouped.setdefault((chrom, bin_start), []).append(float(row["value"]))
    records = [
        {
            "chr": chrom,
            "start": bin_start,
            "end": bin_start + window,
            "value": sum(values) / len(values),
        }
        for (chrom, bin_start), values in sorted(grouped.items())
    ]
    return records[:limit]


async def _fetch_bed_records_for_chrom(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    bed_type: str,
    chrom: str,
    window: int | None,
    start: int | None,
    end: int | None,
    limit: int,
    source: str | None = None,
) -> list[dict[str, Any]]:
    chrom_clean = normalize_chromosome(chrom)
    if bed_type == "coverage" and window:
        rows = await _fetch_raw_track_rows(
            session,
            assembly_name=sample_context.assembly_name,
            sample_uuid=sample_context.sample_uuid,
            family_uuid=sample_context.family_uuid,
            track_type=bed_type,
            chrom=chrom_clean,
            source=source,
            start=start,
            end=end,
        )
        return _windowed_coverage_rows(rows, window, limit)
    if bed_type == "apcad" and window:
        if not sample_context.assembly_name:
            return []
        return await fetch_apcad_downsampled(
            sample_context.assembly_name,
            sample_uuid=sample_context.sample_uuid,
            family_uuid=sample_context.family_uuid,
            chromosomes=[chrom_clean],
            origins=["paternal", "maternal"],
            budget=limit,
            start=start,
            end=end,
        )
    rows = await _fetch_raw_track_rows(
        session,
        assembly_name=sample_context.assembly_name,
        sample_uuid=sample_context.sample_uuid,
        track_type=bed_type,
        chrom=chrom_clean,
        origins=["paternal", "maternal"] if bed_type in {"apcad", "apcad_pcf"} else None,
        source=source,
        start=start,
        end=end,
        limit=limit,
    )
    return [_serialize_bed_record(bed_type, row) for row in rows]


async def _fetch_bed_records_for_chroms(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    bed_type: str,
    chroms: list[str],
    window: int | None,
    start: int | None,
    end: int | None,
    limit: int,
    source: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch and process BED records for many chromosomes with a single
    ClickHouse query (``chrom IN (...)``) instead of one query per chromosome.

    Rows are regrouped back into per-chromosome buckets so coverage windowing and
    plain per-chromosome serialization stay identical to the previous
    one-query-per-chromosome path. (Windowed APCAD never reaches here — it is
    selected and downsampled entirely in ClickHouse by ``fetch_apcad_downsampled``.)
    """
    _ = session
    # Deduplicate while preserving request order.
    ordered: list[str] = []
    seen: set[str] = set()
    for chrom in chroms:
        chrom_clean = normalize_chromosome(chrom)
        if chrom_clean and chrom_clean not in seen:
            seen.add(chrom_clean)
            ordered.append(chrom_clean)
    if not ordered or not sample_context.assembly_name:
        return []

    # Map every chromosome alias (e.g. "chr1"/"1", "M"/"MT") back to the
    # requested chromosome, mirroring fetch_interval_track_rows' own alias
    # expansion, so rows can be regrouped by whichever form ClickHouse stores.
    alias_to_chrom: dict[str, str] = {}
    for chrom in ordered:
        for alias in chromosome_aliases(chrom):
            alias_to_chrom[normalize_chromosome(alias)] = chrom

    # APCAD is selected and downsampled entirely in ClickHouse (informative +
    # quality-gated + het-preserving) so Python never materializes the full
    # SNV-resolution track. `limit` is the genome-wide point budget.
    if bed_type == "apcad" and window:
        return await fetch_apcad_downsampled(
            sample_context.assembly_name,
            sample_uuid=sample_context.sample_uuid,
            family_uuid=sample_context.family_uuid,
            chromosomes=ordered,
            origins=["paternal", "maternal"],
            budget=limit,
            start=start,
            end=end,
        )

    origins = ["paternal", "maternal"] if bed_type in {"apcad", "apcad_pcf"} else None
    # Windowed paths bin in Python and need every row; the non-windowed
    # per-chromosome `limit` is re-applied after grouping, so no DB-side limit is
    # used here (the query already orders by chrom, start).
    rows = await fetch_interval_track_rows(
        sample_context.assembly_name,
        sample_uuid=sample_context.sample_uuid,
        family_uuid=sample_context.family_uuid,
        track_type=bed_type,
        chromosomes=ordered,
        origins=origins,
        source=source,
        start=start,
        end=end,
    )

    grouped: dict[str, list[dict[str, Any]]] = {chrom: [] for chrom in ordered}
    for row in rows:
        chrom = alias_to_chrom.get(normalize_chromosome(str(row.get("chr") or "")))
        if chrom is not None:
            grouped[chrom].append(row)

    records: list[dict[str, Any]] = []
    for chrom in ordered:
        chrom_rows = grouped[chrom]
        if not chrom_rows:
            continue
        if bed_type == "coverage" and window:
            records.extend(_windowed_coverage_rows(chrom_rows, window, limit))
        else:
            records.extend(_serialize_bed_record(bed_type, row) for row in chrom_rows[:limit])
    return records


async def fetch_bed_text(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    bed_type: str,
    chrom: str,
    window: int | None,
    start: int | None,
    end: int | None,
    limit: int,
    source: str | None = None,
) -> PlainTextResponse:
    validate_bed_type(bed_type)
    records = await _fetch_bed_records_for_chrom(
        session,
        sample_context=sample_context,
        bed_type=bed_type,
        chrom=chrom,
        window=window,
        start=start,
        end=end,
        limit=limit,
        source=source,
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
    start: int | None,
    end: int | None,
    limit: int,
    source: str | None = None,
) -> JSONResponse:
    validate_bed_type(bed_type)
    records = await _fetch_bed_records_for_chrom(
        session,
        sample_context=sample_context,
        bed_type=bed_type,
        chrom=chrom,
        window=window,
        start=start,
        end=end,
        limit=limit,
        source=source,
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
    start: int | None,
    end: int | None,
    limit: int,
    source: str | None = None,
) -> PlainTextResponse:
    validate_bed_type(bed_type)
    if not chroms:
        raise HTTPException(status_code=400, detail="At least one chromosome is required")
    records = await _fetch_bed_records_for_chroms(
        session,
        sample_context=sample_context,
        bed_type=bed_type,
        chroms=chroms,
        window=window,
        start=start,
        end=end,
        limit=limit,
        source=source,
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
    start: int | None,
    end: int | None,
    limit: int,
    source: str | None = None,
) -> JSONResponse:
    validate_bed_type(bed_type)
    if not chroms:
        raise HTTPException(status_code=400, detail="At least one chromosome is required")
    records = await _fetch_bed_records_for_chroms(
        session,
        sample_context=sample_context,
        bed_type=bed_type,
        chroms=chroms,
        window=window,
        start=start,
        end=end,
        limit=limit,
        source=source,
    )
    if not records:
        raise HTTPException(status_code=404, detail="No BED data found")
    return JSONResponse({"bed_type": bed_type, "items": records})


async def _apply_haplotype_lineage(
    context: FamilyMetadataContext,
    *,
    segments_by_uuid: dict[str, list[dict[str, Any]]],
    chrom: str,
    start: int | None,
    end: int | None,
) -> dict[str, list[dict[str, Any]]]:
    """Re-colour relatives by pedigree-aware IBD matching and tag the nuclear core
    by role. Falls back to the stored (uuid-keyed) segments untouched if anything
    is missing, so the haplotype view degrades gracefully rather than failing."""
    # Imported lazily to avoid a heavy import at module load and any import cycle.
    from .clickhouse_family_variants import fetch_imputed_phased_genotypes

    genotype_rows: list[tuple[int, list[str], list[str]]] = []
    if start is not None and end is not None and end > start and context.assembly_name:
        genotype_rows = await fetch_imputed_phased_genotypes(
            context,
            chrom=chrom,
            start=int(start),
            end=int(end),
            limit=LINEAGE_PHASED_FETCH_LIMIT,
        )
    segments_by_name = {
        context.sample_uuid_to_name[sample_uuid]: segs
        for sample_uuid, segs in segments_by_uuid.items()
    }
    annotated = annotate_lineage(
        sample_rows=context.sample_rows,
        relationship_rows=context.relationship_rows,
        segments_by_name=segments_by_name,
        genotype_rows=genotype_rows,
        chrom=chrom,
        region_start=start,
        region_end=end,
        # Even in a bounded region the fetch can cap; do not colour relatives past the
        # last fetched site within the window.
        genotype_truncated=len(genotype_rows) >= LINEAGE_PHASED_FETCH_LIMIT,
    )
    name_to_uuid = context.sample_name_to_uuid
    return {
        name_to_uuid[name]: segs
        for name, segs in annotated.items()
        if name in name_to_uuid
    }


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
                "ps": row.get("ps"),
            }
        )
    segments = await _apply_haplotype_lineage(
        context, segments_by_uuid=segments, chrom=chr, start=start, end=end
    )
    return HaplotypeResponse(
        chr=chr,
        start=start,
        end=end,
        samples=[
            {
                "sample": context.sample_uuid_to_name[sample_uuid],
                "segments": segments.get(sample_uuid, []),
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
                "ps": row.get("ps"),
            }
        )
    # Genome-wide view: tag the nuclear core by role and recolour relatives by
    # per-chromosome IBD matching (with recombination segmentation).
    segments = await _apply_haplotype_lineage_genomewide(
        context, segments_by_uuid=segments, chromosomes=chromosomes
    )
    return HaplotypeResponse(
        chr="genome",
        samples=[
            {
                "sample": context.sample_uuid_to_name[sample_uuid],
                "segments": segments.get(sample_uuid, []),
            }
            for sample_uuid in sample_ids
        ],
    )


def _lineage_hash(context: FamilyMetadataContext) -> str:
    """Fingerprint of the inputs that determine the lineage colours: the pedigree
    edges, each member's role, and the affected set. If this changes, a stored
    precompute is stale and must not be served."""
    pedigree = sorted(
        (
            str(r.get("relationship_type") or ""),
            str(r.get("sample_id_a") or ""),
            str(r.get("sample_id_b") or ""),
            str(r.get("role_a") or ""),
            str(r.get("role_b") or ""),
        )
        for r in context.relationship_rows
    )
    roles = sorted(
        (str(row.get("sample_id") or ""), str(row.get("role") or ""))
        for row in context.sample_rows
    )
    payload = json.dumps(
        {"pedigree": pedigree, "roles": roles, "affected": sorted(context.affected_sample_names)},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


async def _compute_genomewide_lineage(
    context: FamilyMetadataContext,
    *,
    segments_by_name: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """The heavy genome-wide IBD lineage, per chromosome with an (effectively)
    uncapped genotype fetch. Mirrors the bounded `_apply_haplotype_lineage` but over
    every stored chromosome — run ONCE at upload (background), never per request."""
    from .clickhouse_family_variants import fetch_imputed_phased_genotypes

    chroms = sorted(
        {
            normalize_chromosome(str(seg.get("chr") or ""))
            for segs in segments_by_name.values()
            for seg in segs
        }
        - {""}
    )
    merged: dict[str, list[dict[str, Any]]] = {name: [] for name in segments_by_name}
    for chrom in chroms:
        chrom_segments = {
            name: [seg for seg in segs if normalize_chromosome(str(seg.get("chr") or "")) == chrom]
            for name, segs in segments_by_name.items()
        }
        if not any(chrom_segments.values()):
            continue
        genotype_rows = await fetch_imputed_phased_genotypes(
            context, chrom=chrom, start=0, end=2_000_000_000, limit=LINEAGE_PRECOMPUTE_FETCH_LIMIT
        )
        annotated = annotate_lineage(
            sample_rows=context.sample_rows,
            relationship_rows=context.relationship_rows,
            segments_by_name=chrom_segments,
            genotype_rows=genotype_rows,
            chrom=chrom,
            genotype_truncated=len(genotype_rows) >= LINEAGE_PRECOMPUTE_FETCH_LIMIT,
        )
        for name, segs in annotated.items():
            merged[name].extend(segs)
    return merged


def _lineage_interval_rows(
    context: FamilyMetadataContext,
    segments_by_name: dict[str, list[dict[str, Any]]],
    *,
    lineage_hash: str,
) -> list[dict[str, Any]]:
    """Convert lineage-tagged blocks into interval rows for the haplotype_lineage
    track: shade in hap1/hap2, the two lineage tags packed into ``origin`` as
    ``"hap1l|hap2l"``, and the fingerprint stored in ``metadata_json``."""
    metadata_json = json.dumps({"lineage_hash": lineage_hash, "filename": LINEAGE_TRACK_TYPE})
    rows: list[dict[str, Any]] = []
    for name, segs in segments_by_name.items():
        sample_uuid = context.sample_name_to_uuid.get(name)
        if not sample_uuid:
            continue
        for seg in segs:
            rows.append(
                {
                    "sample_id": sample_uuid,
                    "family_id": context.family_uuid,
                    "track_type": LINEAGE_TRACK_TYPE,
                    "source": "glimpse2_lineage",
                    "filename": LINEAGE_TRACK_TYPE,
                    "chr": seg.get("chr") or "",
                    "start": int(seg["start"]),
                    "end": int(seg["end"]),
                    "hap1": seg.get("hap1"),
                    "hap2": seg.get("hap2"),
                    "ps": seg.get("ps"),
                    "origin": f"{seg.get('hap1_lineage') or ''}|{seg.get('hap2_lineage') or ''}",
                    "value": None,
                    "record_id": None,
                    "metadata_json": metadata_json,
                }
            )
    return rows


async def precompute_family_haplotype_lineage(context: FamilyMetadataContext) -> int:
    """Compute the genome-wide lineage once and persist it as the haplotype_lineage
    interval track (replacing any prior one), so the genome overview can serve
    precise relative colours without recomputing IBD on every request. Returns the
    number of stored blocks. Intended to run in the background (upload / pedigree
    edit); callers should log-and-ignore failures so a precompute hiccup never fails
    the surrounding operation."""
    if not context.assembly_name:
        return 0
    sample_uuids = list(context.sample_uuid_to_name)
    if not sample_uuids:
        return 0
    rows = await fetch_interval_track_rows(
        context.assembly_name,
        family_uuid=context.family_uuid,
        sample_uuids=sample_uuids,
        track_type="haplotype",
        chromosomes=[],  # every stored chromosome
    )
    segments_by_name: dict[str, list[dict[str, Any]]] = {
        context.sample_uuid_to_name[uuid]: [] for uuid in sample_uuids
    }
    for row in rows:
        name = context.sample_uuid_to_name.get(row["sample_uuid"])
        if name is None:
            continue
        segments_by_name[name].append(
            {
                "chr": row["chr"],
                "start": int(row["start"]),
                "end": int(row["end"]),
                "hap1": str(row.get("hap1") or ""),
                "hap2": str(row.get("hap2") or ""),
                "ps": row.get("ps"),
            }
        )
    annotated = await _compute_genomewide_lineage(context, segments_by_name=segments_by_name)
    lineage_rows = _lineage_interval_rows(context, annotated, lineage_hash=_lineage_hash(context))
    await delete_interval_tracks(
        context.assembly_name, family_uuid=context.family_uuid, track_type=LINEAGE_TRACK_TYPE
    )
    if lineage_rows:
        await insert_interval_track_rows(context.assembly_name, lineage_rows)
    return len(lineage_rows)


async def precompute_family_lineage_safe(family_identifier: str, user: Any) -> None:
    """Best-effort background (re)precompute after a pedigree/affected-status edit.

    Scheduled via FastAPI BackgroundTasks, so it opens its OWN session — the request
    that triggered it is long gone by the time this runs. Logs and swallows every
    error: if it fails, the genome overview simply falls back to the fast
    grey-relatives path, and the hash guard guarantees the now-stale precompute is
    never served in the meantime. Until this finishes, the edited family's overview
    shows grey relatives (hash mismatch); afterwards, precise colours return."""
    from ..core.postgres import get_postgres_engine, get_postgres_sessionmaker
    from .family_metadata_context import build_family_metadata_context

    try:
        get_postgres_engine()
        async with get_postgres_sessionmaker()() as session:
            context = await build_family_metadata_context(
                session, family_identifier=family_identifier, user=user
            )
            stored = await precompute_family_haplotype_lineage(context)
            logger.info(
                "Re-precomputed genome-wide haplotype lineage for family %s: %s blocks",
                family_identifier,
                stored,
            )
    except Exception:  # pragma: no cover - background best-effort
        logger.exception(
            "Background haplotype lineage precompute failed for family %s; genome "
            "overview will use the fast grey-relatives fallback",
            family_identifier,
        )


async def _fetch_precomputed_lineage(
    context: FamilyMetadataContext,
) -> dict[str, list[dict[str, Any]]] | None:
    """Return the stored genome-wide lineage as ``{uuid: segments}`` when it exists
    AND matches the current pedigree/status fingerprint; otherwise None (caller then
    falls back to the fast grey-relatives path). The hash guard means a pedigree or
    affected-status edit never serves stale colours, even before a re-precompute."""
    if not context.assembly_name:
        return None
    stored_hash = await get_interval_track_lineage_hash(
        context.assembly_name, family_uuid=context.family_uuid, track_type=LINEAGE_TRACK_TYPE
    )
    if not stored_hash or stored_hash != _lineage_hash(context):
        return None
    rows = await fetch_interval_track_rows(
        context.assembly_name,
        family_uuid=context.family_uuid,
        sample_uuids=list(context.sample_uuid_to_name),
        track_type=LINEAGE_TRACK_TYPE,
        chromosomes=[],  # every stored chromosome
    )
    if not rows:
        return None
    by_uuid: dict[str, list[dict[str, Any]]] = {uuid: [] for uuid in context.sample_uuid_to_name}
    for row in rows:
        uuid = row["sample_uuid"]
        if uuid not in by_uuid:
            continue
        hap1_lineage, _, hap2_lineage = str(row.get("origin") or "").partition("|")
        by_uuid[uuid].append(
            {
                "chr": row["chr"],
                "start": int(row["start"]),
                "end": int(row["end"]),
                "hap1": str(row.get("hap1") or ""),
                "hap2": str(row.get("hap2") or ""),
                "ps": row.get("ps"),
                "hap1_lineage": hap1_lineage or None,
                "hap2_lineage": hap2_lineage or None,
            }
        )
    return by_uuid


async def _apply_haplotype_lineage_genomewide(
    context: FamilyMetadataContext,
    *,
    segments_by_uuid: dict[str, list[dict[str, Any]]],
    chromosomes: list[str],
) -> dict[str, list[dict[str, Any]]]:
    """Genome-OVERVIEW lineage: serve the upload-time precompute when present, else
    tag the nuclear core by role and grey relatives.

    Genome-wide IBD matching is far too expensive to run per request — it fetches
    millions of phased-genotype rows and re-runs per-chromosome Python IBD (~58s for
    a family with any relative). So it runs ONCE at upload (background) and the result
    is stored as the haplotype_lineage track; ``_fetch_precomputed_lineage`` returns
    it here (a couple of cheap interval reads), hash-guarded so a pedigree/affected
    edit never serves stale colours.

    When no (current) precompute exists — a family uploaded before this feature, or
    one whose pedigree just changed — we fall back to the cheap path: the nuclear core
    (father/mother + their children/embryos) keeps its role-based colours and
    relatives (plus single-parent donor lanes) are greyed, "not resolved at this
    zoom". Their precise blue/green lineage is still available in the bounded
    chromosome / region-of-interest view (`get_family_haplotypes_response`), which
    fetches only the visible window. ``annotate_lineage`` with no genotypes +
    ``chrom="genome"`` does exactly this role-tag-and-grey."""
    precomputed = await _fetch_precomputed_lineage(context)
    if precomputed is not None:
        return precomputed
    _ = chromosomes  # the overview spans every stored chromosome; no per-chrom work
    segments_by_name = {
        context.sample_uuid_to_name[sample_uuid]: segs
        for sample_uuid, segs in segments_by_uuid.items()
    }
    annotated = annotate_lineage(
        sample_rows=context.sample_rows,
        relationship_rows=context.relationship_rows,
        segments_by_name=segments_by_name,
        genotype_rows=[],
        chrom="genome",
    )
    return _segments_by_uuid(context, annotated)


def _segments_by_uuid(
    context: FamilyMetadataContext,
    segments_by_name: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    return {
        context.sample_name_to_uuid[name]: segs
        for name, segs in segments_by_name.items()
        if name in context.sample_name_to_uuid
    }


async def get_track_sources_by_sample(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    track_type: str,
    chromosomes: list[str],
    start: int | None = None,
    end: int | None = None,
) -> dict[str, list[str]]:
    """Sample name → the callers that have rows for ``track_type``.

    Supersedes bare presence for the track types more than one caller can write.
    Being told a sample "has coverage" is not enough to draw it: HiFiCNV,
    WisecondorX and QDNAseq each produce their own, and they are separate tracks.
    """

    validate_bed_type(track_type, allow_haplotype=True)
    if not context.sample_uuid_to_name or not context.assembly_name:
        return {}
    _ = session
    return await get_interval_track_sources_by_sample(
        context.assembly_name,
        family_uuid=context.family_uuid,
        sample_uuid_to_name=context.sample_uuid_to_name,
        track_type=track_type,
        chromosomes=chromosomes,
        start=start,
        end=end,
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
