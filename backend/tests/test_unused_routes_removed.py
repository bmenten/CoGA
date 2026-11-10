"""The routes no screen calls are gone, and the BED endpoints answer JSON only.

The owner's decision of 2026-10-28: remove what no screen calls, except the two routes a
planned screen needs (``docs/ROADMAP.md``): the SV filter preset's delete and a
phenotype's edit. Removed:

* ``GET /assemblies/{species_id}`` (the screens read every assembly, ``GET /assemblies/``);
* ``GET /families/{family_id}/hpo/query`` and ``GET /hpo/{hpo_id}`` (the screens search the
  terms, ``GET /hpo/search``, and read a family's phenotypes, ``GET /families/{id}/hpo``);
* ``DELETE /families/{family_id}/small-variant-filter-presets/{preset_id}`` (the *Settings*
  page deletes a small-variant preset through ``DELETE /auth/small-variant-filter-presets/{id}``);
* a BED read's ``format``: ``GET /bed/{sample}/{type}`` and its ``/batch`` answer JSON.

Through the app, signed in as an administrator; the BED reads go through the real routes and
``bed_service`` with a fake interval store. Synthetic IDs.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.app.main import app
from backend.tests._bed_route_client import bed_route_client

REMOVED = [
    ("get", "/api/assemblies/{species_id}"),
    ("get", "/api/families/{family_id}/hpo/query"),
    ("get", "/api/hpo/{hpo_id}"),
    ("delete", "/api/families/{family_id}/small-variant-filter-presets/{preset_id}"),
]
# What the screens call instead, and what a planned screen needs.
KEPT = [
    ("get", "/api/assemblies/"),
    ("get", "/api/hpo/search"),
    ("get", "/api/families/{family_id}/hpo"),
    ("put", "/api/families/{family_id}/hpo/{annotation_id}"),
    ("delete", "/api/auth/small-variant-filter-presets/{preset_id}"),
    ("delete", "/api/families/{family_id}/structural-variant-filter-presets/{preset_id}"),
    ("get", "/api/bed/{sample_id}/{bed_type}"),
    ("get", "/api/bed/{sample_id}/{bed_type}/batch"),
]
PRESET = "00000000-0000-4000-8000-0000000000e5"


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch):
    """An administrator's client (``_bed_route_client``). The BED routes find the sample, and
    the interval store holds one coverage row on chromosome 1; ``reads`` records each read of
    the store. The session answers no statement, so a request that reaches a database read
    fails."""
    reads: list[str] = []

    async def rows(_assembly: str, *, track_type: str, **_kwargs: Any) -> list[dict[str, Any]]:
        reads.append(track_type)
        return [{"chr": "1", "start": 0, "end": 1000, "value": 400.0, "record_id": None, "origin": None}]

    with bed_route_client(monkeypatch, rows=rows) as test_client:
        yield test_client, reads


def test_the_routes_no_screen_calls_are_gone_from_the_api() -> None:
    paths = app.openapi()["paths"]
    assert [(method, path) for method, path in REMOVED if method in paths.get(path, {})] == []


def test_the_routes_the_screens_call_and_the_planned_screens_need_stay() -> None:
    paths = app.openapi()["paths"]
    assert [(method, path) for method, path in KEPT if method not in paths.get(path, {})] == []


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/assemblies/3fa85f64-5717-4562-b3fc-2c963f66afa6"),
        ("get", "/api/families/FAM1/hpo/query?hpo_id=HP:0001250"),
        ("get", "/api/hpo/HP:0001250"),
        ("delete", f"/api/families/FAM1/small-variant-filter-presets/{PRESET}"),
    ],
)
def test_a_request_to_a_removed_route_finds_none(client, method: str, path: str) -> None:
    test_client, _reads = client
    response = test_client.request(method.upper(), path)
    # No route: 404, or 405 where another method shares the path (a phenotype's PUT and DELETE).
    assert response.status_code in (404, 405), (path, response.status_code, response.text)


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/api/bed/PROBAND/coverage", {"chrom": "1"}),
        ("/api/bed/PROBAND/coverage/batch", {"chrom": ["1"]}),
    ],
    ids=["one-chromosome", "batch"],
)
def test_a_bed_read_answers_json_without_format(client, path: str, params: dict[str, Any]) -> None:
    test_client, _reads = client
    response = test_client.get(path, params=params)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {
        "bed_type": "coverage",
        "items": [{"chr": "1", "start": 0, "end": 1000, "value": 400.0}],
    }
