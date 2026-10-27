from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Dict, Iterable, Iterator, Literal, Optional

from .clickhouse_column_bounds import UINT32, column_int
from .data_scope import normalize_chromosome

StructuralVariantRecordFormat = Literal["manual", "sniffles", "spectre"]

BND_ALT_RE = re.compile(r"[\[\]]?([^:\[\]]+):(\d+)[\[\]]?")
# One symbolic allele (``<DEL>``, ``<DUP:TANDEM>``) and one sequence allele (bases only).
_SYMBOLIC_ALLELE_RE = re.compile(r"<([^<>,]+)>")
_SEQUENCE_ALLELE_RE = re.compile(r"[ACGTN]+", re.IGNORECASE)
# The first-level types of the symbolic alleles whose END VCF 4.4 puts at POS + SVLEN
# (INFO END): they span reference bases after their padding base. Every other allele (an
# <INS>, a breakend, a sequence allele) ends at POS + len(REF) - 1.
_SPANNING_SYMBOLIC_TYPES = frozenset({"DEL", "DUP", "INV", "CNV"})


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
    # The SVLEN the upload stores (``record_sv_len``): the record's own, else the length its
    # sequence allele states.
    svlen: int | None = None
    remote_chr: str | None = None
    remote_start: int | None = None
    remote_end: int | None = None
    # Phase set (PS) from the sample FORMAT — present only for phased (e.g. long-read) SV
    # calls; lets the SNV+SV second-hit analysis read cis/trans directly. None when unphased.
    phase_set: int | None = None
    # The sample's copy number (FORMAT/CN), which gives a CNV its direction
    # (``sv_copy_change``). None when the call states none.
    copy_number: int | None = None


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
        except (ValueError, OverflowError):
            return None


def info_value(info: Dict[str, str], key: str) -> str | None:
    """An INFO value, None when the record leaves it out or writes it missing (``.``)."""
    value = info.get(key)
    if value is None or value.strip() in {"", "."}:
        return None
    return value


def is_breakend_alt(alt: str) -> bool:
    """Whether ``alt`` is written in VCF breakend notation: a mate position in brackets
    (``N[5:3000000[``, ``]5:3000000]N``) or a single breakend, bases with a dot before or
    after them (``G.``, ``.G``)."""
    alt = alt.strip()
    if "[" in alt or "]" in alt:
        return True
    if alt.startswith("."):
        return bool(_SEQUENCE_ALLELE_RE.fullmatch(alt[1:]))
    if alt.endswith("."):
        return bool(_SEQUENCE_ALLELE_RE.fullmatch(alt[:-1]))
    return False


def sequence_allele_sv_len(ref: str, alt: str) -> int | None:
    """The length a call written as a sequence allele states by its alleles: len(ALT) −
    len(REF), the bases an insertion inserts (positive) or a deletion deletes (negative, the
    sign VCF 4.2 gives a deletion's SVLEN, as Sniffles2 and pbsv write it). VCF 4.4 writes
    SVLEN for a symbolic allele only, and ``.`` for a sequence allele, whose bases hold its
    length. None when REF or ALT is no sequence of bases (a symbolic allele, breakend
    notation, several ALT alleles, ``.`` or ``*``), or when the two are as long as each
    other: such alleles state no length."""
    ref, alt = ref.strip(), alt.strip()
    if not (_SEQUENCE_ALLELE_RE.fullmatch(ref) and _SEQUENCE_ALLELE_RE.fullmatch(alt)):
        return None
    return (len(alt) - len(ref)) or None


def record_sv_len(*, sv_len: int | None, ref: str, alt: str) -> int | None:
    """A record's SVLEN as CoGA stores it (``svLen``), the length the SV list shows and the
    length filters measure: its INFO/SVLEN as written, else the length its sequence allele
    states (``sequence_allele_sv_len``), else None, and the list measures the call by END −
    POS. Measuring an insertion written as its sequence without SVLEN by END − POS made it
    0 bp, so a minimum length dropped it (CLIN-41); a deletion written so measured its REF,
    as its alleles do."""
    return sv_len if sv_len is not None else sequence_allele_sv_len(ref, alt)


