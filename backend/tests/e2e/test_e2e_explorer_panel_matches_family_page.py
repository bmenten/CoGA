"""The Variant Explorer and the family page keep the same variants for a gene panel (E2E).

The family page keeps a variant in one of a panel's regions (the regions PanelApp gave for
the assembly, and each gene's locus in the gene reference) or annotated with one of its
genes, whatever the panel's size: a panel of genes with more than 1,000 regions, such as the
Mendeliome, was matched on gene symbols alone until the owner's decision of 2026-10-14. The
Variant Explorer read the panel's gene symbols only: a panel of regions only filtered
nothing, and a variant reached through a region alone was left out (CLIN-48).

A family is created through the PED upload under a new id each run, and a VEP-annotated VCF
goes in through the family small-variant upload. Gene-reference rows are seeded for the run
and removed at the end. The panels are made as CoGA makes them: a PanelApp import (PanelApp
itself answered by the test) for a panel of regions, and of genes and regions; the panel
API for a panel of genes. Each panel then goes, as the one query string the filter form
builds for both pages, to the family page's endpoint (unranked, and ranked) and to the
Variant Explorer's search and CSV export, narrowed to the variants' locus because other runs
and tests share the assembly. All answer with exactly the expected variants, and a panel
that does not exist is answered 404 by each. Everything runs over HTTP through an in-process
``httpx.ASGITransport`` client, on one event loop (see test_e2e_api_contract.py for why).

Synthetic data only. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e``
job sets it.
"""

from __future__ import annotations

import csv
import io
from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

# Chromosome 9 around 137.1 Mb: no other test writes there, and the explorer counts every
# family of the assembly. 137,101,312 is a multiple of 65,536: the explorer's region join
# lists a region under the 64-kb windows it touches, and one deletion below starts in the
# window before the region's.
_LOCUS = {"chr": "9", "start": 137_100_000, "end": 137_300_000}
_SEEDED_GENE_SOURCE = "e2e-explorer-panel-regions"

_CSQ_FORMAT = "Allele|Consequence|IMPACT|SYMBOL|Gene|Feature_type|Feature|CANONICAL|gnomADe_AF"


def _csq(allele: str, consequence: str, impact: str, gene: str = "") -> str:
    """One VEP CSQ entry; an intergenic one has no gene and no transcript."""
    feature = ["Transcript", f"ENST_{gene}_1", "YES"] if gene else ["", "", ""]
    return "|".join([allele, consequence, impact, gene, f"ENSG_{gene}" if gene else "", *feature, "0.0001"])


# Annotated with the panel gene SYN48A.
_IN_GENE = "9-137100101-C-T"
# Annotated with SYN48B, inside the locus the gene reference gives SYN48LOC (as if the
# annotation used another symbol for that gene): reached by position only.
_IN_LOCUS = "9-137100201-C-T"
# A 5-base deletion from 137,101,310, in the window before the PanelApp region's, overlapping
# the region, which starts at 137,101,312.
_DELETION_INTO_REGION = "9-137101310-CTTTT-C"
# Intergenic, inside the PanelApp region.
_INTERGENIC_IN_REGION = "9-137101500-G-A"
# Annotated with SYN48E, outside every region and panel gene.
_OUTSIDE = "9-137200101-C-T"

_VARIANTS: dict[str, str] = {
    _IN_GENE: _csq("T", "missense_variant", "MODERATE", "SYN48A"),
    _IN_LOCUS: _csq("T", "missense_variant", "MODERATE", "SYN48B"),
    _DELETION_INTO_REGION: _csq("-", "frameshift_variant", "HIGH", "SYN48C"),
    _INTERGENIC_IN_REGION: _csq("A", "intergenic_variant", "MODIFIER"),
    _OUTSIDE: _csq("T", "missense_variant", "MODERATE", "SYN48E"),
}
_ALL = set(_VARIANTS)

