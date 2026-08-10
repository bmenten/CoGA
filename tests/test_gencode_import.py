from __future__ import annotations

import json

from backend.app.services.gencode_import import (
    iter_gencode_gene_rows,
    parse_gencode_refseq_metadata,
    parse_gencode_release,
)

# A GENCODE GTF in miniature: the preamble that states the release, a gene with two
# transcripts (one of them MANE Select), and a second gene on the minus strand.
GENCODE_GTF = """\
##description: evidence-based annotation of the human genome (GRCh38), version 50 (Ensembl 116)
##provider: GENCODE
##format: gtf
##date: 2026-04-08
chr17\tHAVANA\tgene\t43044295\t43125364\t.\t-\t.\tgene_id "ENSG00000012048.24"; gene_type "protein_coding"; gene_name "BRCA1"; level 2; hgnc_id "HGNC:1100";
chr17\tHAVANA\ttranscript\t43044295\t43125364\t.\t-\t.\tgene_id "ENSG00000012048.24"; transcript_id "ENST00000357654.9"; gene_type "protein_coding"; gene_name "BRCA1"; transcript_type "protein_coding"; transcript_name "BRCA1-201"; level 2; tag "basic"; tag "MANE_Select"; tag "CCDS"; ccdsid "CCDS11453.1";
chr17\tHAVANA\texon\t43125271\t43125364\t.\t-\t.\tgene_id "ENSG00000012048.24"; transcript_id "ENST00000357654.9"; exon_number 1; exon_id "ENSE00001871077.1";
chr17\tHAVANA\texon\t43124017\t43124115\t.\t-\t.\tgene_id "ENSG00000012048.24"; transcript_id "ENST00000357654.9"; exon_number 2; exon_id "ENSE00003513709.1";
chr17\tHAVANA\ttranscript\t43045629\t43125300\t.\t-\t.\tgene_id "ENSG00000012048.24"; transcript_id "ENST00000471181.7"; gene_type "protein_coding"; gene_name "BRCA1"; transcript_type "protein_coding"; transcript_name "BRCA1-206"; level 2; tag "basic";
chr17\tHAVANA\texon\t43125171\t43125300\t.\t-\t.\tgene_id "ENSG00000012048.24"; transcript_id "ENST00000471181.7"; exon_number 1; exon_id "ENSE00001872812.1";
chrM\tENSEMBL\tgene\t3307\t4262\t.\t+\t.\tgene_id "ENSG00000198888.2"; gene_type "protein_coding"; gene_name "MT-ND1"; level 3; hgnc_id "HGNC:7455";
chrM\tENSEMBL\ttranscript\t3307\t4262\t.\t+\t.\tgene_id "ENSG00000198888.2"; transcript_id "ENST00000361390.2"; gene_type "protein_coding"; gene_name "MT-ND1"; transcript_type "protein_coding"; transcript_name "MT-ND1-201"; level 3; tag "basic";
chrM\tENSEMBL\texon\t3307\t4262\t.\t+\t.\tgene_id "ENSG00000198888.2"; transcript_id "ENST00000361390.2"; exon_number 1; exon_id "ENSE00001435714.2";
"""


def _rows(**kwargs):
    return list(iter_gencode_gene_rows(GENCODE_GTF.splitlines(), assembly_id="A1", **kwargs))


def test_parse_gencode_release_reads_the_preamble() -> None:
    release = parse_gencode_release(GENCODE_GTF.splitlines())

    assert release.version == "50"
    assert release.ensembl_release == "116"
    assert release.date == "2026-04-08"
    assert release.label == "v50 (Ensembl 116)"


def test_iter_gencode_gene_rows_emits_one_row_per_transcript() -> None:
    rows = _rows()

    # The refGene import produced one row per transcript with gene_id holding the
    # accession; keeping that shape is what leaves the table's consumers untouched.
    assert [row["gene_id"] for row in rows] == [
        "ENST00000357654.9",
        "ENST00000471181.7",
        "ENST00000361390.2",
    ]
    assert [row["hgnc_symbol"] for row in rows] == ["BRCA1", "BRCA1", "MT-ND1"]
    assert {row["source"] for row in rows} == {"gencode"}


