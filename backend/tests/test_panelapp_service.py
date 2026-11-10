from __future__ import annotations

from fastapi import HTTPException
import httpx
import pytest

import backend.app.services.panelapp_service as panelapp_service
from backend.app.services.panelapp_service import (
    extract_panelapp_import_content,
    panelapp_confidence_counts,
    panelapp_default_panel_name,
    panelapp_selection,
    panelapp_selection_label,
    panelapp_summary,
)


def _panelapp_payload() -> dict:
    return {
        "id": 20,
        "name": "Hereditary ataxia",
        "disease_group": "Neurology",
        "disease_sub_group": "Motor Disorders",
        "status": "public",
        "version": "1.345",
        "version_created": "2026-05-01T14:28:12.522359Z",
        "relevant_disorders": ["R54"],
        "stats": {"number_of_genes": 2, "number_of_strs": 1, "number_of_regions": 1},
        "types": [{"name": "Rare Disease 100K"}],
        "genes": [
            {
                "gene_data": {
                    "hgnc_symbol": "AAAS",
                    "ensembl_genes": {
                        "GRch38": {"90": {"location": "12:53307456-53324864"}}
                    },
                },
                "entity_name": "AAAS",
                "confidence_level": "3",
            },
            {
                "gene_data": {
                    "hgnc_symbol": "AMBER",
                    "ensembl_genes": {
                        "GRch38": {"90": {"location": "1:100-200"}}
                    },
                },
                "entity_name": "AMBER",
                "confidence_level": "2",
            },
        ],
        "strs": [
            {
                "gene_data": {"hgnc_symbol": "ATN1"},
                "entity_name": "ATN1_CAG",
                "confidence_level": "3",
                "chromosome": "12",
                "grch38_coordinates": [6936717, 6936772],
            }
        ],
        "regions": [
            {
                "entity_name": "ISCA-37478-Gain",
                "confidence_level": "3",
                "chromosome": "15",
                "grch38_coordinates": [23465365, 28134728],
            }
        ],
    }


def test_panelapp_summary_maps_public_panel_fields() -> None:
    summary = panelapp_summary(_panelapp_payload())

    assert summary.panelapp_id == 20
    assert summary.name == "Hereditary ataxia"
    assert summary.version == "1.345"
    assert summary.gene_count == 2
    assert summary.str_count == 1
    assert summary.region_count == 1
    assert summary.types == ["Rare Disease 100K"]
    assert summary.url == "https://panelapp.genomicsengland.co.uk/panels/20/"


def test_extract_panelapp_import_content_keeps_green_genes_regions_and_strs() -> None:
    content = extract_panelapp_import_content(
        _panelapp_payload(),
        confidence_levels=["3"],
        include_regions=True,
        include_strs=True,
        assembly="GRCh38",
    )

    assert content.genes == ["AAAS", "ATN1"]
    assert [(region.gene, region.chr, region.start, region.end) for region in content.regions] == [
        ("AAAS", "12", 53307456, 53324864),
        ("ATN1", "12", 6936717, 6936772),
        ("ISCA-37478-Gain", "15", 23465365, 28134728),
    ]
    assert content.metadata["import_options"]["confidence_levels"] == ["3"]
    assert content.metadata["selected_counts"] == {"genes": 1, "regions": 1, "strs": 1}


def test_extract_panelapp_import_content_can_include_amber_entities() -> None:
    content = extract_panelapp_import_content(
        _panelapp_payload(),
        confidence_levels=["2", "3"],
        include_regions=False,
        include_strs=False,
        assembly="GRCh38",
    )

    assert content.genes == ["AAAS", "AMBER"]
    assert [(region.gene, region.chr) for region in content.regions] == [
        ("AAAS", "12"),
        ("AMBER", "1"),
    ]


def test_the_counts_give_each_kind_of_entity_by_confidence_level() -> None:
    # The search lists only each panel's totals; the panel's entities give their levels.
    payload = _panelapp_payload()
    payload["genes"] += [
        {"entity_name": "RED1", "confidence_level": "1"},
        {"entity_name": "RED2", "confidence_level": 1},
        {"entity_name": "REMOVED", "confidence_level": "0"},
        {"entity_name": "NOLEVEL"},
        "not an entity",
    ]
    payload["strs"] = None

    counts = panelapp_confidence_counts(payload)

    assert (counts.panelapp_id, counts.version) == (20, "1.345")
    assert counts.genes.model_dump() == {"total": 6, "green": 1, "amber": 1, "red": 2, "unrated": 2}
    assert counts.strs.model_dump() == {"total": 0, "green": 0, "amber": 0, "red": 0, "unrated": 0}
    assert counts.regions.model_dump() == {"total": 1, "green": 1, "amber": 0, "red": 0, "unrated": 0}