# PanelApp's region entity, with GRCh38 coordinates.
_PANELAPP_REGION = {
    "entity_name": "SYN48_REGION",
    "confidence_level": "3",
    "chromosome": "9",
    "grch38_coordinates": [137_101_312, 137_101_900],
}

# The panels, and the variants both pages keep for each.
_EXPECTED: dict[str, set[str]] = {
    "regions only": {_DELETION_INTO_REGION, _INTERGENIC_IN_REGION},
    "genes and regions": {_IN_GENE, _DELETION_INTO_REGION, _INTERGENIC_IN_REGION},
    "a gene reached by its locus": {_IN_LOCUS},
    # SYN48A by its gene; SYN48LOC's variant by its locus, as for the smaller panel above (it
    # was matched on gene symbols alone, and left _IN_LOCUS out).
    "more than 1,000 regions": {_IN_GENE, _IN_LOCUS},
}
# Filler genes of the panel of more than 1,000 regions, one locus each on chromosome 21.
_FILLERS = 1_000

_VCF_HEADER = (
    "##fileformat=VCFv4.2\n"
    "##DeepVariant_version=1.10.0\n"
    "##reference=GRCh38\n"
    f'##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from Ensembl VEP. Format: {_CSQ_FORMAT}">\n'
    '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
    '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">\n'
    '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Read depth">\n'
    '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allele depths">\n'
)


def _vcf(sample: str) -> bytes:
    lines = [_VCF_HEADER + f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}"]
    for variant_id, csq in _VARIANTS.items():
        chrom, pos, ref, alt = variant_id.split("-")
        lines.append(f"{chrom}\t{pos}\t.\t{ref}\t{alt}\t50\tPASS\tCSQ={csq}\tGT:GQ:DP:AD\t0/1:40:30:15,15")
    return ("\n".join(lines) + "\n").encode()


def _panelapp_payload(panelapp_id: int, *, genes: list[str]) -> dict[str, Any]:
    return {
        "id": panelapp_id,
        "name": f"CLIN-48 e2e {panelapp_id}",
        "version": "1.0",
        "stats": {"number_of_genes": len(genes), "number_of_strs": 0, "number_of_regions": 1},
        "genes": [
            {"entity_name": gene, "confidence_level": "3", "gene_data": {"hgnc_symbol": gene}}
            for gene in genes
        ],
        "strs": [],
        "regions": [_PANELAPP_REGION],
    }


async def _seed_genes(assembly_id: str | None) -> int:
    """Seed (or, with None, remove) the gene-reference rows of the run, and return how many
    are left: SYN48LOC over the _IN_LOCUS variant, spelled "chr9" as some references spell
    it, and 1,000 filler genes on chromosome 21."""
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker

    async with get_postgres_sessionmaker()() as session:
        await session.execute(text("DELETE FROM genes WHERE source = :source"), {"source": _SEEDED_GENE_SOURCE})
        if assembly_id is not None:
            loci = [
                ("SYN48LOC", "chr9", 137_100_150, 137_100_250),
                *(
                    (f"CL48FILL{index:04d}", "21", 30_000_000 + 1_000 * index, 30_000_000 + 1_000 * index + 500)
                    for index in range(_FILLERS)
                ),
            ]
            await session.execute(
                text(
                    """
                    INSERT INTO genes (assembly_id, gene_id, hgnc_symbol, chr, start, "end",
                                       strand, biotype, description, source)
                    VALUES (CAST(:assembly_id AS uuid), :gene_id, :symbol, :chr, :start, :end,
                            1, 'protein_coding', 'E2E explorer panel regions', :source)
                    """
                ),
                [
                    {
                        "assembly_id": assembly_id,
                        "gene_id": f"E2E_CL48_{symbol}",
                        "symbol": symbol,
                        "chr": chrom,
                        "start": start,
                        "end": end,
                        "source": _SEEDED_GENE_SOURCE,
                    }
                    for symbol, chrom, start, end in loci
                ],
            )
        left = (
            await session.execute(
                text("SELECT count(*) FROM genes WHERE source = :source"), {"source": _SEEDED_GENE_SOURCE}
            )
        ).scalar_one()
        await session.commit()
    return int(left)


