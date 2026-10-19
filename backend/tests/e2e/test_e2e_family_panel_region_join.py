"""The family search keeps what it kept for a gene panel's regions, on every path (E2E, ENG-15).

The family page kept a variant in one of a panel's regions by testing each of its rows against
every region (``arrayExists`` over the regions): 1.3 s at 500 regions and 2.1 s at 1,000 for a
family of about 190,000 rows. It now joins the family's own rows to the regions on 64-kb
windows (``clickhouse_variant_queries._small_family_region_condition``).
``integration/test_family_panel_regions_integration.py`` shows on synthetic rows that the two
keep the same rows; this shows it on the family search's SQL paths, through the API.

A trio is created through the PED upload under a new id each run, and a VEP-annotated VCF goes
in through the family small-variant upload: variants on each side of a region's first and last
base, deletions that end on a region's first base or one before it, one that starts in the
64-kb window before a region it reaches, variants in a region that spans several windows, a
compound-het pair, a homozygous variant and a gene both parents carry in. Two panels are made
as CoGA makes them: one of PanelApp regions only, through the PanelApp import (PanelApp itself
answered by the test), and one of two genes through the panel API, with gene-reference loci
seeded for the run (one spelled "chr7", one over variants whose annotation names another gene).
Each panel goes to the family search unranked, ranked, at a locus, with compound-het and
recessive pairing, with expanded carrier screening (unranked and ranked), as the genome-browser
track, and to the CSV export (unranked and ranked), once with the join and once with the
per-row test patched back in, over an emptied ranking cache each time. Every answer is the
same: the variants, their order, the pairs, the totals and flags, the scores and ranks, and the
exported file. Everything runs over HTTP through an in-process ``httpx.ASGITransport`` client,
on one event loop (see test_e2e_api_contract.py for why).

Synthetic data only. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job
sets it.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_WINDOW = 1 << 16
# A 64-kb window boundary on chromosome 7 (2001 windows in): no other test writes near it.
_B = 2001 * _WINDOW
_SEEDED_GENE_SOURCE = "e2e-family-panel-region-join"

_CSQ_FORMAT = "Allele|Consequence|IMPACT|SYMBOL|Gene|Feature_type|Feature|CANONICAL|gnomADe_AF"

# Genotypes (father, mother, proband).
_PATERNAL = ("0/1", "0/0", "0/1")
_MATERNAL = ("0/0", "0/1", "0/1")
_FATHER_ONLY = ("0/1", "0/0", "0/0")
_HOMOZYGOUS = ("0/1", "0/1", "1/1")

_DEL_8 = "C" + "A" * 7  # an 8-base REF
_DEL_11 = "C" + "A" * 10  # an 11-base REF


def _variant(position: int, ref: str = "C", alt: str = "T") -> str:
    return f"7-{position}-{ref}-{alt if len(ref) == 1 else 'C'}"


# The variants: id -> (annotated gene, genotypes). The regions are below.
_BEFORE_RA = _variant(_B - 2)
_ON_RA_START = _variant(_B, "G", "A")
_DELETION_INTO_RA = _variant(_B - 5, _DEL_8)  # bases B-5..B+2, from the window before RA's
_DELETION_BEFORE_RA = _variant(_B - 12, _DEL_8)  # bases B-12..B-5
_BEFORE_RB = _variant(_B + 999)
_ON_RB_START = _variant(_B + 1000)
_ON_RB_END = _variant(_B + 1500)
_AFTER_RB = _variant(_B + 1501)
_DELETION_ONTO_RB = _variant(_B + 990, _DEL_11)  # bases B+990..B+1000: RB's first base
_DELETION_SHORT_OF_RB = _variant(_B + 989, _DEL_11)  # bases B+989..B+999
_ON_RC = _variant(_B + 7000)  # RC is one base
_AFTER_RC = _variant(_B + 7001)
_INSIDE_RD = _variant(_B + 4 * _WINDOW)  # RD spans several windows
_ON_RD_END = _variant(_B + 5 * _WINDOW + 10)
_AFTER_RD = _variant(_B + 5 * _WINDOW + 11)
_PAIR_PATERNAL = _variant(_B + 20_010)
_PAIR_MATERNAL = _variant(_B + 20_050)
_PATERNAL_OUTSIDE_RE = _variant(_B + 20_500)
_HOMOZYGOUS_IN_RB = _variant(_B + 1200)
_CARRIER_FATHER = _variant(_B + 1300)
_CARRIER_MOTHER = _variant(_B + 1400)

_VARIANTS: dict[str, tuple[str, tuple[str, str, str]]] = {
    **{
        variant_id: ("ENG15REG", _PATERNAL)
        for variant_id in (
            _BEFORE_RA, _ON_RA_START, _DELETION_INTO_RA, _DELETION_BEFORE_RA, _BEFORE_RB,
            _ON_RB_START, _ON_RB_END, _AFTER_RB, _DELETION_ONTO_RB, _DELETION_SHORT_OF_RB,
            _ON_RC, _AFTER_RC, _INSIDE_RD, _ON_RD_END, _AFTER_RD,
        )
    },
    _PAIR_PATERNAL: ("ENG15CH", _PATERNAL),
    _PAIR_MATERNAL: ("ENG15CH", _MATERNAL),
    _PATERNAL_OUTSIDE_RE: ("ENG15CH", _PATERNAL),
    _HOMOZYGOUS_IN_RB: ("ENG15HOM", _HOMOZYGOUS),
    _CARRIER_FATHER: ("ENG15CAR", _FATHER_ONLY),
    _CARRIER_MOTHER: ("ENG15CAR", _MATERNAL),
}

# PanelApp's regions (GRCh38): name -> (start, end).
_PANELAPP_REGIONS = {
    "ENG15_RA": (_B, _B + 2),  # starts on a window boundary
    "ENG15_RB": (_B + 1000, _B + 1500),
    "ENG15_RC": (_B + 7000, _B + 7000),  # one base
    "ENG15_RD": (_B + 3 * _WINDOW - 10, _B + 5 * _WINDOW + 10),  # several windows
    "ENG15_RE": (_B + 20_000, _B + 20_100),
}
# The gene reference's loci of the gene panel's genes: symbol -> (chromosome, start, end).
# ENG15LOC lies over two variants annotated ENG15REG: they are reached by its locus only.
_SEEDED_LOCI = {
    "ENG15CH": ("7", _B + 20_000, _B + 20_600),
    "ENG15LOC": ("chr7", _B + 6990, _B + 7005),
}

# What the search without other filters keeps, per panel.
_EXPECTED: dict[str, set[str]] = {
    "PanelApp regions": {
        _ON_RA_START, _DELETION_INTO_RA, _ON_RB_START, _ON_RB_END, _DELETION_ONTO_RB, _ON_RC,
        _INSIDE_RD, _ON_RD_END, _PAIR_PATERNAL, _PAIR_MATERNAL, _HOMOZYGOUS_IN_RB,
        _CARRIER_FATHER, _CARRIER_MOTHER,
    },
    "genes and their loci": {_ON_RC, _AFTER_RC, _PAIR_PATERNAL, _PAIR_MATERNAL, _PATERNAL_OUTSIDE_RE},
}

# The family search's paths: name -> (endpoint, query parameters).
_PATHS: dict[str, tuple[str, dict[str, Any]]] = {
    "plain": ("list", {}),
    "plain, at a locus": ("list", {"chr": "7", "start": _B - 10, "end": _B + 7003, "overlap": "true"}),
    "ranked": ("list", {"prioritize": "true"}),
    "compound het": ("list", {"inheritance": "compound_het"}),
    "compound het, ranked": ("list", {"inheritance": "compound_het", "prioritize": "true"}),
    "recessive": ("list", {"inheritance": "recessive"}),
    "carrier screening": ("list", {"expanded_carrier_screening": "true"}),
    "carrier screening, ranked": ("list", {"expanded_carrier_screening": "true", "prioritize": "true"}),
    "track": (
        "list",
        {
            "track_mode": "true",
            "track_result_limit": 1000,
            "chr": "7",
            "start": _B - 100_000,
            "end": _B + 400_000,
            "overlap": "true",
        },
    ),
    "export": ("export", {}),
    "export, ranked": ("export", {"prioritize": "true"}),
}

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


def _call(genotype: str) -> str:
    depths = {"0/0": "30,0", "0/1": "15,15", "1/1": "0,30"}[genotype]
    return f"{genotype}:40:30:{depths}"


def _vcf(samples: tuple[str, str, str]) -> bytes:
    lines = [_VCF_HEADER + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + "\t".join(samples)]
    for variant_id, (gene, genotypes) in sorted(_VARIANTS.items(), key=lambda item: int(item[0].split("-")[1])):
        chrom, pos, ref, alt = variant_id.split("-")
        allele = "-" if len(ref) > 1 else alt
        impact = ("frameshift_variant", "HIGH") if len(ref) > 1 else ("missense_variant", "MODERATE")
        csq = "|".join([allele, impact[0], impact[1], gene, f"ENSG_{gene}", "Transcript", f"ENST_{gene}_1", "YES", "0.0001"])
        lines.append(
            f"{chrom}\t{pos}\t.\t{ref}\t{alt}\t50\tPASS\tCSQ={csq}\tGT:GQ:DP:AD\t"
            + "\t".join(_call(genotype) for genotype in genotypes)
        )
    return ("\n".join(lines) + "\n").encode()


def _panelapp_payload(panelapp_id: int) -> dict[str, Any]:
    return {
        "id": panelapp_id,
        "name": f"ENG-15 e2e {panelapp_id}",
        "version": "1.0",
        "stats": {"number_of_genes": 0, "number_of_strs": 0, "number_of_regions": len(_PANELAPP_REGIONS)},
        "genes": [],
        "strs": [],
        "regions": [
            {"entity_name": name, "confidence_level": "3", "chromosome": "7", "grch38_coordinates": [start, end]}
            for name, (start, end) in _PANELAPP_REGIONS.items()
        ],
    }


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
                            1, 'protein_coding', 'E2E family panel region join', :source)
                    """
                ),
                [
                    {
                        "assembly_id": assembly_id,
                        "gene_id": f"E2E_ENG15_{symbol}",
                        "symbol": symbol,
                        "chr": chrom,
                        "start": start,
                        "end": end,
                        "source": _SEEDED_GENE_SOURCE,
                    }
                    for symbol, (chrom, start, end) in _SEEDED_LOCI.items()
                ],
            )
        left = (
            await session.execute(
                text("SELECT count(*) FROM genes WHERE source = :source"), {"source": _SEEDED_GENE_SOURCE}
            )
        ).scalar_one()
        await session.commit()
    return int(left)


