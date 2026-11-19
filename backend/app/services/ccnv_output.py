"""The output of ccnv, the laboratory's copy-number tool for shallow whole-genome sequencing,
in its prenatal mode: a conventional NIPT of a maternal plasma (cfDNA).

ccnv writes, per sample (``<sample>_…``), five files that CoGA reads four of:

- ``_bins.bed.gz``: each 100 kb bin's log2 ratio (``ratio``) against ccnv's reference, its
  z-score, precision weight, reference size, whether ccnv masked it, and its reference branch
  (``autosomal``; ``female_sex`` for chrX, which prenatal mode normalises against the diploid
  XX reference). A masked bin has no ratio and is not stored.
- ``_segments.bed.gz``: the segments with their weighted log2 ratio, z-score, bin count and
  call (``gain``, ``loss`` or none), the log2 ratio a fetal trisomy or monosomy is expected at
  (``expected_ratio``), the fetal fraction the segment's ratio implies (``implied_ff``), whether
  the segment is discordant with the fetal fraction and why, its fragment-length statistics and
  its fragmentomic origin call.
- ``_calls.vcf.gz``: the segments called gain or loss, as ``<DUP>`` and ``<DEL>`` records with
  their values in FORMAT and INFO, and the run's fetal sex, fetal fraction and sex-chromosome
  call in its header. The ``_calls.bed.gz`` and ``_segments.vcf.gz`` copies are not read.
- ``_qc.json``: the run's read metrics, its fetal fractions (the fused ``active_ff``, the
  Fisher-weighted ``effective_ff`` and each estimator's), the fetal sex, the sex-chromosome
  aneuploidy call, the limits of detection and its warnings.

This module reads the files and refuses what is not a ccnv prenatal output; it touches no
database. ccnv's own description of its methods: https://matthdsm.github.io/ccnv/.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Iterable, Iterator, Mapping

from .coga_package import _DuplicateKey, _non_finite, _object_without_duplicate_keys, _refuse_constant, _unstorable
from .data_scope import normalize_chromosome


#: The ``source`` ccnv's tracks and calls are stored under.
CCNV_SOURCE = "ccnv"

#: The files a ccnv dataset's entry names for its sample, each required.
CCNV_ROLES: tuple[str, ...] = ("bins", "segments", "calls", "qc")

#: The mode a conventional NIPT's ccnv output is of. ccnv's other mode, ``germline``, reads a
#: constitutional sample, and its output carries no fetal fraction.
CCNV_PRENATAL_MODE = "prenatal"

#: The major version of the QC file's format CoGA reads (``schema_version`` 2.x.y).
CCNV_QC_SCHEMA_MAJOR = 2

#: A QC file is a few kilobytes; one larger than this is no ccnv QC file.
CCNV_QC_MAX_BYTES = 1024 * 1024

#: The header lines of the calls VCF that state the run's results, which must agree with its
#: QC file: two files of different runs or samples cannot be read as one result.
_VCF_FETAL_SEX = "##fetalSex="
_VCF_FETAL_FRACTION = "##fetalFraction="
_VCF_SCA_CALL = "##scaCall="

#: The calls VCF writes the fetal fraction rounded to four decimals.
_VCF_FETAL_FRACTION_TOLERANCE = 0.00005 + 1e-9


class CcnvOutputError(ValueError):
    """A ccnv file that is not the prenatal output CoGA reads, said in a sentence that names the
    file and never its values."""


@dataclass(frozen=True, slots=True)
class CcnvQc:
    """A ccnv prenatal QC file: the values CoGA checks the other files against, and the file's
    object as it was read (``payload``), which the import stores."""

    sample: str
    schema_version: str
    fetal_sex: str
    sca_call: str
    active_ff: float
    effective_ff: float
    payload: dict[str, Any]


def _number(value: Any) -> float | None:
    """A JSON number (not a boolean) as a float, or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _fraction(block: Mapping[str, Any], key: str, *, file: str) -> float:
    """A fetal fraction the QC file must give: a number from 0 to 1."""
    value = _number(block.get(key))
    if value is None or not 0.0 <= value <= 1.0:
        raise CcnvOutputError(f"The ccnv QC file {file} gives no fetal fraction prenatal.{key} from 0 to 1")
    return value


def _text(block: Mapping[str, Any], key: str, *, file: str, where: str) -> str:
    value = block.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CcnvOutputError(f"The ccnv QC file {file} gives no {where}{key}")
    return value.strip()


