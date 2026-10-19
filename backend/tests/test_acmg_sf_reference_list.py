"""The ACMG secondary-findings list CoGA ships, and the gene reference sync's verdict (CLIN-19).

The gene page's "ACMG secondary finding" row read a key no source wrote, so it said "—" for
every gene, BRCA1 included. The list is now vendored (data/ref-data/acmg-sf/, ACMG SF v3.3)
and the gene reference sync reads it for every gene: a listed gene gets its entry and the
status ``success``, any other gene ``missing`` ("not on the list"), both with the list's
version. A list that cannot be read whole is an ``error`` for every gene, never a
``missing``. Nothing here goes to the network.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import pytest

from backend.app.schemas import GeneInfoSourceStatusOut
from backend.app.services import gene_info_bulk_sources, gene_info_external
from backend.app.services.gene_info_bulk_sources import GeneBulkSourceDataset, HumanGeneBulkContext

_LIST = gene_info_bulk_sources.ACMG_SF_LIST_PATH
_README = _LIST.parent / "README.md"


def _shipped() -> dict[str, Any]:
    return json.loads(_LIST.read_text(encoding="utf-8"))


def test_the_shipped_list_is_acmg_sf_v3_3_with_its_84_genes() -> None:
    version, entries = gene_info_bulk_sources.parse_acmg_sf_list(_LIST.read_text(encoding="utf-8"))

    assert version == "3.3"
    assert len(entries) == 84
    # v3.3 added three genes to v3.2's 81 and removed none.
    assert sorted(symbol for symbol, entry in entries.items() if entry["added_in"] == "3.3") == [
        "ABCD1",
        "CYP27A1",
        "PLN",
    ]
    assert {entry["category"] for entry in entries.values()} == {
        "Cancer Predisposition",
        "Cardiovascular Disease Predisposition",
        "Inborn Errors of Metabolism",
        "Other Disease Phenotypes",
    }
    assert entries["BRCA1"] == {
        "list": "ACMG SF",
        "version": "3.3",
        "gene": "BRCA1",
        "hgnc_id": "HGNC:1100",
        "phenotype": "Hereditary breast and/or ovarian cancer",
        "omim_disorders": ["604370"],
        "category": "Cancer Predisposition",
        "inheritance": "AD",
        "variants_to_report": "All P and LP",
        "added_in": "1.0",
        "notes": [],
        "source_url": "https://doi.org/10.1016/j.gim.2025.101454",
    }
    # The footnote that narrows TTN to truncating variants travels with the gene.
    assert [note["footnote"] for note in entries["TTN"]["notes"]] == ["f"]
    assert "CFTR" not in entries


def test_the_readme_states_the_shipped_files_version_gene_count_and_checksum() -> None:
    document = _shipped()
    readme = _README.read_text(encoding="utf-8")

    assert f"| List | **ACMG SF v{document['version']}** |" in readme
    assert f"| Genes | {document['gene_count']} " in readme
    assert f"`{hashlib.sha256(_LIST.read_bytes()).hexdigest()}`" in readme
    assert document["source"]["doi"] in readme


def _bundle(symbol: str, dataset: GeneBulkSourceDataset) -> dict[str, Any]:
    return gene_info_bulk_sources.build_bulk_gene_bundle(
        symbol=symbol, bulk_context=HumanGeneBulkContext(datasets={"acmg_sf": dataset})
    )


def test_brca1_is_on_the_list_and_a_gene_the_list_leaves_out_is_not() -> None:
    dataset = gene_info_bulk_sources._load_acmg_sf_dataset()

    brca1 = _bundle("BRCA1", dataset)
    status = brca1["source_status"]["acmg_sf"]
    assert status["status"] == "success"
    assert status["release"] == "v3.3"
    assert status["release_detail"]["checksum"] == hashlib.sha256(_LIST.read_bytes()).hexdigest()
    entry = brca1["extra"]["acmg_secondary_finding"]
    assert (entry["version"], entry["phenotype"], entry["category"]) == (
        "3.3",
        "Hereditary breast and/or ovarian cancer",
        "Cancer Predisposition",
    )

    # CFTR is not on the list: the verdict is "missing", against the same release.
    cftr = _bundle("CFTR", dataset)
    assert cftr["source_status"]["acmg_sf"]["status"] == "missing"
    assert cftr["source_status"]["acmg_sf"]["release"] == "v3.3"
    assert "acmg_secondary_finding" not in cftr["extra"]
    # The gene profile serves each status through this model; one it refused would fail
    # the profile of every gene.
    for bundle in (brca1, cftr):
        GeneInfoSourceStatusOut(**bundle["source_status"]["acmg_sf"])


@pytest.mark.asyncio
async def test_a_gene_reference_sync_reads_the_list_into_every_genes_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_downloads(*, symbols: Any = None) -> dict[str, GeneBulkSourceDataset]:
        return {
            "hgnc_complete_set": GeneBulkSourceDataset(
                name="HGNC complete set",
                source_url="https://example.test/hgnc",
                status="success",
                records_by_symbol={
                    "BRCA1": {"profile": {"hgnc_id": "HGNC:1100"}},
                    "CFTR": {"profile": {"hgnc_id": "HGNC:1884"}},
                },
            )
        }

    def no_dbnsfp(*, symbols: Any) -> GeneBulkSourceDataset:
        return GeneBulkSourceDataset(name="dbNSFP gene", source_url=None, status="missing")

    async def no_ncbi(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(gene_info_bulk_sources, "_load_online_gene_bulk_datasets", no_downloads)
    monkeypatch.setattr(gene_info_bulk_sources, "_load_dbnsfp_gene_dataset", no_dbnsfp)
    monkeypatch.setattr(gene_info_external, "fetch_ncbi_gene", no_ncbi)

    context = await gene_info_bulk_sources.load_human_gene_bulk_context()

    # What the sync writes into each gene's gene_info row.
    brca1 = await gene_info_external.fetch_external_gene_bundle(
        symbol="BRCA1", species_document={"name": "Homo sapiens"}, bulk_context=context
    )
    cftr = await gene_info_external.fetch_external_gene_bundle(
        symbol="CFTR", species_document={"name": "Homo sapiens"}, bulk_context=context
    )
    assert brca1["source_status"]["acmg_sf"]["status"] == "success"
    assert brca1["extra"]["acmg_secondary_finding"]["phenotype"] == (
        "Hereditary breast and/or ovarian cancer"
    )
    assert cftr["source_status"]["acmg_sf"]["status"] == "missing"
    assert cftr["source_status"]["acmg_sf"]["release"] == "v3.3"
    assert "acmg_secondary_finding" not in cftr["extra"]


def test_a_listed_gene_hgnc_renamed_since_is_found_by_its_hgnc_id() -> None:
    # The list names BRCA1; suppose HGNC has since renamed HGNC:1100. The sync keys the gene
    # by its current symbol, and it must still read as listed, not as left out.
    dataset = gene_info_bulk_sources._load_acmg_sf_dataset()
    hgnc = GeneBulkSourceDataset(
        name="HGNC complete set",
        source_url="https://example.test/hgnc",
        status="success",
        records_by_symbol={"BRCA1RENAMED": {"profile": {"hgnc_id": "HGNC:1100"}}},
    )

    gene_info_bulk_sources._key_acmg_sf_by_current_symbol(dataset, hgnc)

    renamed = _bundle("BRCA1RENAMED", dataset)
    assert renamed["source_status"]["acmg_sf"]["status"] == "success"
    assert renamed["extra"]["acmg_secondary_finding"]["gene"] == "BRCA1"
    # A listed gene the loaded HGNC set does not hold keeps the list's symbol.
    assert _bundle("TP53", dataset)["source_status"]["acmg_sf"]["status"] == "success"


def test_without_the_hgnc_set_the_list_is_read_by_its_own_symbols() -> None:
    dataset = gene_info_bulk_sources._load_acmg_sf_dataset()
    failed_hgnc = GeneBulkSourceDataset(
        name="HGNC complete set", source_url="https://example.test/hgnc", status="error"
    )

    gene_info_bulk_sources._key_acmg_sf_by_current_symbol(dataset, failed_hgnc)

    assert _bundle("BRCA1", dataset)["source_status"]["acmg_sf"]["status"] == "success"


def _drop_a_gene(document: dict[str, Any]) -> None:
    document["genes"].pop()


def _repeat_a_gene(document: dict[str, Any]) -> None:
    document["genes"].append(dict(document["genes"][0]))
    document["gene_count"] += 1


def _blank_an_hgnc_id(document: dict[str, Any]) -> None:
    document["genes"][0]["hgnc_id"] = ""


def _blank_a_phenotype(document: dict[str, Any]) -> None:
    document["genes"][0]["phenotype"] = " "


def _drop_the_version(document: dict[str, Any]) -> None:
    del document["version"]


def _empty_the_list(document: dict[str, Any]) -> None:
    document["genes"] = []
    document["gene_count"] = 0


@pytest.mark.parametrize(
    "damage",
    [
        _drop_a_gene,
        _repeat_a_gene,
        _blank_an_hgnc_id,
        _blank_a_phenotype,
        _drop_the_version,
        _empty_the_list,
    ],
)
def test_a_list_that_is_not_whole_is_an_error_for_every_gene_never_not_listed(
    tmp_path: Path, damage: Callable[[dict[str, Any]], None]
) -> None:
    document = _shipped()
    damage(document)
    path = tmp_path / "acmg-sf-genes.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    dataset = gene_info_bulk_sources._load_acmg_sf_dataset(path)

    assert dataset.status == "error"
    for symbol in ("BRCA1", "CFTR"):
        bundle = _bundle(symbol, dataset)
        assert bundle["source_status"]["acmg_sf"]["status"] == "error"
        assert "acmg_secondary_finding" not in bundle["extra"]


@pytest.mark.parametrize("content", [None, b"", b"{not json", b"\xff\xfe"])
def test_a_missing_or_unreadable_list_is_an_error_for_every_gene(
    tmp_path: Path, content: bytes | None
) -> None:
    path = tmp_path / "acmg-sf-genes.json"
    if content is not None:
        path.write_bytes(content)

    dataset = gene_info_bulk_sources._load_acmg_sf_dataset(path)

    assert dataset.status == "error"
    assert str(path) in (dataset.message or "")
    status = _bundle("BRCA1", dataset)["source_status"]["acmg_sf"]
    assert status["status"] == "error"
    assert status["release"] is None
    GeneInfoSourceStatusOut(**status)
