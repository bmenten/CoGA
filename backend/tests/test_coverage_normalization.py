"""Putting three CNV callers' coverage on one axis.

WisecondorX and QDNAseq report a log2 ratio against a reference; HiFiCNV reports
absolute read depth. Stacked for comparison those are not the same picture -- a
1-copy loss is -1 in the ratio tracks and "about half of whatever this sample's
baseline is" in the depth track. The depth track is therefore normalised to a
log2 ratio against the sample's own autosomal median on the way in.
"""

from __future__ import annotations

from math import log2

import pytest

from app.services.family_package_bigwig import autosomal_median
from app.services.family_package_datasets import (
    _MIN_LOG2_RATIO,
    _log2_copy_number_transform,
    _log2_ratio_transform,
)


class FakeBigWig:
    def __init__(self, intervals: dict[str, list[tuple[int, int, float]]]) -> None:
        self._intervals = intervals
        self.requested: list[str] = []

    def chroms(self) -> dict[str, int]:
        return {name: 1_000_000 for name in self._intervals}

    def intervals(self, chrom: str, start: int, end: int):
        self.requested.append(chrom)
        return self._intervals.get(chrom, [])

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# The normaliser
# ---------------------------------------------------------------------------


def test_the_median_is_taken_over_autosomes_only() -> None:
    reader = FakeBigWig(
        {
            "chr1": [(0, 100, 20.0), (100, 200, 20.0)],
            "chr2": [(0, 100, 20.0)],
            # A male sample's sex chromosomes sit at half depth. Including them
            # would pull the normaliser down and show up as a genome-wide shift.
            "chrX": [(0, 100, 10.0)] * 50,
            "chrY": [(0, 100, 10.0)] * 50,
        }
    )

    assert autosomal_median(reader) == 20.0
    assert reader.requested == ["chr1", "chr2"]


def test_zero_depth_bins_do_not_count_toward_the_median() -> None:
    reader = FakeBigWig({"chr1": [(0, 100, 30.0), (100, 200, 0.0), (200, 300, 0.0)]})

    # A depth bigWig spans the assembly gaps; counting those as observations would
    # drag the median toward zero and inflate every ratio derived from it.
    assert autosomal_median(reader) == 30.0
    assert autosomal_median(reader, skip_zero=False) == 0.0


def test_a_track_with_nothing_measurable_has_no_median() -> None:
    assert autosomal_median(FakeBigWig({"chr1": []})) is None
    assert autosomal_median(FakeBigWig({"chrX": [(0, 100, 10.0)]})) is None


# ---------------------------------------------------------------------------
# The transform
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("depth", "expected"),
    [
        (20.0, 0.0),  # at baseline
        (10.0, -1.0),  # one copy of two lost
        (30.0, log2(1.5)),  # one copy gained
        (40.0, 1.0),  # doubled
    ],
)
def test_depth_becomes_a_log2_ratio_against_the_sample_baseline(
    depth: float, expected: float
) -> None:
    transform = _log2_ratio_transform(20.0)
    assert transform is not None
    # These are the same numbers the ratio callers report for the same events, so
    # a loss sits at the same height in all three stacked tracks.
    assert transform(depth) == pytest.approx(expected)


def test_zero_and_near_zero_depth_are_floored_not_infinite() -> None:
    transform = _log2_ratio_transform(20.0)
    assert transform is not None

    # log2(0) is -inf: unstorable and undrawable.
    assert transform(0.0) == _MIN_LOG2_RATIO
    # A smoothed assembly gap leaves a vanishing depth behind; at 0.0005x against a
    # 20x median that is -15.3, which would stretch the plotted range to show nothing.
    assert transform(0.0005) == _MIN_LOG2_RATIO
    assert _MIN_LOG2_RATIO < -1, "the floor must sit below a real single-copy loss"


@pytest.mark.parametrize("normaliser", [None, 0.0, -1.0])
def test_no_usable_baseline_leaves_the_values_alone(normaliser: float | None) -> None:
    # Normalising against an invented baseline would render the whole track as a
    # genome-wide gain or loss. Raw depth on the wrong axis is at least honest, and
    # the recorded metadata says which it is.
    assert _log2_ratio_transform(normaliser) is None


# ---------------------------------------------------------------------------
# Copy number onto the same axis
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("copy_number", "expected"),
    [
        (2, 0.0),  # diploid baseline
        (1, -1.0),  # one copy lost
        (3, log2(1.5)),  # one copy gained
        (4, 1.0),  # duplicated
    ],
)
def test_copy_number_becomes_a_log2_ratio_against_diploid(
    copy_number: int, expected: float
) -> None:
    # HiFiCNV's bedGraph holds an integer copy number, but the `segments` track holds
    # a log2 ratio -- that is what WisecondorX and QDNAseq write and what the chart
    # axis is calibrated for. Stored raw, a normal CN of 2 sat above the top of a
    # +-1.5 axis and drew a solid line across the genome at the clip boundary.
    assert _log2_copy_number_transform(copy_number) == pytest.approx(expected)


def test_a_homozygous_deletion_takes_the_same_floor_as_the_depth_track() -> None:
    # CN 0 is a real call, and log2(0) is -inf.
    assert _log2_copy_number_transform(0) == _MIN_LOG2_RATIO
    assert _log2_copy_number_transform(-1) == _MIN_LOG2_RATIO


def test_copy_number_and_depth_agree_on_what_a_loss_looks_like() -> None:
    depth = _log2_ratio_transform(20.0)
    assert depth is not None
    # The whole point of normalising both: a single-copy loss is -1 whether it
    # reached the track as a called copy number or as read depth.
    assert _log2_copy_number_transform(1) == pytest.approx(depth(10.0))
    assert _log2_copy_number_transform(2) == pytest.approx(depth(20.0))