def _ids(response: Any, field: str) -> Any:
    """The variant ids of an answer (``field`` of each row; "csv" for an export), or its
    status and body when it is not a 200."""
    if response.status_code != 200:
        try:
            return (response.status_code, response.json())
        except ValueError:
            return (response.status_code, response.text)
    if field == "csv":
        return {row["Variant ID"] for row in csv.DictReader(io.StringIO(response.text))}
    return {row[field] for row in response.json()["variants"]}


async def _exercise(family_id: str, panelapp_ids: dict[str, int]) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    async with get_postgres_sessionmaker()() as session:
        _admin, project_id, assembly_id = await _harness.ensure_e2e_project(session)

    proband = f"PROBAND_{family_id}"
    run = family_id.lower()
    out: dict[str, Any] = {"pages": {}, "cleanup": {}, "panels": {}}
    panels: dict[str, str] = {}
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        token = await _harness.login_admin_token(ac)
        ac.headers["Authorization"] = f"Bearer {token}"
        try:
            ped = await ac.post(
                "/api/ped/upload",
                params={"project_id": project_id},
                files={
                    "file": (f"{family_id}.ped", f"{family_id}\t{proband}\t0\t0\t2\t2\n".encode(), "text/plain")
                },
            )
            upload = await ac.post(
                f"/api/families/{family_id}/small-variants/upload",
                params={"overwrite": "false"},
                files={"file": (f"{family_id}.vcf", _vcf(proband), "text/plain")},
            )
            out["setup"] = {"ped": (ped.status_code, ped.text), "upload": (upload.status_code, upload.text)}
            await _seed_genes(assembly_id)

            # PanelApp's panels, through CoGA's import: one of regions only, one of a gene
            # and the region.
            for name in ("regions only", "genes and regions"):
                imported = await ac.post(
                    "/api/panels/import/panelapp",
                    json={
                        "panelapp_id": panelapp_ids[name],
                        "confidence_levels": ["3"],
                        "include_regions": True,
                        "include_strs": False,
                        "assembly": "GRCh38",
                        "name": f"e2e-clin48-{run}-{name}",
                    },
                )
                out["panels"][name] = (imported.status_code, imported.text)
                if imported.status_code == 201:
                    panel = imported.json()["panel"]
                    panels[name] = panel["_id"]
                    out["panels"][name] = {
                        "genes": panel["genes"],
                        "regions": [
                            (region["chr"], region["start"], region["end"], region["assembly_id"])
                            for region in panel["regions"]
                        ],
                    }
            # Panels of genes, through the panel API.
            fillers = [f"CL48FILL{index:04d}" for index in range(_FILLERS)]
            for name, genes in (
                ("a gene reached by its locus", ["SYN48LOC"]),
                ("more than 1,000 regions", ["SYN48A", "SYN48LOC", *fillers]),
            ):
                created = await ac.post("/api/panels/", json={"name": f"e2e-clin48-{run}-{name}", "genes": genes})
                out["panels"][name] = (created.status_code, created.text[:300])
                if created.status_code == 201:
                    panels[name] = created.json()["panel"]["_id"]
                    out["panels"][name] = {"genes": len(genes)}
            out["assembly_id"] = assembly_id

            explorer_scope = {"assembly_id": assembly_id, **_LOCUS}
            cases = {name: panels.get(name, str(uuid4())) for name in _EXPECTED}
            cases["a panel that does not exist"] = str(uuid4())
            for name, panel_id in cases.items():
                answers: dict[str, Any] = {}
                for label, prioritize in (("family page", "false"), ("family page, ranked", "true")):
                    response = await ac.get(
                        f"/api/families/{family_id}/small-variants",
                        params={"page": 1, "page_size": 100, "prioritize": prioritize, "panel_id": panel_id},
                    )
                    answers[label] = _ids(response, "_id")
                response = await ac.get(
                    "/api/variant-explorer/small-variants",
                    params={"page_size": 200, "panel_id": panel_id, **explorer_scope},
                )
                answers["explorer"] = _ids(response, "variant_id")
                response = await ac.get(
                    "/api/variant-explorer/small-variants/export",
                    params={"panel_id": panel_id, **explorer_scope},
                )
                answers["explorer export"] = _ids(response, "csv")
                out["pages"][name] = answers
            # Without a panel both pages list every variant of the locus.
            response = await ac.get(
                "/api/variant-explorer/small-variants", params={"page_size": 200, **explorer_scope}
            )
            out["explorer without a panel"] = _ids(response, "variant_id")
        finally:
            for name, panel_id in panels.items():
                removed = await ac.delete(f"/api/panels/{panel_id}")
                out["cleanup"][name] = removed.status_code
            out["seeded_genes_left"] = await _seed_genes(None)
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict[str, Any]:
    from backend.app.services import panel_metadata_service, raw_import_files_pg
    from backend.tests.e2e import _harness

    base = 900_000_000 + uuid4().int % 90_000_000
    panelapp_ids = {"regions only": base, "genes and regions": base + 1}
    payloads = {
        panelapp_ids["regions only"]: _panelapp_payload(panelapp_ids["regions only"], genes=[]),
        panelapp_ids["genes and regions"]: _panelapp_payload(panelapp_ids["genes and regions"], genes=["SYN48A"]),
    }

    async def panelapp(panelapp_id: int, *, version: str | None = None) -> dict[str, Any]:
        return payloads[int(panelapp_id)]

    mp = pytest.MonkeyPatch()
    # The upload keeps a managed copy of each file; keep it out of the repository's data/.
    mp.setattr(raw_import_files_pg, "DATA_DIR", tmp_path_factory.mktemp("explorer_panel_upload"))
    # PanelApp itself is not called: the import reads the test's panels.
    mp.setattr(panel_metadata_service, "fetch_panelapp_panel", panelapp)
    try:
        return _harness.run_async(lambda: _exercise(f"CL48_{uuid4().hex[:8].upper()}", panelapp_ids))
    finally:
        mp.undo()


