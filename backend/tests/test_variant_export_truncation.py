"""#512 — a CSV export must never be silently cut at the page clamp, nor silently miss
the matches beyond a capped candidate read; nor may the Variant Explorer's export at its
row cap or its tag / classification cap (DATA-2)."""

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


# --- the Variant Explorer export (DATA-2) ---------------------------------------------------
# It stopped at 50,000 rows, and searched at most 200,000 ids of its tag / classification
# filter, without a word: no TRUNCATED file name, no X-CoGA-Export-* headers. It now says
# so, as the family exports do.


def _explorer_row(index: int):
    from backend.app.schemas import GlobalVariantRowOut

    return GlobalVariantRowOut(
        key=str(index), variant_id=f"1-{100 + index}-A-G", chr="1", pos=100 + index
    )


def _explorer_service(monkeypatch, *, matching: int) -> tuple[types.ModuleType, dict]:
    """The explorer service over one scope, where ``matching`` variants pass the filters;
    like the ClickHouse query, the row read returns at most the limit it is asked for."""
    from backend.app.services import variant_explorer_service as svc

    scope = svc.ExplorerScope(
        assembly_id="asm-1",
        assembly_name="GRCh38",
        project_ids=["11111111-1111-1111-1111-111111111111"],
    )
    seen: dict = {}

    async def _resolve_scope(session, user, assembly_id):
        return scope

    async def _fetch_variant_rows(session, *, limit, params, **_kwargs):
        seen["limit"] = limit
        seen["tag_variant_ids"] = params.get("tag_variant_ids")
        rows = [_explorer_row(index) for index in range(min(limit, matching))]
        return rows, [(0, 0, index) for index in range(len(rows))]

    monkeypatch.setattr(svc, "resolve_scope", _resolve_scope)
    monkeypatch.setattr(svc, "_fetch_variant_rows", _fetch_variant_rows)
    return svc, seen


class _ReviewSession:
    """A session whose tag / classification query returns ``variant_ids`` in variant_id
    order, as many as its LIMIT takes (all of them without one), as Postgres does."""

    def __init__(self, variant_ids: list[str]) -> None:
        self.variant_ids = sorted(variant_ids)

    async def execute(self, statement, params=None):
        limit = (params or {}).get("limit")
        ids = self.variant_ids if limit is None else self.variant_ids[:limit]
        return types.SimpleNamespace(all=lambda: [(variant_id,) for variant_id in ids])


def _export(svc, session=None, **kwargs):
    filters = kwargs.pop("filters", None) or svc.GlobalVariantFilters()
    return asyncio.run(svc.export_global_small_variants(session, user=None, filters=filters, **kwargs))


def test_explorer_export_beyond_the_cap_is_reported_truncated(monkeypatch) -> None:
    svc, seen = _explorer_service(monkeypatch, matching=11)
    export = _export(svc, limit=10)
    # One row more than the cap is read, so a larger result is known to be larger.
    assert seen["limit"] == 11
    assert len(export.rows) == 10
    assert export.truncated is True
    assert export.truncated_reason == "row-limit"
    assert (export.limit, export.assembly_name) == (10, "GRCh38")


@pytest.mark.parametrize("matching", [0, 3, 10])
def test_explorer_export_within_the_cap_is_complete(monkeypatch, matching) -> None:
    svc, _seen = _explorer_service(monkeypatch, matching=matching)
    export = _export(svc, limit=10)
    assert len(export.rows) == matching
    assert export.truncated is False
    assert export.truncated_reason is None


def test_explorer_export_cap_is_50000_rows(monkeypatch) -> None:
    svc, seen = _explorer_service(monkeypatch, matching=3)
    assert _export(svc).limit == 50_000
    assert seen["limit"] == 50_001
    # A larger limit is held to the cap.
    assert _export(svc, limit=1_000_000).limit == 50_000
    assert seen["limit"] == 50_001


def test_explorer_export_through_a_capped_review_filter_is_reported_truncated(monkeypatch) -> None:
    # The tag / classification filter reaches ClickHouse as a list of at most
    # _MAX_TAG_FILTER_VARIANT_IDS ids. Past it a match can be missing however few rows the
    # file holds, so the export says its search was partial, not "the first N rows".
    svc, seen = _explorer_service(monkeypatch, matching=2)
    monkeypatch.setattr(svc, "_MAX_TAG_FILTER_VARIANT_IDS", 3)
    tagged = ["1-500-A-G", "1-100-A-G", "1-400-A-G", "1-200-A-G", "1-300-A-G"]
    export = _export(
        svc,
        _ReviewSession(tagged),
        filters=svc.GlobalVariantFilters(review_tags=["report"]),
        limit=10,
    )
    assert len(export.rows) == 2
    assert export.truncated is True
    assert export.truncated_reason == "review-filter-limit"
    # The search took the first ids in variant_id order: the same ones on every request.
    assert seen["tag_variant_ids"] == ("1-100-A-G", "1-200-A-G", "1-300-A-G")


