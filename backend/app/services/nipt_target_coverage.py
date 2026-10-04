"""Per-target coverage tables of a capture panel (monogenic NIPT).

The NIPT-M pipeline writes, per sample, one row per capture target (an exon of a panel
transcript, CDS +/- 20 bp) with its depth statistics:

    #build  chromosome  start  end  attribute  length  min  max  mean  median  stdev
    zero_coverage_bases  proportion_covered  length_above_10X  %_above_10X ...

``start``/``end`` are BED coordinates (0-based start, end exclusive), ``attribute`` is
``GENE;NM_...;ENST...;ENSE...;exon number`` (quoted, several transcripts comma-separated),
``proportion_covered`` is the percentage of the target's bases with any coverage, and the
numbers may carry a decimal comma (``1183,91``).

CoGA stores the table as the sample's ``target_coverage`` interval track (value = mean
depth, record_id = gene, the other columns in the row's metadata) and reads it for two
things: the coverage QC of the targets (``summarize_target_coverage``), and the plasma's
depth at a position where its VCF has no call, which tells how many alt reads a fetal
allele would have shown there (``TargetDepthLookup``).

Pure: no I/O; the import and the reads live in family_package_datasets and nipt_service.
"""

from __future__ import annotations

import bisect
import gzip
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from .data_scope import normalize_chromosome

TARGET_COVERAGE_TRACK_TYPE = "target_coverage"
TARGET_COVERAGE_SOURCE = "coverage_table"

# The R NIPT-M pipeline's target QC (v0.5.1): a target is weak when its mean depth is below
# the critical threshold or any of its bases has no coverage; below the advisory threshold
# it is reported, not flagged.
CRITICAL_MEAN_DEPTH = 300.0
ADVISORY_MEAN_DEPTH = 1000.0
FULL_COVERAGE_PERCENT = 100.0

_REQUIRED_COLUMNS = ("chromosome", "start", "end", "attribute", "mean")


class TargetCoverageFormatError(ValueError):
    """The table lacks a column the parser needs."""


@dataclass(slots=True)
class TargetCoverageRow:
    """One capture target of the table."""

    chrom: str
    start: int  # BED: 0-based, the first base is start + 1
    end: int  # BED: end exclusive, the last base is end
    gene: str
    attribute: str
    mean: float | None
    median: float | None = None
    min: float | None = None
    max: float | None = None
    proportion_covered: float | None = None  # percent of bases with any coverage
    zero_coverage_bases: int | None = None

    @property
    def length(self) -> int:
        return max(0, self.end - self.start)

    def depth(self) -> float | None:
        """The depth a position inside the target is taken to have: the lower of the mean
        and the median (the median is robust to a deep spike, the mean to a shallow edge)."""
        values = [value for value in (self.mean, self.median) if value is not None]
        return min(values) if values else None

    def is_weak(self, critical_mean_depth: float = CRITICAL_MEAN_DEPTH) -> bool:
        if self.mean is None or self.mean < critical_mean_depth:
            return True
        return self.proportion_covered is not None and self.proportion_covered < FULL_COVERAGE_PERCENT


def parse_number(value: str | None) -> float | None:
    """A table number: ``1183,91`` and ``1.183,91`` are 1183.91; '', 'NA' and '.' are None."""
    if value is None:
        return None
    text = value.strip().strip('"')
    if text in ("", ".", "NA", "NaN", "nan"):
        return None
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _gene_of(attribute: str) -> str:
    return attribute.split(";", 1)[0].strip()


def target_table_missing_columns(path: Path) -> list[str]:
    """The columns the parser needs that the table's header (its first line) lacks."""
    handle = (
        gzip.open(path, "rt", encoding="utf-8", errors="replace")
        if path.name.endswith(".gz")
        else path.open("r", encoding="utf-8", errors="replace")
    )
    with handle:
        first = handle.readline()
    header = [name.strip().lstrip("#").strip() for name in first.rstrip("\n\r").split("\t")]
    return [name for name in _REQUIRED_COLUMNS if name not in header]


