"""Reading bigWig signal files shipped inside a family package.

nf-core/lrsvar's HiFiCNV step emits two bigWigs per sample: a binned read-depth
track (``<sample>.depth.bw``) and a minor-allele-fraction track
(``<sample>.maf.bw``). Both are dense binary signal files rather than the
line-oriented BED/bedGraph the other interval-track importers parse, so they get
their own reader.

Two properties of the format drive the design here:

* A bigWig carries **every** contig the aligner saw -- 195 for the reference
  HG002 package, most of them ALT/random/decoy scaffolds no CoGA view can plot.
  Iteration is therefore restricted to primary chromosomes, which drops the
  stored row count by roughly the scaffold fraction without losing anything a
  reader could look at.
* ``pyBigWig.intervals()`` materialises a whole chromosome as a Python list --
  chr1 of the MAF track is ~350k tuples. That is tolerable per chromosome but
  not for a whole genome at once, so rows are streamed chromosome by chromosome
  and the caller batches them into ClickHouse.

The reader is deliberately ignorant of what the values *mean*: the depth track
becomes a ``coverage`` track and MAF becomes ``apcad`` points, but that mapping
belongs to the importer, not here.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol

from .data_scope import is_primary_chromosome, normalize_chromosome


class BigWigReader(Protocol):
    """The slice of the ``pyBigWig`` API this module uses."""

    def chroms(self) -> dict[str, int]: ...

    def intervals(self, chrom: str, start: int, end: int) -> Any: ...

    def close(self) -> None: ...


class BigWigUnavailableError(RuntimeError):
    """Raised when a bigWig is offered but no reader is installed."""


def open_bigwig(path: Path) -> BigWigReader:
    """Open ``path`` for reading, or explain why it cannot be read.

    ``pyBigWig`` is a compiled extension. Importing it lazily keeps a broken or
    absent build from taking down every import path in the service -- a package
    with no bigWig in it must still import cleanly.
    """

    try:
        import pyBigWig  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - exercised by the dependency gate
        raise BigWigUnavailableError(
            "Reading bigWig signal tracks requires the pyBigWig package, which is not "
            "installed in this environment."
        ) from exc
    handle = pyBigWig.open(str(path))
    if handle is None:
        raise BigWigUnavailableError(f"{path.name} could not be opened as a bigWig file")
    return handle


def _ordered_primary_chroms(chroms: dict[str, int]) -> list[str]:
    """Primary contigs in karyotype order, keeping the file's own naming.

    The file may say ``chr1`` or ``1``; both are kept verbatim for the
    ``intervals()`` lookup and normalised only when a row is built.
    """

    def sort_key(name: str) -> tuple[int, str]:
        normalized = normalize_chromosome(name).upper()
        if normalized.isdigit():
            return (int(normalized), "")
        return (100, normalized)

    primary = [name for name in chroms if is_primary_chromosome(name)]
    return sorted(primary, key=sort_key)


def iter_bigwig_intervals(
    reader: BigWigReader,
    *,
    chromosomes: list[str] | None = None,
    skip_zero: bool = False,
) -> Iterator[tuple[str, int, int, float]]:
    """Yield ``(chrom, start, end, value)`` for every primary-contig interval.

    ``chrom`` is normalised (no ``chr`` prefix), matching how every other
    interval track is stored. Coordinates stay half-open 0-based as bigWig
    defines them, which is also what the interval table holds.

    ``skip_zero`` drops exactly-zero intervals. A depth bigWig covers the whole
    genome including telomeric and centromeric gaps, so a genome-wide depth
    track is roughly half zero-valued bins that plot as a flat line on the axis;
    dropping them halves the stored rows. It is off by default because for MAF a
    zero is a real measurement (a homozygous site), not an absence.
    """

    available = reader.chroms() or {}
    names = chromosomes if chromosomes is not None else _ordered_primary_chroms(available)
    for name in names:
        length = available.get(name)
        if not length:
            continue
        normalized = normalize_chromosome(name)
        for entry in reader.intervals(name, 0, int(length)) or ():
            start, end, value = int(entry[0]), int(entry[1]), float(entry[2])
            if end <= start:
                continue
            if skip_zero and value == 0.0:
                continue
            yield normalized, start, end, value


def bigwig_chrom_summary(reader: BigWigReader) -> dict[str, int]:
    """Primary-contig name → length, for recording what a track actually spans."""

    available = reader.chroms() or {}
    return {
        normalize_chromosome(name): int(length)
        for name, length in available.items()
        if is_primary_chromosome(name) and length
    }