def _answer(response: Any, endpoint: str) -> Any:
    """What a page or an export answered, without what changes from one computation to the
    next (when a ranking was computed)."""
    if response.status_code != 200:
        return (response.status_code, response.text)
    if endpoint == "export":
        headers = {key: value for key, value in response.headers.items() if key.lower().startswith("x-coga-export")}
        return {"csv": response.text, "headers": headers}
    body = response.json()

    def variant(row: dict[str, Any]) -> tuple[str, Any]:
        return (str(row["_id"]), row.get("priority"))

    return {
        "variants": [variant(row) for row in body.get("variants") or []],
        "groups": [
            ([variant(row) for row in group.get("variants") or []], group.get("phase"), group.get("phase_evidence"))
            for group in body.get("variant_groups") or []
        ],
        **{
            key: body.get(key)
            for key in (
                "total",
                "total_is_estimated",
                "count_limit",
                "candidates_capped",
                "candidate_limit",
                "ranking_truncated",
                "ranking_cached",
            )
        },
    }


async def _exercise(family_id: str, panelapp_id: int) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services import clickhouse_family_variants, clickhouse_variant_queries
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    async with get_postgres_sessionmaker()() as session:
        _admin, project_id, assembly_id = await _harness.ensure_e2e_project(session)

    samples = (f"FATHER_{family_id}", f"MOTHER_{family_id}", f"PROBAND_{family_id}")
    ped = (
        f"{family_id}\t{samples[0]}\t0\t0\t1\t1\n"
        f"{family_id}\t{samples[1]}\t0\t0\t2\t1\n"
        f"{family_id}\t{samples[2]}\t{samples[0]}\t{samples[1]}\t1\t2\n"
    )
    run = family_id.lower()
    out: dict[str, Any] = {"answers": {}, "sql": {}, "panels": {}, "cleanup": {}}
    panels: dict[str, str] = {}

    # The family page's panel queries, as sent: the per-row test or the join.
    sent: list[str] = []
    execute = clickhouse_family_variants._execute_clickhouse

    async def recording_execute(query: str, params: dict[str, Any]) -> Any:
        if any(name.startswith("panel_region_") for name in params):
            sent.append(query)
        return await execute(query, params)

    def per_row_condition(context: Any, filters: Any) -> Any:
        return clickhouse_variant_queries._small_region_filter_condition

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e", timeout=120) as ac:
        token = await _harness.login_admin_token(ac)
        ac.headers["Authorization"] = f"Bearer {token}"
        try:
            ped_response = await ac.post(
                "/api/ped/upload",
                params={"project_id": project_id},
                files={"file": (f"{family_id}.ped", ped.encode(), "text/plain")},
            )
            upload = await ac.post(
                f"/api/families/{family_id}/small-variants/upload",
                params={"overwrite": "false"},
                files={"file": (f"{family_id}.vcf", _vcf(samples), "text/plain")},
            )
            out["setup"] = {
                "ped": (ped_response.status_code, ped_response.text),
                "upload": (upload.status_code, upload.text),
            }
            await _seed_genes(assembly_id)
            async with get_postgres_sessionmaker()() as session:
                family_uuid = str(
                    (
                        await session.execute(
                            text("SELECT id FROM families WHERE family_id = :family_id"), {"family_id": family_id}
                        )
                    ).scalar_one()
                )

            imported = await ac.post(
                "/api/panels/import/panelapp",
                json={
                    "panelapp_id": panelapp_id,
                    "confidence_levels": ["3"],
                    "include_regions": True,
                    "include_strs": False,
                    "assembly": "GRCh38",
                    "name": f"e2e-eng15-{run}-regions",
                },
            )
            out["panels"]["PanelApp regions"] = (imported.status_code, imported.text[:500])
            if imported.status_code == 201:
                panels["PanelApp regions"] = imported.json()["panel"]["_id"]
            created = await ac.post(
                "/api/panels/", json={"name": f"e2e-eng15-{run}-genes", "genes": sorted(_SEEDED_LOCI)}
            )
            out["panels"]["genes and their loci"] = (created.status_code, created.text[:500])
            if created.status_code == 201:
                panels["genes and their loci"] = created.json()["panel"]["_id"]

            async def clear_rankings() -> None:
                async with get_postgres_sessionmaker()() as session:
                    await session.execute(
                        text("DELETE FROM family_variant_ranking_cache WHERE family_id = CAST(:family AS uuid)"),
                        {"family": family_uuid},
                    )
                    await session.commit()

            async def answer(panel_id: str, endpoint: str, params: dict[str, Any]) -> Any:
                await clear_rankings()
                url = f"/api/families/{family_id}/small-variants"
                if endpoint == "export":
                    url += "/export"
                query = {"panel_id": panel_id, **params}
                if endpoint == "list":
                    query = {"page": 1, "page_size": 100, **query}
                return _answer(await ac.get(url, params=query), endpoint)

            with pytest.MonkeyPatch.context() as recording:
                recording.setattr(clickhouse_family_variants, "_execute_clickhouse", recording_execute)
                for condition in ("join", "per-row test"):
                    with pytest.MonkeyPatch.context() as per_row:
                        if condition == "per-row test":
                            # The queries recorded below show the patch took effect.
                            per_row.setattr(
                                clickhouse_variant_queries,
                                "_small_family_region_condition",
                                per_row_condition,
                                raising=False,
                            )
                        sent.clear()
                        for panel_name, panel_id in panels.items():
                            for path_name, (endpoint, params) in _PATHS.items():
                                out["answers"].setdefault(panel_name, {}).setdefault(path_name, {})[condition] = (
                                    await answer(panel_id, endpoint, params)
                                )
                        out["sql"][condition] = {
                            "queries": len(sent),
                            "joined": sum("AS v INNER JOIN (SELECT region.1 AS region_chrom" in query for query in sent),
                            "per row": sum("arrayExists((region_chrom, region_start, region_end)" in query for query in sent),
                        }
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

    panelapp_id = 910_000_000 + uuid4().int % 80_000_000

    async def panelapp(requested: int, *, version: str | None = None) -> dict[str, Any]:
        assert int(requested) == panelapp_id
        return _panelapp_payload(panelapp_id)

    mp = pytest.MonkeyPatch()
    # The upload keeps a managed copy of each file; keep it out of the repository's data/.
    mp.setattr(raw_import_files_pg, "DATA_DIR", tmp_path_factory.mktemp("family_panel_region_join_upload"))
    # PanelApp itself is not called: the import reads the test's panel.
    mp.setattr(panel_metadata_service, "fetch_panelapp_panel", panelapp)
    try:
        return _harness.run_async(lambda: _exercise(f"ENG15_{uuid4().hex[:8].upper()}", panelapp_id))
    finally:
        mp.undo()