def read_ccnv_qc(raw: bytes, *, file: str) -> CcnvQc:
    """The ccnv QC file ``file`` (its bytes ``raw``), read strictly: one JSON object, no key
    given twice in an object, no NaN or Infinity, no NUL or lone surrogate (no stored JSON
    holds them); of the format version CoGA reads; of ccnv's prenatal mode; naming its sample;
    with the fetal fractions, the fetal sex and the sex-chromosome call. Raises
    ``CcnvOutputError`` otherwise."""
    if len(raw) > CCNV_QC_MAX_BYTES:
        raise CcnvOutputError(f"The ccnv QC file {file} is larger than a ccnv QC file is")
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_refuse_constant,
        )
    except _DuplicateKey as exc:
        raise CcnvOutputError(f"The ccnv QC file {file} gives a key twice in one object") from exc
    except (UnicodeDecodeError, ValueError) as exc:
        raise CcnvOutputError(f"The ccnv QC file {file} is no JSON text") from exc
    if not isinstance(payload, dict):
        raise CcnvOutputError(f"The ccnv QC file {file} is no JSON object")
    if _unstorable(payload):
        raise CcnvOutputError(f"The ccnv QC file {file} holds a NUL or a lone surrogate")
    if _non_finite(payload):
        raise CcnvOutputError(f"The ccnv QC file {file} holds a number out of range")
    schema_version = payload.get("schema_version")
    major = str(schema_version).split(".", 1)[0] if isinstance(schema_version, str) else ""
    if major != str(CCNV_QC_SCHEMA_MAJOR):
        raise CcnvOutputError(
            f"The ccnv QC file {file} is not of the format version {CCNV_QC_SCHEMA_MAJOR}.x "
            "CoGA reads (schema_version)"
        )
    mode = payload.get("mode")
    if mode != CCNV_PRENATAL_MODE:
        raise CcnvOutputError(
            f"The ccnv QC file {file} is not of ccnv's prenatal mode: CoGA reads ccnv's output of a "
            "conventional NIPT"
        )
    sample = _text(payload, "sample", file=file, where="")
    prenatal = payload.get("prenatal")
    if not isinstance(prenatal, dict):
        raise CcnvOutputError(f"The ccnv QC file {file} holds no prenatal results")
    sca = prenatal.get("sca")
    if not isinstance(sca, dict):
        raise CcnvOutputError(f"The ccnv QC file {file} holds no sex-chromosome call (prenatal.sca)")
    return CcnvQc(
        sample=sample,
        schema_version=str(schema_version),
        fetal_sex=_text(prenatal, "fetal_sex", file=file, where="prenatal."),
        sca_call=_text(sca, "call", file=file, where="prenatal.sca."),
        active_ff=_fraction(prenatal, "active_ff", file=file),
        effective_ff=_fraction(prenatal, "effective_ff", file=file),
        payload=payload,
    )


def ccnv_vcf_header_problems(header_lines: Iterable[str], qc: CcnvQc, *, file: str) -> list[str]:
    """What in the calls VCF ``file``'s header (its ``##`` lines) does not agree with the QC
    file of its entry: the fetal sex, the fetal fraction (the QC file's ``active_ff``, which the
    VCF writes to four decimals) and the sex-chromosome call each run states in both. A line
    missing is a problem too: ccnv's prenatal VCF writes all three. Empty when they agree."""
    stated: dict[str, str] = {}
    for line in header_lines:
        text = line.rstrip("\r\n")
        for prefix in (_VCF_FETAL_SEX, _VCF_FETAL_FRACTION, _VCF_SCA_CALL):
            if text.startswith(prefix):
                stated.setdefault(prefix, text[len(prefix) :].strip())
    problems: list[str] = []
    fetal_sex = stated.get(_VCF_FETAL_SEX)
    if fetal_sex is None:
        problems.append(f"The ccnv calls VCF {file} states no fetal sex (##fetalSex)")
    elif fetal_sex != qc.fetal_sex:
        problems.append(f"The ccnv calls VCF {file} states another fetal sex than its QC file")
    fraction_text = stated.get(_VCF_FETAL_FRACTION)
    fraction: float | None = None
    if fraction_text is not None:
        try:
            fraction = float(fraction_text)
        except ValueError:
            fraction = None
    if fraction is None or not math.isfinite(fraction):
        problems.append(f"The ccnv calls VCF {file} states no fetal fraction (##fetalFraction)")
    elif abs(fraction - qc.active_ff) > _VCF_FETAL_FRACTION_TOLERANCE:
        problems.append(f"The ccnv calls VCF {file} states another fetal fraction than its QC file")
    sca_call = stated.get(_VCF_SCA_CALL)
    if sca_call is None:
        problems.append(f"The ccnv calls VCF {file} states no sex-chromosome call (##scaCall)")
    elif sca_call != qc.sca_call:
        problems.append(f"The ccnv calls VCF {file} states another sex-chromosome call than its QC file")
    return problems


#: The columns each track file must have: what CoGA stores, draws or reads the result from.
_REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "coverage": ("contig", "start", "end", "ratio", "zscore", "weight", "masked"),
    "segments": (
        "contig",
        "start",
        "end",
        "ratio",
        "zscore",
        "bin_count",
        "call",
        "expected_ratio",
        "implied_ff",
        "discordant",
        "discordance_reason",
        "origin_call",
    ),
}

