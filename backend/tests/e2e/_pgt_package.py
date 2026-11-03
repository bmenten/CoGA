"""The synthetic PGT package the PGT end-to-end modules import.

A PGT family of an affected father, the mother, their affected child as the index and two
embryos: a phased GLIMPSE2 family VCF of twenty sites on chr1, the inheritance model AD and an
ROI on chr1. ``test_e2e_signed_pgt_analysis.py`` reads the embryo calls of this family and
signs them out; ``test_e2e_outdated_haplotype_blocks.py`` corrects a member's record after
its haplotype blocks were built. ``SIGN_OUT_BODY`` acknowledges the drift and Sample-QC gates the package raises.
"""

from __future__ import annotations

from pathlib import Path

_FORMAT = "GT:DP:AD:AF:GP:PS"
# Between sites, so the ROI holds six of them whatever the bounds: 1,005,000 to 1,010,000.
ROI = "1:1004500-1010500"
SIGN_OUT_BODY = {
    "acknowledge_drift": True,
    "drift_acknowledgement_reason": "e2e: nothing reported",
    "acknowledge_qc": True,
    "qc_acknowledgement_reason": "e2e: synthetic genotypes at twenty sites",
}


def _cell(gt: str) -> str:
    # A phased genotype with GLIMPSE2's posteriors and one phase set.
    return f"{gt}:30:15,15:0.5:0.9,0.05,0.05:1"


def write_pgt_package(root: Path, family_id: str) -> dict[str, str]:
    """A PGT package: the father (affected), the mother, their affected child (the index)
    and two embryos. At even sites the father is heterozygous and the mother not, at odd
    sites the other way round, so each child's inherited homolog is read on both sides. The
    index and E_RISK inherit the father's homolog 1, E_CLEAR his homolog 0."""
    names = {role: f"{role}_{family_id}" for role in ("FATHER", "MOTHER", "INDEX", "E_RISK", "E_CLEAR")}
    father, mother = names["FATHER"], names["MOTHER"]
    root.mkdir(parents=True, exist_ok=True)
    (root / "family.ped").write_text(
        "\n".join(
            [
                f"{family_id}\t{father}\t0\t0\t1\t2",
                f"{family_id}\t{mother}\t0\t0\t2\t1",
                f"{family_id}\t{names['INDEX']}\t{father}\t{mother}\t2\t2",
                f"{family_id}\t{names['E_RISK']}\t{father}\t{mother}\t2\t0",
                f"{family_id}\t{names['E_CLEAR']}\t{father}\t{mother}\t1\t0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "manifest.yaml").write_text(
        "schema_version: 1\n"
        f"family_id: {family_id}\n"
        "ped: family.ped\n"
        f"roi: \"{ROI}\"\n"
        "metadata:\n"
        "  pgt:\n"
        "    inheritance_model: AD\n"
        "family:\n"
        "  members:\n"
        f"    {names['E_RISK']}: {{role: embryo}}\n"
        f"    {names['E_CLEAR']}: {{role: embryo}}\n"
        "datasets:\n"
        "  haplotypes:\n"
        "    family_vcf: haplotypes/family.glimpse2.vcf\n"
        "    source_format: glimpse2\n",
        encoding="utf-8",
    )
    columns = [names[role] for role in ("FATHER", "MOTHER", "INDEX", "E_RISK", "E_CLEAR")]
    header = [
        "##fileformat=VCFv4.2",
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
        '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">',
        '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allele depths">',
        '##FORMAT=<ID=AF,Number=A,Type=Float,Description="Allele frequency">',
        '##FORMAT=<ID=GP,Number=G,Type=Float,Description="Genotype posteriors">',
        '##FORMAT=<ID=PS,Number=1,Type=Integer,Description="Phase set">',
        "#" + "\t".join(["CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO", "FORMAT", *columns]),
    ]
    rows = []
    for index in range(20):
        position = 1_000_000 + index * 1_000
        if index % 2 == 0:
            # The father's homolog 1 carries the ALT allele: the index and E_RISK have it.
            genotypes = ["0|1", "0|0", "1|0", "1|0", "0|0"]
        else:
            # The mother's homolog 1 carries it: E_RISK inherited it, the others her homolog 0.
            genotypes = ["0|0", "0|1", "0|0", "0|1", "0|0"]
        rows.append(
            "\t".join(["1", str(position), ".", "A", "G", "60", "PASS", ".", _FORMAT, *map(_cell, genotypes)])
        )
    vcf = root / "haplotypes" / "family.glimpse2.vcf"
    vcf.parent.mkdir(parents=True, exist_ok=True)
    vcf.write_text("\n".join(header + rows) + "\n", encoding="utf-8")
    return names
