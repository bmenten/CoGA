from __future__ import annotations

import asyncio
import csv
from itertools import chain
import json
from pathlib import Path
import re
from typing import Any, Awaitable, Callable, Iterable, Iterator

from sqlalchemy.ext.asyncio import AsyncSession

from .clickhouse_interval_tracks import (
    delete_interval_track_sources,
    delete_interval_tracks,
    insert_interval_track_rows,
    upsert_interval_track_source,
)
from .data_scope import normalize_chromosome
from .family_package_bigwig import iter_bigwig_intervals, open_bigwig
from .family_metadata_context import (
    SampleMetadataContext,
)

from .family_package_common import APCAD_PCF_SOURCE, APCAD_PCF_TRACK_TYPE, APCAD_SOURCE, ParsedPed, _coerce_finite_float, _coerce_int, _is_vcf_file, _jsonb_safe, _missing_scalar, _normalize_header_key, _open_package_text, _parse_format, _parse_vcf_info, package_text_handle
from .clickhouse_column_bounds import UINT64, column_int
from .import_progress import bytes_read_on_disk, file_size, read_stats


class EmptyTrackFileError(RuntimeError):
    """A track file in which its reader finds no row to store (SAFE-29): no record, or none
    it can read. Every reader of a package's track files measures its sample across the
    genome (bins, segments, depth, allele fractions), so such a file is no result. It is
    refused before anything is deleted: its dataset fails, and the stored track stays as it
    was. It used to replace the track with none, the dataset reading as imported."""


def _first_stored_row(rows: Iterator[dict[str, Any] | None]) -> tuple[dict[str, Any] | None, int]:
    """Read ``rows`` (a reader's row for each line, None for a line it leaves out) up to
    the first row it stores: that row, None when there is none, and the lines left out
    before it. The rest of ``rows`` is left to read."""
    left_out = 0
    for row in rows:
        if row is not None:
            return row, left_out
        left_out += 1
    return None, left_out


def _coordinate(value: Any) -> int | None:
    """A track row's start, end or position: a whole number the interval table's UInt64
    columns hold, else None, and the row is left out as malformed (SAFE-30): a negative
    coordinate failed the insert of the whole batch."""
    return column_int(_coerce_int(value), UINT64)


async def _delete_sample_interval_source(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    track_type: str,
    source: str,
    filename: str | None = None,
) -> None:
    """Delete one source's ``track_type`` track of a sample (of one of its files, with
    ``filename``): its rows and Postgres's record of it. A sample can carry several callers'
    tracks of one type (the coverage of a BED, WisecondorX, QDNAseq and HiFiCNV; HiFiCNV's
    minor allele fraction beside the APCAD), so a loader replaces its own source's track and
    no other (SAFE-22). The admin deletes of a sample's or a family's data remove every
    source."""
    if not sample_context.assembly_name:
        raise RuntimeError("Cannot delete interval tracks without an assembly name")
    await delete_interval_tracks(
        sample_context.assembly_name,
        sample_uuid=sample_context.sample_uuid,
        track_type=track_type,
        source=source,
        filename=filename,
    )
    await delete_interval_track_sources(
        session,
        sample_uuid=sample_context.sample_uuid,
        track_type=track_type,
        source=source,
        filename=filename,
    )


# Rows per ClickHouse insert of an interval track. Each insert is a part, and the interval
# table is partitioned by track type: a per-embryo APCAD VCF holds about six million sites,
# so 5,000-row inserts made over a thousand parts per file. ClickHouse delayed the inserts
# while it merged them, until the client's read of the socket timed out and the dataset
# failed. A batch of this size is a few tens of megabytes in Python.
INTERVAL_INSERT_BATCH_ROWS = 50_000
# How often an APCAD VCF's import reports the bytes it has read, in records.
APCAD_PROGRESS_RECORDS = 50_000


async def _insert_interval_track_rows(
    session: AsyncSession,
    rows: list[dict[str, Any]],
) -> None:
    _ = session
    if not rows:
        return
    assembly_names = {str(row.get("assembly_name") or "") for row in rows}
    assembly_names.discard("")
    if len(assembly_names) != 1:
        raise RuntimeError("Interval-track rows must belong to exactly one assembly")
    await insert_interval_track_rows(next(iter(assembly_names)), rows)


def _header_map(parts: list[str]) -> dict[str, int]:
    return {part.strip().lower(): index for index, part in enumerate(parts)}


def _normalized_header_map(parts: list[str]) -> dict[str, int]:
    return {_normalize_header_key(part): index for index, part in enumerate(parts)}


def _header_value(parts: list[str], header: dict[str, int], *names: str) -> str | None:
    for name in names:
        index = header.get(name)
        if index is not None and index < len(parts):
            return parts[index]
    return None


def _normalized_header_value(parts: list[str], header: dict[str, int], *names: str) -> str | None:
    for name in names:
        index = header.get(_normalize_header_key(name))
        if index is not None and index < len(parts):
            return parts[index]
    return None


def _split_delimited_line(line: str) -> list[str]:
    stripped = line.strip()
    if "," in stripped:
        return next(csv.reader([stripped]))
    if ";" in stripped:
        return next(csv.reader([stripped], delimiter=";"))
    if "\t" in stripped:
        return stripped.split("\t")
    return stripped.split()


def _looks_like_interval_header(parts: list[str]) -> bool:
    normalized = {_normalize_header_key(part) for part in parts}
    has_chrom = bool(normalized & {"chr", "chrom", "chromosome"})
    has_start = bool(normalized & {"start", "windowstart", "from", "pos", "position"})
    has_end = bool(normalized & {"end", "stop", "windowend", "to", "pos", "position"})
    return has_chrom and has_start and has_end


_COPY_NUMBER_VALUE_COLUMNS = (
    "ratio",
    "value",
    "log2",
    "log2ratio",
    "log2copyratio",
    "copynumber",
    "copy",
    "cn",
    "segmented",
    "segmentedratio",
    "segmean",
    "mean",
)


