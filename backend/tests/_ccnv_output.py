"""A synthetic ccnv prenatal output for a conventional NIPT's maternal plasma, laid out as ccnv
writes it: ``<sample>_bins.bed.gz``, ``_segments.bed.gz``, ``_calls.vcf.gz`` and ``_qc.json``.

Every sample ID and value is made up. The bins cover a few chromosomes with one masked bin; one
segment of chr21 is called a gain at the fetal fraction, and the calls VCF holds it.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

BINS_HEADER = "#contig\tstart\tend\tid\tratio\tzscore\tweight\treference_size\tmasked\tbranch\n"
SEGMENTS_HEADER = (
    "#contig\tstart\tend\tratio\tzscore\tweight\tbin_count\tcall\texpected_ratio\timplied_ff\tdiscordant\t"
    "discordance_reason\tfrag_short_ratio\tfrag_llr_size\torigin_call\n"
)

_BINS = [
    # chr1: one masked bin (no ratio), then three bins.
    ("chr1", 0, 100_000, ".", ".", "0.000000", "0", "true", "autosomal"),
    ("chr1", 100_000, 200_000, "0.010000", "0.200000", "150.0", "300", "false", "autosomal"),
    ("chr1", 200_000, 300_000, "-0.020000", "-0.400000", "160.0", "310", "false", "autosomal"),
    ("chr1", 300_000, 400_000, "0.005000", "0.100000", "155.0", "305", "false", "autosomal"),
    # chr21: raised, as by a fetal trisomy at a fetal fraction of about 12%.
    ("chr21", 0, 100_000, "0.090000", "4.100000", "140.0", "290", "false", "autosomal"),
    ("chr21", 100_000, 200_000, "0.080000", "3.900000", "142.0", "295", "false", "autosomal"),
    ("chrX", 0, 100_000, "0.001000", "0.050000", "120.0", "280", "false", "female_sex"),
]

_SEGMENTS = [
    ("chr1", 100_000, 400_000, "-0.001667", "-0.100000", "465.0", "3", "", "-0.091790", "0.001500", "false", "", "0.180000", "-300.0", ""),
    ("chr21", 0, 200_000, "0.085000", "5.600000", "282.0", "2", "gain", "0.086298", "0.118000", "false", "", "0.205000", "120.5", "FETAL_CONSTITUTIONAL"),
    ("chrX", 0, 100_000, "0.001000", "0.050000", "120.0", "1", "", "0.086298", "0.000900", "false", "", "0.170000", "-80.0", ""),
]


def qc_payload(sample: str, **prenatal: Any) -> dict[str, Any]:
    """A ccnv prenatal QC object for ``sample``; ``prenatal`` replaces values of its prenatal
    block (a value of None leaves the key out)."""
    block: dict[str, Any] = {
        "fetal_sex": "F",
        "active_ff": 0.081234,
        "effective_ff": 0.121111,
        "active_ff_se": 0.005,
        "active_ff_source": "FUSED",
        "ffy": 0.0,
        "ffx": 0.09,
        "ffa": 0.085,
        "ff_size": 0.07,
        "chrx_ratio": 1.0,
        "chry_score": 0.00001,
        "yfrac_threshold": 0.00004,
        "sca": {"call": "XX", "is_aneuploid": False, "confidence": 0.998, "chrx_zscore": 0.3, "ff_delta": 0.01},
        "low_ff_warning": False,
        "warnings": [],
        "origin_summary": {"FETAL_CONSTITUTIONAL": 1},
        "lod_ff_35mb": 0.03,
        "lod_ff_3mb": 0.1,
    }
    for key, value in prenatal.items():
        if value is None:
            block.pop(key, None)
        else:
            block[key] = value
    return {
        "schema_version": "2.0.0",
        "mode": "prenatal",
        "sample": sample,
        "read_metrics": {"effective_reads_kept": 12_000_000, "usable_bins": 6, "total_bins": 7, "lambda_gc": 1.0},
        "cnv_summary": {"total_segments": 3, "gain_calls": 1, "loss_calls": 0, "discordant_segments": 0},
        "fragmentomics": {"paired_end": True, "warnings": []},
        "prenatal": block,
    }


def calls_vcf(sample: str, *, fetal_sex: str = "F", fetal_fraction: str = "0.0812", sca_call: str = "XX") -> str:
    """The calls VCF of ``sample``: its header with the run's results, and the chr21 gain."""
    return (
        "##fileformat=VCFv4.2\n"
        "##source=ccnv_predict\n"
        "##contig=<ID=chr21,length=46709983>\n"
        '##INFO=<ID=END,Number=1,Type=Integer,Description="End">\n'
        '##INFO=<ID=SVTYPE,Number=1,Type=String,Description="Type">\n'
        '##FORMAT=<ID=LOG2R,Number=1,Type=Float,Description="Log2 ratio">\n'
        f"##fetalSex={fetal_sex}\n"
        f"##fetalFraction={fetal_fraction}\n"
        f"##scaCall={sca_call}\n"
        f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}\n"
        "chr21\t1\t.\tN\t<DUP>\t.\tPASS\tEND=200000;SVTYPE=DUP;IMPRECISE;ORIGIN=FETAL_CONSTITUTIONAL\t"
        "LOG2R:ZSCORE:WEIGHT:BINS:EXP_LOG2R:IMPLIED_FF\t0.085:5.6:282.0:2:0.086298:0.118\n"
    )


def _gzip_text(path: Path, text: str) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(text)


def write_ccnv_output(
    folder: Path,
    sample: str = "CFDNA1",
    *,
    qc: dict[str, Any] | None = None,
    vcf: str | None = None,
) -> dict[str, str]:
    """Write ``sample``'s ccnv output into ``folder``; returns the dataset entry naming the four
    files, relative to ``folder``. ``qc`` and ``vcf`` replace the QC object and the calls VCF."""
    folder.mkdir(parents=True, exist_ok=True)
    bins = BINS_HEADER + "".join(
        f"{c}\t{s}\t{e}\t{c}:{s}-{e}\t{r}\t{z}\t{w}\t{ref}\t{m}\t{b}\n" for c, s, e, r, z, w, ref, m, b in _BINS
    )
    segments = SEGMENTS_HEADER + "".join("\t".join(str(v) for v in row) + "\n" for row in _SEGMENTS)
    _gzip_text(folder / f"{sample}_bins.bed.gz", bins)
    _gzip_text(folder / f"{sample}_segments.bed.gz", segments)
    _gzip_text(folder / f"{sample}_calls.vcf.gz", vcf if vcf is not None else calls_vcf(sample))
    (folder / f"{sample}_qc.json").write_text(json.dumps(qc if qc is not None else qc_payload(sample)), encoding="utf-8")
    return {
        "bins": f"{sample}_bins.bed.gz",
        "segments": f"{sample}_segments.bed.gz",
        "calls": f"{sample}_calls.vcf.gz",
        "qc": f"{sample}_qc.json",
    }
