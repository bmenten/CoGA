from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.services import gene_info_bulk_sources, gene_info_external
from backend.app.services.gene_info_bulk_sources import (
    GeneBulkSourceDataset,
    HumanGeneBulkContext,
)


def test_parse_clingen_validity_rows_groups_by_symbol() -> None:
    csv_text = """GENE SYMBOL,GENE ID (HGNC),DISEASE LABEL,DISEASE ID (MONDO),MOI,SOP,CLASSIFICATION,ONLINE REPORT,CLASSIFICATION DATE,GCEP
BRCA1,HGNC:1100,Breast-ovarian cancer,MONDO:0012934,AD,SOP v1,Definitive,https://example.test/report,2026-01-01,Hereditary Cancer GCEP
"""

    result = gene_info_bulk_sources.parse_clingen_validity_rows(csv_text)

    assert result["BRCA1"]["extra"]["clingen_curation_counts"]["gene_disease_validity"] == 1
    assert result["BRCA1"]["extra"]["clingen_validity_assertions"] == [
        {
            "disease_label": "Breast-ovarian cancer",
            "disease_id": "MONDO:0012934",
            "moi": "AD",
            "sop": "SOP v1",
            "classification": "Definitive",
            "online_report": "https://example.test/report",
            "classification_date": "2026-01-01",
            "gcep": "Hereditary Cancer GCEP",
        }
    ]


# The shape ClinGen actually serves: a title line, FILE CREATED, the webpage URL and a
# "+++" rule ahead of the real header, plus one more rule directly under it. Parsing this
# as a plain CSV takes the title line as the field names and silently yields nothing,
# which is how both ClinGen sources contributed no data at all while still reporting a
# successful download.
CLINGEN_VALIDITY_WITH_BANNER = """\
"CLINGEN GENE DISEASE VALIDITY CURATIONS","","","","","","","","",""
"FILE CREATED: 2026-08-10","","","","","","","","",""
"WEBPAGE: https://search.clinicalgenome.org/kb/gene-validity","","","","","","","","",""
"+++++++++++","++++++++++++++","+++++++++++++","++++++++++++++++++","+++++++++","+++++++++","++++++++++++++","+++++++++++++","+++++++++++++++++++","++++++++++++"
"GENE SYMBOL","GENE ID (HGNC)","DISEASE LABEL","DISEASE ID (MONDO)","MOI","SOP","CLASSIFICATION","ONLINE REPORT","CLASSIFICATION DATE","GCEP"
"+++++++++++","++++++++++++++","+++++++++++++","++++++++++++++++++","+++++++++","+++++++++","++++++++++++++","+++++++++++++","+++++++++++++++++++","++++++++++++"
"BRCA1","HGNC:1100","Breast-ovarian cancer","MONDO:0012934","AD","SOP10","Definitive","https://example.test/report","2026-01-01","Hereditary Cancer GCEP"
"""

CLINGEN_DOSAGE_WITH_BANNER = """\
"CLINGEN DOSAGE SENSITIVITY CURATIONS","","","","",""
"FILE CREATED: 2026-08-10","","","","",""
"WEBPAGE: https://search.clinicalgenome.org/kb/gene-dosage","","","","",""
"+++++++++++","+++++++","++++++++++++++++++","+++++++++++++++++","+++++++++++++","++++"
"GENE SYMBOL","HGNC ID","HAPLOINSUFFICIENCY","TRIPLOSENSITIVITY","ONLINE REPORT","DATE"
"+++++++++++","+++++++","++++++++++++++++++","+++++++++++++++++","+++++++++++++","++++"
"BRCA1","HGNC:1100","Sufficient Evidence for Haploinsufficiency","No Evidence for Triplosensitivity","https://example.test/dosage","2021-09-23T08:27:44-04:00"
"""


