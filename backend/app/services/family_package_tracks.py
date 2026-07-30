from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
import re
from typing import Any, Awaitable, Callable

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

from .family_package_common import APCAD_PCF_SOURCE, APCAD_PCF_TRACK_TYPE, ParsedPed, _coerce_finite_float, _coerce_int, _is_vcf_file, _jsonb_safe, _missing_scalar, _normalize_header_key, _open_package_text, _parse_format, _parse_vcf_info  # noqa: F401


logger = logging.getLogger(__name__)


async def _delete_sample_interval_track(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    track_type: str,
) -> None:
    if not sample_context.assembly_name:
        raise RuntimeError("Cannot delete interval tracks without an assembly name")
    await delete_interval_tracks(
        sample_context.assembly_name,
        sample_uuid=sample_context.sample_uuid,
        track_type=track_type,
    )
    await delete_interval_track_sources(
        session,
        sample_uuid=sample_context.sample_uuid,
        track_type=track_type,
    )


async def _delete_sample_interval_source(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    track_type: str,
    source: str,
    filename: str | None = None,
) -> None:
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

    start = _coerce_int(start_raw)
    end = _coerce_int(end_raw)
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

    start = _coerce_int(start_raw)
    end = _coerce_int(end_raw)
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


async def _import_wisecondorx_track(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    path: Path,
    track_type: str,
    progress: Callable[[dict[str, int]], Awaitable[None]] | None = None,
) -> dict[str, int]:
    if not sample_context.assembly_name:
        raise RuntimeError("Cannot import WisecondorX interval tracks without an assembly name")
    await _delete_sample_interval_source(
        session,
        sample_context=sample_context,
        track_type=track_type,
        source="wisecondorx",
        filename=path.name,
    )

    processed = 0
    inserted = 0
    skipped = 0
    last_reported = 0
    batch: list[dict[str, Any]] = []
    header: dict[str, int] | None = None
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split("\t") if "\t" in stripped else stripped.split()
            lowered = [part.strip().lower() for part in parts]
            if header is None and {"chr", "start", "end"}.issubset(set(lowered)):
                header = _header_map(parts)
                continue
            processed += 1
            row = _parse_wisecondorx_interval_row(
                parts,
                header=header,
                sample_context=sample_context,
                track_type=track_type,
                path=path,
                line_no=line_no,
            )
            if row is None:
                skipped += 1
                continue
            batch.append(row)
            if len(batch) >= 5000:
                await _insert_interval_track_rows(session, batch)
                inserted += len(batch)
                batch = []
                if progress is not None and processed - last_reported >= 50000:
                    last_reported = processed
                    await progress(
                        {
                            "processed": processed,
                            "inserted": inserted,
                            "skipped": skipped,
                        }
                    )
    if batch:
        await _insert_interval_track_rows(session, batch)
        inserted += len(batch)
    await upsert_interval_track_source(
        session,
        sample_context=sample_context,
        track_type=track_type,
        source="wisecondorx",
        filename=path.name,
        row_count=inserted,
        metadata={
            "source": "wisecondorx",
            "filename": path.name,
            "uploaded_from": "family_package",
        },
    )
    await session.commit()
    result = {
        "processed": processed,
        "inserted": inserted,
        "skipped": skipped,
    }
    if progress is not None:
        await progress(result)
    return result


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
) -> dict[str, int]:
    """Import a delimited copy-number file as interval-track rows.

    ``value_transform`` converts each value before storage, for a caller whose file
    holds a different quantity from the track's axis -- HiFiCNV's bedGraph carries
    integer copy number where the segments track holds a log2 ratio.
    """

    if not sample_context.assembly_name:
        raise RuntimeError("Cannot import copy-number interval tracks without an assembly name")
    await _delete_sample_interval_source(
        session,
        sample_context=sample_context,
        track_type=track_type,
        source=source,
        filename=path.name,
    )

    processed = 0
    inserted = 0
    skipped = 0
    last_reported = 0
    batch: list[dict[str, Any]] = []
    header: dict[str, int] | None = None
    async with _open_package_text(path) as handle:
        for line_no, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = _split_delimited_line(stripped)
            if header is None and _looks_like_interval_header(parts):
                header = _normalized_header_map(parts)
                continue
            processed += 1
            row = _parse_copy_number_interval_row(
                parts,
                header=header,
                sample_context=sample_context,
                track_type=track_type,
                source=source,
                path=path,
                line_no=line_no,
            )
            if row is None:
                skipped += 1
                continue
            if value_transform is not None and row.get("value") is not None:
                row["value"] = value_transform(float(row["value"]))
            batch.append(row)
            if len(batch) >= 5000:
                await _insert_interval_track_rows(session, batch)
                inserted += len(batch)
                batch = []
                if progress is not None and processed - last_reported >= 50000:
                    last_reported = processed
                    await progress(
                        {
                            "processed": processed,
                            "inserted": inserted,
                            "skipped": skipped,
                        }
                    )
    if batch:
        await _insert_interval_track_rows(session, batch)
        inserted += len(batch)
    await upsert_interval_track_source(
        session,
        sample_context=sample_context,
        track_type=track_type,
        source=source,
        filename=path.name,
        row_count=inserted,
        metadata={
            "source": source,
            "filename": path.name,
            "uploaded_from": "family_package",
        },
    )
    await session.commit()
    result = {
        "processed": processed,
        "inserted": inserted,
        "skipped": skipped,
    }
    if progress is not None:
        await progress(result)
    return result


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
    progress: Callable[[dict[str, int]], Awaitable[None]] | None = None,
) -> dict[str, int]:
    """Import a bigWig signal file as interval-track rows.

    Same contract as :func:`_import_copy_number_track` -- replace this
    (track_type, source, filename) triple, stream rows in batches, then record
    the source -- but reading a binary signal file instead of a delimited one.

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
    await _delete_sample_interval_source(
        session,
        sample_context=sample_context,
        track_type=track_type,
        source=source,
        filename=path.name,
    )

    processed = 0
    inserted = 0
    last_reported = 0
    batch: list[dict[str, Any]] = []
    reader = open_bigwig(path)
    try:
        for chrom, start, end, raw_value in iter_bigwig_intervals(reader, skip_zero=skip_zero):
            processed += 1
            value = raw_value if value_transform is None else value_transform(raw_value)
            batch.append(
                {
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
                    "metadata_json": json.dumps(
                        _jsonb_safe({"source": source, "filename": path.name})
                    ),
                }
            )
            if len(batch) >= 5000:
                await _insert_interval_track_rows(session, batch)
                inserted += len(batch)
                batch = []
                if progress is not None and processed - last_reported >= 50000:
                    last_reported = processed
                    await progress({"processed": processed, "inserted": inserted, "skipped": 0})
    finally:
        reader.close()
    if batch:
        await _insert_interval_track_rows(session, batch)
        inserted += len(batch)

    await upsert_interval_track_source(
        session,
        sample_context=sample_context,
        track_type=track_type,
        source=source,
        filename=path.name,
        row_count=inserted,
        metadata={
            "source": source,
            "filename": path.name,
            "uploaded_from": "family_package",
            "format": "bigwig",
            **(extra_metadata or {}),
        },
    )
    await session.commit()
    result = {"processed": processed, "inserted": inserted, "skipped": processed - inserted}
    if progress is not None:
        await progress(result)
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
        "source": "apcad",
        "chr": normalize_chromosome(chrom),
        "start": start,
        "end": end,
        "record_id": record_id,
        "value": value,
        "origin": origin,
        "metadata_json": _apcad_metadata(
            source="apcad",
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
    pos = _coerce_int(pos_raw)
    start = _coerce_int(start_raw)
    end = _coerce_int(end_raw)
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


async def _import_apcad_interval_file(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    path: Path,
) -> dict[str, int]:
    if not sample_context.assembly_name:
        raise RuntimeError("Cannot import APCAD interval tracks without an assembly name")
    processed = 0
    inserted = 0
    skipped = 0
    batch: list[dict[str, Any]] = []
    header: dict[str, int] | None = None
    async with _open_package_text(path) as handle:
        for line_no, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = _split_delimited_line(stripped)
            if header is None and _looks_like_interval_header(parts):
                header = _normalized_header_map(parts)
                continue
            processed += 1
            row = _parse_apcad_interval_row(
                parts,
                header=header,
                sample_context=sample_context,
                path=path,
                line_no=line_no,
            )
            if row is None:
                skipped += 1
                continue
            batch.append(row)
            if len(batch) >= 5000:
                await _insert_interval_track_rows(session, batch)
                inserted += len(batch)
                batch = []
    if batch:
        await _insert_interval_track_rows(session, batch)
        inserted += len(batch)
    return {"processed": processed, "inserted": inserted, "skipped": skipped}


async def _import_apcad_vcf_file(
    session: AsyncSession,
    *,
    path: Path,
    sample_contexts: dict[str, SampleMetadataContext],
    ped: ParsedPed | None = None,
    selected_sample_id: str | None = None,
    selected_vcf_sample: str | None = None,
) -> dict[str, Any]:
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

    async with _open_package_text(path) as handle:
        for line_no, line in enumerate(handle, start=1):
            if line.startswith("#CHROM"):
                header = line.strip().split("\t")
                sample_names = header[9:]
                sample_index_by_name = {name: index for index, name in enumerate(sample_names)}
                continue
            if not line or line.startswith("#"):
                continue
            fields = line.rstrip("\n\r").split("\t")
            if len(fields) < 8:
                continue
            chrom, pos_raw, record_id, ref, alt, qual, filt, info_raw = fields[:8]
            pos = _coerce_int(pos_raw)
            if pos is None:
                continue
            info = _parse_vcf_info(info_raw)
            fmt_keys = fields[8].split(":") if len(fields) > 8 else []
            sample_fields = fields[9:] if len(fields) > 9 else []
            targets: list[tuple[str, SampleMetadataContext, dict[str, str]]] = []
            if selected_sample_id is not None:
                sample_context = sample_contexts.get(selected_sample_id)
                if sample_context is None:
                    continue
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
                if len(batches[sample_id]) >= 5000:
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
) -> dict[str, Any]:
    target_contexts = (
        {selected_sample_id: sample_contexts[selected_sample_id]}
        if selected_sample_id is not None and selected_sample_id in sample_contexts
        else sample_contexts
    )
    for sample_context in target_contexts.values():
        await _delete_sample_interval_track(
            session,
            sample_context=sample_context,
            track_type="apcad",
        )
    if _is_vcf_file(path):
        sample_results = await _import_apcad_vcf_file(
            session,
            path=path,
            sample_contexts=sample_contexts,
            ped=ped,
            selected_sample_id=selected_sample_id,
            selected_vcf_sample=selected_vcf_sample,
        )
    else:
        sample_results = {}
        for sample_id, sample_context in target_contexts.items():
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
            source="apcad",
            filename=path.name,
            row_count=inserted,
            metadata={
                "source": "apcad",
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
    start = _coerce_int(_pcf_value(row, "start.pos", "start", "start_pos"))
    end = _coerce_int(_pcf_value(row, "end.pos", "end", "end_pos", "stop"))
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


async def _import_pcf_segment_file(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    path: Path,
    origin: str,
) -> dict[str, int]:
    if not sample_context.assembly_name:
        raise RuntimeError("Cannot import PCF segment tracks without an assembly name")
    processed = 0
    inserted = 0
    skipped = 0
    batch: list[dict[str, Any]] = []
    async with _open_package_text(path) as handle:
        reader = csv.DictReader(handle)
        for raw_row in reader:
            processed += 1
            row = _parse_pcf_segment_row(
                raw_row,
                sample_context=sample_context,
                path=path,
                line_no=reader.line_num,
                origin=origin,
            )
            if row is None:
                skipped += 1
                continue
            batch.append(row)
            if len(batch) >= 5000:
                await _insert_interval_track_rows(session, batch)
                inserted += len(batch)
                batch = []
    if batch:
        await _insert_interval_track_rows(session, batch)
        inserted += len(batch)
    await upsert_interval_track_source(
        session,
        sample_context=sample_context,
        track_type=APCAD_PCF_TRACK_TYPE,
        source=APCAD_PCF_SOURCE,
        filename=path.name,
        row_count=inserted,
        metadata={
            "source": APCAD_PCF_SOURCE,
            "filename": path.name,
            "origin": origin,
            "uploaded_from": "family_package",
        },
    )
    await session.commit()
    return {"processed": processed, "inserted": inserted, "skipped": skipped}