def test_the_family_its_variants_and_the_panels_are_stored(run: dict[str, Any]) -> None:
    assert run["setup"]["ped"][0] == 200, run["setup"]["ped"]
    assert run["setup"]["upload"][0] == 200, run["setup"]["upload"]
    for name in _EXPECTED:
        assert run["panels"][name][0] == 201, run["panels"][name]


@pytest.mark.parametrize("panel", list(_EXPECTED))
def test_each_panel_keeps_the_variants_in_its_regions_and_genes(run: dict[str, Any], panel: str) -> None:
    plain = run["answers"][panel]["plain"]["join"]
    assert isinstance(plain, dict), plain
    assert {variant_id for variant_id, _priority in plain["variants"]} == _EXPECTED[panel]
    assert plain["total"] == len(_EXPECTED[panel])


@pytest.mark.parametrize("path", list(_PATHS))
@pytest.mark.parametrize("panel", list(_EXPECTED))
def test_the_join_answers_what_the_per_row_test_answered(run: dict[str, Any], panel: str, path: str) -> None:
    answers = run["answers"][panel][path]
    assert isinstance(answers["join"], dict), answers["join"]
    assert answers["join"] == answers["per-row test"]


def test_the_paths_find_pairs_carriers_and_the_track_s_variants(run: dict[str, Any]) -> None:
    # Not a vacuous comparison: each path keeps something, and the panels differ.
    regions, genes = run["answers"]["PanelApp regions"], run["answers"]["genes and their loci"]
    pair = [_PAIR_PATERNAL, _PAIR_MATERNAL]
    assert [sorted(v for v, _p in group[0]) for group in regions["compound het"]["join"]["groups"]] == [sorted(pair)]
    assert len(genes["compound het"]["join"]["groups"]) == 2
    assert {v for v, _p in regions["recessive"]["join"]["variants"]} == {_HOMOZYGOUS_IN_RB}
    assert {v for v, _p in regions["carrier screening"]["join"]["variants"]} == {
        *pair, _HOMOZYGOUS_IN_RB, _CARRIER_FATHER, _CARRIER_MOTHER,
    }
    assert {v for v, _p in regions["track"]["join"]["variants"]} == _EXPECTED["PanelApp regions"]
    assert {v for v, _p in regions["plain, at a locus"]["join"]["variants"]} == {
        _ON_RA_START, _DELETION_INTO_RA, _ON_RB_START, _ON_RB_END, _DELETION_ONTO_RB, _ON_RC,
        _HOMOZYGOUS_IN_RB, _CARRIER_FATHER, _CARRIER_MOTHER,
    }
    assert all(priority and priority.get("rank") for _v, priority in regions["ranked"]["join"]["variants"])
    exported = regions["export, ranked"]["join"]["csv"].splitlines()
    assert len(exported) == 1 + len(_EXPECTED["PanelApp regions"])


def test_the_family_page_sent_the_join_and_the_patch_the_per_row_test(run: dict[str, Any]) -> None:
    joined, per_row = run["sql"]["join"], run["sql"]["per-row test"]
    assert joined["queries"] > 0 and joined["joined"] == joined["queries"] and joined["per row"] == 0, joined
    assert per_row["queries"] > 0 and per_row["per row"] == per_row["queries"] and per_row["joined"] == 0, per_row


def test_the_panels_and_seeded_genes_are_removed(run: dict[str, Any]) -> None:
    assert set(run["cleanup"].values()) == {204}, run["cleanup"]
    assert run["seeded_genes_left"] == 0