def test_parse_clingen_validity_rows_skips_the_download_banner() -> None:
    result = gene_info_bulk_sources.parse_clingen_validity_rows(CLINGEN_VALIDITY_WITH_BANNER)

    assert set(result) == {"BRCA1"}
    assertions = result["BRCA1"]["extra"]["clingen_validity_assertions"]
    assert [entry["classification"] for entry in assertions] == ["Definitive"]
    assert result["BRCA1"]["extra"]["clingen_curation_counts"]["gene_disease_validity"] == 1


def test_parse_clingen_dosage_rows_skips_the_download_banner() -> None:
    result = gene_info_bulk_sources.parse_clingen_dosage_rows(CLINGEN_DOSAGE_WITH_BANNER)

    assert set(result) == {"BRCA1"}
    assert result["BRCA1"]["extra"]["clingen_dosage_assertions"] == [
        {
            "hgnc_id": "HGNC:1100",
            "haploinsufficiency": "Sufficient Evidence for Haploinsufficiency",
            "triplosensitivity": "No Evidence for Triplosensitivity",
            "online_report": "https://example.test/dosage",
            "date": "2021-09-23T08:27:44-04:00",
        }
    ]


def test_parse_clingen_rows_never_emits_the_banner_rule_as_a_gene() -> None:
    # The "+++" rule under the header has a value in the GENE SYMBOL column, so it would
    # otherwise be ingested as a gene named "+++++++++++".
    for result in (
        gene_info_bulk_sources.parse_clingen_validity_rows(CLINGEN_VALIDITY_WITH_BANNER),
        gene_info_bulk_sources.parse_clingen_dosage_rows(CLINGEN_DOSAGE_WITH_BANNER),
    ):
        assert not any(set(symbol) == {"+"} for symbol in result)


def test_parse_clingen_rows_still_reads_a_file_served_without_a_banner() -> None:
    # The header is located by its columns rather than a fixed offset, so ClinGen
    # dropping the banner would not break parsing the other way round.
    csv_text = (
        '"GENE SYMBOL","HGNC ID","HAPLOINSUFFICIENCY","TRIPLOSENSITIVITY","ONLINE REPORT","DATE"\n'
        '"TP53","HGNC:11998","Sufficient Evidence for Haploinsufficiency","No Evidence for Triplosensitivity","https://example.test/tp53","2020-01-01"\n'
    )

    result = gene_info_bulk_sources.parse_clingen_dosage_rows(csv_text)

    assert set(result) == {"TP53"}


def test_parse_clingen_validity_rows_filters_to_requested_symbols() -> None:
    csv_text = """GENE SYMBOL,GENE ID (HGNC),DISEASE LABEL,DISEASE ID (MONDO),MOI,SOP,CLASSIFICATION,ONLINE REPORT,CLASSIFICATION DATE,GCEP
BRCA1,HGNC:1100,Breast-ovarian cancer,MONDO:0012934,AD,SOP v1,Definitive,https://example.test/1,2026-01-01,GCEP
TP53,HGNC:11998,Li-Fraumeni syndrome,MONDO:0018875,AD,SOP v1,Definitive,https://example.test/2,2026-01-02,GCEP
"""

    only_brca = gene_info_bulk_sources.parse_clingen_validity_rows(csv_text, symbols=["BRCA1"])
    assert set(only_brca) == {"BRCA1"}  # TP53 not built/retained

    both = gene_info_bulk_sources.parse_clingen_validity_rows(csv_text)
    assert set(both) == {"BRCA1", "TP53"}  # no filter -> all symbols


def test_parse_gencc_rows_counts_classifications_once_per_unique_row() -> None:
    csv_text = """gene_curie,gene_symbol,disease_curie,disease_title,classification_title,moi_title,submitter_title,submitted_as_public_report_url
HGNC:1100,BRCA1,MONDO:0012934,Breast-ovarian cancer,Definitive,AD,Genomics England,https://example.test/1
HGNC:1100,BRCA1,MONDO:0012934,Breast-ovarian cancer,Definitive,AD,Genomics England,https://example.test/1
HGNC:1100,BRCA1,MONDO:0012934,Breast-ovarian cancer,Strong,AD,ClinGen,https://example.test/2
"""

    result = gene_info_bulk_sources.parse_gencc_rows(csv_text)

    assert result["BRCA1"]["extra"]["clingen_gene_facts"]["gencc_classifications"] == {
        "Definitive": 1,
        "Strong": 1,
    }
    assert len(result["BRCA1"]["extra"]["gencc_assertions"]) == 2


