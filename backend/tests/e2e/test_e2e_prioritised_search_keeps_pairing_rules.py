"""A ranked search keeps what the same search keeps unranked, end to end (CLIN-44).

Imports the golden trio into real Postgres + ClickHouse and opens the family's small-variant
search with each inheritance mode and with expanded carrier screening (the trio's parents
are the couple), unranked and with *Phenotype prioritization*, through the API:

* the ranked search keeps the same single variants, the same compound-het pairs (with
  their phase and its evidence) and the same total as the unranked one. Before, a ranked
  compound-het or recessive search listed every variant the proband is heterozygous for
  (all eight), not the GENE_CH pair, and a ranked carrier screen every variant either
  parent carries, not GENE_CH's two;
* its ranks number the variants it keeps, a pair listed at its better-ranked variant;
* opened again it is served from the cache, unchanged;
* its CSV export holds the same variants as the unranked export, with their ranks.

The modes the SQL decides for each variant (de novo / dominant, recessive homozygous,
X-linked) and the search without a mode are opened as guards. Everything runs in ONE event
loop via an in-process ``httpx.ASGITransport`` client (see test_e2e_api_contract.py for why).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import csv
import io
import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).parent / "fixtures" / "golden_trio"
FAMILY = "FAM_TRIO"

# The golden trio's compound-het pair in GENE_CH: one variant from each parent.
_GENE_CH_PAIR = ["1-4000-T-C", "1-5000-A-T"]

SEARCHES: dict[str, list[tuple[str, str]]] = {
    "compound_het": [("inheritance", "compound_het")],
    "recessive": [("inheritance", "recessive")],
    "carrier_screening": [("expanded_carrier_screening", "true")],
    "carrier_screening_compound_het": [
        ("expanded_carrier_screening", "true"),
        ("inheritance", "compound_het"),
    ],
    "de_novo_dominant": [("inheritance", "de_novo_dominant")],
    "recessive_homozygous": [("inheritance", "recessive_homozygous")],
    "x_linked": [("inheritance", "x_linked")],
    "no_inheritance": [],
}


def _page(resp) -> dict:
    out: dict = {"status": resp.status_code, "text": resp.text}
    if resp.status_code >= 400:
        return out
    body = resp.json()
    # VariantOut serialises its id under the `_id` alias.
    out["variants"] = sorted(str(variant["_id"]) for variant in body.get("variants") or [])
    out["pairs"] = sorted(
        (
            [str(variant["_id"]) for variant in group.get("variants") or []],
            group.get("phase"),
            group.get("phase_evidence"),
        )
        for group in body.get("variant_groups") or []
    )
    out["total"] = body.get("total")
    out["flags"] = {
        key: body.get(key)
        for key in ("total_is_estimated", "candidates_capped", "ranking_truncated", "candidate_limit")
    }
    out["ranking_cached"] = body.get("ranking_cached")
    out["ranks"] = {
        str(variant["_id"]): (variant.get("priority") or {}).get("rank")
        for variant in [
            *(variant for group in body.get("variant_groups") or [] for variant in group.get("variants") or []),
            *(body.get("variants") or []),
        ]
    }
    out["group_ranks"] = [
        [(variant.get("priority") or {}).get("rank") for variant in group.get("variants") or []]
        for group in body.get("variant_groups") or []
    ]
    return out


async def _exercise(root: Path) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.main import app
    from backend.tests.e2e import _harness

    facts = await _harness.import_golden_trio(root)
    out: dict = {"facts": facts, "searches": {}}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://e2e") as ac:
        token = await _harness.login_admin_token(ac)
        ac.headers["Authorization"] = f"Bearer {token}"
        url = f"/api/families/{FAMILY}/small-variants"

        for name, filters in SEARCHES.items():
            base = [*filters, ("page", "1"), ("page_size", "100")]
            out["searches"][name] = {
                "unranked": _page(await ac.get(url, params=[*base, ("prioritize", "false")])),
                "ranked": _page(await ac.get(url, params=[*base, ("prioritize", "true")])),
                "ranked_again": _page(await ac.get(url, params=[*base, ("prioritize", "true")])),
            }

        exports: dict = {}
        for prioritize in ("false", "true"):
            exported = await ac.get(
                f"{url}/export",
                params=[("inheritance", "compound_het"), ("prioritize", prioritize)],
            )
            rows = (
                list(csv.DictReader(io.StringIO(exported.text)))
                if exported.status_code == 200
                else []
            )
            exports[prioritize] = {
                "status": exported.status_code,
                "text": exported.text,
                "variants": sorted(
                    f"{row['Chromosome']}-{row['Start']}-{row['Ref']}-{row['Alt']}" for row in rows
                ),
                "ranks": sorted(row.get("Rank") or "" for row in rows),
            }
        out["exports"] = exports
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    if not (_FIXTURE / "manifest.yaml").exists():
        pytest.fail("golden_trio fixture missing: it is committed, so restore it (scripts/generate_golden_trio.py rebuilds it)")

    root = tmp_path_factory.mktemp("golden_prioritised_pairing") / "FAM_TRIO"
    shutil.copytree(_FIXTURE, root)

    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(root.parent)])
    request.addfinalizer(mp.undo)

    snapshot = _harness.run_async(lambda: _exercise(root))
    assert snapshot["facts"]["completed"] is True, snapshot["facts"]
    return snapshot


def _kept(page: dict) -> dict:
    assert page["status"] == 200, page["text"]
    return {key: page[key] for key in ("variants", "pairs", "total", "flags")}


@pytest.mark.parametrize("name", list(SEARCHES))
def test_the_ranked_search_keeps_what_the_unranked_search_keeps(run, name: str) -> None:
    search = run["searches"][name]
    unranked = _kept(search["unranked"])
    assert _kept(search["ranked"]) == unranked
    assert search["ranked"]["ranking_cached"] is False
    # Opened again, served from the cache, unchanged.
    assert _kept(search["ranked_again"]) == unranked
    assert search["ranked_again"]["ranking_cached"] is True
    assert search["ranked_again"]["ranks"] == search["ranked"]["ranks"]


@pytest.mark.parametrize("name", ["compound_het", "recessive", "carrier_screening_compound_het"])
def test_a_ranked_pairing_search_keeps_the_gene_ch_pair_alone(run, name: str) -> None:
    ranked = run["searches"][name]["ranked"]
    assert ranked["pairs"] == [(_GENE_CH_PAIR, "trans", "segregation")], ranked
    assert ranked["variants"] == []
    assert ranked["total"] == 1
    # The ranks number the pair's two variants only, the pair at its better one.
    assert sorted(ranked["ranks"].values()) == [1, 2]
    assert [sorted(ranks) for ranks in ranked["group_ranks"]] == [[1, 2]]


def test_a_ranked_carrier_screen_keeps_the_gene_both_parents_carry_in(run) -> None:
    ranked = run["searches"]["carrier_screening"]["ranked"]
    assert ranked["variants"] == _GENE_CH_PAIR, ranked
    assert ranked["pairs"] == []
    assert sorted(ranked["ranks"].values()) == [1, 2]
    assert not ranked["flags"]["candidates_capped"]


def test_the_ranked_csv_export_holds_the_unranked_export_s_variants(run) -> None:
    unranked, ranked = run["exports"]["false"], run["exports"]["true"]
    assert unranked["status"] == 200, unranked["text"]
    assert ranked["status"] == 200, ranked["text"]
    assert ranked["variants"] == unranked["variants"] == _GENE_CH_PAIR
    assert ranked["ranks"] == ["1", "2"]
