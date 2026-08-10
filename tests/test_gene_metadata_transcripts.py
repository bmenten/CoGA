from __future__ import annotations

from backend.app.services.gene_metadata_service import _transcript_relevance_flags


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