def test_parse_clinvar_gene_condition_rows_preserves_commas_in_disease_names() -> None:
    tsv_text = """#GeneID\tAssociatedGenes\tRelatedGenes\tConceptID\tDiseaseName\tSourceName\tSourceID\tDiseaseMIM\tLastUpdated
672\tBRCA1;NBR2\t\tC0006142\tBreast-ovarian cancer, familial, susceptibility to, 1\tMONDO\tMONDO:0012934\t604370\tApr 12 2026
"""

    result = gene_info_bulk_sources.parse_clinvar_gene_condition_rows(tsv_text)

    for symbol in ("BRCA1", "NBR2"):
        assert result[symbol]["extra"]["omim_diseases"] == [
            {
                "label": "Breast-ovarian cancer, familial, susceptibility to, 1",
                "omim_id": "604370",
                "href": "https://www.omim.org/entry/604370",
            }
        ]
        assert result[symbol]["extra"]["dbnsfp_disease_associations"] == [
            {
                "label": "Breast-ovarian cancer, familial, susceptibility to, 1",
                "source": "ClinVar",
                "details": "MONDO · MONDO:0012934",
            }
        ]


def test_parse_dbnsfp_gene_rows_extracts_constraint_metrics_and_omim(tmp_path: Path) -> None:
    dbnsfp_path = tmp_path / "dbNSFP_gene.tsv"
    dbnsfp_path.write_text(
        "Gene_name\tEnsembl_gene\tGene_other_names\tMIM_id\tGene_full_name\tFunction_description\tMIM_disease\t"
        "GenCC_disease_title\tGenCC_impact_class\tGenCC_model_of_inheritance\tmis_z\ts_het\t"
        "pHaplo\tpTriplo\tP(HI)\tgnomAD_pLI\tgnomAD_LOEUF\n"
        "BRCA1\tENSG00000012048\tBRCC1;FANCS\t113705\tBRCA1 DNA repair associated\t"
        "FUNCTION: Tumor suppressor involved in DNA repair.\t"
        "[MIM:604370]Breast-ovarian cancer syndrome\tBreast-ovarian cancer syndrome\t"
        "Definitive\tAutosomal dominant\t3.21\t0.094\t0.88\t0.06\t0.99\t1.0\t0.16\n",
        encoding="utf-8",
    )

    result = gene_info_bulk_sources.parse_dbnsfp_gene_rows(dbnsfp_path)

    assert result["BRCA1"]["omim_gene_id"] == "113705"
    assert result["BRCA1"]["profile"]["display_name"] == "BRCA1 DNA repair associated"
    assert result["BRCA1"]["profile"]["summary"] == "Tumor suppressor involved in DNA repair."
    assert result["BRCA1"]["profile"]["aliases"] == ["BRCC1", "FANCS"]
    assert result["BRCA1"]["profile"]["ensembl_gene_id"] == "ENSG00000012048"
    assert result["BRCA1"]["extra"]["constraint_metrics"] == {
        "missense_z": 3.21,
        "shet": 0.094,
        "phaplo": 0.88,
        "ptriplo": 0.06,
        "p_hi": 0.99,
        "gnomad_pli": 1.0,
        "gnomad_loeuf": 0.16,
    }
    assert result["BRCA1"]["extra"]["clingen_gene_facts"] == {
        "hgnc_name": "BRCA1 DNA repair associated",
        "alias_symbols": ["BRCC1", "FANCS"],
        "function": "Tumor suppressor involved in DNA repair.",
        "haploinsufficiency_index": 99.0,
        "pli": 1.0,
        "loeuf": 0.16,
        "gencc_classifications": {"Definitive": 1},
    }
    assert result["BRCA1"]["extra"]["omim_diseases"] == [
        {
            "label": "Breast-ovarian cancer syndrome",
            "omim_id": "604370",
            "href": "https://www.omim.org/entry/604370",
        }
    ]
    assert result["BRCA1"]["extra"]["gencc_assertions"] == [
        {
            "disease_title": "Breast-ovarian cancer syndrome",
            "classification_title": "Definitive",
            "moi_title": "Autosomal dominant",
        }
    ]