_COPY_NUMBER_SEGMENT_VALUE_COLUMNS = (
    "segmented",
    "segmentedratio",
    "segmean",
    "segmentmean",
    "segment",
    "ratio",
    "value",
    "log2",
    "log2ratio",
    "log2copyratio",
    "copynumber",
    "copy",
    "cn",
    "mean",
)


_COPY_NUMBER_METADATA_COLUMNS = (
    "zscore",
    "z",
    "call",
    "probes",
    "nprobes",
    "reads",
    "gc",
    "mappability",
    "blacklist",
    "residual",
    "use",
)


def _first_header_value(
    parts: list[str],
    header: dict[str, int],
    names: tuple[str, ...],
) -> str | None:
    return _normalized_header_value(parts, header, *names)


def _parse_copy_number_interval_row(
    parts: list[str],
    *,
    header: dict[str, int] | None,
    sample_context: SampleMetadataContext,
    track_type: str,
    source: str,
    path: Path,
    line_no: int,
) -> dict[str, Any] | None:
    if header is not None:
        chrom = _first_header_value(parts, header, ("chr", "chrom", "chromosome"))
        start_raw = _first_header_value(parts, header, ("start", "window_start", "from"))
        end_raw = _first_header_value(parts, header, ("end", "stop", "window_end", "to"))
        record_id = _first_header_value(parts, header, ("id", "record_id", "name", "bin"))
        value_raw = _first_header_value(
            parts,
            header,
            _COPY_NUMBER_SEGMENT_VALUE_COLUMNS if track_type == "segments" else _COPY_NUMBER_VALUE_COLUMNS,
        )
    else:
        if len(parts) < 4:
            return None
        chrom, start_raw, end_raw = parts[:3]
        record_id = parts[3] if len(parts) > 4 and _coerce_finite_float(parts[3]) is None else None
        value_candidates = parts[4:] if record_id is not None else parts[3:]
        value_raw = next(
            (value for value in value_candidates if _coerce_finite_float(value) is not None),
            None,
        )

    start = _coordinate(start_raw)
    end = _coordinate(end_raw)
    value = _coerce_finite_float(value_raw)
    if chrom is None or start is None or end is None or value is None:
        return None

    metadata: dict[str, Any] = {
        "source": source,
        "filename": path.name,
        "line_no": line_no,
    }
    if header is not None:
        for column in _COPY_NUMBER_METADATA_COLUMNS:
            raw_value = _normalized_header_value(parts, header, column)
            if raw_value in (None, ""):
                continue
            numeric_value = _coerce_finite_float(raw_value)
            metadata[_normalize_header_key(column)] = numeric_value if numeric_value is not None else raw_value

    return {
        "sample_id": sample_context.sample_uuid,
        "family_id": sample_context.family_uuid,
        "assembly_id": sample_context.assembly_id or "",
        "assembly_name": sample_context.assembly_name or "",
        "track_type": track_type,
        "source": source,
        "chr": normalize_chromosome(str(chrom)),
        "start": start,
        "end": end,
        "record_id": record_id or f"{chrom}:{start}-{end}",
        "value": value,
        "origin": None,
        "metadata_json": json.dumps(_jsonb_safe(metadata)),
    }


def _parse_wisecondorx_interval_row(
    parts: list[str],
    *,
    header: dict[str, int] | None,
    sample_context: SampleMetadataContext,
    track_type: str,
    path: Path,
    line_no: int,
) -> dict[str, Any] | None:
    if header is not None:
        chrom = _header_value(parts, header, "chr", "chrom", "chromosome")
        start_raw = _header_value(parts, header, "start", "window_start")
        end_raw = _header_value(parts, header, "end", "stop", "window_end")
        record_id = _header_value(parts, header, "id", "record_id", "name")
        value_raw = _header_value(parts, header, "ratio", "value", "log2", "log2ratio")
        zscore_raw = _header_value(parts, header, "zscore", "z_score", "z")
    else:
        if len(parts) < 4:
            return None
        chrom, start_raw, end_raw = parts[:3]
        record_id = parts[3] if track_type == "coverage" and len(parts) > 4 else None
        value_raw = parts[4] if track_type == "coverage" and len(parts) > 4 else parts[3]
        zscore_raw = (
            parts[5]
            if track_type == "coverage" and len(parts) > 5
            else (parts[4] if len(parts) > 4 else None)
        )

    start = _coordinate(start_raw)
    end = _coordinate(end_raw)
    value = _coerce_finite_float(value_raw)
    if chrom is None or start is None or end is None or value is None:
        return None

    zscore = _coerce_finite_float(zscore_raw)
    metadata: dict[str, Any] = {
        "source": "wisecondorx",
        "filename": path.name,
        "line_no": line_no,
    }
    if zscore is not None:
        metadata["zscore"] = zscore

    return {
        "sample_id": sample_context.sample_uuid,
        "family_id": sample_context.family_uuid,
        "assembly_id": sample_context.assembly_id or "",
        "assembly_name": sample_context.assembly_name or "",
        "track_type": track_type,
        "source": "wisecondorx",
        "chr": normalize_chromosome(str(chrom)),
        "start": start,
        "end": end,
        "record_id": record_id or f"{chrom}:{start}-{end}",
        "value": value,
        "origin": None,
        "metadata_json": json.dumps(metadata),
    }


