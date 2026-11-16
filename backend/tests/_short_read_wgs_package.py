"""A short-read (Illumina) WGS family folder as the laboratory delivers it, built in the tests.

The family's folder holds its PED at the top (``<family>.ped``), the joint GATK callset twice:
unannotated (``<family>.vcf.gz``) and VEP-annotated (``<family>.GRCh38.annotated.vcf.gz``), each
with its index, and one folder per sample with its CRAM (``<sample>/<sample>.cram`` + ``.crai``).
The annotated callset keeps GATK's header (HaplotypeCaller's command line, the physical-phasing
FORMAT fields whose description says "phased") and its record order (the contigs as the joint
calling concatenated them, not sorted), and names VEP's cache fields after their source
(``MAX_AF_VEP_ALL_POPULATIONS``, ``CLIN_SIG_VEP_CACHE``). Its first record is physically
phased, so the loader's own reading of the file would take it for imputed genotypes. Every
value is synthetic: a trio ``<family>_PROBAND``, ``<family>_MOTHER``, ``<family>_FATHER1``.
"""

from __future__ import annotations

from pathlib import Path

from backend.tests._bgzf import bgzip

# The annotated callset's CSQ fields, a subset of the laboratory's VEP 116 annotation in its order.
CSQ_FIELDS = (
    "Allele|Consequence|IMPACT|SYMBOL|Gene|Feature_type|Feature|BIOTYPE|HGVSc|HGVSp|CANONICAL|"
    "gnomADg_AF|MAX_AF_VEP_ALL_POPULATIONS|MAX_AF_POPS_VEP_ALL_POPULATIONS|CLIN_SIG_VEP_CACHE|"
    "ClinVar_CLNSIG|PIPELINE_WARNINGS"
)

# The VEP cache's frequency over all its populations, which only the renamed field holds.
MAX_AF = 0.0123
GNOMAD_GENOMES_AF = 0.0004


def members(family_id: str) -> tuple[str, str, str]:
    """The trio's sample IDs: proband, mother, father."""
    return (f"{family_id}_PROBAND", f"{family_id}_MOTHER", f"{family_id}_FATHER1")


def _csq_entry(**values: str) -> str:
    return "|".join(values.get(name, "") for name in CSQ_FIELDS.split("|"))