@pytest.mark.asyncio
async def test_fetch_external_gene_bundle_uses_dbnsfp_without_online_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def forbidden_fetch(*args, **kwargs):
        raise AssertionError("Online source should not be called when dbNSFP has the gene")

    # NCBI is the only per-gene lookup that still exists; dbNSFP covering the gene must
    # short-circuit before it is reached.
    monkeypatch.setattr(gene_info_external, "fetch_ncbi_gene", forbidden_fetch)

    bulk_context = HumanGeneBulkContext(
        datasets={
            "dbnsfp_gene": GeneBulkSourceDataset(
                name="dbNSFP gene",
                source_url="/data/ref-data/dbNSFP5.4_gene.gz",
                status="success",
                records_by_symbol={
                    "BRCA1": {
                        "omim_gene_id": "113705",
                        "profile": {
                            "display_name": "BRCA1 DNA repair associated",
                            "summary": "Tumor suppressor involved in DNA repair.",
                            "aliases": ["BRCC1"],
                            "previous_symbols": ["RNF53"],
                            "ensembl_gene_id": "ENSG00000012048",
                            "ncbi_gene_id": "672",
                        },
                        "extra": {
                            "constraint_metrics": {"missense_z": 3.21},
                            "clingen_gene_facts": {"gencc_classifications": {"Definitive": 5}},
                        },
                        "homologs": [
                            {
                                "species_name": "Mus Musculus",
                                "common_name": "mouse",
                                "symbol": "Brca1",
                                "homology_type": "dbNSFP model organism ortholog",
                                "in_platform": False,
                            }
                        ],
                    }
                },
            )
        }
    )

    result = await gene_info_external.fetch_external_gene_bundle(
        symbol="BRCA1",
        species_document={"name": "Homo sapiens"},
        species_docs=[],
        bulk_context=bulk_context,
    )

    assert result["display_name"] == "BRCA1 DNA repair associated"
    assert result["summary"] == "Tumor suppressor involved in DNA repair."
    assert result["aliases"] == ["BRCC1"]
    assert result["previous_symbols"] == ["RNF53"]
    assert result["ensembl_gene_id"] == "ENSG00000012048"
    assert result["ncbi_gene_id"] == "672"
    assert result["omim_gene_id"] == "113705"
    assert result["homologs"][0]["symbol"] == "Brca1"
    assert result["source_status"]["dbnsfp_gene"]["status"] == "success"
    assert result["extra"]["constraint_metrics"] == {"missense_z": 3.21}