async def _store_track_rows(
    session: AsyncSession,
    rows: Iterable[dict[str, Any] | None],
    *,
    left_out: int = 0,
    value_transform: Callable[[float], float] | None = None,
    progress: Callable[[dict[str, int]], Awaitable[None]] | None = None,
) -> dict[str, int]:
    """Insert ``rows`` (a reader's row for each line, None for a line it leaves out) in
    batches, each value through ``value_transform`` when given, reporting ``progress``
    after a batch every 50,000 lines. ``left_out`` counts the lines left out before the
    first of ``rows`` (``_first_stored_row``). Returns the lines read (``processed``), the
    rows stored and the lines left out (``skipped``)."""
    processed = left_out
    skipped = left_out
    inserted = 0
    last_reported = 0
    batch: list[dict[str, Any]] = []
    for row in rows:
        processed += 1
        if row is None:
            skipped += 1
            continue
        if value_transform is not None and row.get("value") is not None:
            row["value"] = value_transform(float(row["value"]))
        batch.append(row)
        if len(batch) >= INTERVAL_INSERT_BATCH_ROWS:
            await _insert_interval_track_rows(session, batch)
            inserted += len(batch)
            batch = []
            if progress is not None and processed - last_reported >= 50000:
                last_reported = processed
                await progress({"processed": processed, "inserted": inserted, "skipped": skipped})
    if batch:
        await _insert_interval_track_rows(session, batch)
        inserted += len(batch)
    return {"processed": processed, "inserted": inserted, "skipped": skipped}


