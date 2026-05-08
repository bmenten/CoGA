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
        "Gene_name\tMIM_id\tDisease_description\tmis_z\ts_het\tpHaplo\tpTriplo\tGene_full_name\n"
        "BRCA1\t113705;604370\tBreast-ovarian cancer syndrome\t3.21\t0.094\t0.88\t0.06\tBRCA1 DNA repair associated\n",
        encoding="utf-8",
    )

    result = gene_info_bulk_sources.parse_dbnsfp_gene_rows(dbnsfp_path)

    assert result["BRCA1"]["omim_gene_id"] == "113705"
    assert result["BRCA1"]["extra"]["constraint_metrics"] == {
        "missense_z": 3.21,
        "shet": 0.094,
        "phaplo": 0.88,
        "ptriplo": 0.06,
    }
    assert result["BRCA1"]["extra"]["omim_diseases"] == [
        {
            "label": "Breast-ovarian cancer syndrome",
            "omim_id": "113705",
            "href": "https://www.omim.org/entry/113705",
        }
    ]


@pytest.mark.asyncio
async def test_fetch_external_gene_bundle_merges_bulk_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch_hgnc_gene(symbol: str):
        assert symbol == "BRCA1"
        return {
            "name": "BRCA1 DNA repair associated",
            "alias_symbol": ["BRCC1"],
            "hgnc_id": "HGNC:1100",
        }

    async def fake_fetch_ensembl_gene(symbol: str, species_name: str):
        assert symbol == "BRCA1"
        assert species_name == "Homo sapiens"
        return {
            "id": "ENSG00000012048",
            "description": "BRCA1 DNA repair associated",
            "canonical_transcript": "ENST00000357654",
            "biotype": "protein_coding",
        }

    async def fake_fetch_ensembl_homologies(ensembl_gene_id: str):
        assert ensembl_gene_id == "ENSG00000012048"
        return {"data": []}

    async def fake_fetch_ncbi_gene(symbol: str, species_name: str):
        assert symbol == "BRCA1"
        assert species_name == "Homo sapiens"
        return {
            "uid": "672",
            "summary": "Tumor suppressor involved in DNA repair.",
            "otheraliases": "BRCC1",
        }

    async def fake_fetch_clingen_gene(symbol: str, hgnc_id: str | None):
        assert symbol == "BRCA1"
        assert hgnc_id == "HGNC:1100"
        return {
            "curation_counts": {
                "clinical_actionability": 2,
            },
            "gene_facts": {
                "cytoband": "17q21.31",
                "gencc_classifications": {"Limited": 1},
            },
        }

    monkeypatch.setattr(gene_info_external, "fetch_hgnc_gene", fake_fetch_hgnc_gene)
    monkeypatch.setattr(gene_info_external, "fetch_ensembl_gene", fake_fetch_ensembl_gene)
    monkeypatch.setattr(gene_info_external, "fetch_ensembl_homologies", fake_fetch_ensembl_homologies)
    monkeypatch.setattr(gene_info_external, "fetch_ncbi_gene", fake_fetch_ncbi_gene)
    monkeypatch.setattr(gene_info_external, "fetch_clingen_gene", fake_fetch_clingen_gene)

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
                records_by_symbol={
                    "BRCA1": {
                        "omim_gene_id": "113705",
                        "extra": {
                            "constraint_metrics": {
                                "missense_z": 3.21,
                            }
                        },
                    }
                },
            ),
        }
    )

    result = await gene_info_external.fetch_external_gene_bundle(
        symbol="BRCA1",
        species_document={"name": "Homo sapiens"},
        species_docs=[],
        bulk_context=bulk_context,
    )

    assert result["omim_gene_id"] == "113705"
    assert result["source_status"]["dbnsfp_gene"]["status"] == "success"
    assert isinstance(result["source_status"]["dbnsfp_gene"]["fetched_at"], str)
    assert result["extra"]["clingen_curation_counts"] == {
        "clinical_actionability": 2,
        "gene_disease_validity": 3,
    }
    assert result["extra"]["clingen_gene_facts"]["gencc_classifications"] == {
        "Limited": 1,
        "Definitive": 5,
    }
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
    assert result["extra"]["constraint_metrics"] == {"missense_z": 3.21}