def annotated_vcf_text(family_id: str) -> str:
    proband, mother, father = members(family_id)
    header = (
        "##fileformat=VCFv4.2\n"
        '##FILTER=<ID=PASS,Description="All filters passed">\n'
        '##FILTER=<ID=LowQual,Description="Low quality">\n'
        '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allelic depths for the ref and alt alleles in the order listed">\n'
        '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Approximate read depth">\n'
        '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype Quality">\n'
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        '##FORMAT=<ID=PGT,Number=1,Type=String,Description="Physical phasing haplotype information, describing '
        'how the alternate alleles are phased in relation to one another">\n'
        '##FORMAT=<ID=PID,Number=1,Type=String,Description="Physical phasing ID information">\n'
        '##FORMAT=<ID=PS,Number=1,Type=Integer,Description="Phasing set">\n'
        '##GATKCommandLine=<ID=HaplotypeCaller,CommandLine="HaplotypeCaller --emit-ref-confidence GVCF",'
        'Version="4.4.0.0",Date="January 1, 2023">\n'
        '##GATKCommandLine=<ID=GenotypeGVCFs,CommandLine="GenotypeGVCFs",Version="4.4.0.0",Date="January 1, 2023">\n'
        '##INFO=<ID=AC,Number=A,Type=Integer,Description="Allele count in genotypes">\n'
        '##INFO=<ID=AN,Number=1,Type=Integer,Description="Total number of alleles in called genotypes">\n'
        '##INFO=<ID=DP,Number=1,Type=Integer,Description="Approximate read depth">\n'
        "##source=HaplotypeCaller\n"
        '##VEP="v116.0" API="v116" cache="homo_sapiens/116_GRCh38"\n'
        '##INFO=<ID=CSQ,Number=.,Type=String,Description="Offline VEP116 plus frozen resources; original family '
        f'call fields preserved. Format: {CSQ_FIELDS}">\n'
        '##INFO=<ID=TRIO_ANNOTATION_STATUS,Number=1,Type=String,Description="ANNOTATED or explicit reason retained '
        'without small-variant VEP annotation">\n'
        "##contig=<ID=chr1,length=248956422>\n"
        "##contig=<ID=chr2,length=242193529>\n"
        "##contig=<ID=chrY,length=57227415>\n"
        f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{proband}\t{mother}\t{father}\n"
    )
    transcript = {"Feature_type": "Transcript", "BIOTYPE": "protein_coding"}
    missense = _csq_entry(
        Allele="G",
        Consequence="missense_variant",
        IMPACT="MODERATE",
        SYMBOL="SYNGENE1",
        Gene="ENSG00000900001",
        Feature="ENST00000900001.1",
        HGVSc="c.10A>G",
        HGVSp="p.Lys4Glu",
        CANONICAL="YES",
        gnomADg_AF=str(GNOMAD_GENOMES_AF),
        MAX_AF_VEP_ALL_POPULATIONS=str(MAX_AF),
        MAX_AF_POPS_VEP_ALL_POPULATIONS="gnomADg_AFR",
        CLIN_SIG_VEP_CACHE="benign",
        ClinVar_CLNSIG="Uncertain_significance",
        **transcript,
    )
    intronic = _csq_entry(
        Allele="T",
        Consequence="intron_variant",
        IMPACT="MODIFIER",
        SYMBOL="SYNGENE2",
        Gene="ENSG00000900002",
        Feature="ENST00000900002.1",
        **transcript,
    )
    intergenic = _csq_entry(Allele="C", Consequence="intergenic_variant", IMPACT="MODIFIER")
    records = (
        # Physically phased, as HaplotypeCaller phases nearby hets, and the file's first record.
        f"chr2\t1000100\t.\tA\tG\t512.3\t.\tAC=2;AN=6;DP=60;CSQ={missense};TRIO_ANNOTATION_STATUS=ANNOTATED\t"
        "GT:AD:DP:GQ:PGT:PID:PS\t0|1:10,11:21:99:0|1:1000100_A_G:1000100\t0|1:12,9:21:99:0|1:1000100_A_G:1000100\t"
        "0/0:20,0:20:60:.:.:.\n"
        # The contigs as the joint calling concatenated them: chr1 after chr2.
        f"chr1\t2000200\t.\tC\tT\t88.1\t.\tAC=1;AN=6;DP=55;CSQ={intronic};TRIO_ANNOTATION_STATUS=ANNOTATED\t"
        "GT:AD:DP:GQ\t0/1:9,8:17:80\t0/0:19,0:19:57\t0/0:19,0:19:57\n"
        # chrY: the mother's genotype is missing (GATK writes ".").
        f"chrY\t2800415\t.\tT\tC\t286.0\t.\tAC=2;AN=4;DP=16;CSQ={intergenic};TRIO_ANNOTATION_STATUS=ANNOTATED\t"
        "GT:AD:DP:GQ\t1/1:0,8:8:24\t.:0,0:.:.\t1/1:0,8:8:24\n"
    )
    return header + records


def raw_vcf_text(family_id: str) -> str:
    """The unannotated callset: the same calls without CSQ, which CoGA does not read."""
    return "".join(
        line
        for line in annotated_vcf_text(family_id).splitlines(keepends=True)
        if not line.startswith(("##INFO=<ID=CSQ", '##VEP="'))
    ).replace(";CSQ=", ";X_CSQ=")


def write_short_read_wgs_folder(folder: Path, family_id: str, *, crams: bool = True) -> Path:
    """The family's folder ``folder`` as the laboratory delivers it (see the module's text)."""
    proband, mother, father = members(family_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{family_id}.ped").write_text(
        f"{family_id}\t{proband}\t{father}\t{mother}\t0\t2\n"
        f"{family_id}\t{mother}\t0\t0\t2\t1\n"
        f"{family_id}\t{father}\t0\t0\t1\t1\n",
        encoding="utf-8",
    )
    (folder / f"{family_id}.vcf.gz").write_bytes(bgzip(raw_vcf_text(family_id).encode("utf-8")))
    (folder / f"{family_id}.vcf.gz.tbi").write_bytes(b"TBI\x01")
    (folder / f"{family_id}.GRCh38.annotated.vcf.gz").write_bytes(bgzip(annotated_vcf_text(family_id).encode("utf-8")))
    (folder / f"{family_id}.GRCh38.annotated.vcf.gz.tbi").write_bytes(b"TBI\x01")
    (folder / "vep-commands.json").write_text('{"vep": "116"}\n', encoding="utf-8")
    if crams:
        for sample in (proband, mother, father):
            (folder / sample).mkdir(exist_ok=True)
            (folder / sample / f"{sample}.cram").write_bytes(b"CRAM\x03\x00")
            (folder / sample / f"{sample}.cram.crai").write_bytes(b"\x1f\x8b")
    return folder
