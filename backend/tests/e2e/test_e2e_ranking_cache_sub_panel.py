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
* a panel of more regions than the panel filter inlines (a Mendeliome stand-in) is
  matched on gene symbols alone, and a smaller panel that reaches the GENE_DENOVO variant
  by position only (through the gene reference's other symbol for that gene) is computed
  for itself, with that variant, instead of served from the stand-in's ranking without it.

The cache rows are read back from the real table, so the record of the panel goes through
the jsonb round trip. The panels and the seeded genes are removed at the end. Everything
runs in ONE event loop via an in-process ``httpx.ASGITransport`` client (see
test_e2e_api_contract.py for why).

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
    the first of GENE_CH's pair (1:4000) and not the second (1:5000); and as many filler
    genes on chromosome 2 as the panel filter inlines regions."""
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services.clickhouse_variant_queries import _PANEL_REGION_INLINE_LIMIT

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
                    for index in range(_PANEL_REGION_INLINE_LIMIT)
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


async def _exercise(root: Path) -> dict:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.app.services.clickhouse_variant_queries import _PANEL_REGION_INLINE_LIMIT
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
            fillers = [f"FILLER{index:04d}" for index in range(_PANEL_REGION_INLINE_LIMIT)]
            await make_panel("mendeliome", "GENE_PATH", "GENE_RENAMED", *fillers)
            out["mendeliome"] = await prioritised("mendeliome")
            await make_panel("renamed", "GENE_PATH", "GENE_RENAMED")
            out["renamed_served"] = await prioritised("renamed")
            out["renamed_fresh"] = await fresh("renamed")
        finally:
            for key, panel_id in panels.items():
                removed = await ac.delete(f"/api/panels/{panel_id}")
                out["cleanup"][key] = removed.status_code
            out["seeded_genes_left"] = await _seed_genes(None)
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    if not (_FIXTURE / "manifest.yaml").exists():
        pytest.fail("golden_trio fixture missing: it is committed, so restore it (scripts/generate_golden_trio.py rebuilds it)")

    root = tmp_path_factory.mktemp("golden_sub_panel") / "FAM_TRIO"
    shutil.copytree(_FIXTURE, root)

    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(root.parent)])
    request.addfinalizer(mp.undo)

    snapshot = _harness.run_async(lambda: _exercise(root))
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


def test_a_variant_the_panel_reaches_by_position_is_not_lost_to_a_ranking_on_symbols(run) -> None:
    # Matched on gene symbols alone: the GENE_DENOVO variant inside GENE_RENAMED's locus is
    # not in the stand-in's ranking.
    _served(run["mendeliome"], cached=False, genes=["GENE_PATH"])
    served = run["renamed_served"]
    _served(served, cached=False, genes=["GENE_DENOVO", "GENE_PATH"])
    _same_as_fresh(served, run["renamed_fresh"])


def test_the_panels_and_seeded_genes_are_removed(run) -> None:
    assert run["cleanup"] and set(run["cleanup"].values()) == {204}, run["cleanup"]
    assert run["seeded_genes_left"] == 0
