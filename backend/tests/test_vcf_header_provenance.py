"""Unit tests for VCF/TRGT header provenance extraction.

Realistic header fragments from the tools CoGA ingests (DeepVariant/GATK +
VEP/snpEff/bcftools for SNV, Sniffles/Spectre for SV, TRGT for repeats). The
parser is best-effort: it must extract what it can and never raise.
"""

from __future__ import annotations

import pytest

from app.services.vcf_header_provenance import (
    extract_header_provenance,
    extract_info_description_provenance,
    extract_vep_tab_provenance,
    merge_module_maps,
)

# Real-shape NeedlR SV header: no structured version lines — the annotation
# database releases live inside the ##INFO descriptions.
SV_INFO_HEADER = """\
##fileformat=VCFv4.2
##INFO=<ID=Genes,Number=.,Type=String,Description="Genes overlapped by the SV (gencode v45, +/-5kb)">
##INFO=<ID=OMIM_phenotype,Number=.,Type=String,Description="OMIM phenotypes associated with any gene the SV overlaps (OMIM 20260129)">
##INFO=<ID=GENCC_phenotype,Number=.,Type=String,Description="GenCC phenotypes for overlapping genes (GenCC 20250510)">
##INFO=<ID=pLI,Number=.,Type=String,Description="pLI scores from gnomAD v4.1">
##INFO=<ID=Segdup,Number=1,Type=String,Description="SV overlaps a segmental duplication (GIAB v3.3)">
##INFO=<ID=HiConf,Number=1,Type=String,Description="SV fully contained in a high-confidence region (GIAB T2TQ100-V1.0)">
##INFO=<ID=Alt_Reads,Number=1,Type=Integer,Description="Number of reads supporting the SV">
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO
chr1\t100\t.\tA\t<DEL>\t.\tPASS\tSVTYPE=DEL
"""

# Real-shape VEP --tab output header: versions are stated as "## <name> version
# <value>" (plus the banner + cache line), not the VCF ##KEY=value form.
VEP_TAB_HEADER = """\
## ENSEMBL VARIANT EFFECT PREDICTOR v115.1
## Output produced at 2026-05-07 15:32:28
## Using cache in /data/ref/cache/homo_sapiens/115_GRCh38
## Using API version 115, DB version ?
## ensembl version 115.266b84d
## ensembl-variation version 115.b7c2637
## 1000genomes version phase3
## COSMIC version 101
## ClinVar version 202502
## HGMD-PUBLIC version 20204
## assembly version GRCh38.p14
## dbSNP version 156
## gencode version GENCODE 49
## gnomADe version v4.1
## gnomADg version v4.1
## polyphen version 2.2.3
## sift version 6.2.1
## Column descriptions:
## Uploaded_variation : Identifier of uploaded variant
#Uploaded_variation\tLocation\tAllele\tGene
chr1\t100\tA\tBRCA1
"""

SNV_VEP_HEADER = """\
##fileformat=VCFv4.2
##fileDate=20240115
##source=DeepVariant
##DeepVariant_version=1.6.0
##reference=/data/ref/GRCh38.fa
##VEP="v110" time="2024-01-15 10:00:00" cache="/data/vep/homo_sapiens/110_GRCh38" ensembl-version=110 gnomADe="r2.1.1" gnomADg="v3.1.2" ClinVar="202301" dbNSFP="4.3a" SpliceAI="1.3" dbSNP="154" assembly="GRCh38.p14" sift="sift5.2.2" polyphen="2.2.2"
##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from Ensembl VEP">
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPROBAND
"""

SNV_GATK_SNPEFF_HEADER = """\
##fileformat=VCFv4.2
##GATKCommandLine=<ID=HaplotypeCaller,CommandLine="HaplotypeCaller --input x.bam",Version="4.4.0.0",Date="2024-01-15">
##SnpEffVersion="5.1d (build 2022-04-19 15:05), by Pablo Cingolani"
##SnpEffCmd="SnpEff  GRCh38.105 input.vcf"
##bcftools_normVersion=1.17+htslib-1.17
##bcftools_normCommand=norm -f ref.fa; Date=2024-01-15
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO
"""

SV_SNIFFLES_HEADER = """\
##fileformat=VCFv4.2
##source=Sniffles2_2.2
##command="sniffles --input proband.bam --vcf out.vcf"
##fileDate=2024-02-01
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPROBAND
"""

SV_SPECTRE_HEADER = """\
##fileformat=VCFv4.2
##source=Spectre
##spectreVersion=0.2.1
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO
"""