def test_the_family_its_variants_and_the_panels_are_stored(run: dict[str, Any]) -> None:
    assert run["setup"]["ped"][0] == 200, run["setup"]["ped"]
    assert run["setup"]["upload"][0] == 200, run["setup"]["upload"]
    # PanelApp's region is stored for the e2e assembly, so the family's own assembly reads it.
    for name in ("regions only", "genes and regions"):
        panel = run["panels"][name]
        assert isinstance(panel, dict), panel
        assert ("9", 137_101_312, 137_101_900, run["assembly_id"]) in panel["regions"], panel
    assert run["panels"]["regions only"]["genes"] == []
    assert run["panels"]["genes and regions"]["genes"] == ["SYN48A"]
    for name in ("a gene reached by its locus", "more than 1,000 regions"):
        assert isinstance(run["panels"][name], dict), run["panels"][name]
    # Without a panel the explorer lists every variant of the locus.
    assert run["explorer without a panel"] == _ALL


@pytest.mark.parametrize("panel", list(_EXPECTED))
def test_the_explorer_keeps_what_the_family_page_keeps_for_a_panel(run: dict[str, Any], panel: str) -> None:
    expected = _EXPECTED[panel]
    assert run["pages"][panel] == {
        "family page": expected,
        "family page, ranked": expected,
        "explorer": expected,
        "explorer export": expected,
    }


def test_a_panel_that_does_not_exist_is_answered_404_by_both_pages(run: dict[str, Any]) -> None:
    missing = (404, {"detail": "Panel not found"})
    assert run["pages"]["a panel that does not exist"] == {
        "family page": missing,
        "family page, ranked": missing,
        "explorer": missing,
        "explorer export": missing,
    }


def test_the_panels_and_seeded_genes_are_removed(run: dict[str, Any]) -> None:
    assert set(run["cleanup"].values()) == {204}, run["cleanup"]
    assert run["seeded_genes_left"] == 0
