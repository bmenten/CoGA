"""CLIN-27 end-to-end: a narrower panel is served from a broader cached ranking only while
that ranking's panel holds what it held when the ranking was computed.

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

The cache rows are read back from the real table, so the record of the panel goes through
the jsonb round trip. The panels are removed at the end. Everything runs in ONE event loop
via an in-process ``httpx.ASGITransport`` client (see test_e2e_api_contract.py for why).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).parent / "fixtures" / "golden_trio"
FAMILY = "FAM_TRIO"


def _cap(resp) -> dict:
    out = {"status": resp.status_code, "text": resp.text}
    if resp.status_code < 400:
        body = resp.json()
        out["ranking_cached"] = body.get("ranking_cached")
        out["genes"] = sorted(str(variant.get("gene")) for variant in body.get("variants") or [])
    return out


async def _exercise(root: Path) -> dict:
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
        finally:
            for key, panel_id in panels.items():
                removed = await ac.delete(f"/api/panels/{panel_id}")
                out["cleanup"][key] = removed.status_code
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


def test_the_panels_are_removed(run) -> None:
    assert run["cleanup"] and set(run["cleanup"].values()) == {204}, run["cleanup"]
