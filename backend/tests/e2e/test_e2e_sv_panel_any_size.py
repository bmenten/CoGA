"""The SV page keeps, for a gene panel of any size, the SVs named by its genes or in its regions (E2E).

The owner decided on 2026-10-20 that the structural-variant page follows the small-variant
rule (#897): a gene panel of any size, the Mendeliome included, keeps an SV whose annotation
names one of its genes or that overlaps one of its regions. Until then a panel of more than
1,000 regions was matched on the genes an SV's annotation names alone, and a smaller panel on
its regions alone; the genome view's track availability matched a panel's regions alone.

The family here is its own (a new id each run): a trio imported as a package, each member with
a HiFiCNV file whose records name their genes in VEP's CSQ. Two genes are seeded in the gene
reference for the run, SVPNLA with two transcript loci that chain and SVPNLB with one, and two
panels are made through the panel API: one of the two genes, and one of the two genes and
1,000 filler genes (a locus each on chromosome 21), a panel of more than 1,000 regions, as the
Mendeliome is. The son has a duplication in SVPNLB's locus naming it in lower case, a deletion
in SVPNLA's locus naming an earlier symbol, a CNV that starts on SVPNLB's last base and one a
base beyond it, a CNV stored with its END before its POS over SVPNLA's two loci where they
chain (no locus holds both its END and its POS) and a deletion elsewhere; the mother a deletion
5 kb before SVPNLB naming it (VEP's upstream gene); the father a deletion elsewhere.

Each panel goes to the SV list (``GET /families/{family_id}/structural-variants``) unranked and
ranked, to each member's genome track, to the CSV export unranked and ranked, and to the track
availability (``GET /families/{family_id}/track-availability``). Both panels keep the same
SVs on every path: the duplication, the deletion named otherwise, the CNV on the last base and
the mother's deletion beside SVPNLB. The panels and the seeded genes are removed afterwards.
Everything runs in ONE event loop, with an in-process ``httpx.ASGITransport`` client (see
test_e2e_api_contract.py for why).

Synthetic data only. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job
sets it.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_SEEDED_GENE_SOURCE = "e2e-sv-panel-any-size"

_HEADER = (
    "##fileformat=VCFv4.2\n"
    '##INFO=<ID=SVTYPE,Number=1,Type=String,Description="Type of the SV.">\n'
    '##INFO=<ID=SVLEN,Number=1,Type=Integer,Description="Length of the SV">\n'
    '##INFO=<ID=END,Number=1,Type=Integer,Description="End position">\n'
    '##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from Ensembl VEP. '
    'Format: Allele|Consequence|IMPACT|SYMBOL|Gene">\n'
    '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
    '##FORMAT=<ID=CN,Number=1,Type=Float,Description="Copy number">\n'
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSample0\n"
)

# The gene reference's loci of the panel's genes: symbol -> [(chromosome, start, end)].
_LOCI = {
    "SVPNLA": [("3", 8_000_000, 8_060_000), ("3", 8_040_000, 8_100_000)],
    "SVPNLB": [("chr2", 5_000_000, 5_050_000)],
}
# The filler genes of the panel of more than 1,000 regions: a locus each on chromosome 21,
# where the family has no SV.
_FILLERS = {f"SVPNLF{index:04d}": [("21", 30_000_000 + 1_000 * index, 30_000_500 + 1_000 * index)] for index in range(1_000)}

# Each member's records: (chromosome, POS, END, type, gene its CSQ names or None).
_Record = tuple[str, int, int, str, str | None]
_DUPLICATION = ("chr2", 5_010_000, 5_020_000, "DUP", "svpnlb")
_NAMED_OTHERWISE = ("chr3", 8_050_000, 8_055_000, "DEL", "OLDSVPNLA")
_ON_LAST_BASE = ("chr2", 5_050_000, 5_060_000, "DEL", None)
_A_BASE_BEYOND = ("chr2", 5_050_001, 5_060_000, "DUP", None)
_END_BEFORE_POS = ("chr3", 8_070_000, 8_030_000, "CNV", None)
_SON_ELSEWHERE = ("chr4", 1_000_000, 1_010_000, "DEL", "SVPNLOTHER")
_BESIDE_SVPNLB = ("chr2", 4_990_000, 4_995_000, "DEL", "SVPNLB")
_FATHER_ELSEWHERE = ("chr5", 2_000_000, 2_010_000, "DEL", "SVPNLOTHER")
_RECORDS: dict[str, list[_Record]] = {
    "SON": [_DUPLICATION, _NAMED_OTHERWISE, _ON_LAST_BASE, _A_BASE_BEYOND, _END_BEFORE_POS, _SON_ELSEWHERE],
    "MOTHER": [_BESIDE_SVPNLB],
    "FATHER": [_FATHER_ELSEWHERE],
}


def _locus(record: _Record) -> tuple[str, int]:
    return record[0].removeprefix("chr"), record[1]


# What both panels keep, on every path.
_KEPT = {_locus(record) for record in (_DUPLICATION, _NAMED_OTHERWISE, _ON_LAST_BASE, _BESIDE_SVPNLB)}
_EVERY_SV = {_locus(record) for records in _RECORDS.values() for record in records}

_PANELS = ("two genes", "more than 1,000 regions")
_PATHS: dict[str, tuple[str, dict[str, Any]]] = {
    "list": ("list", {}),
    "ranked": ("list", {"prioritize": "true"}),
    "export": ("export", {}),
    "export, ranked": ("export", {"prioritize": "true"}),
}


def _vcf(records: list[_Record]) -> str:
    lines = []
    for chrom, pos, end, sv_type, gene in records:
        csq = f";CSQ={sv_type}|feature_truncation|HIGH|{gene}|ENSG_{gene}" if gene else ""
        lines.append(
            f"{chrom}\t{pos}\t.\tN\t<{sv_type}>\t50\tPASS\tSVTYPE={sv_type};END={end};SVLEN={abs(end - pos)}{csq}"
            f"\tGT:CN\t0/1:{1 if sv_type == 'DEL' else 3}\n"
        )
    return _HEADER + "".join(lines)


def _write_package(root: Path, family_id: str) -> None:
    father, mother, son = (f"{role}_{family_id}" for role in ("FATHER", "MOTHER", "SON"))
    root.mkdir(parents=True)
    (root / "family.ped").write_text(
        f"{family_id}\t{father}\t0\t0\t1\t1\n{family_id}\t{mother}\t0\t0\t2\t1\n{family_id}\t{son}\t{father}\t{mother}\t1\t2\n",
        encoding="utf-8",
    )
    (root / "cnv").mkdir()
    entries = []
    for role, records in _RECORDS.items():
        sample = f"{role}_{family_id}"
        (root / "cnv" / f"{sample}.vcf").write_text(_vcf(records), encoding="utf-8")
        entries.append(f"      {sample}: {{vcf: cnv/{sample}.vcf}}")
    lines = ["schema_version: 1", f"family_id: {family_id}", "ped: family.ped", "datasets:", "  cnv:", "    per_sample:", *entries]
    (root / "manifest.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _seed_genes(assembly_id: str | None) -> int:
    """Seed (or, with None, remove) the run's gene-reference loci, and return how many are left."""
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker

    async with get_postgres_sessionmaker()() as session:
        await session.execute(text("DELETE FROM genes WHERE source = :source"), {"source": _SEEDED_GENE_SOURCE})
        if assembly_id is not None:
            await session.execute(
                text(
                    """
                    INSERT INTO genes (assembly_id, gene_id, hgnc_symbol, chr, start, "end",
                                       strand, biotype, description, source)
                    VALUES (CAST(:assembly_id AS uuid), :gene_id, :symbol, :chr, :start, :end,
                            1, 'protein_coding', 'E2E SV panel of any size', :source)
                    """
                ),
                [
                    {
                        "assembly_id": assembly_id,
                        "gene_id": f"E2E_SVPNL_{symbol}",
                        "symbol": symbol,
                        "chr": chrom,
                        "start": start,
                        "end": end,
                        "source": _SEEDED_GENE_SOURCE,
                    }
                    for symbol, loci in {**_LOCI, **_FILLERS}.items()
                    for chrom, start, end in loci
                ],
            )
        left = (
            await session.execute(
                text("SELECT count(*) FROM genes WHERE source = :source"), {"source": _SEEDED_GENE_SOURCE}
            )
        ).scalar_one()
        await session.commit()
    return int(left)


