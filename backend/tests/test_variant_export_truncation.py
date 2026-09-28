"""#512 — a CSV export must never be silently cut at the page clamp."""

from __future__ import annotations

import asyncio
import types

import pytest

from backend.app.core.csv_export import export_response_headers
from backend.app.services import clickhouse_family_variants as cfv


def _page(n_variants: int, *, ranking_truncated: bool = False, groups=()):
    return types.SimpleNamespace(
        variants=[types.SimpleNamespace(id=f"v{i}") for i in range(n_variants)],
        variant_groups=list(groups),
        ranking_truncated=ranking_truncated,
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