TRGT_HEADER = """\
##fileformat=VCFv4.2
##fileDate=2024-03-01
##trgtVersion=0.7.0
##trgtCommand=trgt genotype --genome ref.fa --repeats catalog.bed
##reference=GRCh38.fa
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPROBAND
"""


def _modules(text: str, modality: str | None = None) -> dict:
    return extract_header_provenance(text.splitlines(), modality=modality).as_modules()


def test_snv_vep_extracts_caller_engine_and_db_versions():
    prov = extract_header_provenance(SNV_VEP_HEADER.splitlines(), modality="snv")
    mods = prov.as_modules()
    assert prov.fileformat == "VCFv4.2"
    assert prov.file_date == "20240115"
    assert prov.reference == "GRCh38.fa"
    assert mods["deepvariant"]["version"] == "1.6.0"
    assert mods["vep"]["version"] == "110"
    assert "110_GRCh38" in mods["vep"]["detail"]  # cache path
    # Database releases embedded in the VEP header.
    assert mods["gnomad"]["version"] == "r2.1.1"
    assert mods["clinvar"]["version"] == "202301"
    assert mods["dbnsfp"]["version"] == "4.3a"
    assert mods["spliceai"]["version"] == "1.3"
    assert mods["dbsnp"]["version"] == "154"
    assert mods["sift"]["version"] == "sift5.2.2"


def test_snv_vep_caller_tagged_with_modality():
    prov = extract_header_provenance(SNV_VEP_HEADER.splitlines(), modality="snv")
    assert prov.caller is not None and prov.caller["name"] == "deepvariant"
    assert prov.modules["deepvariant"]["detail"] == "snv caller"


def test_gatk_snpeff_bcftools():
    mods = _modules(SNV_GATK_SNPEFF_HEADER)
    assert mods["gatk"]["version"] == "4.4.0.0"
    assert mods["snpeff"]["version"] == "5.1d"
    assert mods["snpeff"]["detail"] == "GRCh38.105"  # genome db from SnpEffCmd
    assert mods["bcftools"]["version"] == "1.17+htslib-1.17"


def test_sv_sniffles_name_version_split():
    prov = extract_header_provenance(SV_SNIFFLES_HEADER.splitlines(), modality="sv")
    mods = prov.as_modules()
    assert mods["sniffles"]["version"] == "2.2"
    assert prov.caller["name"] == "sniffles"


def test_sv_spectre():
    mods = _modules(SV_SPECTRE_HEADER, modality="sv")
    assert "spectre" in mods
    assert mods["spectre"]["version"] == "0.2.1"


def test_trgt_repeats():
    prov = extract_header_provenance(TRGT_HEADER.splitlines(), modality="repeats")
    mods = prov.as_modules()
    assert mods["trgt"]["version"] == "0.7.0"
    assert prov.reference == "GRCh38.fa"


def test_stops_at_column_header_and_ignores_data():
    # A data line with a '##'-looking value must not be parsed as header.
    text = SV_SPECTRE_HEADER + "chr1\t100\t.\tA\t<DEL>\t.\tPASS\tSVTYPE=DEL\n"
    mods = _modules(text)
    assert "spectre" in mods  # header parsed
    assert "del" not in mods  # data line ignored


def test_empty_and_malformed_never_raise():
    assert extract_header_provenance([]).as_modules() == {}
    # Malformed lines: no '=', stray quotes, truncated VEP — must not raise.
    junk = [
        "##VEP=",
        "##GATKCommandLine=<ID=,Version=>",
        "##source=",
        "##weird line without equals",
        "##bcftools_=",
        "##fooVersion=<",
        '##fooVersion="',
        '##fooVersion=<ID=foo,Version=">',
        "##fooCommand=",
        "not even a header",
    ]
    assert isinstance(extract_header_provenance(junk).as_modules(), dict)


def test_merge_prefers_existing_then_fills_gaps():
    base = {"vep": {"version": "110"}, "clinvar": {"version": "202301"}}
    new = {"vep": {"version": "112"}, "gnomad": {"version": "v4.1"}}
    merged = merge_module_maps(base, new)
    assert merged["vep"]["version"] == "110"  # base wins
    assert merged["clinvar"]["version"] == "202301"  # base-only kept
    assert merged["gnomad"]["version"] == "v4.1"  # gap filled from new