@pytest.mark.asyncio
async def test_fetch_external_gene_bundle_falls_back_to_online_sources_without_dbnsfp_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    async def fake_fetch_ncbi_gene(symbol: str, species_name: str):
        assert symbol == "BRCA1"
        assert species_name == "Homo sapiens"
        return {
            "uid": "672",
            "summary": "Tumor suppressor involved in DNA repair.",
            "otheraliases": "BRCC1",
        }

    # NCBI is the only per-gene lookup left; the others were removed outright, which
    # backend/tests/test_gene_info_external.py asserts.
    monkeypatch.setattr(gene_info_external, "fetch_ncbi_gene", fake_fetch_ncbi_gene)

    bulk_context = HumanGeneBulkContext(
        datasets={
            "clingen_gene_validity": GeneBulkSourceDataset(
                name="ClinGen gene validity",
                source_url="https://example.test/clingen-validity",
                status="success",
                records_by_symbol={
                    "BRCA1": {
                        "extra": {
                            "clingen_curation_counts": {"gene_disease_validity": 3},
                            "clingen_validity_assertions": [{"disease_label": "Breast cancer"}],
                        },
                    }
                },
            ),
            "gencc": GeneBulkSourceDataset(
                name="GenCC",
                source_url="https://example.test/gencc",
                status="success",
                records_by_symbol={
                    "BRCA1": {
                        "extra": {
                            "clingen_gene_facts": {
                                "gencc_classifications": {"Definitive": 5},
                            }
                        },
                    }
                },
            ),
            "clinvar_gene_condition": GeneBulkSourceDataset(
                name="ClinVar gene-condition",
                source_url="https://example.test/clinvar",
                status="success",
                records_by_symbol={
                    "BRCA1": {
                        "extra": {
                            "omim_diseases": [
                                {
                                    "label": "Breast-ovarian cancer, familial, susceptibility to, 1",
                                    "omim_id": "604370",
                                }
                            ],
                            "dbnsfp_disease_associations": [
                                {
                                    "label": "Hereditary breast and ovarian cancer syndrome",
                                    "source": "ClinVar",
                                }
                            ],
                        },
                    }
                },
            ),
            "dbnsfp_gene": GeneBulkSourceDataset(
                name="dbNSFP gene",
                source_url="/tmp/dbNSFP_gene.tsv.gz",
                status="success",
                records_by_symbol={},
            ),
        }
    )

    result = await gene_info_external.fetch_external_gene_bundle(
        symbol="BRCA1",
        species_document={"name": "Homo sapiens"},
        species_docs=[],
        bulk_context=bulk_context,
    )

    assert result["omim_gene_id"] is None
    assert result["source_status"]["dbnsfp_gene"]["status"] == "missing"
    assert isinstance(result["source_status"]["dbnsfp_gene"]["fetched_at"], str)
    # ClinGen's curations still reach the gene, but from the bulk gene-validity file
    # rather than the page scrape — so only the bulk count is present now.
    assert result["extra"]["clingen_curation_counts"] == {"gene_disease_validity": 3}
    assert result["extra"]["clingen_gene_facts"]["gencc_classifications"] == {"Definitive": 5}
    assert result["extra"]["omim_diseases"] == [
        {
            "label": "Breast-ovarian cancer, familial, susceptibility to, 1",
            "omim_id": "604370",
        }
    ]
    assert result["extra"]["dbnsfp_disease_associations"] == [
        {
            "label": "Hereditary breast and ovarian cancer syndrome",
            "source": "ClinVar",
        }
    ]


# The real HGNC complete set is a 45k-row TSV; these two rows carry the columns the
# parser reads, including the rename that broke gene lookups when dbNSFP 5.4 shipped
# LMTK1 for the gene the assembly still calls AATK.
HGNC_COMPLETE_SET = (
    "hgnc_id\tsymbol\tname\tlocus_group\tlocus_type\tstatus\tlocation\talias_symbol\t"
    "prev_symbol\tgene_group\tdate_approved_reserved\tdate_symbol_changed\tdate_modified\t"
    "entrez_id\tensembl_gene_id\tvega_id\tucsc_id\trefseq_accession\tccds_id\tuniprot_ids\t"
    "omim_id\torphanet\tmane_select\tmgd_id\n"
    "HGNC:1100\tBRCA1\tBRCA1 DNA repair associated\tprotein-coding gene\tgene with protein product\t"
    "Approved\t17q21.31\tRNF53|PPP1R53\tBRCAI|BRCC1\tRing finger proteins\t1994-01-01\t\t2026-01-01\t"
    "672\tENSG00000012048\tOTTHUMG00000157426\tuc002ict.4\tNM_007294\tCCDS11453\tP38398\t113705\t145\t"
    "ENST00000357654.9|NM_007294.4\tMGI:104537\n"
    "HGNC:21\tLMTK1\tlemur tyrosine kinase 1\tprotein-coding gene\tgene with protein product\t"
    "Approved\t17q25.3\tAATYK|LMR1\tAATK\tLemur tyrosine kinases\t1997-01-01\t2026-02-01\t2026-02-01\t"
    "9625\tENSG00000181409\t\t\tNM_001080395\t\tQ6ZMQ8\t\t\t\t\n"
)


