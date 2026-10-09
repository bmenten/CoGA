from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Dict, Iterable, Iterator, Literal, Optional

from .data_scope import normalize_chromosome

StructuralVariantRecordFormat = Literal["manual", "sniffles", "spectre"]

BND_ALT_RE = re.compile(r"[\[\]]?([^:\[\]]+):(\d+)[\[\]]?")


@dataclass(frozen=True)
class ParsedStructuralVariant:
    variant_id: str
    chrom: str
    start: int
    end: int
    ref: str
    alt: str
    svtype: str
    gt: str
    info: Dict[str, str]
    qual: float | None = None
    filter: str | None = None
    svlen: int | None = None
    remote_chr: str | None = None
    remote_start: int | None = None
    remote_end: int | None = None
    # Phase set (PS) from the sample FORMAT — present only for phased (e.g. long-read) SV
    # calls; lets the SNV+SV second-hit analysis read cis/trans directly. None when unphased.
    phase_set: int | None = None


def parse_info(info_field: str) -> Dict[str, str]:
    info: Dict[str, str] = {}
    if info_field and info_field != ".":
        for item in info_field.split(";"):
            if "=" in item:
                key, value = item.split("=", 1)
                info[key] = value
    return info


def parse_format(format_field: str, sample_field: str) -> Dict[str, str]:
    keys = format_field.split(":")
    values = sample_field.split(":")
    return {key: value for key, value in zip(keys, values)}


def parse_phase_set(value: str | None) -> int | None:
    """The PS FORMAT value as an int, or None when absent/missing ('.', '')."""
    if value in (None, "", "."):
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def parse_bnd_alt(alt: str) -> tuple[Optional[str], Optional[int]]:
    match = BND_ALT_RE.search(alt)
    if match:
        chrom, pos = match.groups()
        return normalize_chromosome(chrom), int(pos)
    return None, None


def split_chrom_pos(chrom: str, pos: str) -> tuple[str, int]:
    if ":" in pos:
        chrom_from_pos, pos = pos.split(":", 1)
        chrom = chrom_from_pos or chrom
    if "-" in pos:
        pos, _ = pos.split("-", 1)
    if not pos.isdigit():
        raise ValueError(f"Unparsable POS field: {pos}")
    return chrom, int(pos)


def parse_end(end_val: str) -> int:
    if ":" in end_val:
        _, end_val = end_val.split(":", 1)
    if "-" in end_val:
        _, end_val = end_val.split("-", 1)
    return int(end_val)


def parse_svlen(value: str | None) -> int | None:
    if value is None:
        return None
    value = value.strip()
    if value in {"", "."}:
        return None
    try:
        return int(value)
    except ValueError:
        try:
            return int(float(value))
        except ValueError:
            return None


def _coerce_int(value: Any) -> int | None:
    """Parse a required integer coordinate; None when unparseable/missing so a
    malformed record can be skipped rather than aborting the whole ingest."""
    if value is None or str(value).strip() in {"", "."}:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        try:
            return int(float(str(value).strip()))
        except (TypeError, ValueError):
            return None


def _coerce_qual(value: Any) -> float | None:
    """Parse the optional QUAL column; None (not an error) when missing/non-finite."""
    if value is None or str(value).strip() in {"", "."}:
        return None
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _iter_manual_records(lines: Iterable[str]) -> Iterator[ParsedStructuralVariant]:
    for line in lines:
        if not line or line.startswith("#"):
            continue
        parts = line.strip().split()
        if len(parts) < 8:
            continue
        variant_id, chrom, start_s, end_s, ref, alt, svtype, gt = parts[:8]
        start = _coerce_int(start_s)
        end = _coerce_int(end_s)
        if start is None or end is None:
            # Unpositionable record — skip rather than raise mid-ingest.
            continue
        remote_chr: str | None = None
        remote_start: int | None = None
        remote_end: int | None = None
        if svtype == "BND":
            remote_chr, remote_start = parse_bnd_alt(alt)
            if remote_start is not None:
                remote_end = remote_start
        yield ParsedStructuralVariant(
            variant_id=variant_id,
            chrom=normalize_chromosome(chrom),
            start=start,
            end=end,
            ref=ref,
            alt=alt,
            svtype=svtype,
            gt=gt,
            info={},
            remote_chr=remote_chr,
            remote_start=remote_start,
            remote_end=remote_end,
        )


