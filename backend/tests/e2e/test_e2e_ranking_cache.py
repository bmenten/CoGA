"""#509 end-to-end: a cached prioritised ranking must not outlive the variant data.

Imports the golden trio into real Postgres + ClickHouse and opens the prioritised
small-variant view twice (the second open is a cache hit). It then deletes the family's
small variants through the admin data endpoint — a write path that, before #509, never
cleared the ranking cache, so the next open kept serving the old ranking (with the old
total over variants that no longer exist). The cache key now covers the family's
storage-level data version, so the next open must recompute over the emptied data.

The golden trio is re-imported at the end so any module that runs afterwards sees the
usual state. Everything runs in ONE event loop via an in-process ``httpx.ASGITransport``
client (see test_e2e_api_contract.py for why).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).parent / "fixtures" / "golden_trio"
FAMILY = "FAM_TRIO"
_PRIORITISED = {"prioritize": "true", "page": 1, "page_size": 100}


def _cap(resp) -> dict:
    out = {"status": resp.status_code, "text": resp.text}
    if resp.status_code < 400:
        out["json"] = resp.json()
    return out


async def _exercise_cache(root: Path) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.main import app
    from backend.app.services.clickhouse_variant_storage import (
        get_family_small_variant_data_version,
    )
    from backend.tests.e2e import _harness

    facts = await _harness.import_golden_trio(root)
    family_uuid = facts["family_uuid"]
    out: dict = {"facts": facts}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://e2e") as ac:
        token = await _harness.login_admin_token(ac)
        ac.headers["Authorization"] = f"Bearer {token}"
        url = f"/api/families/{FAMILY}/small-variants"

        out["first"] = _cap(await ac.get(url, params=_PRIORITISED))
        out["second"] = _cap(await ac.get(url, params=_PRIORITISED))
        out["version_before"] = await get_family_small_variant_data_version(
            _harness.ASSEMBLY, family_uuid
        )
        out["delete"] = _cap(
            await ac.delete(
                f"/api/admin/data/families/{FAMILY}/small_variants", params={"confirm": "true"}
            )
        )
        out["version_after"] = await get_family_small_variant_data_version(
            _harness.ASSEMBLY, family_uuid
        )
        out["after_delete"] = _cap(await ac.get(url, params=_PRIORITISED))

    # Restore the fixture state for any module that runs later.
    out["restored"] = await _harness.import_golden_trio(root)
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    if not (_FIXTURE / "manifest.yaml").exists():
        pytest.skip("golden_trio fixture missing; run scripts/generate_golden_trio.py")

    root = tmp_path_factory.mktemp("golden_ranking") / "FAM_TRIO"
    shutil.copytree(_FIXTURE, root)

    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(root.parent)])
    request.addfinalizer(mp.undo)

    snapshot = _harness.run_async(lambda: _exercise_cache(root))
    assert snapshot["facts"]["completed"] is True, snapshot["facts"]
    return snapshot


def test_first_open_computes_and_second_is_served_from_cache(run) -> None:
    first, second = run["first"], run["second"]
    assert first["status"] == 200, first["text"]
    assert second["status"] == 200, second["text"]
    assert first["json"]["ranking_cached"] is False
    assert first["json"]["variants"], "the golden trio should yield prioritised variants"
    assert second["json"]["ranking_cached"] is True
    # VariantOut serialises its id under the `_id` alias.
    assert [v["_id"] for v in second["json"]["variants"]] == [
        v["_id"] for v in first["json"]["variants"]
    ]


def test_admin_delete_moves_the_family_data_version(run) -> None:
    assert run["delete"]["status"] == 200, run["delete"]["text"]
    assert run["version_before"] != run["version_after"]


def test_prioritised_view_recomputes_after_the_delete(run) -> None:
    after = run["after_delete"]
    assert after["status"] == 200, after["text"]
    # Before #509 this was a cache hit: ranking_cached True, the old total, no variants.
    assert after["json"]["ranking_cached"] is False
    assert after["json"]["total"] == 0
    assert after["json"]["variants"] == []


def test_golden_trio_is_restored_for_later_modules(run) -> None:
    assert run["restored"]["completed"] is True
    assert run["restored"]["n_small_variants"] == run["facts"]["n_small_variants"]
