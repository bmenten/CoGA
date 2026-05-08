
#!/usr/bin/env python3
"""Convert a GENCODE GTF into a gene-level BED with CCDS annotations.

The generated BED file contains one row per gene with columns describing
exon and intron coordinates. Only CCDS-tagged transcripts are considered by
default, but an optional flag allows a fallback to the longest non-CCDS
transcript when a CCDS entry is not available.
"""

import argparse
from collections import defaultdict
from typing import Dict, Iterable, Iterator, List, Tuple


def parse_attrs(attr_field: str) -> Dict[str, str]:
    """Parse the semicolon-delimited GTF attributes field into a dictionary."""

    attrs: Dict[str, str] = {}
    for part in attr_field.strip().split(";"):
        part = part.strip()
        if not part:
            continue
        if " " in part:
            key, val = part.split(" ", 1)
            attrs[key] = val.strip().strip("\"")
    return attrs


def gtf_iter(handle: Iterable[str]) -> Iterator[Dict]:
    """Yield parsed GTF records from ``handle`` skipping comments/bad lines."""

    for line in handle:
        if not line or line.startswith("#"):
            continue
        fields = line.rstrip("\n").split("\t")
        if len(fields) < 9:
            continue
        chrom, source, feature, start, end, score, strand, frame, attrs = fields
        try:
            start_i = int(start)
            end_i = int(end)
        except ValueError:
            continue
        yield {
            "chrom": chrom,
            "feature": feature,
            "start": start_i,
            "end": end_i,
            "strand": strand,
            "attrs": parse_attrs(attrs),
        }


def pick_best_tx(transcripts: List[Dict]) -> Dict:
    """Select the optimal transcript based on exon coverage and span."""

    best: Dict | None = None

    def metrics(t: Dict) -> Tuple[int, int, str]:
        """Return comparison metrics for ``t``.

        The tuple consists of total exonic bases, transcript span and the
        transcript identifier to provide deterministic ordering.
        """

        exonic = sum(e - s + 1 for s, e in t["exons"])
        span = max(e for _, e in t["exons"]) - min(s for s, _ in t["exons"]) + 1
        return (exonic, span, t["tx_id"])

    for t in transcripts:
        if best is None or metrics(t) > metrics(best):
            best = t

    return best if best is not None else {}


def main() -> None:
    """Entry point for command-line execution."""

    ap = argparse.ArgumentParser(
        description=(
            "Create one-line-per-gene BED with exon/intron columns using "
            "CCDS transcripts from GENCODE GTF (GRCh38)."
        )
    )
    ap.add_argument(
        "--gtf",
        required=True,
        help="GENCODE GTF (GRCh38/hg38) with CCDS annotations (ccds_id).",
    )
    ap.add_argument("--out", required=True, help="Output BED-like file path.")
    ap.add_argument(
        "--allow-nonccds",
        action="store_true",
        help=(
            "If a gene has no CCDS transcript, fall back to the longest "
            "non-CCDS transcript."
        ),
    )
    args = ap.parse_args()

    # Collect transcripts grouped by gene name
    by_gene: Dict[str, List[Dict]] = defaultdict(list)

    with open(args.gtf) as inh:
        for rec in gtf_iter(inh):
            if rec["feature"] != "exon":
                continue
            a = rec["attrs"]
            gene_name = a.get("gene_name") or a.get("gene_id")
            tx_id = a.get("transcript_id")
            ccds_id = a.get("ccds_id")
            if not gene_name or not tx_id:
                continue

            # Locate any existing transcript entry for this gene
            existing = None
            for t in by_gene[gene_name]:
                if t["tx_id"] == tx_id:
                    existing = t
                    break
            if existing is None:
                existing = {
                    "tx_id": tx_id,
                    "ccds_id": ccds_id,  # may be None
                    "chrom": rec["chrom"],
                    "strand": rec["strand"],
                    "exons": [],
                }
                by_gene[gene_name].append(existing)

            existing["exons"].append((rec["start"], rec["end"]))

    # Filter to CCDS per gene (or fallback if requested)
    out_rows: List[str] = []
    for gene, tlist in by_gene.items():
        ccds_ts = [t for t in tlist if t["ccds_id"]]
        if ccds_ts:
            chosen = pick_best_tx(ccds_ts)
        elif args.allow_nonccds:
            chosen = pick_best_tx(tlist)
        else:
            continue  # skip non-CCDS genes by default

        # Prepare exon/intron intervals
        chrom = chosen["chrom"]
        strand = chosen["strand"]
        exons = sorted(chosen["exons"], key=lambda x: (x[0], x[1]))

        # BED coordinates for exons are zero-based half-open
        exons_bed = [(s - 1, e) for (s, e) in exons]
        exon_intervals_str = ",".join([f"{s}-{e}" for s, e in exons_bed])
        exon_count = len(exons_bed)

        # Determine intron intervals in transcriptional order
        exons_order = exons_bed if strand == "+" else list(reversed(exons_bed))
        introns: List[Tuple[int, int]] = []
        for i in range(len(exons_order) - 1):
            prev_s, prev_e = exons_order[i]
            next_s, next_e = exons_order[i + 1]
            intron_start = prev_e
            intron_end = next_s
            if intron_end > intron_start:
                introns.append((intron_start, intron_end))
        intron_count = len(introns)
        intron_intervals_str = ",".join([f"{s}-{e}" for s, e in introns])

        # Gene bounds from chosen transcript exons
        g_start = min(s for s, _ in exons_bed)
        g_end = max(e for _, e in exons_bed)

        row = [
            chrom,
            str(g_start),
            str(g_end),
            gene,
            "0",
            strand,
            chosen["ccds_id"] or "",
            chosen["tx_id"],
            str(exon_count),
            exon_intervals_str,
            str(intron_count),
            intron_intervals_str,
        ]
        out_rows.append("\t".join(row))

    with open(args.out, "w") as out:
        # header as a comment to aid downstream parsing
        out.write(
            "#chrom\tstart\tend\tgene\tscore\tstrand\tccds_id\ttranscript_id\texon_count\t"
            "exon_intervals\tintron_count\tintron_intervals\n"
        )
        for line in out_rows:
            out.write(line + "\n")

    print(f"Wrote {args.out} with {len(out_rows)} genes.")


if __name__ == "__main__":
    main()