def _wisecondorx_rows(
    handle: Iterable[str],
    *,
    sample_context: SampleMetadataContext,
    track_type: str,
    path: Path,
) -> Iterator[dict[str, Any] | None]:
    """Each data line of a WisecondorX bins or segments file as the row it is stored as,
    None for a line left out. A header line names the columns of the lines after it."""
    header: dict[str, int] | None = None
    for line_no, line in enumerate(handle, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split("\t") if "\t" in stripped else stripped.split()
        lowered = [part.strip().lower() for part in parts]
        if header is None and {"chr", "start", "end"}.issubset(set(lowered)):
            header = _header_map(parts)
            continue
        yield _parse_wisecondorx_interval_row(
            parts,
            header=header,
            sample_context=sample_context,
            track_type=track_type,
            path=path,
            line_no=line_no,
        )


async def _import_wisecondorx_track(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    path: Path,
    track_type: str,
    progress: Callable[[dict[str, int]], Awaitable[None]] | None = None,
    check_only: bool = False,
) -> dict[str, int]:
    """Store a WisecondorX bins or segments file as the sample's ``track_type`` track,
    source ``wisecondorx``, in place of the one stored from that file.

    The file is read up to its first row before anything is deleted: one without a row is
    refused (``EmptyTrackFileError``) with the stored track as it was (SAFE-29).
    ``check_only`` stops there, deleting and storing nothing: the check of the file before
    its track is replaced (``_import_interval_track_unless_present``)."""
    if not sample_context.assembly_name:
        raise RuntimeError("Cannot import WisecondorX interval tracks without an assembly name")
    with path.open("r", encoding="utf-8") as handle:
        rows = _wisecondorx_rows(handle, sample_context=sample_context, track_type=track_type, path=path)
        first, left_out = _first_stored_row(rows)
        if first is None:
            raise EmptyTrackFileError(
                f"The WisecondorX file {path.name} holds no record of the {track_type} track; "
                "its track was not replaced"
            )
        if check_only:
            return {}
        await _delete_sample_interval_source(
            session,
            sample_context=sample_context,
            track_type=track_type,
            source="wisecondorx",
            filename=path.name,
        )
        result = await _store_track_rows(
            session, chain([first], rows), left_out=left_out, progress=progress
        )
    await upsert_interval_track_source(
        session,
        sample_context=sample_context,
        track_type=track_type,
        source="wisecondorx",
        filename=path.name,
        row_count=result["inserted"],
        metadata={
            "source": "wisecondorx",
            "filename": path.name,
            "uploaded_from": "family_package",
        },
    )
    await session.commit()
    if progress is not None:
        await progress(result)
    return result


def _copy_number_rows(
    handle: Iterable[str],
    *,
    sample_context: SampleMetadataContext,
    track_type: str,
    source: str,
    path: Path,
) -> Iterator[dict[str, Any] | None]:
    """Each data line of a delimited copy-number file as the row it is stored as (its value
    as the file gives it), None for a line left out. A header line names the columns of the
    lines after it."""
    header: dict[str, int] | None = None
    for line_no, line in enumerate(handle, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = _split_delimited_line(stripped)
        if header is None and _looks_like_interval_header(parts):
            header = _normalized_header_map(parts)
            continue
        yield _parse_copy_number_interval_row(
            parts,
            header=header,
            sample_context=sample_context,
            track_type=track_type,
            source=source,
            path=path,
            line_no=line_no,
        )


async def _import_copy_number_track(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    path: Path,
    track_type: str,
    source: str,
    value_transform: Callable[[float], float] | None = None,
    extra_metadata: dict[str, Any] | None = None,
    progress: Callable[[dict[str, int]], Awaitable[None]] | None = None,
    check_only: bool = False,
) -> dict[str, int]:
    """Import a delimited copy-number file as interval-track rows.

    ``value_transform`` converts each value before storage, for a caller whose file
    holds a different quantity from the track's axis -- HiFiCNV's bedGraph carries
    integer copy number where the segments track holds a log2 ratio. A transformed
    track must record what it was transformed by: pass it in ``extra_metadata``, which
    is recorded with the track's source, as :func:`_import_bigwig_interval_track`
    records its own.

    The file is read up to its first row before anything is deleted: one without a row is
    refused (``EmptyTrackFileError``) with the stored track as it was (SAFE-29).
    ``check_only`` stops there, deleting and storing nothing.
    """

    if not sample_context.assembly_name:
        raise RuntimeError("Cannot import copy-number interval tracks without an assembly name")
    async with _open_package_text(path) as handle:
        rows = _copy_number_rows(
            handle, sample_context=sample_context, track_type=track_type, source=source, path=path
        )
        first, left_out = _first_stored_row(rows)
        if first is None:
            raise EmptyTrackFileError(
                f"The copy-number file {path.name} holds no record of the {track_type} track; "
                "its track was not replaced"
            )
        if check_only:
            return {}
        await _delete_sample_interval_source(
            session,
            sample_context=sample_context,
            track_type=track_type,
            source=source,
            filename=path.name,
        )
        result = await _store_track_rows(
            session,
            chain([first], rows),
            left_out=left_out,
            value_transform=value_transform,
            progress=progress,
        )
    await upsert_interval_track_source(
        session,
        sample_context=sample_context,
        track_type=track_type,
        source=source,
        filename=path.name,
        row_count=result["inserted"],
        metadata={
            "source": source,
            "filename": path.name,
            "uploaded_from": "family_package",
            # The transform the stored values went through, when they went through one:
            # without it a stored log2 ratio cannot be traced back to the file's value.
            **(extra_metadata or {}),
        },
    )
    await session.commit()
    if progress is not None:
        await progress(result)
    return result


def _bigwig_rows(
    intervals: Iterable[tuple[str, int, int, float]],
    *,
    sample_context: SampleMetadataContext,
    track_type: str,
    source: str,
    origin: str | None,
    path: Path,
) -> Iterator[dict[str, Any]]:
    """Each interval of a bigWig (``iter_bigwig_intervals``) as the row it is stored as."""
    for chrom, start, end, value in intervals:
        yield {
            "sample_id": sample_context.sample_uuid,
            "family_id": sample_context.family_uuid,
            "assembly_id": sample_context.assembly_id or "",
            "assembly_name": sample_context.assembly_name or "",
            "track_type": track_type,
            "source": source,
            "chr": chrom,
            "start": start,
            "end": end,
            "record_id": f"{chrom}:{start}-{end}",
            "value": value,
            "origin": origin,
            # Deliberately no per-row line number: a bigWig has no lines, and
            # a synthetic index would only invite someone to grep for it.
            "metadata_json": json.dumps(_jsonb_safe({"source": source, "filename": path.name})),
        }


async def _import_bigwig_interval_track(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    path: Path,
    track_type: str,
    source: str,
    origin: str | None = None,
    skip_zero: bool = False,
    value_transform: Callable[[float], float] | None = None,
    extra_metadata: dict[str, Any] | None = None,
    check_only: bool = False,
) -> dict[str, int]:
    """Import a bigWig signal file as interval-track rows.

    Same contract as :func:`_import_copy_number_track` -- replace this
    (track_type, source, filename) triple, stream rows in batches, then record
    the source -- but reading a binary signal file instead of a delimited one.
    A file with no interval on a primary chromosome (with ``skip_zero``, none with a
    value other than zero) is refused before anything is deleted, and ``check_only``
    stops at its first interval, as there.

    ``origin`` is written to every row when given. bigWig has nowhere to put a
    parent-of-origin call, so a track fed from one is unphased by construction;
    passing ``"und"`` states that explicitly rather than leaving the column null
    and letting a reader infer a missing value means something.

    ``value_transform`` converts each value before it is stored -- read depth to a
    log2 ratio, say. A transformed track must record what it was transformed by:
    pass the normaliser in ``extra_metadata`` so the stored numbers can be traced
    back to the file they came from.
    """

    if not sample_context.assembly_name:
        raise RuntimeError("Cannot import bigWig interval tracks without an assembly name")
    reader = open_bigwig(path)
    try:
        rows = _bigwig_rows(
            iter_bigwig_intervals(reader, skip_zero=skip_zero),
            sample_context=sample_context,
            track_type=track_type,
            source=source,
            origin=origin,
            path=path,
        )
        first = next(rows, None)
        if first is None:
            raise EmptyTrackFileError(
                f"The bigWig file {path.name} holds no interval on a primary chromosome"
                + (" with a value other than zero" if skip_zero else "")
                + "; its track was not replaced"
            )
        if check_only:
            return {}
        await _delete_sample_interval_source(
            session,
            sample_context=sample_context,
            track_type=track_type,
            source=source,
            filename=path.name,
        )
        result = await _store_track_rows(session, chain([first], rows), value_transform=value_transform)
    finally:
        reader.close()

    await upsert_interval_track_source(
        session,
        sample_context=sample_context,
        track_type=track_type,
        source=source,
        filename=path.name,
        row_count=result["inserted"],
        metadata={
            "source": source,
            "filename": path.name,
            "uploaded_from": "family_package",
            "format": "bigwig",
            **(extra_metadata or {}),
        },
    )
    await session.commit()
    return result


_APCAD_VALUE_KEYS = (
    "APCAD",
    "AP",
    "BAF",
    "AF",
    "AB",
    "VAF",
    "RATIO",
    "VALUE",
)


_APCAD_ORIGIN_KEYS = (
    "ORIGIN",
    "PO",
    "POO",
    "PARENT",
    "PARENT_ORIGIN",
    "PARENTAL_ORIGIN",
    "PARENT_OF_ORIGIN",
    "TRANSMITTED_FROM",
)


def _first_mapping_value(mapping: dict[str, str], keys: tuple[str, ...]) -> str | None:
    normalized_keys = {_normalize_header_key(key): value for key, value in mapping.items()}
    for key in keys:
        value = normalized_keys.get(_normalize_header_key(key))
        if not _missing_scalar(value):
            return value
    return None


def _first_finite_from_list(value: str | None) -> float | None:
    if value is None:
        return None
    for item in str(value).replace("|", ",").split(","):
        parsed = _coerce_finite_float(item)
        if parsed is not None:
            return parsed
    return None


def _normalize_origin(value: str | None) -> str:
    if value is None:
        return "und"
    token = str(value).split(",", 1)[0].strip().lower()
    if token in {"paternal", "pat", "father", "dad", "p", "fa"}:
        return "paternal"
    if token in {"maternal", "mat", "mother", "mom", "m", "mo"}:
        return "maternal"
    return "und"


def _apcad_value(
    info: dict[str, str],
    fmt_vals: dict[str, str],
    *,
    allow_info_fallback: bool = True,
) -> float | None:
    value = _first_finite_from_list(_first_mapping_value(fmt_vals, _APCAD_VALUE_KEYS))
    if value is not None:
        return value
    ad_raw = _first_mapping_value(fmt_vals, ("AD",))
    if ad_raw is not None:
        depths = [_coerce_int(item) for item in ad_raw.split(",")]
        depths = [depth for depth in depths if depth is not None]
        if len(depths) >= 2:
            total = sum(depths)
            return depths[1] / total if total > 0 else None
    if not allow_info_fallback:
        return None
    return _first_finite_from_list(_first_mapping_value(info, _APCAD_VALUE_KEYS))


def _apcad_origin(info: dict[str, str], fmt_vals: dict[str, str]) -> str:
    return _normalize_origin(
        _first_mapping_value(fmt_vals, _APCAD_ORIGIN_KEYS)
        or _first_mapping_value(info, _APCAD_ORIGIN_KEYS)
    )


def _gt_has_alt_allele(fmt_vals: dict[str, str], allele: str = "1") -> bool:
    gt = _first_mapping_value(fmt_vals, ("GT",))
    if gt in (None, "", "."):
        return False
    return allele in {token for token in re.split(r"[\/|]", gt) if token not in {"", "."}}


def _infer_apcad_origin_from_parent_genotypes(
    *,
    father_fmt: dict[str, str] | None,
    mother_fmt: dict[str, str] | None,
) -> str:
    paternal = _gt_has_alt_allele(father_fmt or {})
    maternal = _gt_has_alt_allele(mother_fmt or {})
    if paternal and not maternal:
        return "paternal"
    if maternal and not paternal:
        return "maternal"
    return "und"


def _apcad_metadata(
    *,
    source: str,
    path: Path,
    line_no: int,
    extra: dict[str, Any] | None = None,
) -> str:
    return json.dumps(
        _jsonb_safe(
            {
                "source": source,
                "filename": path.name,
                "line_no": line_no,
                "uploaded_from": "family_package",
                **(extra or {}),
            }
        )
    )


def _apcad_row(
    *,
    sample_context: SampleMetadataContext,
    path: Path,
    line_no: int,
    chrom: str,
    start: int,
    end: int,
    record_id: str | None,
    value: float,
    origin: str,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "sample_id": sample_context.sample_uuid,
        "family_id": sample_context.family_uuid,
        "assembly_id": sample_context.assembly_id or "",
        "assembly_name": sample_context.assembly_name or "",
        "track_type": "apcad",
        "source": APCAD_SOURCE,
        "chr": normalize_chromosome(chrom),
        "start": start,
        "end": end,
        "record_id": record_id,
        "value": value,
        "origin": origin,
        "metadata_json": _apcad_metadata(
            source=APCAD_SOURCE,
            path=path,
            line_no=line_no,
            extra=extra_metadata,
        ),
    }


def _parse_apcad_interval_row(
    parts: list[str],
    *,
    header: dict[str, int] | None,
    sample_context: SampleMetadataContext,
    path: Path,
    line_no: int,
) -> dict[str, Any] | None:
    if header is not None:
        chrom = _normalized_header_value(parts, header, "chr", "chrom", "chromosome")
        start_raw = _normalized_header_value(parts, header, "start", "window_start")
        end_raw = _normalized_header_value(parts, header, "end", "stop", "window_end")
        pos_raw = _normalized_header_value(parts, header, "pos", "position")
        record_id = _normalized_header_value(parts, header, "id", "record_id", "name")
        value_raw = _normalized_header_value(parts, header, *_APCAD_VALUE_KEYS)
        origin_raw = _normalized_header_value(parts, header, *_APCAD_ORIGIN_KEYS)
        ref = _normalized_header_value(parts, header, "ref")
        alt = _normalized_header_value(parts, header, "alt")
    else:
        if len(parts) >= 7 and _coerce_int(parts[1]) is not None and _coerce_int(parts[2]) is None:
            chrom = parts[0]
            pos_raw = parts[1]
            start_raw = None
            end_raw = None
            ref = parts[2]
            alt = parts[3]
            record_id = parts[4]
            origin_raw = parts[5]
            value_raw = parts[6]
        elif len(parts) >= 6:
            chrom, start_raw, end_raw, record_id, value_raw, origin_raw = parts[:6]
            pos_raw = None
            ref = None
            alt = None
        else:
            return None
    if chrom is None:
        return None
    pos = _coordinate(pos_raw)
    start = _coordinate(start_raw)
    end = _coordinate(end_raw)
    if pos is not None and (start is None or end is None):
        start = max(0, pos - 1)
        end = pos
    value = _coerce_finite_float(value_raw)
    if start is None or end is None or value is None:
        return None
    return _apcad_row(
        sample_context=sample_context,
        path=path,
        line_no=line_no,
        chrom=chrom,
        start=start,
        end=end,
        record_id=None if record_id in (None, "", ".") else str(record_id),
        value=value,
        origin=_normalize_origin(origin_raw),
        extra_metadata={"ref": ref, "alt": alt},
    )


def _apcad_interval_rows(
    handle: Iterable[str],
    *,
    sample_context: SampleMetadataContext,
    path: Path,
) -> Iterator[dict[str, Any] | None]:
    """Each data line of an APCAD BED or table as the row it is stored as, None for a line
    left out. A header line names the columns of the lines after it."""
    header: dict[str, int] | None = None
    for line_no, line in enumerate(handle, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = _split_delimited_line(stripped)
        if header is None and _looks_like_interval_header(parts):
            header = _normalized_header_map(parts)
            continue
        yield _parse_apcad_interval_row(
            parts,
            header=header,
            sample_context=sample_context,
            path=path,
            line_no=line_no,
        )


async def _import_apcad_interval_file(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    path: Path,
) -> dict[str, int]:
    if not sample_context.assembly_name:
        raise RuntimeError("Cannot import APCAD interval tracks without an assembly name")
    async with _open_package_text(path) as handle:
        return await _store_track_rows(
            session, _apcad_interval_rows(handle, sample_context=sample_context, path=path)
        )


def _apcad_vcf_targets(
    fields: list[str],
    *,
    sample_names: list[str],
    sample_index_by_name: dict[str, int],
    sample_contexts: dict[str, SampleMetadataContext],
    selected_sample_id: str | None,
    selected_vcf_sample: str | None,
) -> list[tuple[str, SampleMetadataContext, dict[str, str]]]:
    """The samples an APCAD VCF record is read for, each with its FORMAT values: the
    selected sample, with the values of its column (``selected_vcf_sample``, else its id;
    no values when the file has no such column); else each column named after a sample of
    ``sample_contexts``; else, in a VCF without sample columns, every sample, whose value
    the record's INFO gives."""
    fmt_keys = fields[8].split(":") if len(fields) > 8 else []
    sample_fields = fields[9:] if len(fields) > 9 else []
    targets: list[tuple[str, SampleMetadataContext, dict[str, str]]] = []
    if selected_sample_id is not None:
        sample_context = sample_contexts.get(selected_sample_id)
        if sample_context is None:
            return targets
        vcf_sample_name = selected_vcf_sample or selected_sample_id
        sample_index = sample_index_by_name.get(vcf_sample_name)
        fmt_vals = (
            _parse_format(":".join(fmt_keys), sample_fields[sample_index])
            if sample_index is not None and sample_index < len(sample_fields)
            else {}
        )
        targets.append((selected_sample_id, sample_context, fmt_vals))
    elif sample_names:
        for sample_name, sample_field in zip(sample_names, sample_fields):
            sample_context = sample_contexts.get(sample_name)
            if sample_context is None:
                continue
            targets.append((sample_name, sample_context, _parse_format(":".join(fmt_keys), sample_field)))
    else:
        for sample_id, sample_context in sample_contexts.items():
            targets.append((sample_id, sample_context, {}))
    return targets


def _apcad_vcf_samples_with_values(
    path: Path,
    *,
    sample_contexts: dict[str, SampleMetadataContext],
    selected_sample_id: str | None = None,
    selected_vcf_sample: str | None = None,
) -> tuple[set[str], bool]:
    """Read the APCAD VCF ``path`` as ``_import_apcad_vcf_file`` reads it, storing nothing,
    until every sample it is read for (``_apcad_vcf_targets``) has a value it would store,
    or to its end: the samples it found a value for, and whether the file holds a record the
    reader reads at all."""
    wanted = {selected_sample_id} if selected_sample_id is not None else set(sample_contexts)
    found: set[str] = set()
    any_record = False
    sample_names: list[str] = []
    sample_index_by_name: dict[str, int] = {}
    with package_text_handle(path) as handle:
        for line in handle:
            if line.startswith("#CHROM"):
                sample_names = line.strip().split("\t")[9:]
                sample_index_by_name = {name: index for index, name in enumerate(sample_names)}
                continue
            if not line or line.startswith("#"):
                continue
            fields = line.rstrip("\n\r").split("\t")
            if len(fields) < 8 or _coerce_int(fields[1]) is None:
                continue
            any_record = True
            info = _parse_vcf_info(fields[7])
            for sample_id, _context, fmt_vals in _apcad_vcf_targets(
                fields,
                sample_names=sample_names,
                sample_index_by_name=sample_index_by_name,
                sample_contexts=sample_contexts,
                selected_sample_id=selected_sample_id,
                selected_vcf_sample=selected_vcf_sample,
            ):
                if sample_id not in found and _apcad_value(
                    info, fmt_vals, allow_info_fallback=not bool(sample_names)
                ) is not None:
                    found.add(sample_id)
            if wanted <= found:
                break
    return found, any_record


def _apcad_interval_file_holds_a_row(path: Path, *, sample_context: SampleMetadataContext) -> bool:
    """Whether an APCAD BED or table holds a row its reader stores, read up to the first."""
    with package_text_handle(path) as handle:
        first, _left_out = _first_stored_row(
            _apcad_interval_rows(handle, sample_context=sample_context, path=path)
        )
    return first is not None


async def _import_apcad_vcf_file(
    session: AsyncSession,
    *,
    path: Path,
    sample_contexts: dict[str, SampleMetadataContext],
    ped: ParsedPed | None = None,
    selected_sample_id: str | None = None,
    selected_vcf_sample: str | None = None,
    progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Store the APCAD of a VCF's sites as the samples' ``apcad`` track. ``progress`` is
    told the bytes of the file read (``bytes_read`` of ``bytes_total``) every
    ``APCAD_PROGRESS_RECORDS`` records: millions per embryo, minutes per file."""
    sample_names: list[str] = []
    sample_index_by_name: dict[str, int] = {}
    sample_results: dict[str, dict[str, int]] = {}
    batches: dict[str, list[dict[str, Any]]] = {}
    parent_ids_by_sample = (
        {
            member.iid: {
                "father": None if member.pid in {"", "0"} else member.pid,
                "mother": None if member.mid in {"", "0"} else member.mid,
            }
            for member in ped.members
        }
        if ped is not None
        else {}
    )

    async def flush_sample(sample_id: str) -> None:
        batch = batches.get(sample_id) or []
        if not batch:
            return
        await _insert_interval_track_rows(session, batch)
        sample_results.setdefault(sample_id, {"processed": 0, "inserted": 0, "skipped": 0})
        sample_results[sample_id]["inserted"] += len(batch)
        batches[sample_id] = []

    bytes_total = file_size(path) if progress is not None else None
    records_read = 0
    async with _open_package_text(path) as handle:
        for line_no, line in enumerate(handle, start=1):
            if line.startswith("#CHROM"):
                header = line.strip().split("\t")
                sample_names = header[9:]
                sample_index_by_name = {name: index for index, name in enumerate(sample_names)}
                continue
            if not line or line.startswith("#"):
                continue
            records_read += 1
            if progress is not None and records_read % APCAD_PROGRESS_RECORDS == 0:
                await progress(read_stats(bytes_read_on_disk(handle), bytes_total))
            fields = line.rstrip("\n\r").split("\t")
            if len(fields) < 8:
                continue
            chrom, pos_raw, record_id, ref, alt, qual, filt, info_raw = fields[:8]
            pos = _coordinate(pos_raw)
            if pos is None:
                continue
            info = _parse_vcf_info(info_raw)
            fmt_keys = fields[8].split(":") if len(fields) > 8 else []
            sample_fields = fields[9:] if len(fields) > 9 else []
            targets = _apcad_vcf_targets(
                fields,
                sample_names=sample_names,
                sample_index_by_name=sample_index_by_name,
                sample_contexts=sample_contexts,
                selected_sample_id=selected_sample_id,
                selected_vcf_sample=selected_vcf_sample,
            )

            for sample_id, sample_context, fmt_vals in targets:
                sample_results.setdefault(sample_id, {"processed": 0, "inserted": 0, "skipped": 0})
                sample_results[sample_id]["processed"] += 1
                value = _apcad_value(
                    info,
                    fmt_vals,
                    allow_info_fallback=not bool(sample_names),
                )
                if value is None:
                    sample_results[sample_id]["skipped"] += 1
                    continue
                origin = _apcad_origin(info, fmt_vals)
                if origin == "und" and sample_names:
                    parent_ids = parent_ids_by_sample.get(sample_id) or {}
                    father_fmt: dict[str, str] | None = None
                    mother_fmt: dict[str, str] | None = None
                    father_id = parent_ids.get("father")
                    if father_id:
                        father_index = sample_index_by_name.get(father_id)
                        if father_index is not None and father_index < len(sample_fields):
                            father_fmt = _parse_format(":".join(fmt_keys), sample_fields[father_index])
                    mother_id = parent_ids.get("mother")
                    if mother_id:
                        mother_index = sample_index_by_name.get(mother_id)
                        if mother_index is not None and mother_index < len(sample_fields):
                            mother_fmt = _parse_format(":".join(fmt_keys), sample_fields[mother_index])
                    origin = _infer_apcad_origin_from_parent_genotypes(
                        father_fmt=father_fmt,
                        mother_fmt=mother_fmt,
                    )
                row = _apcad_row(
                    sample_context=sample_context,
                    path=path,
                    line_no=line_no,
                    chrom=chrom,
                    start=max(0, pos - 1),
                    end=pos,
                    record_id=None if record_id in {"", "."} else record_id,
                    value=value,
                    origin=origin,
                    extra_metadata={
                        "ref": ref,
                        "alt": alt,
                        "qual": qual,
                        "filter": filt,
                        "vcf_sample": selected_vcf_sample or sample_id,
                    },
                )
                batches.setdefault(sample_id, []).append(row)
                if len(batches[sample_id]) >= INTERVAL_INSERT_BATCH_ROWS:
                    await flush_sample(sample_id)
    for sample_id in list(batches):
        await flush_sample(sample_id)
    return sample_results


async def _import_apcad_track_file(
    session: AsyncSession,
    *,
    sample_contexts: dict[str, SampleMetadataContext],
    path: Path,
    ped: ParsedPed | None = None,
    selected_sample_id: str | None = None,
    selected_vcf_sample: str | None = None,
    progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    check_only: bool = False,
) -> dict[str, Any]:
    """Store an APCAD file as the samples' ``apcad`` track, source ``apcad``, in place of the
    one this source stored before. HiFiCNV's minor allele fraction, on the same track type
    (source ``hificnv``), and a BED upload's APCAD (``web``) stay (SAFE-22).

    The file is read before any track is deleted (SAFE-29). It replaces the track of each
    sample it holds a value for: the selected sample, or each sample of ``sample_contexts``
    a VCF column is named after (every one for a BED or table, which names no sample, or a
    VCF without sample columns). A sample it holds no value for keeps its track, and a file
    that holds none for any of them is refused (``EmptyTrackFileError``) with every track as
    it was. A VCF in which the reader finds no record at all, read for a selected sample,
    deletes and stores nothing and returns ``{}``: the ``apcad`` dataset then reads it as a
    BED (``upload_bed_data``, which refuses a file without a BED record before it deletes).

    ``check_only`` stops before the delete, deleting and storing nothing, and returns an
    empty result for each sample whose track the file would replace (``{}`` for a VCF read
    as a BED, as above): the check a dataset of one APCAD file per embryo makes of every
    embryo's file before it clears any embryo's track (SAFE-3).
    """
    target_contexts = (
        {selected_sample_id: sample_contexts[selected_sample_id]}
        if selected_sample_id is not None and selected_sample_id in sample_contexts
        else sample_contexts
    )
    if not target_contexts:
        return {}
    if _is_vcf_file(path):
        found, any_record = await asyncio.to_thread(
            _apcad_vcf_samples_with_values,
            path,
            sample_contexts=sample_contexts,
            selected_sample_id=selected_sample_id,
            selected_vcf_sample=selected_vcf_sample,
        )
        if not any_record and selected_sample_id is not None:
            return {}
    else:
        holds_a_row = await asyncio.to_thread(
            _apcad_interval_file_holds_a_row,
            path,
            sample_context=next(iter(target_contexts.values())),
        )
        found = set(target_contexts) if holds_a_row else set()
    replaced = {
        sample_id: sample_context
        for sample_id, sample_context in target_contexts.items()
        if sample_id in found
    }
    if not replaced:
        raise EmptyTrackFileError(
            f"The APCAD file {path.name} holds no APCAD value for "
            f"{', '.join(sorted(target_contexts))}; no track was replaced"
        )
    if check_only:
        return {sample_id: {} for sample_id in replaced}
    for sample_context in replaced.values():
        await _delete_sample_interval_source(
            session,
            sample_context=sample_context,
            track_type="apcad",
            source=APCAD_SOURCE,
        )
    if _is_vcf_file(path):
        sample_results = await _import_apcad_vcf_file(
            session,
            path=path,
            sample_contexts=replaced,
            ped=ped,
            selected_sample_id=selected_sample_id,
            selected_vcf_sample=selected_vcf_sample,
            progress=progress,
        )
    else:
        sample_results = {}
        for sample_id, sample_context in replaced.items():
            sample_results[sample_id] = await _import_apcad_interval_file(
                session,
                sample_context=sample_context,
                path=path,
            )
    for sample_id, stats in sample_results.items():
        sample_context = sample_contexts.get(sample_id)
        if sample_context is None:
            continue
        inserted = int(stats.get("inserted", 0)) if isinstance(stats, dict) else 0
        await upsert_interval_track_source(
            session,
            sample_context=sample_context,
            track_type="apcad",
            source=APCAD_SOURCE,
            filename=path.name,
            row_count=inserted,
            metadata={
                "source": APCAD_SOURCE,
                "filename": path.name,
                "uploaded_from": "family_package",
            },
        )
    await session.commit()
    return sample_results


def _pcf_value(row: dict[str, Any], *names: str) -> str | None:
    normalized = {_normalize_header_key(str(key)): value for key, value in row.items()}
    for name in names:
        value = normalized.get(_normalize_header_key(name))
        if not _missing_scalar(value):
            return str(value)
    return None


def _parse_pcf_segment_row(
    row: dict[str, Any],
    *,
    sample_context: SampleMetadataContext,
    path: Path,
    line_no: int,
    origin: str,
) -> dict[str, Any] | None:
    chrom = _pcf_value(row, "CHROM", "chr", "chromosome")
    start = _coordinate(_pcf_value(row, "start.pos", "start", "start_pos"))
    end = _coordinate(_pcf_value(row, "end.pos", "end", "end_pos", "stop"))
    value = _coerce_finite_float(_pcf_value(row, "mean", "value", "seg.mean", "segment_mean"))
    if chrom is None or start is None or end is None or value is None:
        return None
    if end < start:
        return None

    sample_id_from_file = _pcf_value(row, "sampleID", "sample_id", "sample")
    arm = _pcf_value(row, "arm")
    n_probes = _coerce_int(_pcf_value(row, "n.probes", "n_probes", "probes"))
    normalized_chrom = normalize_chromosome(chrom)
    record_id_parts = [sample_context.sample_id, origin, normalized_chrom]
    if arm:
        record_id_parts.append(arm)
    record_id_parts.append(f"{start}-{end}")
    metadata = {
        "source": APCAD_PCF_SOURCE,
        "filename": path.name,
        "line_no": line_no,
        "uploaded_from": "family_package",
        "sample_id": sample_id_from_file,
        "arm": arm,
        "n_probes": n_probes,
    }
    return {
        "sample_id": sample_context.sample_uuid,
        "family_id": sample_context.family_uuid,
        "assembly_id": sample_context.assembly_id or "",
        "assembly_name": sample_context.assembly_name or "",
        "track_type": APCAD_PCF_TRACK_TYPE,
        "source": APCAD_PCF_SOURCE,
        "chr": normalized_chrom,
        "start": start,
        "end": end,
        "record_id": ":".join(record_id_parts),
        "value": value,
        "origin": origin,
        "metadata_json": json.dumps(_jsonb_safe(metadata)),
    }


def _pcf_segment_rows(
    handle: Iterable[str],
    *,
    sample_context: SampleMetadataContext,
    path: Path,
    origin: str,
) -> Iterator[dict[str, Any] | None]:
    """Each row of a PCF segment table (CSV with a header) as the row it is stored as,
    None for a row left out."""
    reader = csv.DictReader(handle)
    for raw_row in reader:
        yield _parse_pcf_segment_row(
            raw_row,
            sample_context=sample_context,
            path=path,
            line_no=reader.line_num,
            origin=origin,
        )


async def _import_pcf_segment_file(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    path: Path,
    origin: str,
    check_only: bool = False,
) -> dict[str, int]:
    """Store one parent's PCF segment table as rows of the sample's ``apcad_pcf`` track; the
    ``pcf`` dataset has deleted the sample's track first. A table without a segment is
    refused (``EmptyTrackFileError``, SAFE-29): the dataset reads each of a sample's tables
    with ``check_only``, up to its first segment, before it deletes the track."""
    if not sample_context.assembly_name:
        raise RuntimeError("Cannot import PCF segment tracks without an assembly name")
    async with _open_package_text(path) as handle:
        rows = _pcf_segment_rows(handle, sample_context=sample_context, path=path, origin=origin)
        first, left_out = _first_stored_row(rows)
        if first is None:
            raise EmptyTrackFileError(
                f"The PCF file {path.name} holds no segment; its track was not replaced"
            )
        if check_only:
            return {}
        result = await _store_track_rows(session, chain([first], rows), left_out=left_out)
    await upsert_interval_track_source(
        session,
        sample_context=sample_context,
        track_type=APCAD_PCF_TRACK_TYPE,
        source=APCAD_PCF_SOURCE,
        filename=path.name,
        row_count=result["inserted"],
        metadata={
            "source": APCAD_PCF_SOURCE,
            "filename": path.name,
            "origin": origin,
            "uploaded_from": "family_package",
        },
    )
    await session.commit()
    return result
