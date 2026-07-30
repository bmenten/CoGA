"""Reading HiFiCNV's bigWig signal tracks, and where their values land.

HiFiCNV ships two bigWigs per sample plus a bedGraph, measuring three different
things. Getting a value onto the wrong track is not a cosmetic error -- a copy
number of 2 plotted on an axis calibrated for read depth reads as catastrophic
loss -- so both the reader and the routing are pinned here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.services.family_package_bigwig import (
    BigWigUnavailableError,
    bigwig_chrom_summary,
    iter_bigwig_intervals,
    open_bigwig,
)
from app.services.family_package_discovery import NAMING_SCHEMES
from app.services.family_package_validation import _validate_dataset
from app.services.family_package_common import ManifestDataset


class FakeBigWig:
    """Stands in for a pyBigWig handle.

    The real files in the reference package are 19 MB and 143 MB, so the unit
    suite cannot carry one; what it can pin is that the reader asks for the
    right contigs and normalises what comes back.
    """

    def __init__(self, chroms: dict[str, int], intervals: dict[str, list[tuple[int, int, float]]]):
        self._chroms = chroms
        self._intervals = intervals
        self.requested: list[str] = []
        self.closed = False

    def chroms(self) -> dict[str, int]:
        return self._chroms

    def intervals(self, chrom: str, start: int, end: int) -> Any:
        self.requested.append(chrom)
        return self._intervals.get(chrom, [])

    def close(self) -> None:
        self.closed = True


def _reader() -> FakeBigWig:
    return FakeBigWig(
        chroms={
            "chr1": 1000,
            "chr2": 1000,
            "chrX": 500,
            # The reference package carries 195 contigs; these are the shapes of
            # the 170 that are not primary.
            "chr1_KI270706v1_random": 200,
            "chrUn_GL000195v1": 200,
            "chr19_KI270938v1_alt": 200,
        },
        intervals={
            "chr1": [(0, 100, 12.5), (100, 200, 0.0), (200, 300, 7.25)],
            "chr2": [(0, 50, 3.0)],
            "chrX": [(0, 10, 1.5)],
            "chr1_KI270706v1_random": [(0, 100, 99.0)],
            "chrUn_GL000195v1": [(0, 100, 99.0)],
            "chr19_KI270938v1_alt": [(0, 100, 99.0)],
        },
    )


def test_only_primary_contigs_are_read() -> None:
    reader = _reader()
    rows = list(iter_bigwig_intervals(reader))

    # ALT/random/decoy scaffolds cannot be plotted by any CoGA view, and in the
    # reference package they are 170 of 195 contigs.
    assert reader.requested == ["chr1", "chr2", "chrX"]
    assert {row[0] for row in rows} == {"1", "2", "X"}
    assert 99.0 not in {row[3] for row in rows}


def test_chromosomes_are_normalized_but_lookups_use_the_file_naming() -> None:
    reader = _reader()
    rows = list(iter_bigwig_intervals(reader))

    # Stored chromosomes never carry the prefix (matching every other track),
    # but the file must still be queried by the name it uses internally.
    assert rows[0] == ("1", 0, 100, 12.5)
    assert all(not row[0].startswith("chr") for row in rows)
    assert all(name.startswith("chr") for name in reader.requested)


def test_contigs_are_read_in_karyotype_order() -> None:
    reader = FakeBigWig(
        chroms={"chr10": 10, "chr2": 10, "chrX": 10, "chr1": 10},
        intervals={name: [(0, 1, 1.0)] for name in ("chr10", "chr2", "chrX", "chr1")},
    )
    list(iter_bigwig_intervals(reader))

    # Lexical order would give 1, 10, 2 — the tracks are read into a table
    # ordered by chromosome, so the natural order keeps inserts sequential.
    assert reader.requested == ["chr1", "chr2", "chr10", "chrX"]


def test_skip_zero_drops_empty_depth_bins_only_when_asked() -> None:
    assert [row[3] for row in iter_bigwig_intervals(_reader())] == [12.5, 0.0, 7.25, 3.0, 1.5]

    # A depth bigWig spans the whole genome, so its zero bins are the assembly
    # gaps — ~95k of 1.2M in the reference package. For MAF a zero is a real
    # measurement (a homozygous site), which is why this is opt-in.
    assert [row[3] for row in iter_bigwig_intervals(_reader(), skip_zero=True)] == [
        12.5,
        7.25,
        3.0,
        1.5,
    ]


def test_zero_length_intervals_are_dropped() -> None:
    reader = FakeBigWig(
        chroms={"chr1": 100},
        intervals={"chr1": [(10, 10, 5.0), (10, 20, 6.0)]},
    )
    assert list(iter_bigwig_intervals(reader)) == [("1", 10, 20, 6.0)]


def test_chrom_summary_reports_primary_contigs_only() -> None:
    assert bigwig_chrom_summary(_reader()) == {"1": 1000, "2": 1000, "X": 500}


def test_a_missing_reader_is_named_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "pyBigWig":
            raise ImportError("no module named pyBigWig")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    # Silently importing zero rows would look like a package with no depth data.
    with pytest.raises(BigWigUnavailableError, match="pyBigWig"):
        open_bigwig(Path("does-not-matter.bw"))


# ---------------------------------------------------------------------------
# Routing: which file feeds which track
# ---------------------------------------------------------------------------


def test_maf_bigwig_is_discovered_despite_the_doubled_sample_id(tmp_path: Path) -> None:
    from app.services.family_package_discovery import _glob_candidate_paths

    target = tmp_path / "cnv" / "HG002" / "HG002.HG002.maf.bw"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"")

    pattern = NAMING_SCHEMES["standard_v1"]["datasets"]["cnv"]["maf_bigwig"][0]
    # HiFiCNV names the file after the run *and* the sample.
    assert _glob_candidate_paths(tmp_path, pattern.format(sample_id="HG002")) == [
        str(target.relative_to(tmp_path))
    ]


def test_depth_and_maf_bigwigs_validate_as_optional_cnv_files(tmp_path: Path) -> None:
    root = tmp_path
    (root / "cnv" / "HG002" / "annotation").mkdir(parents=True)
    (root / "cnv" / "HG002" / "annotation" / "HG002_annot.vcf.gz").write_bytes(b"")
    (root / "cnv" / "HG002" / "HG002.Sample0.depth.bw").write_bytes(b"")
    (root / "cnv" / "HG002" / "HG002.HG002.maf.bw").write_bytes(b"")
    (root / "cnv" / "HG002" / "HG002.Sample0.copynum.bedgraph").write_text("chr1\t0\t100\t2\n")

    errors: list[Any] = []
    summary = _validate_dataset(
        root=root,
        dataset_type="cnv",
        dataset=ManifestDataset(
            enabled=True,
            per_sample={
                "HG002": {
                    "vcf": "cnv/HG002/annotation/HG002_annot.vcf.gz",
                    "copy_number_bedgraph": "cnv/HG002/HG002.Sample0.copynum.bedgraph",
                    "depth_bigwig": "cnv/HG002/HG002.Sample0.depth.bw",
                    "maf_bigwig": "cnv/HG002/HG002.HG002.maf.bw",
                }
            },
        ),
        ped_sample_ids={"HG002"},
        errors=errors,
    )

    # A declared MAF bigWig must not be reported as an unknown key: an import that
    # warns about the file it is about to read teaches operators to ignore warnings.
    assert errors == []
    assert summary.status != "error"
