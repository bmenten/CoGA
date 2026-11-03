"""Monarch semsim phenotype matching (``monarch_semsim``), with no network: Monarch is a mock
transport.

A family's present HPO terms are sent to Monarch's semantic-similarity search, which ranks
human genes or diseases by phenotypic similarity. The request carries each term once, sorted,
and a limit held to Monarch's 1-50; the answer's entries are ranked in order, an entry without
a subject id left out. A failed request, an HTTP error or an answer of another shape is a
``MonarchSemsimError`` (the route answers 502), never an empty ranking, and is not cached. A
profile's ranking is cached for an hour, keyed on the group, the sorted terms and the limit;
the cache holds at most ``_CACHE_MAX_ENTRIES`` profiles, dropping the soonest to expire.
"""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from backend.app.services import monarch_semsim
from backend.app.services.monarch_semsim import (
    MONARCH_SEMSIM_SEARCH_URL,
    MonarchSemsimError,
    _normalize_results,
    semsim_search,
)

_GENES = [
    {"subject": {"id": "HGNC:11764", "name": "TG", "category": "biolink:Gene"}, "score": 12.3},
    {"subject": {"id": "HGNC:21071", "name": "IYD", "category": "biolink:Gene"}, "score": 11.4},
]


@pytest.fixture(autouse=True)
def _empty_cache():
    """The process-wide cache, empty for each test and after it."""
    monarch_semsim._cache.clear()
    yield
    monarch_semsim._cache.clear()


def _monarch(monkeypatch: pytest.MonkeyPatch, answer) -> list[httpx.Request]:
    """Monarch answers each request with ``answer(request)``; the list collects the requests."""
    requests: list[httpx.Request] = []
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return answer(request)

    def client(**kwargs):
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(monarch_semsim.httpx, "AsyncClient", client)
    return requests