def test_explorer_export_through_a_review_filter_within_its_cap_is_complete(monkeypatch) -> None:
    svc, seen = _explorer_service(monkeypatch, matching=2)
    monkeypatch.setattr(svc, "_MAX_TAG_FILTER_VARIANT_IDS", 3)
    report = svc.GlobalVariantFilters(review_tags=["report"])
    export = _export(svc, _ReviewSession(["1-300-A-G", "1-100-A-G", "1-200-A-G"]), filters=report)
    assert export.truncated is False
    assert seen["tag_variant_ids"] == ("1-100-A-G", "1-200-A-G", "1-300-A-G")
    # A review filter that matches nothing exports nothing, and nothing is missing.
    seen.clear()
    empty = _export(svc, _ReviewSession([]), filters=report)
    assert (empty.rows, empty.truncated) == ([], False)
    assert seen == {}


def test_a_capped_review_filter_outranks_the_row_cap(monkeypatch) -> None:
    # Both cuts at once: the review cut says more (a match can be missing anywhere in the
    # file, not only past its last row), as the candidate cut does for the family exports.
    svc, _seen = _explorer_service(monkeypatch, matching=50)
    monkeypatch.setattr(svc, "_MAX_TAG_FILTER_VARIANT_IDS", 3)
    export = _export(
        svc,
        _ReviewSession([f"1-{100 + index}-A-G" for index in range(10)]),
        filters=svc.GlobalVariantFilters(review_tags=["report"]),
        limit=2,
    )
    assert len(export.rows) == 2
    assert export.truncated_reason == "review-filter-limit"


def _explorer_route_response(monkeypatch, export):
    from backend.app.routers import variant_explorer as explorer_route
    from backend.app.services.variant_explorer_service import GlobalVariantFilters

    async def _export(*_args, **_kwargs):
        return export

    monkeypatch.setattr(explorer_route, "export_global_small_variants", _export)

    async def _call():
        response = await explorer_route.export_global_small_variants_csv(
            assembly_id=None,
            sort="total_samples",
            order="desc",
            filters=GlobalVariantFilters(),
            session=None,
            user=None,
        )
        chunks = [chunk async for chunk in response.body_iterator]
        body = "".join(chunk if isinstance(chunk, str) else chunk.decode() for chunk in chunks)
        return response, body

    return asyncio.run(_call())


def test_explorer_export_route_names_and_heads_a_file_cut_at_the_cap(monkeypatch) -> None:
    from backend.app.services import variant_explorer_service as svc

    response, body = _explorer_route_response(
        monkeypatch,
        svc.GlobalVariantExport(
            assembly_name="GRCh38",
            rows=[_explorer_row(0), _explorer_row(1)],
            truncated_reason="row-limit",
            limit=2,
        ),
    )
    assert response.headers["content-disposition"] == (
        'attachment; filename="variant-explorer-GRCh38-TRUNCATED-first-2.csv"'
    )
    assert response.headers["x-coga-export-truncated"] == "true"
    assert response.headers["x-coga-export-truncated-reason"] == "row-limit"
    assert response.headers["x-coga-export-rows"] == "2"
    assert response.headers["x-coga-export-limit"] == "2"
    assert len(body.splitlines()) == 3  # the column labels and the two rows


def test_explorer_export_route_names_and_heads_a_partial_review_filter(monkeypatch) -> None:
    from backend.app.services import variant_explorer_service as svc

    response, _body = _explorer_route_response(
        monkeypatch,
        svc.GlobalVariantExport(
            assembly_name="GRCh38", rows=[], truncated_reason="review-filter-limit", limit=50_000
        ),
    )
    assert response.headers["content-disposition"] == (
        'attachment; filename="variant-explorer-GRCh38-TRUNCATED-partial-search.csv"'
    )
    assert response.headers["x-coga-export-truncated"] == "true"
    assert response.headers["x-coga-export-truncated-reason"] == "review-filter-limit"
    assert response.headers["x-coga-export-rows"] == "0"


def test_explorer_export_route_names_a_complete_file_plainly(monkeypatch) -> None:
    from backend.app.services import variant_explorer_service as svc

    response, body = _explorer_route_response(
        monkeypatch,
        svc.GlobalVariantExport(
            assembly_name="GRCh38", rows=[_explorer_row(0)], truncated_reason=None, limit=50_000
        ),
    )
    assert response.headers["content-disposition"] == 'attachment; filename="variant-explorer-GRCh38.csv"'
    assert response.headers["x-coga-export-truncated"] == "false"
    assert response.headers["x-coga-export-truncated-reason"] == ""
    assert response.headers["x-coga-export-rows"] == "1"
    assert response.headers["x-coga-export-limit"] == "50000"
    assert len(body.splitlines()) == 2


def test_export_headers_name_a_capped_review_filter_as_a_partial_search() -> None:
    cut = export_response_headers(
        "variant-explorer-GRCh38", rows=12, truncated=True, limit=50_000, reason="review-filter-limit"
    )
    assert cut["Content-Disposition"] == (
        'attachment; filename="variant-explorer-GRCh38-TRUNCATED-partial-search.csv"'
    )
    assert cut["X-CoGA-Export-Truncated"] == "true"
    assert cut["X-CoGA-Export-Truncated-Reason"] == "review-filter-limit"
    assert cut["X-CoGA-Export-Rows"] == "12"
