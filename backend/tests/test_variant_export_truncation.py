"""#512 — a CSV export must never be silently cut at the page clamp, nor silently miss
the matches beyond a capped candidate read."""

from __future__ import annotations

import asyncio
import types

import pytest

from backend.app.core.csv_export import export_response_headers
from backend.app.services import clickhouse_family_variants as cfv


def _page(
    n_variants: int,
    *,
    ranking_truncated: bool = False,
    candidates_capped: bool = False,
    groups=(),
):
    return types.SimpleNamespace(
        variants=[types.SimpleNamespace(id=f"v{i}") for i in range(n_variants)],
        variant_groups=list(groups),
        ranking_truncated=ranking_truncated,
        candidates_capped=candidates_capped,
    )


def _capture(monkeypatch, name: str, page) -> dict:
    seen: dict = {}

    async def _fake(session, **kwargs):
        seen.update(kwargs)
        return page

    monkeypatch.setattr(cfv, name, _fake)
    return seen


@pytest.mark.parametrize(
    ("export", "page_fn"),
    [
        (cfv.export_family_small_variants, "get_family_small_variants_page"),
        (cfv.export_family_structural_variants, "get_family_structural_variants_page"),
    ],
)
def test_export_lifts_the_page_clamp_and_asks_for_one_row_more(monkeypatch, export, page_fn) -> None:
    seen = _capture(monkeypatch, page_fn, _page(3))
    out = asyncio.run(export(None, context=None, limit=25_000))
    # Before #512 the page function clamped this to 10,000 rows without a trace.
    assert seen["page_size"] == 25_001
    assert seen["max_page_size"] == 25_001
    assert seen["track_mode"] is False
    assert len(out.rows) == 3 and out.truncated is False and out.limit == 25_000


@pytest.mark.parametrize(
    ("export", "page_fn"),
    [
        (cfv.export_family_small_variants, "get_family_small_variants_page"),
        (cfv.export_family_structural_variants, "get_family_structural_variants_page"),
    ],
)
def test_export_beyond_the_cap_is_reported_truncated(monkeypatch, export, page_fn) -> None:
    _capture(monkeypatch, page_fn, _page(11))
    out = asyncio.run(export(None, context=None, limit=10))
    assert out.truncated is True
    assert len(out.rows) == 10


def test_prioritised_export_over_a_truncated_ranking_is_reported_truncated(monkeypatch) -> None:
    # The ranking only covers the candidate window, so the ranked file is incomplete.
    _capture(monkeypatch, "get_family_small_variants_page", _page(4, ranking_truncated=True))
    out = asyncio.run(cfv.export_family_small_variants(None, context=None, limit=50, prioritize=True))
    assert out.truncated is True


@pytest.mark.parametrize(
    ("export", "page_fn"),
    [
        (cfv.export_family_small_variants, "get_family_small_variants_page"),
        (cfv.export_family_structural_variants, "get_family_structural_variants_page"),
    ],
)
def test_export_over_a_capped_candidate_read_is_reported_truncated(monkeypatch, export, page_fn) -> None:
    # A compound-het / recessive / carrier search (or a Python-filtered SV search) reads a
    # capped candidate window: a match beyond the window is missing from the file even
    # though the file holds far fewer rows than the export cap.
    _capture(monkeypatch, page_fn, _page(4, candidates_capped=True))
    out = asyncio.run(export(None, context=None, limit=50))
    assert len(out.rows) == 4
    assert out.truncated is True
    assert out.truncated_reason == "candidate-limit"


def test_truncation_reason_distinguishes_the_row_cap_from_the_candidate_window(monkeypatch) -> None:
    _capture(monkeypatch, "get_family_small_variants_page", _page(11))
    assert asyncio.run(cfv.export_family_small_variants(None, context=None, limit=10)).truncated_reason == (
        "row-limit"
    )
    _capture(monkeypatch, "get_family_small_variants_page", _page(4, ranking_truncated=True))
    assert asyncio.run(
        cfv.export_family_small_variants(None, context=None, limit=50, prioritize=True)
    ).truncated_reason == "candidate-limit"
    _capture(monkeypatch, "get_family_small_variants_page", _page(4))
    assert asyncio.run(cfv.export_family_small_variants(None, context=None, limit=50)).truncated_reason is None