def _status(response: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"status": response.status_code, "text": response.text[:2000]}
    if response.status_code == 200 and "json" in response.headers.get("content-type", ""):
        out["json"] = response.json()
    return out


async def _exercise(base: Path, family_id: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        admin, project_id, assembly_id = await _harness.ensure_e2e_project(session)

    root = base / "packages" / family_id
    _write_package(root, family_id)
    async with sessionmaker() as session:
        result = await package_import.execute_family_package_import(
            session,
            folder_path=str(root),
            project_id=project_id,
            dry_run=False,
            user=admin,
            conflict_mode="cancel",
        )
    out: dict[str, Any] = {
        "import": {
            "completed": result.completed,
            "error": result.error,
            "datasets": {d.dataset_type: (d.status, d.message) for d in result.datasets},
        },
        "panels": {},
        "answers": {},
        "cleanup": {},
    }
    svs = f"/api/families/{family_id}/structural-variants"
    panels: dict[str, str] = {}
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e", timeout=120) as ac:
        token = await _harness.login_admin_token(ac)
        ac.headers["Authorization"] = f"Bearer {token}"
        try:
            await _seed_genes(assembly_id)
            run = family_id.lower()
            for name, genes in (("two genes", sorted(_LOCI)), ("more than 1,000 regions", [*sorted(_LOCI), *_FILLERS])):
                created = await ac.post("/api/panels/", json={"name": f"e2e-svpnl-{run}-{len(genes)}", "genes": genes})
                out["panels"][name] = (created.status_code, created.text[:500])
                if created.status_code == 201:
                    panels[name] = created.json()["panel"]["_id"]
                    out["panels"][name] = (201, len(created.json()["panel"]["regions"]))
            out["unfiltered"] = _status(await ac.get(svs, params={"page_size": 100}))
            for name, panel_id in panels.items():
                answers = out["answers"].setdefault(name, {})
                for path, (endpoint, params) in _PATHS.items():
                    url = svs if endpoint == "list" else f"{svs}/export"
                    query = {"panel_id": panel_id, **params}
                    if endpoint == "list":
                        query = {"page_size": 100, **query}
                    answers[path] = _status(await ac.get(url, params=query))
                # Each member's genome track over chromosome 2, as the genome view asks for it.
                for role in _RECORDS:
                    answers[f"track {role}"] = _status(
                        await ac.get(
                            svs,
                            params={
                                "panel_id": panel_id,
                                "track_mode": "true",
                                "sample": f"{role}_{family_id}",
                                "chr": "2",
                                "start": 4_900_000,
                                "end": 5_100_000,
                                "overlap": "true",
                                "page_size": 100,
                            },
                        )
                    )
                answers["track availability"] = _status(
                    await ac.get(
                        f"/api/families/{family_id}/track-availability",
                        params={"panel_id": panel_id, "include_small_variants": "false"},
                    )
                )
        finally:
            for name, panel_id in panels.items():
                out["cleanup"][name] = (await ac.delete(f"/api/panels/{panel_id}")).status_code
            out["seeded_genes_left"] = await _seed_genes(None)
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("sv_panel_any_size")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base / "packages")])
    try:
        return _harness.run_async(lambda: _exercise(base, f"SVPNL_{uuid4().hex[:8].upper()}"))
    finally:
        mp.undo()


