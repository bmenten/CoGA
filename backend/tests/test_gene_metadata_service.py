"""The gene page's profile (``gene_metadata_service``): its locus, its transcripts and its links.

A gene placed on a primary chromosome and on an ALT or scaffold contig resolves to the primary
chromosome, the larger contig copy notwithstanding. Each transcript carries the MANE and
Ensembl-canonical badges its annotation's own tags state, its CCDS id and its RefSeq transcript
accessions (protein accessions left out). The external links use the identifiers the gene
reference holds (UniProt, CCDS, UCSC's gene id) and fall back to a search or the locus without
them. The assembly-name rules of the UCSC and gnomAD links are in ``test_assembly_names.py``.
All values are synthetic.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import HTTPException

from backend.app.services import gene_metadata_service
from backend.app.services.access_control import CurrentUser
from backend.app.services.data_scope import is_primary_chromosome
from backend.app.services.gene_info_jobs_pg import HumanGeneContext
from backend.app.services.gene_metadata_service import (
    _build_external_links,
    _first_identifier,
    _pick_primary_gene_doc,
    _transcript_relevance_flags,
)


# ---- The gene's locus: the primary chromosome first --------------------------------------------


def test_is_primary_chromosome_accepts_main_assembly_only() -> None:
    for value in ("1", "22", "X", "Y", "MT", "M", "chr1", "chrX"):
        assert is_primary_chromosome(value), value
    for value in ("1_KI270766v1_alt", "Un_GL000195v1", "17_GL000205v2_random", "GL000009.2", "23", ""):
        assert not is_primary_chromosome(value), value


def test_pick_primary_gene_doc_prefers_effective_chromosome_over_larger_alt_contig() -> None:
    # The ALT-contig copy is intentionally "larger"/exon-richer so a size-only
    # heuristic would choose it.
    alt_contig = {"chr": "1_KI270766v1_alt", "start": 0, "end": 100_000, "exons": [1, 2, 3, 4], "gene_id": "ALT"}
    primary = {"chr": "1", "start": 1_000, "end": 50_000, "exons": [1, 2], "gene_id": "PRIMARY"}

    chosen = _pick_primary_gene_doc([alt_contig, primary])

    assert chosen["chr"] == "1"
    assert chosen["gene_id"] == "PRIMARY"


def test_pick_primary_gene_doc_falls_back_to_alt_contig_when_no_primary_exists() -> None:
    alt_only = {"chr": "Un_GL000195v1", "start": 0, "end": 10_000, "exons": [], "gene_id": "ALT"}

    chosen = _pick_primary_gene_doc([alt_only])

    assert chosen["gene_id"] == "ALT"


# ---- The transcripts' badges, CCDS and RefSeq accessions ----------------------------------------


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


# ---- The external links ----------------------------------------------------------------------

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


# ---- A transcript's RefSeq accessions ----------------------------------------------------------


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


# ---- The profile the gene page serves ----------------------------------------------------------
#
# ``build_gene_profile`` through a session that answers each of its reads: BRCA1 placed on
# GRCh38 (two transcripts) and on GRCh37 (one), the gene reference recorded under GRCh37 only,
# one panel naming the gene, and the GENCODE import of GRCh38.

GRCH38 = "00000000-0000-4000-8000-0000000000a8"
GRCH37 = "00000000-0000-4000-8000-0000000000a7"
HUMAN = HumanGeneContext(
    species={"id": "species-1", "name": "Homo sapiens"},
    assemblies=[
        {"id": GRCH38, "assembly_name": "GRCh38", "version": "p14"},
        {"id": GRCH37, "assembly_name": "GRCh37", "version": "p13"},
    ],
)
IMPORTED_AT = datetime(2026, 4, 8, tzinfo=timezone.utc)


def _gene_row(assembly_id: str, transcript_id: str, start: int, end: int, exons: int, **extra: Any) -> dict:
    return {
        "id": f"row-{transcript_id}",
        "assembly_id": assembly_id,
        "gene_id": transcript_id,
        "hgnc_symbol": "BRCA1",
        "chr": "17",
        "start": start,
        "end": end,
        "exons": [{"name": f"exon{n}"} for n in range(exons)],
        "strand": -1,
        "biotype": "protein_coding",
        "description": "BRCA1 (from the annotation)",
        "source": "gencode",
        "extra": {"transcript_id": transcript_id, **extra},
    }


GENE_ROWS = [
    # GENCODE states CCDS membership two ways on a transcript: the ccdsid attribute and a CCDS
    # tag. The id is the useful one, since it names the consensus record; a tag alone gives none.
    _gene_row(
        GRCH38,
        "ENST00000471181.7",
        43_045_629,
        43_125_300,
        1,
        tags=["basic", "CCDS", "MANE_Plus_Clinical"],
        ccds_id="",
    ),
    _gene_row(
        GRCH38,
        "ENST00000357654.9",
        43_044_295,
        43_125_364,
        2,
        tags=["basic", "Ensembl_canonical", "MANE_Select", "CCDS"],
        mane_select=True,
        ccds_id=" CCDS11453.1 ",
        refseq_accessions=["NM_007294.4", "NP_009225.1"],
    ),
    _gene_row(GRCH37, "ENST00000357654", 41_196_312, 41_277_381, 2, tags=["basic"]),
]
# The gene reference of BRCA1, recorded under GRCh37 alone.
GENE_INFO = {
    "assembly_id": GRCH37,
    "hgnc_symbol": "BRCA1",
    "gene_id": "ENSG00000012048",
    "display_name": "BRCA1 DNA repair associated",
    "summary": "Tumor suppressor involved in DNA repair.",
    "aliases": ["RNF53"],
    "previous_symbols": [],
    "ensembl_gene_id": "ENSG00000012048",
    "ncbi_gene_id": "672",
    "hgnc_id": "HGNC:1100",
    "omim_gene_id": "113705",
    "gene_type": "protein-coding gene",
    "location": "17q21.31",
    "homologs": [],
    "source_status": {},
    "extra": {"dbnsfp_identifiers": {"uniprot_accessions": ["P38398"]}},
    "updated_at": IMPORTED_AT,
}


class _Rows:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self) -> "_Rows":
        return self

    def first(self) -> dict | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict]:
        return self._rows


class _GeneProfileSession:
    """Answers each read of ``build_gene_profile``; ``reads`` names them in order."""

    def __init__(self, gene_rows: list[dict]) -> None:
        self.gene_rows = gene_rows
        self.reads: list[str] = []

    async def execute(self, statement: Any, params: Any = None) -> _Rows:
        sql = " ".join(str(statement).split())
        if "FROM genes g" in sql:
            self.reads.append("genes")
            return _Rows(self.gene_rows)
        if "FROM gene_info WHERE assembly_id = CAST(:assembly_id AS uuid)" in sql:
            self.reads.append(f"gene_info of {params['assembly_id']}")
            return _Rows([GENE_INFO] if params["assembly_id"] == GENE_INFO["assembly_id"] else [])
        if "FROM gene_info WHERE assembly_id IN" in sql:
            self.reads.append("gene_info of any human assembly")
            return _Rows([GENE_INFO])
        if "FROM gene_panels" in sql:
            self.reads.append("panels")
            return _Rows([{"panel_id": "panel-1", "name": "Hereditary cancer", "gene_count": 30}])
        if "FROM reference_dataset_imports" in sql:
            self.reads.append(f"gene import of {params['assembly_id']}")
            return _Rows([{"source": "gencode v50 (Ensembl 116)", "performed_at": IMPORTED_AT}])
        raise AssertionError(f"unexpected SQL: {sql}")


def _profile(monkeypatch: pytest.MonkeyPatch, *, assembly_id: str | None = None, gene_rows=GENE_ROWS):
    async def human(_session: Any) -> HumanGeneContext:
        return HUMAN

    async def no_diseases(_session: Any, **_kwargs: Any) -> list:
        return []

    async def no_phenotypes(_session: Any, **_kwargs: Any) -> dict:
        return {}

    monkeypatch.setattr(gene_metadata_service, "_get_human_context", human)
    monkeypatch.setattr(gene_metadata_service, "list_monarch_gene_disease", no_diseases)
    monkeypatch.setattr(gene_metadata_service, "summarize_disease_phenotypes", no_phenotypes)
    session = _GeneProfileSession(list(gene_rows))
    viewer = CurrentUser(
        id="00000000-0000-4000-8000-0000000000b1",
        username="viewer@example.org",
        email="viewer@example.org",
        role="viewer",
        created_at=IMPORTED_AT,
    )
    profile = asyncio.run(
        gene_metadata_service.build_gene_profile(
            session,  # type: ignore[arg-type]
            symbol="brca1",
            assembly_id=assembly_id,
            family_id=None,
            project_id=None,
            user=viewer,
        )
    )
    return profile, session


def test_transcript_flags_and_ccds_travel_together_from_the_annotation(monkeypatch: pytest.MonkeyPatch) -> None:
    # Each transcript row the gene page serves carries its own badges, its CCDS id as the
    # annotation states it (trimmed; none for a CCDS tag without an id) and its RefSeq
    # transcript accessions, the longest transcript first.
    profile, _session = _profile(monkeypatch)

    assert profile.transcript_count == 2
    assert [
        (
            transcript.transcript_id,
            transcript.ccds_id,
            transcript.mane_select,
            transcript.mane_plus_clinical,
            transcript.ensembl_canonical,
            transcript.refseq_accessions,
            transcript.exon_count,
        )
        for transcript in profile.transcripts
    ] == [
        ("ENST00000357654.9", "CCDS11453.1", True, False, True, ["NM_007294.4"], 2),
        ("ENST00000471181.7", None, False, True, False, [], 1),
    ]


def test_the_gene_profile_opens_on_the_assembly_asked_for_and_lists_every_locus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Without an assembly asked for, the first in the human order that places the gene.
    first, _session = _profile(monkeypatch)
    asked, _session = _profile(monkeypatch, assembly_id=GRCH37)
    # An assembly that is no human assembly is not asked for at all.
    elsewhere, _session = _profile(monkeypatch, assembly_id="00000000-0000-4000-8000-0000000000ff")

    assert (first.assembly_name, first.assembly_version, first.start, first.end) == (
        "GRCh38",
        "p14",
        43_044_295,
        43_125_364,
    )
    assert (asked.assembly_name, asked.start, [t.transcript_id for t in asked.transcripts]) == (
        "GRCh37",
        41_196_312,
        ["ENST00000357654"],
    )
    assert elsewhere.assembly_name == "GRCh38"
    for profile, primary in ((first, "GRCh38"), (asked, "GRCh37")):
        assert [
            (location.assembly_name, location.transcript_count, location.is_primary)
            for location in profile.assembly_locations
        ] == [("GRCh38", 2, primary == "GRCh38"), ("GRCh37", 1, primary == "GRCh37")]


def test_the_gene_profile_reads_the_gene_reference_of_another_assembly_when_its_own_has_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile, session = _profile(monkeypatch)

    # GRCh38's own record first, then any human assembly's; the annotation is GRCh38's import.
    assert session.reads == [
        "genes",
        f"gene_info of {GRCH38}",
        "gene_info of any human assembly",
        "panels",
        f"gene import of {GRCH38}",
    ]
    assert (profile.display_name, profile.summary, profile.ensembl_gene_id, profile.location) == (
        "BRCA1 DNA repair associated",
        "Tumor suppressor involved in DNA repair.",
        "ENSG00000012048",
        "17q21.31",
    )
    assert (profile.gene_annotation_source, profile.gene_annotation_imported_at) == (
        "gencode v50 (Ensembl 116)",
        IMPORTED_AT,
    )
    assert [(panel.name, panel.gene_count) for panel in profile.panels] == [("Hereditary cancer", 30)]
    links = {link.label: link.href for link in profile.external_links}
    # The links take the record's identifiers, on the assembly the page opened on.
    assert links["UniProt"] == "https://www.uniprot.org/uniprotkb/P38398/entry"
    assert links["gnomAD"] == "https://gnomad.broadinstitute.org/gene/ENSG00000012048?dataset=gnomad_r4"

    # A record of its own is read alone.
    _profile_37, session_37 = _profile(monkeypatch, assembly_id=GRCH37)
    assert f"gene_info of {GRCH37}" in session_37.reads
    assert "gene_info of any human assembly" not in session_37.reads


def test_a_gene_no_human_assembly_places_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(HTTPException) as refused:
        _profile(monkeypatch, gene_rows=[])

    assert (refused.value.status_code, refused.value.detail) == (404, "Gene not found")


# ---- The gene–disease links of a page of variant cards ------------------------------------------
#
# ``list_gene_disease_links`` through a session that answers its two reads: Monarch's links and
# the gene reference's OMIM diseases, each filtered on the upper-cased symbols asked. The SQL
# itself (case, empty labels, order) runs on real Postgres in
# integration/test_gene_disease_links_integration.py.


def _monarch_row(symbol: str, mondo: str, label: str, causal: bool, sources: list[str]) -> dict:
    return {"symbol_key": symbol, "mondo_id": mondo, "disease_label": label, "causal": causal, "sources": sources}


class _DiseaseLinkSession:
    """Answers the two reads of ``list_gene_disease_links``; ``reads`` names them in order."""

    def __init__(self, monarch_rows: list[dict], reference_rows: list[dict]) -> None:
        self.monarch_rows = monarch_rows
        self.reference_rows = reference_rows
        self.reads: list[tuple[str, list[str]]] = []

    async def execute(self, statement: Any, params: Any = None) -> _Rows:
        sql = " ".join(str(statement).split())
        symbols = list(params["symbols"])
        if "FROM monarch_gene_disease" in sql:
            self.reads.append(("monarch", symbols))
            return _Rows([row for row in self.monarch_rows if row["symbol_key"] in symbols])
        if "FROM gene_info g JOIN assemblies a" in sql:
            self.reads.append(("gene_info", symbols))
            return _Rows([row for row in self.reference_rows if row["symbol_key"] in symbols])
        raise AssertionError(f"unexpected SQL: {sql}")


def _disease_links(session: _DiseaseLinkSession, symbols: list[str]):
    return asyncio.run(
        gene_metadata_service.list_gene_disease_links(session, symbols=symbols)  # type: ignore[arg-type]
    )


def test_disease_links_answer_each_gene_asked_once_in_the_order_asked() -> None:
    session = _DiseaseLinkSession(
        monarch_rows=[
            _monarch_row("FBN1", "MONDO:0007947", "Marfan syndrome", True, ["clingen", "omim"]),
            _monarch_row("FBN1", "MONDO:0011000", "isolated ectopia lentis", False, ["orphanet"]),
        ],
        reference_rows=[
            {
                "symbol_key": "FBN1",
                "assembly_name": "GRCh38",
                "omim_diseases": [{"label": "Marfan syndrome", "omim_id": "154700"}],
            }
        ],
    )

    links = _disease_links(session, ["fbn1", " BRCA2 ", "FBN1", "", "  "])

    # A symbol asked twice, in another case, is answered once under the spelling asked first;
    # a gene without any link still has its entry, with empty lists.
    assert [(entry.symbol, len(entry.monarch), len(entry.omim)) for entry in links] == [
        ("fbn1", 2, 1),
        ("BRCA2", 0, 0),
    ]
    assert session.reads == [("monarch", ["FBN1", "BRCA2"]), ("gene_info", ["FBN1", "BRCA2"])]
    assert [
        (link.disease_label, link.causal, link.sources, link.monarch_url) for link in links[0].monarch
    ] == [
        ("Marfan syndrome", True, ["clingen", "omim"], "https://monarchinitiative.org/MONDO:0007947"),
        ("isolated ectopia lentis", False, ["orphanet"], "https://monarchinitiative.org/MONDO:0011000"),
    ]


def test_disease_links_read_omim_from_the_first_human_assembly_that_holds_the_gene() -> None:
    def reference(symbol: str, assembly: str, label: str) -> dict:
        return {"symbol_key": symbol, "assembly_name": assembly, "omim_diseases": [{"label": label}]}

    session = _DiseaseLinkSession(
        monarch_rows=[],
        reference_rows=[
            reference("GENE1", "GRCh37", "from GRCh37"),
            reference("GENE1", "GRCh38", "from GRCh38"),
            reference("GENE1", "T2T-CHM13v2.0", "from T2T"),
            reference("GENE2", "GRCh37", "from GRCh37"),
            reference("GENE2", "T2T-CHM13v2.0", "from T2T"),
        ],
    )

    links = _disease_links(session, ["GENE1", "GENE2"])

    # GRCh38 first, then T2T-CHM13, then GRCh37, as the gene profile opens on.
    assert [[disease.label for disease in entry.omim] for entry in links] == [["from GRCh38"], ["from T2T"]]


def test_disease_links_serve_omim_diseases_with_a_label_and_link_an_omim_number_only() -> None:
    session = _DiseaseLinkSession(
        monarch_rows=[],
        reference_rows=[
            {
                "symbol_key": "GENE1",
                "assembly_name": "GRCh38",
                "omim_diseases": [
                    # The link is built from the number, never taken as stored.
                    {"label": " Marfan syndrome ", "omim_id": "154700", "href": "https://example.org/elsewhere"},
                    {"label": "No number"},
                    {"label": "Not a number", "omim_id": "MIM:12"},
                    {"label": "  ", "omim_id": "100100"},
                    "a bare string",
                ],
            },
            {"symbol_key": "GENE2", "assembly_name": "GRCh38", "omim_diseases": None},
        ],
    )

    links = _disease_links(session, ["GENE1", "GENE2"])

    assert [(disease.label, disease.omim_id, disease.href) for disease in links[0].omim] == [
        ("Marfan syndrome", "154700", "https://www.omim.org/entry/154700"),
        ("No number", None, None),
        ("Not a number", "MIM:12", None),
    ]
    assert links[1].omim == []


def test_disease_links_without_a_symbol_read_nothing() -> None:
    session = _DiseaseLinkSession(monarch_rows=[], reference_rows=[])

    assert _disease_links(session, []) == []
    assert _disease_links(session, ["", "  "]) == []
    assert session.reads == []


def test_the_disease_link_lookup_refuses_more_genes_than_a_page_shows() -> None:
    from backend.app.routers.genes import get_gene_disease_links

    session = _DiseaseLinkSession(monarch_rows=[], reference_rows=[])
    viewer = CurrentUser(
        id="00000000-0000-4000-8000-0000000000b1",
        username="viewer@example.org",
        email="viewer@example.org",
        role="viewer",
        created_at=IMPORTED_AT,
    )
    asked = [f"GENE{n}" for n in range(gene_metadata_service.MAX_DISEASE_LINK_SYMBOLS + 1)]

    with pytest.raises(HTTPException) as refused:
        asyncio.run(get_gene_disease_links(symbol=asked, session=session, user=viewer))  # type: ignore[arg-type]

    assert refused.value.status_code == 422
    assert session.reads == []
    # A page's worth is answered.
    answered = asyncio.run(
        get_gene_disease_links(symbol=asked[:-1], session=session, user=viewer)  # type: ignore[arg-type]
    )
    assert len(answered) == gene_metadata_service.MAX_DISEASE_LINK_SYMBOLS
