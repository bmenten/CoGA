"""The report's list of reported SVs holds every reported SV, however large the callset (E2E).

The report page lists its structural variants with a review-tag search
(``GET /structural-variants?review_tag=report``). A review filter keeps that search off the
native page, so it read the first rows of the callset up to the candidate cap (50,000) and
only then kept the reviewed ones: a reported SV past the cap was missing from the report,
while the signed record, which reads the reviews from Postgres, held it.

Over the golden trio (two NeedlR SVs) the cap is lowered to 1, so the window holds the first
SV only. The SV that sorts last is tagged ``report`` through the review API, and then:

* a search with a Python-side filter matching both SVs reads a capped window (the control:
  without the fix, the reported SV would fall outside it);
* the review-tag search, unranked and prioritised, returns the reported SV, with its total
  and no cap flag;
* both report lists answer the page size the report asks for (the API maximum).

The review is cleared afterwards. Everything runs in ONE event loop via an in-process
``httpx.ASGITransport`` client (see test_e2e_api_contract.py for why).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).parent / "fixtures" / "golden_trio"
FAMILY = "FAM_TRIO"
# frontend/src/pages/families/reportedListCompleteness.ts REPORT_LIST_PAGE_SIZE.
_REPORT_PAGE_SIZE = 10_000


def _cap(resp) -> dict:
    out = {"status": resp.status_code, "text": resp.text}
    if resp.status_code < 400:
        out["json"] = resp.json()
    return out


async def _collect(root: Path) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.main import app
    from backend.tests.e2e import _harness

    facts = await _harness.import_golden_trio(root)
    out: dict = {"facts": facts, "responses": {}}
    r = out["responses"]
    svs = f"/api/families/{FAMILY}/structural-variants"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://e2e") as ac:
        token = await _harness.login_admin_token(ac)
        ac.headers["Authorization"] = f"Bearer {token}"

        # A Python-side filter matching every SV: the capped read holds the first SV only.
        r["capped_control"] = _cap(await ac.get(svs, params={"min_length": 1, "page_size": 50}))
        listed = r["capped_control"].get("json", {}).get("variants", [])
        # The whole callset, in the order the capped read takes it.
        r["all"] = _cap(await ac.get(svs, params={"page_size": 50}))
        ordered = [variant["_id"] for variant in r["all"].get("json", {}).get("variants", [])]
        reported = ordered[-1] if ordered else None
        out["reported"] = reported
        out["in_window"] = [variant["_id"] for variant in listed]
        if reported is None:
            return out

        review_url = f"{svs}/{reported}/review"
        r["tag"] = _cap(await ac.put(review_url, json={"tags": ["report"], "note": "E2E: reported"}))
        try:
            report_params = {"review_tag": "report", "page_size": _REPORT_PAGE_SIZE}
            r["report_svs"] = _cap(await ac.get(svs, params=report_params))
            r["report_svs_ranked"] = _cap(await ac.get(svs, params={**report_params, "prioritize": True}))
            r["report_small"] = _cap(
                await ac.get(f"/api/families/{FAMILY}/small-variants", params=report_params)
            )
        finally:
            r["untag"] = _cap(await ac.put(review_url, json={"tags": [], "note": None}))
            r["after_untag"] = _cap(
                await ac.get(svs, params={"review_tag": "report", "page_size": _REPORT_PAGE_SIZE})
            )
    return out


@pytest.fixture(scope="module")
def snap(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    if not (_FIXTURE / "manifest.yaml").exists():
        pytest.fail("golden_trio fixture missing: it is committed, so restore it (scripts/generate_golden_trio.py rebuilds it)")

    root = tmp_path_factory.mktemp("golden_report_lists") / FAMILY
    shutil.copytree(_FIXTURE, root)

    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(root.parent)])
    # The candidate window holds one SV: the golden trio's second SV lies beyond it.
    mp.setattr("backend.app.services.clickhouse_family_variants._SV_NON_NATIVE_STRUCTURAL_CANDIDATE_CAP", 1)
    request.addfinalizer(mp.undo)

    out = _harness.run_async(lambda: _collect(root))
    assert out["facts"]["completed"] is True, out["facts"]
    return out


def _json(snap: dict, key: str) -> dict:
    resp = snap["responses"][key]
    assert resp["status"] == 200, resp
    return resp["json"]


def test_the_control_search_reads_a_capped_window(snap):
    # Without this the test would prove nothing: the reported SV must lie outside the
    # window a capped read takes.
    control = _json(snap, "capped_control")
    assert control["candidates_capped"] is True
    assert len(_json(snap, "all")["variants"]) == 2
    assert snap["reported"] not in snap["in_window"]


def test_the_reported_sv_is_tagged(snap):
    assert snap["responses"]["tag"]["status"] == 200, snap["responses"]["tag"]


@pytest.mark.parametrize("key", ["report_svs", "report_svs_ranked"])
def test_the_report_lists_the_reported_sv_beyond_the_cap(snap, key):
    page = _json(snap, key)
    assert [variant["_id"] for variant in page["variants"]] == [snap["reported"]]
    assert page["total"] == 1
    assert not page.get("candidates_capped")
    assert not page.get("ranking_truncated")
    assert not page.get("total_is_estimated")


def test_both_report_lists_accept_the_report_page_size(snap):
    # A page size above the API maximum is refused (422), which the report would show as a
    # failed list; the report asks for exactly the maximum.
    _json(snap, "report_svs")
    _json(snap, "report_small")


def test_the_review_is_cleared_afterwards(snap):
    assert snap["responses"]["untag"]["status"] == 200, snap["responses"]["untag"]
    assert _json(snap, "after_untag")["variants"] == []
