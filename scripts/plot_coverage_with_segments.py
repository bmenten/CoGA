#!/usr/bin/env python3
"""Plot coverage data with copy number segments overlaid.

This script reads coverage bin data and copy number segment data from
BED-like tab-delimited files and creates a plot for a selected
chromosome. Coverage values are drawn as a line plot and segments are
rendered as horizontal lines on top.
"""

import argparse
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt


def load_bed(path: Path) -> pd.DataFrame:
    """Load a BED-like file into a DataFrame, ignoring comments."""
    return pd.read_csv(path, sep="\t", comment="#")


def plot_coverage_with_segments(
    coverage_path: Path,
    segments_path: Path,
    chrom: str,
    output: Path,
) -> None:
    coverage = load_bed(coverage_path)
    segments = load_bed(segments_path)

    cov_chr = coverage[coverage["chr"] == chrom]
    seg_chr = segments[segments["chr"] == chrom]

    if cov_chr.empty or seg_chr.empty:
        raise ValueError(f"No data found for chromosome {chrom}")

    midpoints = (cov_chr["start"] + cov_chr["end"]) / 2

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(midpoints, cov_chr["ratio"], color="steelblue", lw=0.5)

    for _, row in seg_chr.iterrows():
        ax.hlines(
            row["ratio"],
            row["start"],
            row["end"],
            colors="red",
            linewidth=2,
        )

    ax.set_xlabel(f"Chromosome {chrom} position")
    ax.set_ylabel("Coverage ratio")
    ax.set_title(f"Coverage with segments on chr{chrom}")
    fig.tight_layout()
    fig.savefig(output)
    print(f"Saved plot to {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("coverage", type=Path, help="Coverage bins BED file")
    parser.add_argument("segments", type=Path, help="Segments BED file")
    parser.add_argument(
        "--chrom",
        default="1",
        help="Chromosome to plot (default: 1)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("coverage_segments.png"),
        help="Output image path",
    )
    args = parser.parse_args()

    plot_coverage_with_segments(
        args.coverage, args.segments, args.chrom, args.output
    )


if __name__ == "__main__":
    main()