def test_compound_het_groups_are_flattened_into_the_export(monkeypatch) -> None:
    group = types.SimpleNamespace(variants=[types.SimpleNamespace(id="a"), types.SimpleNamespace(id="b")])
    _capture(monkeypatch, "get_family_small_variants_page", _page(1, groups=[group]))
    out = asyncio.run(cfv.export_family_small_variants(None, context=None, limit=50))
    assert [row.id for row in out.rows] == ["a", "b", "v0"]
    assert out.truncated is False


def test_export_headers_name_a_truncated_file_as_such() -> None:
    full = export_response_headers("family-F1-small-variants", rows=12, truncated=False, limit=50_000)
    assert full["Content-Disposition"] == 'attachment; filename="family-F1-small-variants.csv"'
    assert full["X-CoGA-Export-Truncated"] == "false"
    assert full["X-CoGA-Export-Rows"] == "12"

    cut = export_response_headers("family-F1-small-variants", rows=50_000, truncated=True, limit=50_000)
    assert cut["Content-Disposition"] == (
        'attachment; filename="family-F1-small-variants-TRUNCATED-first-50000.csv"'
    )
    assert cut["X-CoGA-Export-Truncated"] == "true"
    assert cut["X-CoGA-Export-Limit"] == "50000"


def test_export_headers_name_a_partial_search_as_such() -> None:
    # A file cut by the candidate window is not "the first N rows": its name and headers
    # must say the search itself was partial.
    cut = export_response_headers(
        "family-F1-small-variants", rows=37, truncated=True, limit=50_000, reason="candidate-limit"
    )
    assert cut["Content-Disposition"] == (
        'attachment; filename="family-F1-small-variants-TRUNCATED-partial-search.csv"'
    )
    assert cut["X-CoGA-Export-Truncated"] == "true"
    assert cut["X-CoGA-Export-Truncated-Reason"] == "candidate-limit"
    assert cut["X-CoGA-Export-Rows"] == "37"

    row_cut = export_response_headers(
        "family-F1-small-variants", rows=50_000, truncated=True, limit=50_000, reason="row-limit"
    )
    assert row_cut["X-CoGA-Export-Truncated-Reason"] == "row-limit"
    assert row_cut["Content-Disposition"].endswith('-TRUNCATED-first-50000.csv"')

    full = export_response_headers("family-F1-small-variants", rows=12, truncated=False, limit=50_000)
    assert full["X-CoGA-Export-Truncated-Reason"] == ""


def test_small_variant_export_route_reports_a_partial_search(monkeypatch) -> None:
    # The route carries the service's reason into the file name and headers the UI reads.
    from backend.app.routers import families_small_variants as route

    async def _context(*_a, **_k):
        return None

    async def _export(*_a, **_k):
        return cfv.VariantExport(rows=[], truncated_reason="candidate-limit", limit=50_000)

    monkeypatch.setattr(route, "build_family_metadata_context", _context)
    monkeypatch.setattr(route, "export_family_small_variants", _export)
    response = asyncio.run(
        route.export_family_small_variants_csv("F1", filters={}, session=None, user=None)
    )
    assert response.headers["x-coga-export-truncated"] == "true"
    assert response.headers["x-coga-export-truncated-reason"] == "candidate-limit"
    assert "TRUNCATED-partial-search.csv" in response.headers["content-disposition"]


def test_the_truncation_reason_header_is_exposed_to_a_split_origin_ui() -> None:
    from backend.app.core.csv_export import EXPORT_HEADERS

    assert "X-CoGA-Export-Truncated-Reason" in EXPORT_HEADERS