def test_parse_hgnc_complete_set_captures_identity_and_symbol_history() -> None:
    result = gene_info_bulk_sources.parse_hgnc_complete_set_rows(HGNC_COMPLETE_SET)

    assert set(result) == {"BRCA1", "LMTK1"}
    brca1 = result["BRCA1"]
    assert brca1["profile"]["hgnc_id"] == "HGNC:1100"
    assert brca1["profile"]["ensembl_gene_id"] == "ENSG00000012048"
    assert brca1["profile"]["ncbi_gene_id"] == "672"
    assert brca1["profile"]["location"] == "17q21.31"
    assert brca1["omim_gene_id"] == "113705"
    assert brca1["aliases"] == ["RNF53", "PPP1R53"]
    assert brca1["previous_symbols"] == ["BRCAI", "BRCC1"]
    assert brca1["extra"]["hgnc_identifiers"]["uniprot_ids"] == ["P38398"]
    assert brca1["extra"]["hgnc_identifiers"]["mane_select"] == [
        "ENST00000357654.9",
        "NM_007294.4",
    ]
    assert brca1["extra"]["hgnc_gene_facts"]["locus_group"] == "protein-coding gene"
    # MGI addresses the mouse ortholog by this id; there is no route from a human symbol.
    assert brca1["extra"]["hgnc_identifiers"]["mgd_id"] == "MGI:104537"


def test_parse_hgnc_complete_set_skips_withdrawn_entries() -> None:
    withdrawn = HGNC_COMPLETE_SET.replace("Approved\t17q25.3", "Entry Withdrawn\t17q25.3")

    result = gene_info_bulk_sources.parse_hgnc_complete_set_rows(withdrawn)

    assert set(result) == {"BRCA1"}


def test_hgnc_resolver_maps_previous_and_alias_symbols_to_the_current_one() -> None:
    records = gene_info_bulk_sources.parse_hgnc_complete_set_rows(HGNC_COMPLETE_SET)

    resolver = gene_info_bulk_sources.build_hgnc_symbol_resolver(records)

    # The rename dbNSFP 5.4 shipped: the assembly still says AATK, HGNC says LMTK1.
    assert resolver["AATK"] == "LMTK1"
    assert resolver["LMTK1"] == "LMTK1"
    assert resolver["BRCAI"] == "BRCA1"
    assert resolver["RNF53"] == "BRCA1"
    assert "NOT_A_GENE" not in resolver


def test_hgnc_resolver_drops_a_historic_symbol_two_genes_both_claim() -> None:
    contested = HGNC_COMPLETE_SET.replace("\tAATYK|LMR1\tAATK\t", "\tAATYK|RNF53\tAATK\t")

    resolver = gene_info_bulk_sources.build_hgnc_symbol_resolver(
        gene_info_bulk_sources.parse_hgnc_complete_set_rows(contested)
    )

    # RNF53 is an alias of BRCA1 and (in this fixture) of LMTK1 too. Guessing would
    # attach one gene's annotation to the other, so the ambiguous claim is dropped.
    assert "RNF53" not in resolver
    assert resolver["AATK"] == "LMTK1"