@pytest.mark.parametrize(
    ("levels", "selection", "label"),
    [
        (["3"], "3", "green"),
        (["3", "2"], "2,3", "green + amber"),
        (["2", "3", "2"], "2,3", "green + amber"),
        (["1", "2", "3"], "1,2,3", "green + amber + red"),
        (["2"], "2", "amber"),
    ],
)
def test_a_choice_of_levels_is_one_selection_whatever_its_order(levels, selection, label) -> None:
    # The selection is the panel's key next to the PanelApp id: the same levels in another
    # order, or named twice, are the same panel.
    assert panelapp_selection(levels) == selection
    assert panelapp_selection_label(levels) == label
    assert panelapp_default_panel_name(_panelapp_payload(), levels) == f"PanelApp 20: Hereditary ataxia ({label})"


@pytest.mark.asyncio
async def test_fetch_panelapp_panel_builds_numeric_path(monkeypatch: pytest.MonkeyPatch) -> None:
    # The explicit int() at the URL sink pins panelapp_id to digits, so no '/' or
    # traversal can enter the request path (partial-SSRF guard).
    captured: dict = {}

    async def fake_get(path, params=None):
        captured["path"] = path
        captured["params"] = params
        return {}

    monkeypatch.setattr(panelapp_service, "_get_panelapp_json", fake_get)
    await panelapp_service.fetch_panelapp_panel(20, version="1.3")

    assert captured["path"] == "/panels/20/"
    assert captured["params"] == {"format": "json", "version": "1.3"}


@pytest.mark.asyncio
async def test_a_panelapp_answer_that_is_no_json_is_answered_502(monkeypatch: pytest.MonkeyPatch) -> None:
    real_client = httpx.AsyncClient

    def client(**kwargs):
        page = httpx.Response(200, text="<html>Service temporarily unavailable</html>")
        return real_client(transport=httpx.MockTransport(lambda _request: page), **kwargs)

    monkeypatch.setattr(panelapp_service.httpx, "AsyncClient", client)

    # A 200 whose body is no JSON raised ValueError past the handler: a 500.
    with pytest.raises(HTTPException) as refused:
        await panelapp_service._get_panelapp_json("/panels/20/")

    assert (refused.value.status_code, refused.value.detail) == (502, "Unexpected PanelApp response")


def _panelapp_answers(monkeypatch: pytest.MonkeyPatch, page: httpx.Response, asked: list | None = None) -> None:
    real_client = httpx.AsyncClient

    def answer(request: httpx.Request) -> httpx.Response:
        if asked is not None:
            asked.append(request)
        return page

    def client(**kwargs):
        return real_client(transport=httpx.MockTransport(answer), **kwargs)

    monkeypatch.setattr(panelapp_service.httpx, "AsyncClient", client)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("page", "refusal"),
    [
        (httpx.Response(404, json={"detail": "Not found."}), (404, "PanelApp panel not found")),
        (httpx.Response(500, text="Internal error"), (502, "Failed to retrieve PanelApp data")),
        (httpx.Response(200, json=["a list"]), (502, "Unexpected PanelApp response")),
    ],
    ids=["unknown panel", "server error", "JSON that is no object"],
)
async def test_a_panelapp_failure_is_answered_as_one_and_never_read_as_a_panel(
    monkeypatch: pytest.MonkeyPatch, page: httpx.Response, refusal: tuple[int, str]
) -> None:
    _panelapp_answers(monkeypatch, page)

    with pytest.raises(HTTPException) as refused:
        await panelapp_service.fetch_panelapp_panel(20)

    assert (refused.value.status_code, refused.value.detail) == refusal


@pytest.mark.asyncio
async def test_a_panelapp_search_asks_for_at_most_50_panels_by_the_trimmed_name(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[httpx.Request] = []
    _panelapp_answers(
        monkeypatch,
        httpx.Response(200, json={"count": 2, "results": [_panelapp_payload(), "not a panel"]}),
        asked,
    )

    summaries, count = await panelapp_service.search_panelapp_panels("  ataxia ", page_size=500)

    [request] = asked
    assert (request.url.path, dict(request.url.params)) == ("/api/v1/panels/", {"page_size": "50", "name": "ataxia"})
    assert ([summary.panelapp_id for summary in summaries], count) == ([20], 2)
    # A blank query lists panels without a name filter, at least one per page.
    asked.clear()
    await panelapp_service.search_panelapp_panels("  ", page_size=0)
    assert dict(asked[0].url.params) == {"page_size": "1"}