def _clock(monkeypatch: pytest.MonkeyPatch, start: float = 1_000.0) -> list[float]:
    """The cache's clock, which the test moves by setting ``clock[0]``."""
    clock = [start]
    monkeypatch.setattr(monarch_semsim, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    return clock


def test_normalize_results_assigns_ranks_and_extracts_subject() -> None:
    results = _normalize_results(_GENES)

    assert [r["rank"] for r in results] == [1, 2]
    assert results[0]["id"] == "HGNC:11764"
    assert results[0]["name"] == "TG"
    assert results[0]["score"] == 12.3


def test_normalize_results_skips_entries_without_subject_id() -> None:
    payload = [
        {"subject": {}, "score": 1.0},
        {"subject": {"id": "HGNC:1", "name": "A", "category": "biolink:Gene"}, "score": 2.0},
    ]
    results = _normalize_results(payload)

    assert len(results) == 1
    assert results[0]["id"] == "HGNC:1"
    assert results[0]["rank"] == 1


def test_normalize_results_rejects_non_list() -> None:
    with pytest.raises(MonarchSemsimError):
        _normalize_results({"not": "a list"})


@pytest.mark.asyncio
async def test_semsim_search_short_circuits_on_empty_termset(monkeypatch: pytest.MonkeyPatch) -> None:
    # Must not touch the network when there are no usable terms.
    requests = _monarch(monkeypatch, lambda _request: httpx.Response(200, json=_GENES))

    assert await semsim_search([], group="Human Genes") == []
    assert await semsim_search(["", "   "], group="Human Genes") == []
    assert requests == []


@pytest.mark.asyncio
async def test_semsim_search_rejects_unknown_group() -> None:
    with pytest.raises(ValueError):
        await semsim_search(["HP:0001250"], group="Martian Genes")


@pytest.mark.asyncio
@pytest.mark.parametrize(("limit", "sent"), [(20, 20), (500, 50), (0, 1)], ids=["as-asked", "capped", "at-least-one"])
async def test_semsim_search_sends_each_term_once_sorted_with_a_bounded_limit(
    monkeypatch: pytest.MonkeyPatch, limit: int, sent: int
) -> None:
    requests = _monarch(monkeypatch, lambda _request: httpx.Response(200, json=_GENES))

    results = await semsim_search([" HP:0001250 ", "HP:0000118", "HP:0001250", ""], group="Human Diseases", limit=limit)

    [request] = requests
    assert (request.method, str(request.url)) == ("POST", MONARCH_SEMSIM_SEARCH_URL)
    assert json.loads(request.content) == {
        "termset": ["HP:0000118", "HP:0001250"],
        "group": "Human Diseases",
        "limit": sent,
    }
    assert [(r["rank"], r["id"], r["name"], r["category"]) for r in results] == [
        (1, "HGNC:11764", "TG", "biolink:Gene"),
        (2, "HGNC:21071", "IYD", "biolink:Gene"),
    ]


@pytest.mark.asyncio
async def test_a_profile_is_asked_of_monarch_once_an_hour(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _clock(monkeypatch)
    requests = _monarch(monkeypatch, lambda _request: httpx.Response(200, json=_GENES))

    first = await semsim_search(["HP:0001250", "HP:0000118"])
    # The same terms in another order: the same profile, from the cache.
    again = await semsim_search(["HP:0000118", "HP:0001250", "HP:0001250"])
    assert again == first
    assert len(requests) == 1
    # Another group, or another limit, is another question.
    await semsim_search(["HP:0001250", "HP:0000118"], group="Human Diseases")
    await semsim_search(["HP:0001250", "HP:0000118"], limit=10)
    assert len(requests) == 3

    # An hour later the profile is asked again.
    clock[0] += monarch_semsim._CACHE_TTL_SECONDS
    await semsim_search(["HP:0001250", "HP:0000118"])
    assert len(requests) == 4


def _connection_refused(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(lambda _request: httpx.Response(503, text="unavailable"), id="http-error"),
        pytest.param(lambda _request: httpx.Response(200, json={"results": _GENES}), id="not-a-list"),
        pytest.param(lambda _request: httpx.Response(200, text="<html>maintenance</html>"), id="not-json"),
        pytest.param(_connection_refused, id="unreachable"),
    ],
)
async def test_a_failed_search_is_a_monarch_semsim_error_and_is_not_cached(
    monkeypatch: pytest.MonkeyPatch, answer
) -> None:
    requests = _monarch(monkeypatch, answer)

    with pytest.raises(MonarchSemsimError):
        await semsim_search(["HP:0001250"])

    assert monarch_semsim._cache == {}
    # Asked again, Monarch is asked again rather than an empty ranking served.
    with pytest.raises(MonarchSemsimError):
        await semsim_search(["HP:0001250"])
    assert len(requests) == 2


def test_cache_round_trip_and_size_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _clock(monkeypatch)
    key = ("Human Genes", ("HP:1",), 10)
    monarch_semsim._cache_put(key, [{"rank": 1, "id": "HGNC:1"}])
    assert monarch_semsim._cache_get(key) == [{"rank": 1, "id": "HGNC:1"}]
    assert monarch_semsim._cache_get(("Human Genes", ("HP:2",), 10)) is None

    # Full: each new profile drops the one that expires soonest, here the first one put.
    for index in range(2, monarch_semsim._CACHE_MAX_ENTRIES + 1):
        clock[0] += 1
        monarch_semsim._cache_put(("Human Genes", (f"HP:{index}",), 10), [])
    assert len(monarch_semsim._cache) == monarch_semsim._CACHE_MAX_ENTRIES
    clock[0] += 1
    monarch_semsim._cache_put(("Human Genes", ("HP:new",), 10), [])
    assert len(monarch_semsim._cache) == monarch_semsim._CACHE_MAX_ENTRIES
    assert key not in monarch_semsim._cache
    assert ("Human Genes", ("HP:2",), 10) in monarch_semsim._cache

    # An entry past its hour is not served, and goes.
    clock[0] += monarch_semsim._CACHE_TTL_SECONDS
    assert monarch_semsim._cache_get(("Human Genes", ("HP:2",), 10)) is None
    assert ("Human Genes", ("HP:2",), 10) not in monarch_semsim._cache


# ---- The family's phenotype match (GET /families/{id}/phenotype-match) ------------------------
#
# Through the app, Monarch a mock transport: the family's annotations, the gene table's answer
# and the local Monarch breakdown are the test's.

_ANNOTATIONS = [
    {"sample_id": "PROBAND", "hpo_id": "HP:0001250", "status": "present"},
    {"sample_id": "PROBAND", "hpo_id": "HP:0004322", "status": "absent"},
    {"sample_id": "MOTHER", "hpo_id": "HP:0000118", "status": "present"},
    {"sample_id": "FATHER1", "hpo_id": "HP:0001250", "status": "present"},
]
_GENE_AND_DISEASE = [
    {"subject": {"id": "HGNC:11764", "name": "TG", "category": "biolink:Gene"}, "score": 12.3},
    {"subject": {"id": "MONDO:0005129", "name": "cataract", "category": "biolink:Disease"}, "score": 9.0},
]


class _GenesSession:
    """Answers the gene table's lookup with ``TG``, the one gene placed on the platform."""

    def __init__(self) -> None:
        self.asked: list[dict] = []

    async def execute(self, statement, params=None):
        assert "FROM genes" in str(statement)
        self.asked.append(dict(params or {}))
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: [{"hgnc_symbol": "TG"}]))

    async def rollback(self) -> None:
        return None