def test_hgnc_resolver_never_lets_an_alias_shadow_an_approved_symbol() -> None:
    # LMTK1 lists AATYK as an alias; if some other gene were approved under AATYK the
    # approved entry has to win, or that gene's own annotation lands on LMTK1.
    records = gene_info_bulk_sources.parse_hgnc_complete_set_rows(
        HGNC_COMPLETE_SET
        + "HGNC:99\tAATYK\tdecoy\tprotein-coding gene\tgene with protein product\tApproved\t1p1\t\t\t\t"
        "2000-01-01\t\t\t1\tENSG00000000001\t\t\t\t\t\t\t\n"
    )

    resolver = gene_info_bulk_sources.build_hgnc_symbol_resolver(records)

    assert resolver["AATYK"] == "AATYK"


def test_dataset_reports_not_consulted_apart_from_no_record() -> None:
    dataset = GeneBulkSourceDataset(
        name="GenCC",
        source_url="https://example.test/gencc",
        status="success",
        records_by_symbol={"BRCA1": {"extra": {}}},
        consulted_symbols={"BRCA1", "TP53"},
    )

    # Asked about and found.
    assert dataset.status_for_symbol("BRCA1")["status"] == "success"
    # Asked about, genuinely absent from the source.
    assert dataset.status_for_symbol("TP53")["status"] == "missing"
    # Never asked about — not evidence of anything about the source's coverage.
    assert dataset.status_for_symbol("SCN1A")["status"] == "not_consulted"


def test_dataset_consulted_for_every_symbol_never_reports_not_consulted() -> None:
    dataset = GeneBulkSourceDataset(
        name="GenCC",
        source_url="https://example.test/gencc",
        status="success",
        records_by_symbol={"BRCA1": {"extra": {}}},
        consulted_symbols=None,
    )

    assert dataset.status_for_symbol("SCN1A")["status"] == "missing"


def test_dbnsfp_release_label_reads_the_version_out_of_the_filename() -> None:
    from pathlib import Path

    assert gene_info_bulk_sources._dbnsfp_release_label(Path("/d/dbNSFP5.4_gene.gz")) == "5.4"
    assert gene_info_bulk_sources._dbnsfp_release_label(Path("/d/dbNSFP4.3a_gene.gz")) == "4.3a"
    assert gene_info_bulk_sources._dbnsfp_release_label(Path("/d/genes.gz")) is None


def test_clingen_release_label_reads_the_banner_date() -> None:
    assert (
        gene_info_bulk_sources._clingen_release_label(CLINGEN_DOSAGE_WITH_BANNER) == "2026-08-10"
    )
    assert gene_info_bulk_sources._clingen_release_label("no banner here") is None


def test_max_column_release_label_takes_the_newest_iso_date() -> None:
    text_value = (
        "symbol\tdate_modified\n"
        "BRCA1\t2026-01-01\n"
        "TP53\t2026-08-07\n"
        "SCN1A\t2025-12-31\n"
    )

    label = gene_info_bulk_sources._max_column_release_label(
        text_value, column="date_modified", delimiter="\t"
    )

    assert label == "2026-08-07"


def test_max_column_release_label_ignores_non_iso_dates() -> None:
    # ClinVar's "Feb 16 2016" style sorts wrongly as a string; a confidently wrong
    # release is worse than none, so those values are skipped.
    text_value = "symbol\tlastupdated\nBRCA1\tFeb 16 2016\nTP53\tJan 02 2020\n"

    label = gene_info_bulk_sources._max_column_release_label(
        text_value, column="lastupdated", delimiter="\t"
    )

    assert label is None


def test_source_status_carries_the_release_for_every_verdict() -> None:
    release = gene_info_bulk_sources.GeneSourceRelease(
        label="2026-08-10", checksum="abc123", size_bytes=42
    )
    dataset = GeneBulkSourceDataset(
        name="ClinGen dosage",
        source_url="https://example.test/dosage",
        status="success",
        records_by_symbol={"BRCA1": {"extra": {}}},
        consulted_symbols={"BRCA1", "TP53"},
        release=release,
    )

    found = dataset.status_for_symbol("BRCA1")
    absent = dataset.status_for_symbol("TP53")
    skipped = dataset.status_for_symbol("SCN1A")

    # "This release had nothing for this gene" is only meaningful with the release
    # attached, so every verdict carries it — not just the successes.
    assert found["release"] == absent["release"] == skipped["release"] == "2026-08-10"
    assert found["release_detail"] == {
        "label": "2026-08-10",
        "checksum": "abc123",
        "size_bytes": 42,
    }