def test_iter_gencode_gene_rows_carries_real_biotypes_and_identifiers() -> None:
    row = _rows()[0]
    extra = json.loads(row["extra"])

    # refGene wrote the literal string "unknown" into biotype for every row.
    assert row["biotype"] == "protein_coding"
    assert extra["ensembl_gene_id"] == "ENSG00000012048"
    assert extra["ensembl_gene_id_versioned"] == "ENSG00000012048.24"
    assert extra["ensembl_transcript_id"] == "ENST00000357654"
    assert extra["hgnc_id"] == "HGNC:1100"
    assert extra["transcript_name"] == "BRCA1-201"
    assert extra["ccds_id"] == "CCDS11453.1"


def test_iter_gencode_gene_rows_flags_mane_transcripts() -> None:
    mane, other, _mito = _rows()

    assert json.loads(mane["extra"])["mane_select"] is True
    assert json.loads(mane["extra"])["tags"] == ["basic", "MANE_Select", "CCDS"]
    assert json.loads(other["extra"])["mane_select"] is False


def test_iter_gencode_gene_rows_normalises_chromosomes_and_strand() -> None:
    rows = _rows()

    # The table stores chromosomes without the chr prefix.
    assert [row["chr"] for row in rows] == ["17", "17", "M"]
    assert rows[0]["strand"] == -1
    assert rows[2]["strand"] == 1


def test_iter_gencode_gene_rows_attaches_exons_to_the_right_transcript() -> None:
    first, second, _mito = _rows()

    assert json.loads(first["exons"]) == [
        {"name": "exon1", "start": 43125271, "end": 43125364},
        {"name": "exon2", "start": 43124017, "end": 43124115},
    ]
    # The second transcript's single exon must not inherit the first transcript's.
    assert json.loads(second["exons"]) == [
        {"name": "exon1", "start": 43125171, "end": 43125300}
    ]
    assert json.loads(first["extra"])["exon_count"] == 2


def test_iter_gencode_gene_rows_uses_the_transcript_span_not_the_gene_span() -> None:
    first, second, _mito = _rows()

    assert (first["start"], first["end"]) == (43044295, 43125364)
    assert (second["start"], second["end"]) == (43045629, 43125300)


def test_iter_gencode_gene_rows_attaches_refseq_accessions() -> None:
    rows = _rows(
        refseq_by_transcript={"ENST00000357654": ["NM_007294.4", "NP_009225.1"]}
    )

    # Lookups naming a RefSeq accession have to keep working after the swap away from
    # the refGene-derived table, which was keyed on exactly those.
    assert json.loads(rows[0]["extra"])["refseq_accessions"] == [
        "NM_007294.4",
        "NP_009225.1",
    ]
    assert "refseq_accessions" not in json.loads(rows[1]["extra"])


def test_iter_gencode_gene_rows_ignores_non_gene_features() -> None:
    with_cds = GENCODE_GTF + (
        'chrM\tENSEMBL\tCDS\t3307\t4262\t.\t+\t0\tgene_id "ENSG00000198888.2"; '
        'transcript_id "ENST00000361390.2";\n'
    )

    rows = list(iter_gencode_gene_rows(with_cds.splitlines(), assembly_id="A1"))

    # CDS/UTR/start_codon lines must not become exons or rows of their own.
    assert len(rows) == 3
    assert len(json.loads(rows[2]["exons"])) == 1


def test_parse_gencode_refseq_metadata_groups_accessions_by_transcript() -> None:
    text_value = (
        "ENST00000357654.9\tNM_007294.4\tNP_009225.1\n"
        "ENST00000471181.7\tNR_027676.2\n"
        "ENST00000357654.9\tNM_007294.4\n"
    )

    mapping = parse_gencode_refseq_metadata(text_value.splitlines())

    assert mapping["ENST00000357654"] == ["NM_007294.4", "NP_009225.1"]
    assert mapping["ENST00000471181"] == ["NR_027676.2"]
