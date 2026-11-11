#!/usr/bin/env python3
"""Generate the synthetic monogenic NIPT demo family.

Writes a deterministic, ground-truthed package folder into ``demo/nipt_family/``:

- ``nipt_combined.vcf`` -- one combined two-sample VCF (father + maternal-plasma
  cfDNA columns) with variants spanning all eight maternal/fetal categories at a
  known fetal fraction (12%), plus a low-quality drop and a recurrent-artifact
  candidate. Each line records its ground-truth category in the INFO field
  (``EXP_CAT``) and gene (``GENE``), which the end-to-end test reads back.
- ``nipt_coverage.bed`` -- cfDNA coverage over the family ROI.
- ``coga-package.json`` -- the package file a pipeline writes (``coga-package/1``,
  docs/coga-package.md): the project and assembly the package belongs to, the
  analysis, the trio (the father, the mother whose sample is the cfDNA, the
  fetus), the datasets, and every file of the folder with its size and SHA-256,
  the hand-written ``README.md`` included.

Run: ``python scripts/generate_nipt_demo.py``. The committed files are the source
of truth for ``backend/tests/test_nipt_end_to_end.py``; regenerate them if you
change the scenario, and after editing the README, whose checksum the package
file lists.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

FETAL_FRACTION = 0.12  # FF; FF/2 = 0.06
FATHER_SAMPLE = "FATHER_NIPT"
CFDNA_SAMPLE = "CFDNA_NIPT"
FETUS_SAMPLE = "FETUS_NIPT"
FAMILY_ID = "FAM_NIPT_DEMO"

# Where the package belongs in CoGA: a project of this name on this assembly. The folder is
# delivered in the project's folder of a watched import folder (``<watched folder>/Demo/``).
PROJECT = "Demo"
ASSEMBLY = "GRCh38"
# The package file's time and run: fixed, so that regenerating writes the same file.
WRITTEN_AT = "2026-11-11T12:00:00+01:00"
PIPELINE = {"name": "generate_nipt_demo", "version": "1", "run_id": "NIPT_DEMO_1"}
PACKAGE_FILE = "coga-package.json"
VCF_FILE = "nipt_combined.vcf"
COVERAGE_FILE = "nipt_coverage.bed"
# Every file of the folder, as the package file lists it.
PACKAGE_FILES = ("README.md", COVERAGE_FILE, VCF_FILE)

# Family ROI covering the demo variants (chr1) -- the coverage target.
ROI_CHROM = "1"
ROI_START = 1_000
ROI_END = 20_000

_DEMO_DIR = Path(__file__).resolve().parents[1] / "demo" / "nipt_family"


def _ad(dp: int, vaf: float) -> tuple[int, int]:
    alt = round(dp * vaf)
    return dp - alt, alt


def _father_ad(gt: str, dp: int) -> tuple[int, int]:
    if gt == "0/0":
        return dp, 0
    if gt == "1/1":
        return 0, dp
    return dp // 2, dp - dp // 2  # 0/1


def _fmt(gt: str, dp: int, ad: tuple[int, int]) -> str:
    vaf = (ad[1] / dp) if dp else 0.0
    return f"{gt}:{dp}:{ad[0]},{ad[1]}:{vaf:.4f}"


def _cf_gt(vaf: float, present: bool) -> str:
    if not present:
        return "0/0"
    if vaf >= 0.9:
        return "1/1"
    return "0/1"


def build_demo_variants() -> list[dict]:
    """The ground-truthed variant list. ``exp_cat`` is the expected category, or
    ``QUAL_DROP`` / ``ARTIFACT`` for the two filtered cases."""
    half = FETAL_FRACTION / 2.0  # 0.06
    variants: list[dict] = [
        # cat 1: de novo in fetus (father hom-ref, low cfDNA VAF).
        dict(pos=1001, gene="GENE_DENOVO", exp_cat="1", father_gt="0/0", cf_vaf=half, cf_dp=500),
        # cat 2: maternal het, fetus did not inherit (0.5 - FF/2).
        dict(pos=2001, gene="GENE_MAT2", exp_cat="2", father_gt="0/0", cf_vaf=0.5 - half, cf_dp=600),
        # cat 3: maternal het, fetus het (0.5) -- the maternal hit of a compound pair.
        dict(pos=3001, gene="GENE_RECESS", exp_cat="3", father_gt="0/0", cf_vaf=0.5, cf_dp=600),
        # cat 4: maternal het, fetus hom (0.5 + FF/2) -- homozygous recessive risk.
        dict(pos=4001, gene="GENE_HOMRISK", exp_cat="4", father_gt="0/1", cf_vaf=0.5 + half, cf_dp=600),
        # cat 5: maternal hom, fetus het (1 - FF/2).
        dict(pos=5001, gene="GENE_MATHOM", exp_cat="5", father_gt="0/0", cf_vaf=1.0 - half, cf_dp=600),
        # cat 6: maternal & fetal hom (1.0).
        dict(pos=6001, gene="GENE_MATHOM2", exp_cat="6", father_gt="1/1", cf_vaf=1.0, cf_dp=600),
        # cat 8: paternal hom-alt, absent in cfDNA -> false negative.
        dict(pos=8001, gene="GENE_PATHOM", exp_cat="8", father_gt="1/1", cf_vaf=0.0, cf_dp=400, present=False),
        # Quality drop: cfDNA depth below the threshold.
        dict(pos=9001, gene="GENE_LOWQUAL", exp_cat="QUAL_DROP", father_gt="0/0", cf_vaf=0.5, cf_dp=5, qual=12.0),
        # Recurrent-artifact candidate (cat 3 shaped); add it via /admin/nipt/artifacts.
        dict(pos=9501, gene="GENE_ARTIFACT", exp_cat="ARTIFACT", father_gt="0/0", cf_vaf=0.5, cf_dp=600),
    ]
    # 40 category-7 sites (paternal allele transmitted) for fetal-fraction
    # estimation, at FF/2 with a small deterministic jitter averaging to 0.06.
    jitter = (0.0575, 0.0625)
    for index in range(40):
        if index == 0:
            gene = "GENE_RECESS"  # paternal hit of the compound pair
        elif index == 1:
            gene = "GENE_CARRIER"  # lone paternal hit (not at risk)
        else:
            gene = ""  # intergenic
        variants.append(
            dict(
                pos=10_000 + index * 10,
                gene=gene,
                exp_cat="7",
                father_gt="0/1",
                cf_vaf=jitter[index % 2],
                cf_dp=400,
            )
        )
    return variants


def _vcf_line(variant: dict) -> str:
    pos = variant["pos"]
    gene = variant["gene"]
    exp_cat = variant["exp_cat"]
    qual = variant.get("qual", 60.0)
    present = variant.get("present", True)
    father_dp = variant.get("father_dp", 50)
    cf_dp = variant["cf_dp"]
    cf_vaf = variant["cf_vaf"]

    father_ad = _father_ad(variant["father_gt"], father_dp)
    cf_ad = _ad(cf_dp, cf_vaf)
    father_field = _fmt(variant["father_gt"], father_dp, father_ad)
    cfdna_field = _fmt(_cf_gt(cf_vaf, present), cf_dp, cf_ad)

    info = f"GENE={gene};EXP_CAT={exp_cat}" if gene else f"EXP_CAT={exp_cat}"
    return "\t".join(
        [
            "1",
            str(pos),
            ".",
            "A",
            "G",
            f"{qual:.1f}",
            "PASS",
            info,
            "GT:DP:AD:AF",
            father_field,
            cfdna_field,
        ]
    )


def render_vcf() -> str:
    header = [
        "##fileformat=VCFv4.2",
        "##source=CoGANiptDemo",
        "##contig=<ID=1>",
        '##INFO=<ID=GENE,Number=1,Type=String,Description="Gene symbol">',
        '##INFO=<ID=EXP_CAT,Number=1,Type=String,Description="Ground-truth NIPT category (1-8, QUAL_DROP, ARTIFACT)">',
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
        '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Read depth">',
        '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allelic depths (ref,alt)">',
        '##FORMAT=<ID=AF,Number=A,Type=Float,Description="Allele fraction">',
        "\t".join(
            ["#CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO", "FORMAT", FATHER_SAMPLE, CFDNA_SAMPLE]
        ),
    ]
    lines = header + [_vcf_line(variant) for variant in build_demo_variants()]
    return "\n".join(lines) + "\n"


def render_coverage_bed() -> str:
    """cfDNA coverage over the ROI: 80x, with one lower-depth window."""
    rows = [
        (ROI_CHROM, ROI_START, 8_000, 80),
        (ROI_CHROM, 8_000, 12_000, 45),  # a lower-coverage stretch
        (ROI_CHROM, 12_000, ROI_END, 80),
    ]
    return "\n".join(f"{chrom}\t{start}\t{end}\t{value}" for chrom, start, end, value in rows) + "\n"


def package_members() -> list[dict[str, Any]]:
    """The trio: the father, the mother (her sample is the maternal plasma, assay ``nipt_cfdna``,
    which tags it for the NIPT analysis) and the affected fetus, without a sample of its own."""
    return [
        {"sample_id": FATHER_SAMPLE, "sex": "male", "clinical_status": "unaffected"},
        {"sample_id": CFDNA_SAMPLE, "sex": "female", "clinical_status": "unaffected", "assay": "nipt_cfdna"},
        {
            "sample_id": FETUS_SAMPLE,
            "sex": "unknown",
            "father": FATHER_SAMPLE,
            "mother": CFDNA_SAMPLE,
            "clinical_status": "affected",
        },
    ]


def package_datasets() -> dict[str, Any]:
    """The datasets: the combined father+cfDNA VCF (``snv``; uncompressed and unindexed, which the
    family-VCF importer accepts) and the plasma's coverage BED, which declares that it holds read
    depths: the NIPT coverage check reads a coverage BED as depth only when it says so."""
    return {
        "snv": {"family_vcf": VCF_FILE},
        "coverage": {"per_sample": {CFDNA_SAMPLE: {"bed": COVERAGE_FILE, "content": "read_depth"}}},
    }


def file_entry(folder: Path, path: str) -> dict[str, Any]:
    """A file of the package as the package file lists it: its path, size and SHA-256."""
    content = (folder / path).read_bytes()
    return {"path": path, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}


def render_package(folder: Path) -> str:
    """The package file of the folder ``folder``, whose other files are written: complete, with
    each file's size and checksum as the folder holds it."""
    package = {
        "schema": "coga-package/1",
        "status": "complete",
        "written_at": WRITTEN_AT,
        "pipeline": PIPELINE,
        "project": PROJECT,
        "assembly": ASSEMBLY,
        "analysis_type": "monogenic_nipt",
        "family": {"family_id": FAMILY_ID},
        "members": package_members(),
        "datasets": package_datasets(),
        "files": [file_entry(folder, path) for path in PACKAGE_FILES],
        "notes": "Synthetic monogenic NIPT demo (fetal fraction 12%).",
    }
    return json.dumps(package, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    _DEMO_DIR.mkdir(parents=True, exist_ok=True)
    (_DEMO_DIR / VCF_FILE).write_text(render_vcf())
    (_DEMO_DIR / COVERAGE_FILE).write_text(render_coverage_bed())
    # Last: it lists the other files with their checksums.
    (_DEMO_DIR / PACKAGE_FILE).write_text(render_package(_DEMO_DIR))
    print(f"Wrote demo NIPT family to {_DEMO_DIR}")


if __name__ == "__main__":
    main()
