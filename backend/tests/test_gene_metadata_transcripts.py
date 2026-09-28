from __future__ import annotations

from backend.app.services.gene_metadata_service import (
    _build_external_links,
    _first_identifier,
    _transcript_relevance_flags,
)


def test_transcript_flags_come_from_the_annotation_tags() -> None:
    doc = {
        "extra": {
            "transcript_id": "ENST00000357654.9",
            "mane_select": True,
            "tags": ["basic", "Ensembl_canonical", "MANE_Select", "CCDS"],
        }
    }

    assert _transcript_relevance_flags(doc) == {
        "mane_select": True,
        "mane_plus_clinical": False,
        "ensembl_canonical": True,
    }


def test_transcript_flags_read_mane_from_the_tag_list_alone() -> None:
    # The importer writes a mane_select boolean, but an annotation that only carries the
    # tag must still badge — the tags are the authority.
    doc = {"extra": {"tags": ["basic", "MANE_Plus_Clinical"]}}

    flags = _transcript_relevance_flags(doc)

    assert flags["mane_plus_clinical"] is True
    assert flags["mane_select"] is False


def test_transcript_flags_default_to_false_without_tags() -> None:
    # refGene-sourced rows carry none of this; they must not be badged by accident.
    assert _transcript_relevance_flags({"extra": {"transcript_id": "NM_007294.4"}}) == {
        "mane_select": False,
        "mane_plus_clinical": False,
        "ensembl_canonical": False,
    }
    assert _transcript_relevance_flags({}) == {
        "mane_select": False,
        "mane_plus_clinical": False,
        "ensembl_canonical": False,
    }


def test_transcript_flags_do_not_confuse_the_extended_canonical_tag() -> None:
    # GENCODE also ships "Ensembl_canonical_extended", which is a different claim.
    doc = {"extra": {"tags": ["basic", "Ensembl_canonical_extended"]}}

    assert _transcript_relevance_flags(doc)["ensembl_canonical"] is False


BRCA1_EXTRA = {
    "dbnsfp_identifiers": {
        "uniprot_accessions": ["P38398"],
        "ccds_ids": ["CCDS11453", "CCDS11454"],
        "ucsc_ids": ["uc002ict.4"],
    }
}


def _links(extra):
    return {
        link.label: link.href
        for link in _build_external_links(
            symbol="BRCA1",
            gene_doc={"chr": "17", "start": 43044295, "end": 43125364},
            assembly_name="GRCh38",
            ensembl_gene_id="ENSG00000012048",
            ncbi_gene_id="672",
            hgnc_id="HGNC:1100",
            omim_gene_id="113705",
            extra=extra,
        )
    }


def test_external_links_use_the_uniprot_accession_we_hold() -> None:
    links = _links(BRCA1_EXTRA)

    # Not a symbol search that hopes the first hit is the right gene.
    assert links["UniProt"] == "https://www.uniprot.org/uniprotkb/P38398/entry"


def test_external_links_add_ccds_and_a_ucsc_gene_page() -> None:
    links = _links(BRCA1_EXTRA)

    assert "CCDS" in links
    assert "DATA=CCDS11453" in links["CCDS"]
    assert "hgg_gene=uc002ict.4" in links["UCSC"]


def test_external_links_fall_back_to_searches_without_identifiers() -> None:
    links = _links({})

    assert links["UniProt"] == "https://www.uniprot.org/uniprotkb?query=gene:BRCA1"
    assert "CCDS" not in links
    # No UCSC id, so the browser opens at the locus instead of the gene model.
    assert "position=chr17" in links["UCSC"]


def test_external_links_fall_back_from_dbnsfp_to_hgnc_identifiers() -> None:
    links = _links({"hgnc_identifiers": {"uniprot_ids": ["Q6ZMQ8"], "ccds_id": ["CCDS999"]}})

    assert links["UniProt"] == "https://www.uniprot.org/uniprotkb/Q6ZMQ8/entry"
    assert "DATA=CCDS999" in links["CCDS"]


def test_first_identifier_skips_empty_entries_and_blocks() -> None:
    extra = {"dbnsfp_identifiers": {"uniprot_accessions": ["", "  ", "P38398"]}}

    assert _first_identifier(extra, ("dbnsfp_identifiers", "uniprot_accessions")) == "P38398"
    assert _first_identifier(extra, ("missing_block", "whatever")) is None
    assert _first_identifier({"dbnsfp_identifiers": "not-a-dict"}, ("dbnsfp_identifiers", "x")) is None


def test_transcript_refseq_accessions_exclude_protein_ids() -> None:
    from backend.app.services.gene_metadata_service import _transcript_refseq_accessions

    # GENCODE's RefSeq mapping lists both sides of the pair; a transcript table should
    # offer the transcript, not the protein it encodes.
    doc = {
        "extra": {
            "refseq_accessions": [
                "NM_007294.4",
                "NP_009225.1",
                "NM_001407598.1",
                "NP_001394527.1",
            ]
        }
    }

    assert _transcript_refseq_accessions(doc) == ["NM_007294.4", "NM_001407598.1"]


def test_transcript_refseq_accessions_keep_non_coding_and_predicted_transcripts() -> None:
    from backend.app.services.gene_metadata_service import _transcript_refseq_accessions

    doc = {"extra": {"refseq_accessions": ["NR_110561.1", "XM_011516.2", "XR_001.1"]}}

    assert _transcript_refseq_accessions(doc) == ["NR_110561.1", "XM_011516.2", "XR_001.1"]


def test_transcript_refseq_accessions_dedupe_and_tolerate_absence() -> None:
    from backend.app.services.gene_metadata_service import _transcript_refseq_accessions

    assert _transcript_refseq_accessions(
        {"extra": {"refseq_accessions": ["NM_007294.4", "NM_007294.4", " "]}}
    ) == ["NM_007294.4"]
    # Most transcripts have no RefSeq equivalent at all.
    assert _transcript_refseq_accessions({"extra": {}}) == []
    assert _transcript_refseq_accessions({}) == []


def test_transcript_flags_and_ccds_travel_together_from_the_annotation() -> None:
    # GENCODE states CCDS membership two ways on a transcript: the ccdsid attribute and
    # a CCDS tag. The id is the useful one, since it names the consensus record.
    doc = {
        "extra": {
            "ccds_id": "CCDS11453.1",
            "tags": ["basic", "CCDS", "MANE_Plus_Clinical"],
        }
    }

    flags = _transcript_relevance_flags(doc)

    assert flags["mane_plus_clinical"] is True
    assert (doc["extra"]).get("ccds_id") == "CCDS11453.1"