@pytest.fixture()
def phenotype_match(monkeypatch: pytest.MonkeyPatch):
    """A viewer's client of the app; ``calls`` records what the route asked of each service.
    The request audit rows are kept, not written."""
    from fastapi.testclient import TestClient

    from backend.app.core.postgres import get_postgres_session
    from backend.app.dependencies import get_current_user
    from backend.app.main import app
    from backend.app.middleware import request_logging
    from backend.app.routers import families as families_router
    from backend.app.services.access_control import CurrentUser

    session = _GenesSession()
    calls: dict[str, list] = {"annotations": [], "closure": [], "breakdown": []}
    viewer = CurrentUser(
        id="00000000-0000-4000-8000-0000000000c1",
        username="viewer@example.org",
        email="viewer@example.org",
        role="viewer",
        created_at=datetime.now(timezone.utc),
    )

    async def context(_session, **_kwargs):
        return SimpleNamespace(family_uuid="family-uuid", family_id="FAM1")

    async def annotations(_session, **kwargs):
        calls["annotations"].append(kwargs)
        return [row for row in _ANNOTATIONS if kwargs.get("sample_id") in (None, row["sample_id"])]

    async def closure(_session, terms):
        calls["closure"].append(list(terms))
        return {"HP:0001250", "HP:0000118"}

    async def breakdown(_session, *, symbols, observed_closure):
        calls["breakdown"].append((set(symbols), set(observed_closure)))
        return {
            "TG": {
                "matching": [{"hpo_id": "HP:0001250", "label": "Seizure"}],
                "extra": [{"hpo_id": "HP:0000821", "label": "Hypothyroidism"}],
            }
        }

    async def override_session():
        yield session

    async def override_user():
        return viewer

    async def keep_audit_row(_payload) -> None:
        return None

    monkeypatch.setattr(families_router, "build_family_metadata_context", context)
    monkeypatch.setattr(families_router, "list_family_hpo_annotations", annotations)
    monkeypatch.setattr(families_router, "phenotype_closure", closure)
    monkeypatch.setattr(families_router, "gene_phenotype_breakdown", breakdown)
    monkeypatch.setattr(request_logging, "write_audit_log_event", keep_audit_row)
    original_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_postgres_session] = override_session
    app.dependency_overrides[get_current_user] = override_user
    app.state.skip_startup_tasks = True
    try:
        with TestClient(app) as client:
            yield client, calls, session
    finally:
        app.dependency_overrides = original_overrides
        app.state.skip_startup_tasks = False