def parse_target_coverage_table(lines: Iterable[str]) -> list[TargetCoverageRow]:
    """Parse a per-target coverage table (tab-separated, header first).

    Raises ``TargetCoverageFormatError`` when the header lacks a needed column. A row whose
    coordinates do not parse is skipped.
    """
    header: list[str] | None = None
    rows: list[TargetCoverageRow] = []
    for raw_line in lines:
        line = raw_line.rstrip("\n\r")
        if not line.strip():
            continue
        fields = line.split("\t")
        if header is None:
            header = [name.strip().lstrip("#").strip() for name in fields]
            missing = [name for name in _REQUIRED_COLUMNS if name not in header]
            if missing:
                raise TargetCoverageFormatError(
                    f"The coverage table has no column {', '.join(missing)}"
                )
            continue
        record = dict(zip(header, fields))
        try:
            start = int(str(record["start"]).strip())
            end = int(str(record["end"]).strip())
        except (KeyError, ValueError):
            continue
        attribute = str(record.get("attribute") or "").strip().strip('"')
        zero_bases = parse_number(record.get("zero_coverage_bases"))
        rows.append(
            TargetCoverageRow(
                chrom=normalize_chromosome(str(record["chromosome"]).strip()),
                start=start,
                end=end,
                gene=_gene_of(attribute),
                attribute=attribute,
                mean=parse_number(record.get("mean")),
                median=parse_number(record.get("median")),
                min=parse_number(record.get("min")),
                max=parse_number(record.get("max")),
                proportion_covered=parse_number(record.get("proportion_covered")),
                zero_coverage_bases=None if zero_bases is None else int(zero_bases),
            )
        )
    if header is None:
        raise TargetCoverageFormatError("The coverage table is empty")
    return rows


def target_row_metadata(row: TargetCoverageRow) -> str:
    """The row's columns besides the mean and the gene, as stored with its track row."""
    return json.dumps(
        {
            "attribute": row.attribute,
            "median": row.median,
            "min": row.min,
            "max": row.max,
            "proportion_covered": row.proportion_covered,
            "zero_coverage_bases": row.zero_coverage_bases,
        },
        separators=(",", ":"),
    )


def target_row_from_track(row: dict[str, Any]) -> TargetCoverageRow:
    """A stored ``target_coverage`` track row back as a table row."""
    metadata: dict[str, Any] = {}
    raw = row.get("metadata_json")
    if isinstance(raw, str) and raw:
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            loaded = {}
        if isinstance(loaded, dict):
            metadata = loaded

    def number(key: str) -> float | None:
        value = metadata.get(key)
        return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None

    zero_bases = number("zero_coverage_bases")
    value = row.get("value")
    return TargetCoverageRow(
        chrom=normalize_chromosome(str(row.get("chr") or row.get("chrom") or "")),
        start=int(row["start"]),
        end=int(row["end"]),
        gene=str(row.get("record_id") or ""),
        attribute=str(metadata.get("attribute") or row.get("record_id") or ""),
        mean=float(value) if value is not None else None,
        median=number("median"),
        min=number("min"),
        max=number("max"),
        proportion_covered=number("proportion_covered"),
        zero_coverage_bases=None if zero_bases is None else int(zero_bases),
    )


class TargetDepthLookup:
    """The target a 1-based position falls in, by binary search over each chromosome's
    targets (sorted by start). Overlapping targets (two transcripts' exons) resolve to the
    deepest one, as the reads cover both."""

    def __init__(self, rows: Iterable[TargetCoverageRow]):
        by_chrom: dict[str, list[TargetCoverageRow]] = {}
        for row in rows:
            by_chrom.setdefault(row.chrom, []).append(row)
        self._rows: dict[str, list[TargetCoverageRow]] = {}
        self._starts: dict[str, list[int]] = {}
        self._max_length: dict[str, int] = {}
        for chrom, chrom_rows in by_chrom.items():
            chrom_rows.sort(key=lambda item: (item.start, item.end))
            self._rows[chrom] = chrom_rows
            self._starts[chrom] = [item.start for item in chrom_rows]
            self._max_length[chrom] = max((item.length for item in chrom_rows), default=0)

    def __bool__(self) -> bool:
        return bool(self._rows)

    def target_at(self, chrom: str, position: int) -> TargetCoverageRow | None:
        """The target holding the 1-based ``position`` (BED: start < position <= end)."""
        key = normalize_chromosome(chrom)
        rows = self._rows.get(key)
        if not rows:
            return None
        starts = self._starts[key]
        # Candidates start before the position and no earlier than the longest target.
        high = bisect.bisect_left(starts, position)
        low = bisect.bisect_left(starts, position - self._max_length[key])
        best: TargetCoverageRow | None = None
        for row in rows[low:high]:
            if row.start < position <= row.end:
                depth = row.depth()
                if best is None or (depth or 0.0) > (best.depth() or 0.0):
                    best = row
        return best

    def depth_at(self, chrom: str, position: int) -> float | None:
        target = self.target_at(chrom, position)
        return target.depth() if target is not None else None


