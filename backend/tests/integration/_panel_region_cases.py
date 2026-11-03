"""The synthetic loci the panel-region join tests share (family page, Variant Explorer, SVs).

Each test runs a region join on 64-kb windows and the per-row rule it replaces, in ClickHouse,
over rows typed as a subquery, and asserts that both keep the same rows. These are the shapes
the rows and regions take there: every chromosome spelling a store may hold, positions on
each side of window boundaries, and regions at the edges of the windows. Not a test module
(pytest collects ``test_*.py`` only). Synthetic data only.
"""

from __future__ import annotations

from itertools import product
from typing import Any

# The join's window: 64 kb.
WINDOW = 1 << 16

# Every chromosome spelling a store may hold: with and without "chr", the mitochondrion as M,
# MT and chrM, and an unplaced contig.
CHROMOSOMES = ["1", "chr1", "X", "chrX", "M", "MT", "chrM", "GL000192.1"]


def boundary_positions() -> list[int]:
    """Positions on each side of three window boundaries, and a few in between."""
    positions: set[int] = {1, 2, 1000, 30_000}
    for boundary in (WINDOW, 2 * WINDOW, 2092 * WINDOW):
        positions.update(boundary + offset for offset in range(-6, 7))
    return sorted(positions)


def small_variant_shapes() -> list[tuple[str, int, int]]:
    """(chromosome as stored, position, REF length): every chromosome and position with REFs
    of 1 to 50 bases, and a few REFs longer than one and than two windows."""
    shapes = list(product(CHROMOSOMES, boundary_positions(), [1, 2, 5, 50]))
    shapes += product(["1", "GL000192.1"], [1, WINDOW - 3, WINDOW + 1, 2 * WINDOW - 2], [WINDOW + 1, 2 * WINDOW + 7])
    return shapes


def window_edge_regions() -> list[Any]:
    """Regions spelled with and without "chr", reversed, repeated, of one base, ending or
    starting on a window boundary, across one, spanning several windows, or without a
    chromosome."""
    from backend.app.services.clickhouse_variant_records import Region

    window_2092 = 2092 * WINDOW
    return [
        Region(chr="chr1", start=WINDOW, end=WINDOW + 2),  # starts on a boundary
        Region(chr="1", start=2 * WINDOW - 3, end=2 * WINDOW - 1),  # ends before one
        Region(chr="1", start=2 * WINDOW - 1, end=2 * WINDOW - 1),  # one base
        Region(chr="1", start=WINDOW + 2, end=WINDOW),  # reversed
        Region(chr="chr1", start=WINDOW, end=WINDOW + 2),  # repeated
        Region(chr="X", start=window_2092 - 4, end=window_2092 + 1),  # across a boundary
        Region(chr="chrM", start=900, end=1_100),  # M, read as MT
        Region(chr="MT", start=WINDOW - 1, end=WINDOW - 1),
        Region(chr="GL000192.1", start=1, end=3 * WINDOW + 5),  # several windows
        Region(chr="", start=1, end=10),  # no chromosome: left out
    ]


def each_region_bounds(regions: Any) -> tuple[list[str], list[int], list[int]]:
    """The regions each read on their own (match key, start and end in order, each once),
    unmerged: the reference the merged or bound regions must answer alike. Patched in for the
    production code's ``_small_region_bounds`` / ``_structural_region_bounds``."""
    from backend.app.services.clickhouse_variant_queries import _chromosome_match_key

    kept = dict.fromkeys(
        (_chromosome_match_key(region.chr), min(region.start, region.end), max(region.start, region.end))
        for region in regions
        if _chromosome_match_key(region.chr)
    )
    return [c for c, _s, _e in kept], [s for _c, s, _e in kept], [e for _c, _s, e in kept]
