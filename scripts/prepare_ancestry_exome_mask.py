#!/usr/bin/env python3
"""Mark which markers of CoGA's ancestry PCA model an exome covers, from a pinned GENCODE GTF.

The exploratory ancestry PCA (docs/ancestry-pca.md) reads a callset as an exome when it carries
the expected share of non-reference calls at these markers and almost none at the others. For
such a callset an absent record is taken as homozygous reference only at a marked marker.

A marker is marked when it lies in the CDS of a protein-coding transcript of the GENCODE basic
annotation, or within 20 bp of one: what exome capture kits target, with their splice padding.

Run with the backend Python environment. No network or family-data access: download the GTF
that ``REFERENCE_GENCODE_GTF_URL`` pins first and pass it with --gtf.

    python scripts/prepare_ancestry_exome_mask.py --gtf gencode.v50.basic.annotation.gtf.gz
"""
from __future__ import annotations

import argparse
import base64
import bisect
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np

GTF_NAME = "gencode.v50.basic.annotation.gtf.gz"
GTF_URL = "https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human/release_50/" + GTF_NAME
GTF_SHA256 = "5b9ad9aa71f6ab5d55123c51f74723644a895bc0cbaa9cee8fbc11bb9dec0bf7"
PADDING = 20
DEFINITION = (f"CDS of protein-coding transcripts, GENCODE v50 basic annotation, ±{PADDING} bp")
MODEL = Path(__file__).resolve().parents[1] / "backend" / "app" / "data" / "ancestry" / "1000g-grch38-v1.npz"


def cds_intervals(gtf: Path) -> dict[str, list[tuple[int, int]]]:
    """Merged, padded CDS intervals of protein-coding transcripts by chromosome (no ``chr``)."""
    raw: dict[str, list[tuple[int, int]]] = {}
    with gzip.open(gtf, "rt") as handle:
        for line in handle:
            if line.startswith("#"):
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 9 or fields[2] != "CDS" or 'transcript_type "protein_coding"' not in fields[8]:
                continue
            chrom = fields[0].removeprefix("chr")
            raw.setdefault(chrom, []).append((int(fields[3]) - PADDING, int(fields[4]) + PADDING))
    merged: dict[str, list[tuple[int, int]]] = {}
    for chrom, intervals in raw.items():
        out: list[list[int]] = []
        for start, end in sorted(intervals):
            if out and start <= out[-1][1] + 1:
                out[-1][1] = max(out[-1][1], end)
            else:
                out.append([start, end])
        merged[chrom] = [(start, end) for start, end in out]
    return merged


def prepare(gtf: Path, model: Path) -> dict:
    if hashlib.sha256(gtf.read_bytes()).hexdigest() != GTF_SHA256:
        raise ValueError(f"Pinned GENCODE input checksum failed: {gtf.name}")
    data = model.read_bytes()
    with np.load(model, allow_pickle=False) as arrays:
        loci = [str(locus).split(":") for locus in arrays["loci"].tolist()]
    intervals = cds_intervals(gtf)
    starts = {chrom: [start for start, _ in spans] for chrom, spans in intervals.items()}
    covered = np.zeros(len(loci), dtype=bool)
    for index, (chrom, position, _ref, _alt) in enumerate(loci):
        spans = intervals.get(chrom, [])
        at = bisect.bisect_right(starts.get(chrom, []), int(position)) - 1
        covered[index] = at >= 0 and spans[at][1] >= int(position)
    return {
        "reference_sha256": hashlib.sha256(data).hexdigest(),
        "definition": DEFINITION,
        "source_url": GTF_URL,
        "source_sha256": GTF_SHA256,
        "marker_count": len(loci),
        "covered_count": int(covered.sum()),
        # One bit per marker in the model's order, most significant bit first (numpy.packbits).
        "covered": base64.b64encode(np.packbits(covered).tobytes()).decode("ascii"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--gtf", type=Path, required=True, help=f"the pinned {GTF_NAME}")
    parser.add_argument("--model", type=Path, default=MODEL)
    args = parser.parse_args()
    output = args.model.with_suffix(".exome.json")
    mask = prepare(args.gtf, args.model)
    output.write_text(json.dumps(mask, indent=2) + "\n", encoding="utf-8")
    print(f"{output}: {mask['covered_count']} of {mask['marker_count']} markers; "
          f"sha256 {hashlib.sha256(output.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