@dataclass(slots=True)
class TargetGeneCoverage:
    """The coverage of one gene's targets."""

    gene: str
    targets: int
    weak_targets: int
    min_mean: float | None
    mean_of_means: float | None
    weak: list[TargetCoverageRow] = field(default_factory=list)


@dataclass(slots=True)
class TargetCoverageSummary:
    """The coverage QC of a sample's targets (all of them, or a gene list's)."""

    targets: int
    median_mean: float | None
    q05_mean: float | None
    below_critical: int
    below_advisory: int
    zero_mean: int
    incomplete: int
    critical_mean_depth: float
    advisory_mean_depth: float
    genes: list[TargetGeneCoverage] = field(default_factory=list)


def _quantile(sorted_values: Sequence[float], fraction: float) -> float | None:
    """Linear-interpolated quantile (R type 7, numpy's default)."""
    if not sorted_values:
        return None
    position = (len(sorted_values) - 1) * fraction
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return sorted_values[low]
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def summarize_target_coverage(
    rows: Sequence[TargetCoverageRow],
    *,
    genes: Iterable[str] | None = None,
    critical_mean_depth: float = CRITICAL_MEAN_DEPTH,
    advisory_mean_depth: float = ADVISORY_MEAN_DEPTH,
) -> TargetCoverageSummary:
    """The coverage QC of the targets, restricted to ``genes`` when given (case-insensitive).

    The per-gene list holds the requested genes, or, without a gene list, only the genes
    with a weak target, weakest first.
    """
    wanted = {gene.strip().upper() for gene in genes or [] if gene and gene.strip()}
    selected = [row for row in rows if not wanted or row.gene.upper() in wanted]
    means = sorted(row.mean for row in selected if row.mean is not None)
    by_gene: dict[str, list[TargetCoverageRow]] = {}
    for row in selected:
        by_gene.setdefault(row.gene, []).append(row)
    gene_rows: list[TargetGeneCoverage] = []
    for gene, gene_targets in by_gene.items():
        weak = [row for row in gene_targets if row.is_weak(critical_mean_depth)]
        if not wanted and not weak:
            continue
        gene_means = [row.mean for row in gene_targets if row.mean is not None]
        gene_rows.append(
            TargetGeneCoverage(
                gene=gene,
                targets=len(gene_targets),
                weak_targets=len(weak),
                min_mean=min(gene_means) if gene_means else None,
                mean_of_means=sum(gene_means) / len(gene_means) if gene_means else None,
                weak=sorted(weak, key=lambda row: (row.mean if row.mean is not None else -1.0)),
            )
        )
    gene_rows.sort(key=lambda item: (item.min_mean if item.min_mean is not None else -1.0, item.gene))
    if wanted:
        missing = sorted(wanted - {item.gene.upper() for item in gene_rows})
        gene_rows.extend(
            TargetGeneCoverage(gene=gene, targets=0, weak_targets=0, min_mean=None, mean_of_means=None)
            for gene in missing
        )
    return TargetCoverageSummary(
        targets=len(selected),
        median_mean=_quantile(means, 0.5),
        q05_mean=_quantile(means, 0.05),
        below_critical=sum(1 for row in selected if row.mean is None or row.mean < critical_mean_depth),
        below_advisory=sum(1 for row in selected if row.mean is None or row.mean < advisory_mean_depth),
        zero_mean=sum(1 for row in selected if not row.mean),
        incomplete=sum(
            1
            for row in selected
            if row.proportion_covered is not None and row.proportion_covered < FULL_COVERAGE_PERCENT
        ),
        critical_mean_depth=critical_mean_depth,
        advisory_mean_depth=advisory_mean_depth,
        genes=gene_rows,
    )