def sv_event(*, ref: str, alt: str, sv_len: int | None, start: int, end: int) -> tuple[str, str, int]:
    """A call's event within its SV id (``clickhouse_variant_ids.event_discriminators``): its
    REF, its ALT and the length the SV list measures it by, without its sign: the length it
    states (``record_sv_len``), else END − POS. A deletion written with SVLEN and one written
    with END alone are one event."""
    length = record_sv_len(sv_len=sv_len, ref=ref, alt=alt)
    return ref, alt, abs(length if length is not None else end - start)


def sv_type_from_alt(ref: str, alt: str) -> str | None:
    """The SV type a record's ALT allele states, for a record without INFO/SVTYPE, which
    VCF 4.4 deprecates as redundant with ALT: a symbolic allele states its name
    (``<DUP:TANDEM>`` is ``DUP:TANDEM``), breakend notation BND, and a sequence allele INS
    or DEL as it is longer or shorter than REF (``sequence_allele_sv_len``). None when the
    ALT states none: a sequence as long as REF, a missing ALT, several ALT alleles."""
    alt = alt.strip()
    symbolic = _SYMBOLIC_ALLELE_RE.fullmatch(alt)
    if symbolic:
        return symbolic.group(1)
    if is_breakend_alt(alt):
        return "BND"
    sv_len = sequence_allele_sv_len(ref, alt)
    if sv_len is None:
        return None
    return "INS" if sv_len > 0 else "DEL"


def structural_variant_end(*, pos: int, ref: str, alt: str, sv_len: int | None) -> int:
    """A record's END when its INFO has none, as VCF 4.4 computes it: POS + SVLEN (without
    its sign) for a ``<DEL>``, ``<DUP>``, ``<INV>`` or ``<CNV>`` allele, subtypes included,
    and POS + len(REF) - 1 for every other allele -- an ``<INS>``, a breakend, a sequence
    allele -- as for one of those four without an SVLEN, whose extent the record does not
    state. Reading such a record's END as its POS put a deletion on one base: the genes it
    spans were not listed with it, so a gene or panel search missed it (CLIN-33)."""
    symbolic = _SYMBOLIC_ALLELE_RE.fullmatch(alt.strip())
    if symbolic and sv_len is not None:
        if symbolic.group(1).split(":", 1)[0].upper() in _SPANNING_SYMBOLIC_TYPES:
            return pos + abs(sv_len)
    return pos + max(len(ref.strip()), 1) - 1


# The types of a translocation, a breakend joining two loci: BND, as VCF writes one, and TRA,
# as older callers (Sniffles 1) do. Compared in capitals.
_TRANSLOCATION_TYPES = frozenset({"BND", "TRA"})


def mate_chromosome(info: Dict[str, str], chrom: str) -> str | None:
    """INFO/CHR2 when it names another chromosome than the record's: the END such a record
    writes is its mate's position, not where it ends (a TRA as Sniffles 1 writes one). None
    without CHR2, and when CHR2 names the record's own chromosome."""
    chr2 = info_value(info, "CHR2")
    if chr2 is None:
        return None
    other = normalize_chromosome(chr2)
    return other if other and other != normalize_chromosome(chrom) else None


def breakend_mate(
    *, sv_type: str, alt: str, info: Dict[str, str], chrom: str
) -> tuple[Optional[str], Optional[int]]:
    """A breakend's mate, ``(chromosome, position)``, as every SV reader reads it: from its
    ALT written in breakend notation (``N]5:3000000]``), whatever its SVTYPE, and for a
    translocation, BND or TRA in any capitals, without one from INFO/CHR2 and its END when
    CHR2 names another chromosome. ``(None, None)`` for any other record, and for a single
    breakend (``G.``), which names no mate.

    The uploads read a mate only for an SVTYPE written ``BND``, so a ``bnd`` and a TRA
    written in breakend notation lost theirs; the chrM and HiFiCNV readers read none; and a
    TRA naming its mate in CHR2 and END was stored with its END on its own chromosome, so
    the tracks and Circos drew it there (CLIN-39)."""
    is_translocation = sv_type.strip().upper() in _TRANSLOCATION_TYPES
    if is_translocation or is_breakend_alt(alt):
        remote_chr, remote_start = parse_bnd_alt(alt)
        if remote_chr is not None:
            return remote_chr, remote_start
    other = mate_chromosome(info, chrom) if is_translocation else None
    if other is not None:
        return other, _position(info_value(info, "END"))
    return None, None


