#!/usr/bin/env python3
"""Generate the synthetic monogenic NIPT demo family.

Writes a deterministic, ground-truthed dataset into ``demo/nipt_family/``:

- ``nipt_combined.vcf`` -- one combined two-sample VCF (father + maternal-plasma
  cfDNA columns) with variants spanning all eight maternal/fetal categories at a
  known fetal fraction (12%), plus a low-quality drop and a recurrent-artifact
  candidate. Each line records its ground-truth category in the INFO field
  (``EXP_CAT``) and gene (``GENE``), which the end-to-end test reads back.
- ``nipt_coverage.bed`` -- cfDNA coverage over the family ROI.
- ``nipt_trio.ped`` -- the father / mother(cfDNA) / fetus trio.

Run: ``python scripts/generate_nipt_demo.py``. The committed files are the source
of truth for ``backend/tests/test_nipt_end_to_end.py``; regenerate them if you
change the scenario.
"""

from __future__ import annotations

from pathlib import Path

FETAL_FRACTION = 0.12  # FF; FF/2 = 0.06
FATHER_SAMPLE = "FATHER_NIPT"
CFDNA_SAMPLE = "CFDNA_NIPT"
FETUS_SAMPLE = "FETUS_NIPT"
FAMILY_ID = "FAM_NIPT_DEMO"

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


def render_manifest() -> str:
    """A package manifest so the demo imports as a NIPT family in one step.

    Declares the analysis type and the cfDNA sample's assay (so Package Import
    tags the family for NIPT) plus the datasets: the combined father+cfDNA VCF
    (snv) and the cfDNA coverage BED. The VCF is uncompressed and unindexed,
    which the family-VCF importer accepts.
    """
    return (
        "schema_version: 1\n"
        f"family_id: {FAMILY_ID}\n"
        "analysis_type: monogenic_nipt\n"
        "ped: nipt_trio.ped\n"
        "samples:\n"
        f"  {CFDNA_SAMPLE}:\n"
        "    assay: nipt_cfdna\n"
        "datasets:\n"
        "  snv:\n"
        "    family_vcf: nipt_combined.vcf\n"
        "  coverage:\n"
        "    per_sample:\n"
        f"      {CFDNA_SAMPLE}:\n"
        "        bed: nipt_coverage.bed\n"
        "metadata:\n"
        "  description: Synthetic monogenic NIPT demo (fetal fraction 12%)\n"
    )


def render_ped() -> str:
    # FID IID PID MID SEX PHEN  (sex: 1 male, 2 female; phen: 1 unaffected, 2 affected)
    rows = [
        (FAMILY_ID, FATHER_SAMPLE, "0", "0", "1", "1"),
        (FAMILY_ID, CFDNA_SAMPLE, "0", "0", "2", "1"),
        (FAMILY_ID, FETUS_SAMPLE, FATHER_SAMPLE, CFDNA_SAMPLE, "0", "2"),
    ]
    return "\n".join("\t".join(row) for row in rows) + "\n"


def main() -> None:
    _DEMO_DIR.mkdir(parents=True, exist_ok=True)
    (_DEMO_DIR / "nipt_combined.vcf").write_text(render_vcf())
    (_DEMO_DIR / "nipt_coverage.bed").write_text(render_coverage_bed())
    (_DEMO_DIR / "nipt_trio.ped").write_text(render_ped())
    (_DEMO_DIR / "manifest.yaml").write_text(render_manifest())
    print(f"Wrote demo NIPT family to {_DEMO_DIR}")


if __name__ == "__main__":
    main()