def test_source_status_without_a_release_states_none_rather_than_guessing() -> None:
    dataset = GeneBulkSourceDataset(
        name="ClinVar gene-condition",
        source_url="https://example.test/clinvar",
        status="success",
        records_by_symbol={"BRCA1": {"extra": {}}},
    )

    status = dataset.status_for_symbol("BRCA1")

    assert status["release"] is None
    assert status["release_detail"] == {}


@pytest.mark.asyncio
async def test_fetch_external_gene_bundle_takes_identity_from_the_bulk_hgnc_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No per-gene HGNC request, yet identity still resolves.

    Everything the removed HGNC REST call supplied — name, aliases, previous symbols,
    Ensembl/Entrez/OMIM ids, locus group, location, VEGA id, RefSeq accessions — is in
    the complete set that is already downloaded once per job.
    """

    async def fake_fetch_ncbi_gene(symbol: str, species_name: str):
        return {"summary": "Tumor suppressor involved in DNA repair."}

    monkeypatch.setattr(gene_info_external, "fetch_ncbi_gene", fake_fetch_ncbi_gene)

    bulk_context = HumanGeneBulkContext(
        datasets={
            "hgnc_complete_set": GeneBulkSourceDataset(
                name="HGNC complete set",
                source_url="https://example.test/hgnc",
                status="success",
                records_by_symbol={
                    "BRCA1": {
                        "profile": {
                            "hgnc_id": "HGNC:1100",
                            "display_name": "BRCA1 DNA repair associated",
                            "ensembl_gene_id": "ENSG00000012048",
                            "ncbi_gene_id": "672",
                            "location": "17q21.31",
                        },
                        "aliases": ["RNF53"],
                        "previous_symbols": ["BRCAI"],
                        "omim_gene_id": "113705",
                        "extra": {
                            "hgnc_identifiers": {
                                "vega_id": "OTTHUMG00000157426",
                                "refseq_accession": ["NM_007294"],
                                "omim_ids": ["113705"],
                            },
                            "hgnc_gene_facts": {
                                "locus_group": "protein-coding gene",
                                "gene_group": ["Ring finger proteins"],
                            },
                        },
                    }
                },
            ),
            "dbnsfp_gene": GeneBulkSourceDataset(
                name="dbNSFP gene",
                source_url="/tmp/dbNSFP_gene.tsv.gz",
                status="success",
                records_by_symbol={},
            ),
        }
    )

    result = await gene_info_external.fetch_external_gene_bundle(
        symbol="BRCA1",
        species_document={"name": "Homo sapiens"},
        species_docs=[],
        bulk_context=bulk_context,
    )

    assert result["display_name"] == "BRCA1 DNA repair associated"
    assert result["hgnc_id"] == "HGNC:1100"
    assert result["ensembl_gene_id"] == "ENSG00000012048"
    assert result["ncbi_gene_id"] == "672"
    assert result["omim_gene_id"] == "113705"
    assert result["gene_type"] == "protein-coding gene"
    assert result["location"] == "17q21.31"
    assert result["aliases"] == ["RNF53"]
    assert result["previous_symbols"] == ["BRCAI"]
    assert result["extra"]["hgnc_vega_id"] == "OTTHUMG00000157426"
    assert result["extra"]["refseq_accessions"] == ["NM_007294"]
    assert result["extra"]["hgnc_gene_group"] == ["Ring finger proteins"]
    # NCBI is the one per-gene source still worth its request.
    assert result["summary"] == "Tumor suppressor involved in DNA repair."
