#!/usr/bin/env python3
"""Generate the deterministic "golden trio" E2E fixture package + EXPECTED.yaml.

The golden trio is a tiny, hand-curated synthetic family whose every record has a
known expected outcome, so the end-to-end import test asserts exact behaviour
(not just "it ran"). It doubles as a controlled verification dataset for the
IVDR technical file.

Family FAM_TRIO (a STRICT trio — required for the segregation/compound-het logic):
  FATHER  (male,   unaffected)
  MOTHER  (female, unaffected)
  PROBAND (male,   affected)

Datasets exercised: snv (clair3), sv_needlr (bespoke INFO format), repeats_trgt,
coverage, paraphase, phenotypes. Haplotype/lineage is intentionally NOT included:
a separate ``haplotypes`` family-VCF dataset imports via the small-variant loader
with overwrite=True and would wipe the clair3 SNVs — lineage gets its own
milestone with controlled switch thresholds.

Run: python scripts/generate_golden_trio.py [TARGET_DIR]
Default TARGET_DIR: backend/tests/e2e/fixtures/golden_trio
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

FAMILY_ID = "FAM_TRIO"
SAMPLES = ["FATHER", "MOTHER", "PROBAND"]

# CSQ subfields the annotation parser maps: SYMBOL->gene, Gene->gene_id,
# Consequence->effect, gnomAD_AF->frequency, etc.
CSQ_FIELDS = ["Consequence", "IMPACT", "SYMBOL", "Gene", "Feature", "gnomAD_AF", "CADD_PHRED", "CLIN_SIG"]


def _gt(call: str, dp: int = 30) -> str:
    """Render a FORMAT GT:GQ:DP:AD:AF cell for a clair3-style VCF."""
    if call == "0/1":
        half = dp // 2
        return f"0/1:99:{dp}:{half},{dp - half}:0.5"
    if call == "1/1":
        return f"1/1:99:{dp}:0,{dp}:1.0"
    return f"0/0:99:{dp}:{dp},0:0.0"


def _csq(consequence: str, impact: str, symbol: str, gnomad_af: str, cadd: str, clin: str) -> str:
    return "|".join([consequence, impact, symbol, f"ENSG_{symbol}", f"ENST_{symbol}", gnomad_af, cadd, clin])


# (pos, ref, alt, CSQ tuple, {sample: GT}, purpose)
SNVS = [
    (1000, "A", "G", ("intron_variant", "MODIFIER", "GENE_BENIGN", "0.42", "1", ""),
     {"FATHER": "0/0", "MOTHER": "0/1", "PROBAND": "0/1"}, "benign common (filtered/deprioritised)"),
    (2000, "C", "T", ("stop_gained", "HIGH", "GENE_PATH", "0.00001", "38", "pathogenic"),
     {"FATHER": "0/0", "MOTHER": "0/0", "PROBAND": "0/1"}, "P/LP candidate (ACMG via review)"),
    (3000, "G", "A", ("missense_variant", "MODERATE", "GENE_DENOVO", "0.0", "26", ""),
     {"FATHER": "0/0", "MOTHER": "0/0", "PROBAND": "0/1"}, "de novo"),
    (4000, "T", "C", ("missense_variant", "MODERATE", "GENE_CH", "0.0005", "22", ""),
     {"FATHER": "0/0", "MOTHER": "0/1", "PROBAND": "0/1"}, "compound-het A (maternal)"),
    (5000, "A", "T", ("missense_variant", "MODERATE", "GENE_CH", "0.0007", "24", ""),
     {"FATHER": "0/1", "MOTHER": "0/0", "PROBAND": "0/1"}, "compound-het B (paternal)"),
    (6000, "C", "G", ("missense_variant", "MODERATE", "BRCA2", "0.0003", "23", ""),
     {"FATHER": "0/0", "MOTHER": "0/1", "PROBAND": "0/1"}, "SNV half of SNV+SV comp-het (maternal)"),
    (7000, "AG", "A", ("frameshift_variant", "HIGH", "GENE_INDEL", "0.0001", "30", ""),
     {"FATHER": "0/0", "MOTHER": "0/1", "PROBAND": "0/1"}, "INDEL (type contract: INDEL in both endpoints)"),
    (8000, "AC", "GT", ("missense_variant", "MODERATE", "GENE_MNV", "0.0002", "21", ""),
     {"FATHER": "0/0", "MOTHER": "0/0", "PROBAND": "0/1"}, "MNV (type contract: INDEL in family endpoint, MNV in explorer)"),
]


def _write_snv(root: Path) -> None:
    lines = [
        "##fileformat=VCFv4.2",
        '##FILTER=<ID=PASS,Description="All filters passed">',
        '##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from Ensembl VEP. '
        f'Format: {"|".join(CSQ_FIELDS)}">',
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
        '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">',
        '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Read depth">',
        '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allele depths">',
        '##FORMAT=<ID=AF,Number=A,Type=Float,Description="Allele frequency">',
        "#" + "\t".join(["CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO", "FORMAT"] + SAMPLES),
    ]
    for pos, ref, alt, csq, gts, _purpose in SNVS:
        info = f"CSQ={_csq(*csq)}"
        cells = [_gt(gts[s]) for s in SAMPLES]
        lines.append("\t".join(["1", str(pos), ".", ref, alt, "60", "PASS", info, "GT:GQ:DP:AD:AF"] + cells))
    (root / "snv").mkdir(parents=True, exist_ok=True)
    (root / "snv" / "family.vcf").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_sv_needlr(root: Path) -> None:
    # Bespoke Needlr format: per-sample calls come from INFO keys (Query_ID is the
    # proband; parents resolved from the PED). DEL over BRCA2 transmitted from the
    # FATHER (Paternal_GT 0/1) pairs in trans with the maternal BRCA2 SNV above.
    header = [
        "##fileformat=VCFv4.2",
        "#" + "\t".join(["CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO"]),
    ]
    del_info = ";".join([
        "SVTYPE=DEL", "SVLEN=-5000", "END=12000", "Genes=BRCA2",
        "Query_ID=PROBAND", "Genotype=0/1", "Alt_Reads=18",
        "Maternal_GT=0/0", "Maternal_Alt_Reads=0",
        "Paternal_GT=0/1", "Paternal_Alt_Reads=15",
    ])
    bnd_info = ";".join([
        "SVTYPE=BND", "Query_ID=PROBAND", "Genotype=0/1", "Alt_Reads=7",
        "Maternal_GT=0/0", "Paternal_GT=0/0",
    ])
    rows = [
        "\t".join(["1", "7000", "SVDEL1", "N", "<DEL>", "60", "PASS", del_info]),
        "\t".join(["1", "20000", "SVBND1", "N", "N]chr5:3000000]", "30", "PASS", bnd_info]),
    ]
    (root / "sv_needlr").mkdir(parents=True, exist_ok=True)
    (root / "sv_needlr" / "family.needlr.vcf").write_text("\n".join(header + rows) + "\n", encoding="utf-8")


def _write_repeats(root: Path) -> None:
    # HD_HTT (Huntington): proband carries a pathogenic 40-CAG allele; parents normal (18).
    header = [
        "##fileformat=VCFv4.2",
        '##INFO=<ID=TRID,Number=1,Type=String,Description="Tandem repeat id">',
        '##INFO=<ID=END,Number=1,Type=Integer,Description="End position">',
        '##INFO=<ID=MOTIFS,Number=.,Type=String,Description="Repeat motifs">',
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
        '##FORMAT=<ID=MC,Number=.,Type=String,Description="Motif counts per allele">',
        '##FORMAT=<ID=MS,Number=.,Type=String,Description="Motif spans per allele">',
        "#" + "\t".join(["CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO", "FORMAT"] + SAMPLES),
    ]
    info = "END=3074933;TRID=HD_HTT;MOTIFS=CAG,CAA"
    normal = "0/0:18_0,18_0:0(0-54),0(0-54)"
    proband = "0/1:18_0,40_2:0(0-54),0(0-120)_1(120-126)"
    row = "\t".join(
        ["chr4", "3074876", ".", "A", "<STR>", ".", "PASS", info, "GT:MC:MS", normal, normal, proband]
    )
    (root / "repeats").mkdir(parents=True, exist_ok=True)
    (root / "repeats" / f"{FAMILY_ID}.trgt.vcf").write_text("\n".join(header + [row]) + "\n", encoding="utf-8")


COVERAGE_BINS = list(range(1000, 6000, 1000))  # 5 bins per sample


def _write_coverage(root: Path) -> None:
    (root / "coverage").mkdir(parents=True, exist_ok=True)
    for sample in SAMPLES:
        rows = [f"1\t{b}\t{b + 1000}\t{0.5 + (b % 3000) / 10000:.4f}" for b in COVERAGE_BINS]
        (root / "coverage" / f"{sample}.coverage.bed").write_text("\n".join(rows) + "\n", encoding="utf-8")


def _write_paraphase(root: Path) -> None:
    import json

    (root / "paraphase").mkdir(parents=True, exist_ok=True)
    payload = {
        "GBA": {
            "total_cn": 2,
            "gene_cn": None,
            "highest_total_cn": 2,
            "sample_sex": "male",
            "phase_region": "38:chr1:10-20",
            "region_depth": {"median": 42},
            "genome_depth": 33.5,
            "final_haplotypes": {"h1": "GBA_hap1"},
        },
        "unknown_region": None,  # non-dict top-level value: must be skipped
    }
    (root / "paraphase" / "PROBAND.paraphase.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _write_phenotypes(root: Path) -> None:
    cols = ["family_id", "individual_id", "hpo_id", "label", "status", "onset", "evidence", "source", "note"]
    rows = [
        "\t".join(cols),
        "\t".join([FAMILY_ID, "PROBAND", "HP:0001250", "Seizure", "present", "", "", "", ""]),
    ]
    (root / "phenotypes.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")


def _write_ped(root: Path) -> None:
    rows = [
        "\t".join([FAMILY_ID, "FATHER", "0", "0", "1", "1"]),
        "\t".join([FAMILY_ID, "MOTHER", "0", "0", "2", "1"]),
        "\t".join([FAMILY_ID, "PROBAND", "FATHER", "MOTHER", "1", "2"]),
    ]
    (root / "family.ped").write_text("\n".join(rows) + "\n", encoding="utf-8")


def _write_manifest(root: Path) -> None:
    manifest = {
        "schema_version": 1,
        "family_id": FAMILY_ID,
        "ped": "family.ped",
        "metadata": {"hpo": ["HP:0001250"]},
        "datasets": {
            "snv": {"family_vcf": "snv/family.vcf"},
            "sv_needlr": {"family_vcf": "sv_needlr/family.needlr.vcf"},
            "repeats_trgt": {"family_vcf": f"repeats/{FAMILY_ID}.trgt.vcf"},
            "coverage": {"per_sample": {s: {"bed": f"coverage/{s}.coverage.bed"} for s in SAMPLES}},
            "paraphase": {"per_sample": {"PROBAND": {"json": "paraphase/PROBAND.paraphase.json"}}},
        },
        "phenotypes": {"file": "phenotypes.tsv", "format": "hpo_tsv"},
    }
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")


def _write_expected(root: Path) -> None:
    """Machine-readable ground truth — the verification record's expected results."""
    expected = {
        "family_id": FAMILY_ID,
        "sample_ids": SAMPLES,
        "member_roles": {"FATHER": "father", "MOTHER": "mother", "PROBAND": "proband"},
        "dataset_status": {
            "snv": "imported",
            "sv_needlr": "imported",
            "repeats_trgt": "imported",
            "coverage": "imported",
            "paraphase": "imported",
        },
        "snv": {
            "count": len(SNVS),
            "compound_het_gene": "GENE_CH",
            # One GENE_CH variant from each parent: the parents' genotypes place the
            # pair in trans (no read-backed phasing in this fixture).
            "compound_het_phase": "trans",
            "compound_het_phase_evidence": "segregation",
            "de_novo_gene": "GENE_DENOVO",
            "indel_gene": "GENE_INDEL",
            "mnv_gene": "GENE_MNV",
        },
        "structural_variants": {"count": 2, "del_gene": "BRCA2"},
        "sv_second_hit": {
            "gene": "BRCA2",
            "phase": "trans",
            "phase_evidence": "segregation",
            "has_deletion": True,
            "deletion_unmasked": True,
        },
        "repeat_expansion": {"locus_id": "HD_HTT", "gene": "HTT", "proband_status": "pathogenic", "pathogenic_min": 36},
        "coverage": {"sources": len(SAMPLES), "total_rows": len(SAMPLES) * len(COVERAGE_BINS)},
        "paraphase_genes": ["GBA"],
        "hpo_terms": ["HP:0001250"],
    }
    (root / "EXPECTED.yaml").write_text(yaml.safe_dump(expected, sort_keys=False), encoding="utf-8")


def generate(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    _write_ped(target)
    _write_manifest(target)
    _write_snv(target)
    _write_sv_needlr(target)
    _write_repeats(target)
    _write_coverage(target)
    _write_paraphase(target)
    _write_phenotypes(target)
    _write_expected(target)
    print(f"Wrote golden trio fixture to {target}")


if __name__ == "__main__":
    default = Path(__file__).resolve().parent.parent / "backend" / "tests" / "e2e" / "fixtures" / "golden_trio"
    out = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else default
    generate(out)