def test_vep_tab_header_extracts_database_versions():
    mods = extract_vep_tab_provenance(VEP_TAB_HEADER.splitlines())
    assert mods["vep"]["version"] == "115.1"
    assert "115_GRCh38" in mods["vep"]["detail"]  # cache dir
    assert mods["clinvar"]["version"] == "202502"
    assert mods["gnomad"]["version"] == "v4.1"  # gnomADe/gnomADg → gnomad
    assert mods["dbsnp"]["version"] == "156"
    assert mods["cosmic"]["version"] == "101"
    assert mods["sift"]["version"] == "6.2.1"
    assert mods["polyphen"]["version"] == "2.2.3"
    assert mods["assembly"]["version"] == "GRCh38.p14"
    assert mods["gencode"]["version"] == "GENCODE 49"
    assert mods["hgmd"]["version"] == "20204"  # from HGMD-PUBLIC


def test_vep_tab_header_excludes_noise_and_column_descriptions():
    mods = extract_vep_tab_provenance(VEP_TAB_HEADER.splitlines())
    # ensembl-internal/build lines, the "DB version ?" line, 1000genomes, and the
    # per-column "## X : description" lines must not become modules.
    for noise in ("ensembl", "ensemblvariation", "1000genomes", "usingapi", "uploaded_variation"):
        assert noise not in mods
    assert "?" not in str(mods)


def test_vep_tab_header_empty_is_safe():
    assert extract_vep_tab_provenance([]) == {}
    assert extract_vep_tab_provenance(["#CHROM\tPOS", "chr1\t1"]) == {}


def test_info_description_mining_extracts_sv_database_versions():
    mods = extract_info_description_provenance(SV_INFO_HEADER.splitlines())
    assert mods["gencode"]["version"] == "v45"
    assert mods["omim"]["version"] == "20260129"
    assert mods["gencc"]["version"] == "20250510"
    assert mods["gnomad"]["version"] == "v4.1"
    assert mods["giab"]["version"] == "v3.3"  # first occurrence (Segdup) wins


def test_info_description_mining_ignores_unversioned_and_data():
    mods = extract_info_description_provenance(SV_INFO_HEADER.splitlines())
    # The Alt_Reads description has no database/version → no spurious module; and
    # only the allowlisted databases appear.
    assert set(mods) <= {"gencode", "omim", "gencc", "gnomad", "giab", "clinvar", "dbsnp", "cosmic"}
    assert "alt_reads" not in mods and "del" not in mods


def test_info_description_mining_empty_safe():
    assert extract_info_description_provenance([]) == {}
    assert extract_info_description_provenance(["#CHROM\tPOS", "chr1\t1\t.\tA\tG"]) == {}


def test_merging_modalities_keeps_each_modalitys_caller():
    # As an importer folds the module maps of its files together (merge_module_maps).
    combined: dict = {}
    for header, modality in ((SNV_VEP_HEADER, "snv"), (SV_SNIFFLES_HEADER, "sv"), (TRGT_HEADER, "repeats")):
        combined = merge_module_maps(combined, _modules(header, modality))
    # Each modality's caller is present in one merged family manifest.
    assert combined["deepvariant"]["version"] == "1.6.0"
    assert combined["sniffles"]["version"] == "2.2"
    assert combined["trgt"]["version"] == "0.7.0"
    assert combined["vep"]["version"] == "110"


# --- A command line names the tool, never its version ------------------------------ #
# ``##<tool>Command=`` and ``##<tool>Cmd=`` lines start with the program or subcommand
# (``view``, ``SnpSift``, ``trgt``), not a version, and the manifest is frozen into each
# sign-out. Only a version line gives the version, wherever the command line stands.


def test_a_bcftools_command_line_records_no_version():
    mods = _modules(
        "##fileformat=VCFv4.2\n"
        "##bcftools_viewCommand=view -Oz -o out.vcf.gz in.vcf.gz; Date=Tue Oct  1 10:00:00 2024\n"
    )
    # bcftools ran, at a version the header does not state: not version "view".
    assert mods == {"bcftools": {}}


def test_snpsift_records_its_version_not_its_name():
    # SnpSift's own header lines: its version line names the tool first.
    header = (
        "##fileformat=VCFv4.2\n"
        '##SnpSiftVersion="SnpSift 5.1d (build 2022-04-19 15:05), by the snpEff author"\n'
        '##SnpSiftCmd="SnpSift annotate -id dbsnp.vcf.gz input.vcf"\n'
    )
    assert _modules(header)["snpsift"] == {"version": "5.1d"}  # not "SnpSift"
    command_only = '##fileformat=VCFv4.2\n##SnpSiftCmd="SnpSift annotate -id dbsnp.vcf.gz input.vcf"\n'
    assert _modules(command_only) == {"snpsift": {}}  # not "SnpSift"