def test_the_phenotype_match_sends_the_familys_present_terms_alone(
    monkeypatch: pytest.MonkeyPatch, phenotype_match
) -> None:
    client, calls, session = phenotype_match
    requests = _monarch(monkeypatch, lambda _request: httpx.Response(200, json=_GENE_AND_DISEASE))

    response = client.get("/api/families/FAM1/phenotype-match", params={"limit": 5})

    assert response.status_code == 200, response.text
    body = response.json()
    # Each member's present terms, once each; an absent term is never sent as observed.
    assert body["query_hpo_ids"] == ["HP:0000118", "HP:0001250"]
    [request] = requests
    assert json.loads(request.content) == {
        "termset": ["HP:0000118", "HP:0001250"],
        "group": "Human Genes",
        "limit": 5,
    }
    assert calls["annotations"] == [{"family_uuid": "family-uuid", "sample_id": None}]
    # The gene is linked when the platform places it, and its phenotypes are split by what the
    # family shows; a disease is no gene.
    assert session.asked == [{"symbols": ["TG"]}]
    assert calls["closure"] == [["HP:0000118", "HP:0001250"]]
    assert calls["breakdown"] == [({"TG"}, {"HP:0001250", "HP:0000118"})]
    gene, disease = body["results"]
    assert (gene["rank"], gene["symbol"], gene["gene_in_platform"]) == (1, "TG", True)
    assert [term["hpo_id"] for term in gene["matching_phenotypes"]] == ["HP:0001250"]
    assert [term["hpo_id"] for term in gene["extra_phenotypes"]] == ["HP:0000821"]
    assert (disease["rank"], disease["id"], disease["symbol"], disease["gene_in_platform"]) == (
        2,
        "MONDO:0005129",
        None,
        False,
    )
    assert disease["matching_phenotypes"] == disease["extra_phenotypes"] == []


def test_a_members_phenotype_match_sends_that_members_present_terms(
    monkeypatch: pytest.MonkeyPatch, phenotype_match
) -> None:
    client, calls, _session = phenotype_match
    requests = _monarch(monkeypatch, lambda _request: httpx.Response(200, json=_GENE_AND_DISEASE))

    response = client.get("/api/families/FAM1/phenotype-match", params={"sample_id": "PROBAND"})

    assert response.status_code == 200, response.text
    assert response.json()["query_hpo_ids"] == ["HP:0001250"]
    assert json.loads(requests[0].content)["termset"] == ["HP:0001250"]
    assert calls["annotations"] == [{"family_uuid": "family-uuid", "sample_id": "PROBAND"}]


async def _absent_only(_session, **_kwargs):
    return [{"sample_id": "PROBAND", "hpo_id": "HP:0004322", "status": "absent"}]


def test_without_a_present_term_monarch_is_not_asked(monkeypatch: pytest.MonkeyPatch, phenotype_match) -> None:
    from backend.app.routers import families as families_router

    client, calls, session = phenotype_match
    requests = _monarch(monkeypatch, lambda _request: httpx.Response(200, json=_GENE_AND_DISEASE))
    monkeypatch.setattr(families_router, "list_family_hpo_annotations", _absent_only)

    response = client.get("/api/families/FAM1/phenotype-match")

    assert response.status_code == 200
    assert (response.json()["query_hpo_ids"], response.json()["results"]) == ([], [])
    assert requests == [] and session.asked == [] and calls["breakdown"] == []


def test_monarch_unavailable_answers_502_and_no_ranking(monkeypatch: pytest.MonkeyPatch, phenotype_match) -> None:
    client, calls, session = phenotype_match
    _monarch(monkeypatch, lambda _request: httpx.Response(503, text="unavailable"))

    response = client.get("/api/families/FAM1/phenotype-match")

    assert response.status_code == 502
    assert response.json()["detail"].startswith("Monarch phenotype matching is unavailable: ")
    assert session.asked == [] and calls["breakdown"] == []


def test_an_unsupported_group_is_refused_before_anything_is_read(
    monkeypatch: pytest.MonkeyPatch, phenotype_match
) -> None:
    client, calls, _session = phenotype_match
    requests = _monarch(monkeypatch, lambda _request: httpx.Response(200, json=_GENE_AND_DISEASE))

    response = client.get("/api/families/FAM1/phenotype-match", params={"group": "Mouse Genes"})

    assert response.status_code == 400
    assert calls["annotations"] == [] and requests == []