@dataclass(slots=True)
class SexChromosomeCoverage:
    """The plasma's chrX and chrY depth relative to the autosomes (R NIPT-M sex profile).

    In maternal plasma chrX sits at about the autosomal level (the panel's own ratio; 1.13
    to 1.30 in the validation cohort) and chrY at zero for a female fetus, or at a small
    fraction for a male one (0.08 to 0.23). A chrY near the autosomal level with chrX near
    0.7 is male DNA: the sample cannot be maternal plasma.
    """

    autosomal_mean: float | None
    x_mean: float | None
    y_mean: float | None
    y_targets: int
    x_ratio: float | None
    y_ratio: float | None
    profile: str  # no_chrY_targets | female_no_chrY_signal | female_with_male_fetal_signal | high_chrY_review | male_like_not_maternal_plasma
    fetal_fraction_estimate: float | None  # indicative only: 2 * (Y/A) / 1.2


# R NIPT-M (cfdna_coverage.R) defaults.
_Y_NOISE_RATIO = 0.02
_MALE_LIKE_Y_RATIO = 0.5
_MALE_LIKE_X_RATIO = 0.9
_MALE_CHRY_CAPTURE_FACTOR = 1.2


def sex_chromosome_coverage(rows: Sequence[TargetCoverageRow]) -> SexChromosomeCoverage:
    """chrX/chrY versus autosomal mean depth, as the R pipeline profiles a plasma sample.

    The means are plain means of the targets' mean depths. The chrY fetal-fraction
    estimate assumes the panel's chrY targets capture 1.2 times as well as the autosomes
    in a male; with a single chrY target it is indicative only.
    """
    autosomal: list[float] = []
    x_values: list[float] = []
    y_values: list[float] = []
    for row in rows:
        if row.mean is None:
            continue
        chrom = row.chrom.upper().removeprefix("CHR")
        if chrom == "X":
            x_values.append(row.mean)
        elif chrom == "Y":
            y_values.append(row.mean)
        elif chrom.isdigit():
            autosomal.append(row.mean)
    autosomal_mean = sum(autosomal) / len(autosomal) if autosomal else None
    x_mean = sum(x_values) / len(x_values) if x_values else None
    y_mean = sum(y_values) / len(y_values) if y_values else None
    x_ratio = x_mean / autosomal_mean if x_mean is not None and autosomal_mean else None
    y_ratio = y_mean / autosomal_mean if y_mean is not None and autosomal_mean else None
    if y_ratio is None:
        profile = "no_chrY_targets"
    elif y_ratio > _MALE_LIKE_Y_RATIO and x_ratio is not None and x_ratio < _MALE_LIKE_X_RATIO:
        profile = "male_like_not_maternal_plasma"
    elif y_ratio > _MALE_LIKE_Y_RATIO:
        profile = "high_chrY_review"
    elif y_ratio > _Y_NOISE_RATIO:
        profile = "female_with_male_fetal_signal"
    else:
        profile = "female_no_chrY_signal"
    fetal_fraction = (
        min(1.0, 2.0 * y_ratio / _MALE_CHRY_CAPTURE_FACTOR)
        if y_ratio is not None and _Y_NOISE_RATIO < y_ratio <= _MALE_LIKE_Y_RATIO
        else None
    )
    return SexChromosomeCoverage(
        autosomal_mean=autosomal_mean,
        x_mean=x_mean,
        y_mean=y_mean,
        y_targets=len(y_values),
        x_ratio=x_ratio,
        y_ratio=y_ratio,
        profile=profile,
        fetal_fraction_estimate=fetal_fraction,
    )