#: The columns kept with each row (its ``metadata_json``), beside its position and log2 ratio,
#: each when the row gives a value: numbers as numbers, ``true``/``false`` as booleans, the
#: rest as text.
_KEPT_COLUMNS: dict[str, tuple[str, ...]] = {
    "coverage": ("zscore", "weight", "reference_size", "branch"),
    "segments": (
        "zscore",
        "weight",
        "bin_count",
        "call",
        "expected_ratio",
        "implied_ff",
        "discordant",
        "discordance_reason",
        "frag_short_ratio",
        "frag_llr_size",
        "origin_call",
    ),
}
_BOOLEAN_COLUMNS = frozenset({"masked", "discordant"})
_TEXT_COLUMNS = frozenset({"branch", "call", "discordance_reason", "origin_call"})
_INTEGER_COLUMNS = frozenset({"reference_size", "bin_count"})

#: The name of each track file, said in a refusal.
CCNV_TRACK_FILE_LABEL = {"coverage": "bins", "segments": "segments"}


@dataclass(frozen=True, slots=True)
class CcnvTrackRow:
    """One stored row of a ccnv bins or segments file: its chromosome (as CoGA names it), its
    BED start and end, its id, its log2 ratio and the columns kept with it."""

    chrom: str
    start: int
    end: int
    record_id: str
    value: float
    line_no: int
    kept: dict[str, Any]


def _header_columns(line: str) -> list[str]:
    return [column.strip().lower() for column in line.lstrip("#").rstrip("\r\n").split("\t")]


def _cell(value: str, column: str) -> Any:
    """A cell of a kept column, as stored: None for an empty or ``.`` cell, and for a number
    that is not finite."""
    text = value.strip()
    if text in ("", "."):
        return None
    if column in _BOOLEAN_COLUMNS:
        lowered = text.lower()
        return True if lowered == "true" else False if lowered == "false" else None
    if column in _TEXT_COLUMNS:
        return text
    try:
        number = float(text)
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    if column in _INTEGER_COLUMNS and number.is_integer():
        return int(number)
    return number


def _coordinate(value: str) -> int | None:
    try:
        number = int(value.strip())
    except ValueError:
        return None
    return number if number >= 0 else None


def ccnv_track_rows(
    lines: Iterable[str], *, track_type: str, file: str
) -> Iterator[CcnvTrackRow | None]:
    """Each data line of a ccnv bins (``track_type`` ``coverage``) or segments (``segments``)
    file as the row it is stored as, None for a line left out: a masked bin, a bin or segment
    without a log2 ratio, a line that is cut short or whose position is no BED interval.

    The file starts with its tab-separated header line (``#contig``, ``start``, ``end``, …),
    which names the columns: a file without it, or without a column CoGA reads, is no ccnv
    prenatal output and raises ``CcnvOutputError`` before any row is read."""
    if track_type not in _REQUIRED_COLUMNS:
        raise ValueError(f"ccnv has no {track_type} track")
    label = CCNV_TRACK_FILE_LABEL[track_type]
    header: dict[str, int] | None = None
    for line_no, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        if header is None:
            columns = _header_columns(line)
            if not line.startswith("#") or "contig" not in columns:
                raise CcnvOutputError(f"The ccnv {label} file {file} does not start with its header line")
            missing = [column for column in _REQUIRED_COLUMNS[track_type] if column not in columns]
            if missing:
                raise CcnvOutputError(
                    f"The ccnv {label} file {file} has no column {', '.join(missing)}: it is no ccnv "
                    "prenatal output"
                )
            header = {column: index for index, column in enumerate(columns)}
            continue
        if line.startswith("#"):
            continue
        yield _track_row(line, header=header, track_type=track_type, line_no=line_no)


def _track_row(line: str, *, header: Mapping[str, int], track_type: str, line_no: int) -> CcnvTrackRow | None:
    parts = line.rstrip("\r\n").split("\t")
    if len(parts) < len(header):
        return None

    def cell(column: str) -> str:
        return parts[header[column]]

    if track_type == "coverage" and _cell(cell("masked"), "masked") is True:
        return None
    start = _coordinate(cell("start"))
    end = _coordinate(cell("end"))
    value = _cell(cell("ratio"), "ratio")
    contig = cell("contig").strip()
    if not contig or start is None or end is None or end <= start or not isinstance(value, float):
        return None
    chrom = normalize_chromosome(contig)
    kept = {
        column: stored
        for column in _KEPT_COLUMNS[track_type]
        if column in header and (stored := _cell(cell(column), column)) is not None
    }
    record_id = cell("id").strip() if "id" in header and cell("id").strip() else f"{contig}:{start}-{end}"
    return CcnvTrackRow(
        chrom=chrom,
        start=start,
        end=end,
        record_id=record_id,
        value=value,
        line_no=line_no,
        kept=kept,
    )