def _listed(answer: dict[str, Any]) -> set[tuple[str, int]]:
    assert answer["status"] == 200, answer["text"]
    return {(variant["chr"].removeprefix("chr"), variant["start"]) for variant in answer["json"]["variants"]}


def _exported(answer: dict[str, Any]) -> set[tuple[str, int]]:
    assert answer["status"] == 200, answer["text"]
    return {(row["Chromosome"].removeprefix("chr"), int(row["Start"])) for row in csv.DictReader(io.StringIO(answer["text"]))}


def test_the_family_and_the_panels_are_stored(run: dict[str, Any]) -> None:
    assert run["import"]["completed"] is True, run["import"]
    assert run["import"]["datasets"]["cnv"][0] == "imported", run["import"]["datasets"]
    # A panel stores one locus per gene; the search adds each gene's loci in the gene
    # reference (SVPNLA's two).
    assert run["panels"]["two genes"] == (201, 2)
    status, regions = run["panels"]["more than 1,000 regions"]
    assert status == 201 and regions > 1_000
    # Without a panel the list holds every SV.
    assert _listed(run["unfiltered"]) == _EVERY_SV


@pytest.mark.parametrize("path", list(_PATHS))
@pytest.mark.parametrize("panel", _PANELS)
def test_each_panel_keeps_the_svs_named_by_its_genes_or_in_its_regions(run: dict[str, Any], panel: str, path: str) -> None:
    # Before: the panel of two genes left out the mother's deletion beside SVPNLB that names
    # it; the panel of more than 1,000 regions the son's deletion in SVPNLA's locus named
    # otherwise and his CNV on SVPNLB's last base.
    answer = run["answers"][panel][path]
    kept = _exported(answer) if path.startswith("export") else _listed(answer)
    assert kept == _KEPT


@pytest.mark.parametrize("panel", _PANELS)
def test_each_members_genome_track_draws_the_svs_the_panel_keeps(run: dict[str, Any], panel: str) -> None:
    answers = run["answers"][panel]
    assert _listed(answers["track SON"]) == {_locus(_DUPLICATION), _locus(_ON_LAST_BASE)}
    assert _listed(answers["track MOTHER"]) == {_locus(_BESIDE_SVPNLB)}
    assert _listed(answers["track FATHER"]) == set()


@pytest.mark.parametrize("panel", _PANELS)
def test_the_track_availability_offers_the_tracks_of_the_svs_the_panel_keeps(run: dict[str, Any], panel: str) -> None:
    # Before: the availability matched a panel's regions alone, so the mother's SV track was
    # not offered, although the panel keeps her SV.
    answer = run["answers"][panel]["track availability"]
    assert answer["status"] == 200, answer["text"]
    offered = {name.split("_", 1)[0]: sample["variants"] for name, sample in answer["json"]["samples"].items()}
    assert offered == {"SON": True, "MOTHER": True, "FATHER": False}


def test_the_panels_and_seeded_genes_are_removed(run: dict[str, Any]) -> None:
    assert set(run["cleanup"].values()) <= {200, 204}, run["cleanup"]
    assert run["seeded_genes_left"] == 0
