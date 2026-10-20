"""A narrower panel served from a broader cached ranking, end to end.

CLIN-27: only while that ranking's panel holds what it held when the ranking was computed.
Imports the golden trio into real Postgres + ClickHouse and opens the prioritised
small-variant view with gene panels made through the panel API:

* a panel's ranking serves another panel with the same gene (its panel unchanged);
* the panel then gains a gene: the version moves, so the exact key misses, and the ranking
  cached for the first version must not serve the panel either (before CLIN-27 it did,
  without the new gene's variant);
* a row that recorded nothing of its panel (cached before rankings recorded it, made here
  by removing the record) is not used;
* a row whose panel was deleted is passed over instead of failing the request with 404;
* a ranking without a panel still serves a narrower panel.

CLIN-34 and CLIN-35: a served panel is what a fresh computation of that panel gives, here
the same panel opened again over an emptied cache. With gene-reference rows seeded for the
cases (the fixture ships none):

* served from the ranking without a panel, a panel with GENE_CH is numbered 1 and 2, as
  computed for itself, not by the broader ranking's ranks, in the list and in the CSV
  export's Rank column;
* a panel reaching only the first of GENE_CH's compound-het pair (through a gene seeded
  over that variant alone) is computed for itself, where the variant has no partner,
  instead of served with the pair's segregation;
* a panel of more than 1,000 regions (a Mendeliome stand-in) reaches the GENE_DENOVO
  variant by position (through the gene reference's other symbol for that gene) as a
  smaller panel does, and its ranking serves the smaller panel as the panel's own
  computation ranks it (owner decision 2026-10-14; it was matched on gene symbols alone,
  so the smaller panel had to be computed for itself);
* a panel imported from PanelApp, whose coordinates for GENE_PATH reach further than any
  gene or locus of the stand-in, is computed for itself, with the variants it reaches
  there, instead of served from the stand-in's ranking without them.

The cache rows are read back from the real table, so the record of the panel goes through
the jsonb round trip. PanelApp itself is answered by the test. The panels and the seeded
genes are removed at the end. Everything runs in ONE event loop via an in-process
``httpx.ASGITransport`` client (see test_e2e_api_contract.py for why).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import csv
import io
import shutil
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).parent / "fixtures" / "golden_trio"
FAMILY = "FAM_TRIO"
_SEEDED_GENE_SOURCE = "e2e-ranking-cache-sub-panel"
# Filler genes of the Mendeliome stand-in, one locus each: with them it has more than 1,000
# regions, as the real Mendeliome has.
_FILLERS = 1_000
# PanelApp's GRCh38 coordinates for GENE_PATH in the imported panel: over the variants at
# 1:2000 to 1:7000 of the golden trio, where the gene reference has no locus for it.
_PANELAPP_GENE_PATH = "1:1900-7100"


def _panelapp_payload(panelapp_id: int) -> dict:
    return {
        "id": panelapp_id,
        "name": f"CLIN-34 e2e {panelapp_id}",
        "version": "1.0",
        "stats": {"number_of_genes": 1, "number_of_strs": 0, "number_of_regions": 0},
        "genes": [
            {
                "entity_name": "GENE_PATH",
                "confidence_level": "3",
                "gene_data": {
                    "hgnc_symbol": "GENE_PATH",
                    "ensembl_genes": {"GRch38": {"90": {"location": _PANELAPP_GENE_PATH}}},
                },
            }
        ],
        "strs": [],
        "regions": [],
    }


def _cap(resp) -> dict:
    out = {"status": resp.status_code, "text": resp.text}
    if resp.status_code < 400:
        body = resp.json()
        variants = body.get("variants") or []
        out["ranking_cached"] = body.get("ranking_cached")
        out["genes"] = sorted(str(variant.get("gene")) for variant in variants)
        out["total"] = body.get("total")
        # The page as ranked: each variant in order, with its rank, scores and segregation.
        out["ranked"] = [
            (str(variant.get("_id")), variant.get("gene"), variant.get("priority"))
            for variant in variants
        ]
    return out


async def _seed_genes(assembly_id: str | None) -> int:
    """Seed (or, with None, remove) the gene-reference rows the CLIN-34/35 cases need, and
    return how many are left. A gene the reference names GENE_RENAMED over the GENE_DENOVO
    variant (1:3000), as if the annotation still used an earlier symbol; a gene overlapping
    the first of GENE_CH's pair (1:4000) and not the second (1:5000); and 1,000 filler genes
    on chromosome 2 for the Mendeliome stand-in."""
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker

    async with get_postgres_sessionmaker()() as session:
        await session.execute(
            text("DELETE FROM genes WHERE source = :source"), {"source": _SEEDED_GENE_SOURCE}
        )
        if assembly_id is not None:
            loci = [
                ("GENE_RENAMED", "1", 2900, 3100),
                ("GENE_CH_OVERLAP", "1", 3900, 4100),
                *(
                    (f"FILLER{index:04d}", "2", 10_000 * (index + 1), 10_000 * (index + 1) + 500)
                    for index in range(_FILLERS)
                ),
            ]
            await session.execute(
                text(
                    """
                    INSERT INTO genes (assembly_id, gene_id, hgnc_symbol, chr, start, "end",
                                       strand, biotype, description, source)
                    VALUES (CAST(:assembly_id AS uuid), :gene_id, :symbol, :chr, :start, :end,
                            1, 'protein_coding', 'E2E ranking-cache sub-panel', :source)
                    """
                ),
                [
                    {
                        "assembly_id": assembly_id,
                        "gene_id": f"E2E_SUB_PANEL_{symbol}",
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
                text("SELECT count(*) FROM genes WHERE source = :source"),
                {"source": _SEEDED_GENE_SOURCE},
            )
        ).scalar_one()
        await session.commit()
    return int(left)


async def _exercise(root: Path, panelapp_id: int) -> dict:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.tests.e2e import _harness

    facts = await _harness.import_golden_trio(root)
    family_uuid = facts["family_uuid"]
    out: dict = {"facts": facts, "cleanup": {}}
    run = uuid.uuid4().hex[:8]
    panels: dict[str, str] = {}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://e2e") as ac:
        token = await _harness.login_admin_token(ac)
        ac.headers["Authorization"] = f"Bearer {token}"
        url = f"/api/families/{FAMILY}/small-variants"

        async def make_panel(key: str, *genes: str) -> None:
            created = await ac.post(
                "/api/panels/", json={"name": f"e2e-sub-panel-{run}-{key}", "genes": list(genes)}
            )
            assert created.status_code == 201, created.text
            panels[key] = created.json()["panel"]["_id"]

        async def prioritised(key: str | None) -> dict:
            params: dict = {"prioritize": "true", "page": 1, "page_size": 100}
            if key is not None:
                params["panel_id"] = panels[key]
            return _cap(await ac.get(url, params=params))

        async def clear_cache() -> None:
            async with get_postgres_sessionmaker()() as session:
                await session.execute(
                    text(
                        "DELETE FROM family_variant_ranking_cache "
                        "WHERE family_id = CAST(:family_uuid AS uuid)"
                    ),
                    {"family_uuid": family_uuid},
                )
                await session.commit()

        async def fresh(key: str) -> dict:
            """The panel computed for itself: opened over an emptied cache."""
            await clear_cache()
            return await prioritised(key)

        try:
            await make_panel("broad", "GENE_PATH")
            out["broad_first"] = await prioritised("broad")
            out["broad_again"] = await prioritised("broad")

            await make_panel("same_gene", "GENE_PATH")
            out["same_gene_from_broad"] = await prioritised("same_gene")

            edit = await ac.put(
                f"/api/panels/{panels['broad']}", json={"genes": ["GENE_PATH", "GENE_DENOVO"]}
            )
            out["edit"] = {"status": edit.status_code, "text": edit.text}
            out["broad_after_edit"] = await prioritised("broad")

            # Rows cached before rankings recorded their panel: take the record away.
            async with get_postgres_sessionmaker()() as session:
                stripped = await session.execute(
                    text(
                        """
                        UPDATE family_variant_ranking_cache
                        SET provenance = provenance - 'panel_members'
                        WHERE family_id = CAST(:family_uuid AS uuid) AND panel_id = :panel_id
                        """
                    ),
                    {"family_uuid": family_uuid, "panel_id": panels["broad"]},
                )
                out["stripped_rows"] = stripped.rowcount
                await session.commit()
            await make_panel("denovo", "GENE_DENOVO")
            out["denovo_unrecorded"] = await prioritised("denovo")

            # The panel of a recorded row is deleted; another panel over its gene opens.
            deleted = await ac.delete(f"/api/panels/{panels.pop('denovo')}")
            out["delete"] = {"status": deleted.status_code, "text": deleted.text}
            await make_panel("denovo_again", "GENE_DENOVO")
            out["after_delete"] = await prioritised("denovo_again")

            out["no_panel"] = await prioritised(None)
            await make_panel("indel", "GENE_INDEL")
            out["indel_from_no_panel"] = await prioritised("indel")

            # CLIN-34 and CLIN-35: each case starts from an empty cache, opens the broader
            # ranking, then the panel, then the panel again over an emptied cache (fresh).
            await _seed_genes(facts["assembly_id"])

            await clear_cache()
            out["ranks_broad"] = await prioritised(None)
            await make_panel("ch", "GENE_CH")
            out["ranks_served"] = await prioritised("ch")
            # The CSV export reads the same ranking: its Rank column.
            exported = await ac.get(
                f"/api/families/{FAMILY}/small-variants/export",
                params={"prioritize": "true", "panel_id": panels["ch"]},
            )
            out["ranks_csv"] = {
                "status": exported.status_code,
                "text": exported.text,
                "ranks": [row["Rank"] for row in csv.DictReader(io.StringIO(exported.text))]
                if exported.status_code == 200
                else None,
            }
            out["ranks_fresh"] = await fresh("ch")

            await clear_cache()
            out["split_broad"] = await prioritised(None)
            await make_panel("ch_overlap", "GENE_CH_OVERLAP")
            out["split_served"] = await prioritised("ch_overlap")
            out["split_fresh"] = await fresh("ch_overlap")

            await clear_cache()
            fillers = [f"FILLER{index:04d}" for index in range(_FILLERS)]
            await make_panel("mendeliome", "GENE_PATH", "GENE_RENAMED", *fillers)
            out["mendeliome"] = await prioritised("mendeliome")
            await make_panel("renamed", "GENE_PATH", "GENE_RENAMED")
            out["renamed_served"] = await prioritised("renamed")
            out["renamed_fresh"] = await fresh("renamed")

            # A panel of GENE_PATH imported from PanelApp, whose coordinates for the gene
            # reach variants no gene or locus of the stand-in reaches.
            imported = await ac.post(
                "/api/panels/import/panelapp",
                json={
                    "panelapp_id": panelapp_id,
                    "confidence_levels": ["3"],
                    "include_regions": False,
                    "include_strs": False,
                    "assembly": "GRCh38",
                    "name": f"e2e-sub-panel-{run}-panelapp",
                },
            )
            out["panelapp_import"] = {"status": imported.status_code, "text": imported.text[:500]}
            if imported.status_code == 201:
                panels["panelapp"] = imported.json()["panel"]["_id"]
                await clear_cache()
                out["mendeliome_again"] = await prioritised("mendeliome")
                out["panelapp_served"] = await prioritised("panelapp")
                out["panelapp_fresh"] = await fresh("panelapp")
        finally:
            for key, panel_id in panels.items():
                removed = await ac.delete(f"/api/panels/{panel_id}")
                out["cleanup"][key] = removed.status_code
            out["seeded_genes_left"] = await _seed_genes(None)
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.app.services import panel_metadata_service
    from backend.tests.e2e import _harness

    if not (_FIXTURE / "manifest.yaml").exists():
        pytest.fail("golden_trio fixture missing: it is committed, so restore it (scripts/generate_golden_trio.py rebuilds it)")

    root = tmp_path_factory.mktemp("golden_sub_panel") / "FAM_TRIO"
    shutil.copytree(_FIXTURE, root)
    panelapp_id = 930_000_000 + uuid.uuid4().int % 60_000_000

    async def panelapp(requested: int, *, version: str | None = None) -> dict:
        assert int(requested) == panelapp_id
        return _panelapp_payload(panelapp_id)

    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(root.parent)])
    # PanelApp itself is not called: the import reads the test's panel.
    mp.setattr(panel_metadata_service, "fetch_panelapp_panel", panelapp)
    request.addfinalizer(mp.undo)

    snapshot = _harness.run_async(lambda: _exercise(root, panelapp_id))
    assert snapshot["facts"]["completed"] is True, snapshot["facts"]
    return snapshot


def _served(page: dict, *, cached: bool, genes: list[str]) -> None:
    assert page["status"] == 200, page["text"]
    assert page["ranking_cached"] is cached, page
    assert page["genes"] == genes, page


def test_a_panel_ranking_is_computed_then_served_from_cache(run) -> None:
    _served(run["broad_first"], cached=False, genes=["GENE_PATH"])
    _served(run["broad_again"], cached=True, genes=["GENE_PATH"])


def test_another_panel_is_served_from_a_ranking_whose_panel_is_unchanged(run) -> None:
    _served(run["same_gene_from_broad"], cached=True, genes=["GENE_PATH"])


def test_a_panel_that_gained_a_gene_is_not_served_from_its_earlier_ranking(run) -> None:
    assert run["edit"]["status"] == 200, run["edit"]["text"]
    # Before CLIN-27: served from the first version's ranking, without GENE_DENOVO.
    _served(run["broad_after_edit"], cached=False, genes=["GENE_DENOVO", "GENE_PATH"])


def test_a_row_without_a_record_of_its_panel_is_not_used(run) -> None:
    # Both rankings of the edited panel lose their record; the newer one would otherwise
    # serve the panel over GENE_DENOVO.
    assert run["stripped_rows"] == 2
    _served(run["denovo_unrecorded"], cached=False, genes=["GENE_DENOVO"])


def test_a_row_whose_panel_was_deleted_is_passed_over(run) -> None:
    assert run["delete"]["status"] == 204, run["delete"]["text"]
    # The lookup of a deleted panel's genes answers 404, which failed the request for
    # another panel when the deleted panel's ranking was a candidate.
    _served(run["after_delete"], cached=False, genes=["GENE_DENOVO"])


def test_a_ranking_without_a_panel_serves_a_narrower_panel(run) -> None:
    no_panel = run["no_panel"]
    assert no_panel["status"] == 200, no_panel["text"]
    assert no_panel["ranking_cached"] is False
    assert "GENE_INDEL" in no_panel["genes"]
    _served(run["indel_from_no_panel"], cached=True, genes=["GENE_INDEL"])


def _same_as_fresh(served: dict, fresh: dict) -> None:
    """The served panel is what the panel computed for itself gives: the same variants in
    the same order, with the same ranks, scores and segregation, and the same total."""
    assert served["status"] == 200, served["text"]
    assert fresh["status"] == 200, fresh["text"]
    assert fresh["ranking_cached"] is False, fresh
    assert served["ranked"] == fresh["ranked"]
    assert served["total"] == fresh["total"]


def test_a_served_panel_is_numbered_within_the_panel(run) -> None:
    broad = run["ranks_broad"]
    assert broad["status"] == 200, broad["text"]
    # In the ranking without a panel, other variants rank above the GENE_CH pair.
    assert [priority["rank"] for _id, gene, priority in broad["ranked"] if gene == "GENE_CH"] != [1, 2]

    served = run["ranks_served"]
    _served(served, cached=True, genes=["GENE_CH", "GENE_CH"])
    assert [priority["rank"] for _id, _gene, priority in served["ranked"]] == [1, 2]
    _same_as_fresh(served, run["ranks_fresh"])
    exported = run["ranks_csv"]
    assert exported["status"] == 200, exported["text"]
    assert exported["ranks"] == ["1", "2"]


def test_a_compound_het_pair_the_panel_splits_is_not_carried_over(run) -> None:
    broad = run["split_broad"]
    assert broad["status"] == 200, broad["text"]
    paired = [priority for _id, gene, priority in broad["ranked"] if gene == "GENE_CH"]
    assert [priority["segregation_modes"] for priority in paired] == [["compound_het"]] * 2

    # The panel reaches the first of the pair alone: computed for itself, it has no partner.
    served = run["split_served"]
    _served(served, cached=False, genes=["GENE_CH"])
    fresh = run["split_fresh"]
    alone = fresh["ranked"][0][2]
    assert alone["segregation_modes"] == []
    assert alone["segregation_weight"] < paired[0]["segregation_weight"]
    _same_as_fresh(served, fresh)


def test_the_mendeliome_reaches_a_variant_by_position_and_serves_the_panel_that_does(run) -> None:
    # Matched by position too, the stand-in of more than 1,000 regions keeps the GENE_DENOVO
    # variant inside GENE_RENAMED's locus. It was matched on gene symbols alone, its ranking
    # lacked the variant, and the smaller panel had to be computed for itself.
    _served(run["mendeliome"], cached=False, genes=["GENE_DENOVO", "GENE_PATH"])
    served = run["renamed_served"]
    _served(served, cached=True, genes=["GENE_DENOVO", "GENE_PATH"])
    _same_as_fresh(served, run["renamed_fresh"])


def test_a_variant_a_panel_reaches_by_panelapp_s_coordinates_is_not_lost_to_a_broader_ranking(run) -> None:
    # The imported panel holds GENE_PATH, which the stand-in holds too, but PanelApp's
    # coordinates for it reach variants of other genes that no gene or locus of the stand-in
    # reaches: the stand-in's ranking lacks them, so the panel is computed for itself.
    assert run["panelapp_import"]["status"] == 201, run["panelapp_import"]["text"]
    _served(run["mendeliome_again"], cached=False, genes=["GENE_DENOVO", "GENE_PATH"])
    reached = ["BRCA2", "GENE_CH", "GENE_CH", "GENE_DENOVO", "GENE_INDEL", "GENE_PATH"]
    served = run["panelapp_served"]
    _served(served, cached=False, genes=reached)
    _same_as_fresh(served, run["panelapp_fresh"])


def test_the_panels_and_seeded_genes_are_removed(run) -> None:
    assert run["cleanup"] and set(run["cleanup"].values()) == {204}, run["cleanup"]
    assert run["seeded_genes_left"] == 0