def record_end(
    *, pos: int, ref: str, alt: str, sv_len: int | None, info: Dict[str, str], chrom: str, end: Any = None
) -> int | None:
    """Where a record ends: its END (``end`` when the caller names it otherwise, else
    INFO/END) as written, else as VCF 4.4 computes it (``structural_variant_end``). An END
    that is a mate's position on another chromosome (``mate_chromosome``) is not the
    record's: such a record ends where its own breakpoint lies. None when the END written
    is no position."""
    written = end if end is not None else info_value(info, "END")
    if written is not None and mate_chromosome(info, chrom) is None:
        return _position(written)
    return structural_variant_end(pos=pos, ref=ref, alt=alt, sv_len=sv_len)


def record_sv_type(info: Dict[str, str], ref: str, alt: str) -> str:
    """A VCF record's SV type: its INFO/SVTYPE, else the type its ALT states
    (``sv_type_from_alt``), else ``SV``. Taking the ALT itself as the type stored a
    breakend as ``N]5:3000000]``, which neither the type filter nor the translocation rule
    of the length filters knows (CLIN-37)."""
    return info_value(info, "SVTYPE") or sv_type_from_alt(ref, alt) or "SV"


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
        except (TypeError, ValueError, OverflowError):
            return None


def _position(value: Any) -> int | None:
    """A record's POS or END: a whole number the SV tables' UInt32 position columns hold,
    else None, and the record is skipped as unpositionable (SAFE-30)."""
    return column_int(_coerce_int(value), UINT32)


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
        start = _position(start_s)
        end = _position(end_s)
        if start is None or end is None:
            # Unpositionable record — skip rather than raise mid-ingest.
            continue
        # A TSV has no INFO: a breakend's mate is the one its ALT names.
        remote_chr, remote_start = breakend_mate(sv_type=svtype, alt=alt, info={}, chrom=chrom)
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
            # A TSV has no SVLEN column: a row written as a sequence allele states its length.
            svlen=record_sv_len(sv_len=None, ref=ref, alt=alt),
            remote_chr=remote_chr,
            remote_start=remote_start,
            remote_end=remote_start,
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
        svtype = record_sv_type(info, ref, alt)
        svlen = parse_svlen(info.get("SVLEN"))

        start = _position(pos)
        if start is None:
            continue
        end = _position(record_end(pos=start, ref=ref, alt=alt, sv_len=svlen, info=info, chrom=chrom))
        if end is None:
            continue
        remote_chr, remote_start = breakend_mate(sv_type=svtype, alt=alt, info=info, chrom=chrom)

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
            svlen=record_sv_len(sv_len=svlen, ref=ref, alt=alt),
            remote_chr=remote_chr,
            remote_start=remote_start,
            remote_end=remote_start,
            phase_set=parse_phase_set(fmt_vals.get("PS")),
            copy_number=_coerce_int(fmt_vals.get("CN")),
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
        svlen = parse_svlen(info.get("SVLEN"))
        end_value = info_value(info, "END")
        try:
            chrom, start = split_chrom_pos(chrom_raw, pos)
            if end_value is not None:
                end = parse_end(end_value)
            elif "-" in pos.split(":", 1)[-1]:
                # A POS written as a range (``start-end``) carries the record's END.
                end = parse_end(pos)
            else:
                end = structural_variant_end(pos=start, ref=ref, alt=alt, sv_len=svlen)
        except (TypeError, ValueError):
            # Unpositionable record — skip rather than raise mid-ingest.
            continue
        if _position(start) is None or _position(end) is None:
            continue

        yield ParsedStructuralVariant(
            variant_id=variant_id,
            chrom=normalize_chromosome(chrom),
            start=start,
            end=end,
            ref=ref,
            alt=alt,
            svtype=record_sv_type(info, ref, alt),
            gt=fmt_vals.get("GT", "./."),
            info=info,
            qual=_coerce_qual(qual),
            filter=filt or None,
            svlen=record_sv_len(sv_len=svlen, ref=ref, alt=alt),
            phase_set=parse_phase_set(fmt_vals.get("PS")),
            copy_number=_coerce_int(fmt_vals.get("CN")),
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