def _iter_sniffles_records(
    lines: Iterable[str], sample_column: int = 0
) -> Iterator[ParsedStructuralVariant]:
    for line in lines:
        if not line or line.startswith("#"):
            continue
        parts = line.strip().split("\t")
        if len(parts) < 10 + sample_column:
            continue

        chrom, pos, variant_id, ref, alt, qual, filt, info_f, fmt = parts[:9]
        sample_f = parts[9 + sample_column]
        info = parse_info(info_f)
        fmt_vals = parse_format(fmt, sample_f)
        svtype = info.get("SVTYPE", alt.strip("<>"))
        remote_chr: str | None = None
        remote_start: int | None = None
        remote_end: int | None = None

        start = _coerce_int(pos)
        end = _coerce_int(info.get("END", pos))
        if start is None or end is None:
            continue

        if svtype == "BND":
            remote_chr, remote_start = parse_bnd_alt(alt)
            if remote_start is not None:
                remote_end = remote_start

        yield ParsedStructuralVariant(
            variant_id=variant_id,
            chrom=normalize_chromosome(chrom),
            start=start,
            end=end,
            ref=ref,
            alt=alt,
            svtype=svtype,
            gt=fmt_vals.get("GT", "./."),
            info=info,
            qual=_coerce_qual(qual),
            filter=filt or None,
            svlen=parse_svlen(info.get("SVLEN")),
            remote_chr=remote_chr,
            remote_start=remote_start,
            remote_end=remote_end,
            phase_set=parse_phase_set(fmt_vals.get("PS")),
        )


def _iter_spectre_records(
    lines: Iterable[str], sample_column: int = 0
) -> Iterator[ParsedStructuralVariant]:
    for line in lines:
        if not line or line.startswith("#"):
            continue
        parts = line.strip().split("\t")
        if len(parts) < 10 + sample_column:
            continue

        chrom_raw, pos, variant_id, ref, alt, qual, filt, info_f, fmt = parts[:9]
        sample_f = parts[9 + sample_column]
        info = parse_info(info_f)
        fmt_vals = parse_format(fmt, sample_f)
        try:
            chrom, start = split_chrom_pos(chrom_raw, pos)
            end = parse_end(info.get("END", pos))
        except (TypeError, ValueError):
            # Unpositionable record — skip rather than raise mid-ingest.
            continue

        yield ParsedStructuralVariant(
            variant_id=variant_id,
            chrom=normalize_chromosome(chrom),
            start=start,
            end=end,
            ref=ref,
            alt=alt,
            svtype=info.get("SVTYPE", alt.strip("<>")),
            gt=fmt_vals.get("GT", "./."),
            info=info,
            qual=_coerce_qual(qual),
            filter=filt or None,
            svlen=parse_svlen(info.get("SVLEN")),
            phase_set=parse_phase_set(fmt_vals.get("PS")),
        )


def iter_structural_variant_records(
    text: str,
    record_format: StructuralVariantRecordFormat,
    *,
    sample_column: int = 0,
) -> Iterator[ParsedStructuralVariant]:
    """The records of an uploaded SV file. A Sniffles or Spectre VCF's call is read from
    its ``#CHROM`` sample column ``sample_column`` (0 is the first), the one the upload
    checked holds its sample (``per_sample_vcf_column``); a record without that column is
    skipped. A manual TSV has no sample column: each row is one call of its sample."""
    lines = text.splitlines()
    if record_format == "manual":
        return _iter_manual_records(lines)
    if record_format == "sniffles":
        return _iter_sniffles_records(lines, sample_column)
    if record_format == "spectre":
        return _iter_spectre_records(lines, sample_column)
    raise ValueError(f"Unsupported structural variant record format: {record_format}")