def test_a_trgt_command_line_before_the_version_line_does_not_block_it():
    header = (
        "##fileformat=VCFv4.2\n"
        "##trgtCommand=trgt genotype --genome ref.fa --repeats catalog.bed --karyotype XX\n"
        "##trgtVersion=0.7.0\n"
    )
    assert _modules(header, modality="repeats")["trgt"] == {"version": "0.7.0"}  # not "trgt"


@pytest.mark.parametrize(
    ("command_line", "version_line", "tool", "version"),
    [
        (
            "##bcftools_viewCommand=view -Oz -o out.vcf.gz in.vcf.gz; Date=Tue Oct  1 10:00:00 2024",
            "##bcftools_viewVersion=1.21+htslib-1.21",
            "bcftools",
            "1.21+htslib-1.21",
        ),
        (
            '##SnpSiftCmd="SnpSift dbnsfp -v -db dbNSFP4.1a.txt.gz input.vcf"',
            '##SnpSiftVersion="SnpSift 4.3t (build 2017-11-24 10:18), by the snpEff author"',
            "snpsift",
            "4.3t",
        ),
        (
            "##HiPhase_command=hiphase --bam s.bam --vcf s.vcf.gz --output-vcf out.vcf.gz",
            "##HiPhase_version=1.4.5-f1bc7a8",
            "hiphase",
            "1.4.5-f1bc7a8",
        ),
        (
            "##paraphase_command=paraphase -b s.bam -o out -r ref.fa",
            "##paraphase_version=3.5.0",
            "paraphase",
            "3.5.0",
        ),
    ],
    ids=["bcftools", "snpsift", "hiphase", "paraphase"],
)
def test_a_version_line_after_a_command_line_gives_the_version(command_line, version_line, tool, version):
    mods = _modules(f"##fileformat=VCFv4.2\n{command_line}\n{version_line}\n")
    assert mods[tool] == {"version": version}


def test_a_command_line_does_not_block_another_files_version_in_the_merge():
    # One TRGT file states only its command line, the next its version line: the folded
    # manifest keeps the version, not the first file's word.
    command_only = "##fileformat=VCFv4.2\n##trgtCommand=trgt genotype --karyotype XY\n"
    combined: dict = {}
    for header in (command_only, TRGT_HEADER):
        combined = merge_module_maps(combined, _modules(header, "repeats"))
    assert combined["trgt"]["version"] == "0.7.0"  # not "trgt"


@pytest.mark.parametrize(
    ("line", "tool", "version"),
    [
        ("##bcftools_normVersion=1.17+htslib-1.17", "bcftools", "1.17+htslib-1.17"),
        ("##DeepVariant_version=1.10.0", "deepvariant", "1.10.0"),
        ("##GLnexusVersion=v1.4.1-0-g68e25e5", "glnexus", "v1.4.1-0-g68e25e5"),
        ("##trgtVersion=5.0.0-e9acee0", "trgt", "5.0.0-e9acee0"),
        ('##SnpEffVersion="5.1d (build 2022-04-19 15:05), by the snpEff author"', "snpeff", "5.1d"),
        ('##SnpSiftVersion="SnpSift 5.1d (build 2022-04-19 15:05)"', "snpsift", "5.1d"),
        ('##SVDB_version=2.8.1 cmd="svdb --merge --vcf a.vcf b.vcf"', "svdb", "2.8.1"),
        # A structured value states its version in its Version field.
        (
            '##DRAGENVersion=<ID=dragen,Version="SW: 07.021.624.3.10.4, HW: 07.021.624">',
            "dragen",
            "SW: 07.021.624.3.10.4, HW: 07.021.624",
        ),
        # One word with a digit, not version-shaped and not the tool's name, is kept as
        # written: a release tag.
        ("##pepper_version=r0.8", "pepper", "r0.8"),
        # No version stated: the tool's own name, no value, a structured value without one.
        ("##clair3_version=Clair3", "clair3", None),
        ("##fooVersion=unknown", "foo", None),
        ('##fooVersion=<ID=foo,Date="2024-01-01">', "foo", None),
    ],
    ids=[
        "bcftools",
        "deepvariant",
        "glnexus",
        "trgt",
        "snpeff",
        "snpsift-names-itself-first",
        "svdb-with-its-command",
        "dragen-structured",
        "release-tag",
        "only-the-tools-name",
        "unknown",
        "structured-without-version",
    ],
)
def test_a_version_line_records_only_the_version_it_states(line, tool, version):
    mods = _modules(f"##fileformat=VCFv4.2\n{line}\n")
    assert mods[tool].get("version") == version
