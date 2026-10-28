"""The routes no screen calls are gone, and the BED endpoints answer JSON only (ENG-5).

The owner's decision of 2026-10-28: remove what no screen calls, except the two routes a
planned screen needs (``docs/ROADMAP.md``): the SV filter preset's delete (PROD-3) and a
phenotype's edit (PROD-8). Removed:

* ``GET /assemblies/{species_id}`` (the screens read every assembly, ``GET /assemblies/``);
* ``GET /families/{family_id}/hpo/query`` and ``GET /hpo/{hpo_id}`` (the screens search the
  terms, ``GET /hpo/search``, and read a family's phenotypes, ``GET /families/{id}/hpo``);
* ``DELETE /families/{family_id}/small-variant-filter-presets/{preset_id}`` (the *Settings*
  page deletes a small-variant preset through ``DELETE /auth/small-variant-filter-presets/{id}``);
* the BED endpoints' text format: ``GET /bed/{sample}/{type}`` and its ``/batch`` answer JSON,
  without ``format`` too, and refuse ``format=text`` (422) before reading anything.

Through the app, signed in as an administrator; the BED reads go through the real routes and
``bed_service`` with a fake interval store. Synthetic IDs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi.testclient import TestClient
import pytest

from backend.app.core.postgres import get_postgres_session
from backend.app.dependencies import get_current_user
from backend.app.main import app
from backend.app.middleware import request_logging
from backend.app.routers import bed as bed_router
from backend.app.services import bed_service
from backend.app.services.access_control import CurrentUser
from backend.app.services.family_metadata_context import SampleMetadataContext

REMOVED = [
    ("get", "/api/assemblies/{species_id}"),
    ("get", "/api/families/{family_id}/hpo/query"),
    ("get", "/api/hpo/{hpo_id}"),
    ("delete", "/api/families/{family_id}/small-variant-filter-presets/{preset_id}"),
]
# What the screens call instead, and what a planned screen needs (PROD-3, PROD-8).
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
SAMPLE = SampleMetadataContext(
    sample_uuid="uuid-proband",
    sample_id="PROBAND",
    family_uuid="family-uuid",
    family_id="FAM_BED",
    sex="female",
    project_ids=["project-uuid"],
    assembly_id="assembly-uuid",
    assembly_name="GRCh38",
)


class _FakeSession:
    async def rollback(self) -> None:
        return None


async def _keep_audit_row(_payload: Any) -> None:
    return None


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch):
    """An administrator's client. The BED routes find the sample, and the interval store holds
    one coverage row on chromosome 1; ``reads`` records each read of the store. The session
    answers no statement, so a request that reaches a database read fails; the request audit
    rows are kept, not written."""
    original_overrides = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True
    admin = CurrentUser(
        id="a1",
        username="admin@example.org",
        email="admin@example.org",
        role="admin",
        created_at=datetime.now(timezone.utc),
    )
    reads: list[str] = []

    async def override_get_postgres_session():
        yield _FakeSession()

    async def override_get_current_user():
        return admin

    async def sample_context(_session: Any, *, sample_identifier: str, user: CurrentUser) -> SampleMetadataContext:
        return SAMPLE

    async def sources(_assembly: str, **_kwargs: Any) -> dict[str, list[str]]:
        return {"PROBAND": ["web"]}

    async def rows(_assembly: str, *, track_type: str, **_kwargs: Any) -> list[dict[str, Any]]:
        reads.append(track_type)
        return [{"chr": "1", "start": 0, "end": 1000, "value": 400.0, "record_id": None, "origin": None}]

    monkeypatch.setattr(bed_router, "build_sample_metadata_context", sample_context)
    monkeypatch.setattr(bed_service, "get_interval_track_sources_by_sample", sources)
    monkeypatch.setattr(bed_service, "fetch_interval_track_rows", rows)
    monkeypatch.setattr(request_logging, "write_audit_log_event", _keep_audit_row)
    app.dependency_overrides[get_postgres_session] = override_get_postgres_session
    app.dependency_overrides[get_current_user] = override_get_current_user
    try:
        with TestClient(app) as test_client:
            yield test_client, reads
    finally:
        app.dependency_overrides = original_overrides


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
    # Text (a header line and tab-separated rows) was the default.
    for asked in (params, {**params, "format": "json"}):
        response = test_client.get(path, params=asked)
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "application/json"
        assert response.json() == {
            "bed_type": "coverage",
            "items": [{"chr": "1", "start": 0, "end": 1000, "value": 400.0}],
        }


@pytest.mark.parametrize(
    "path",
    ["/api/bed/PROBAND/coverage", "/api/bed/PROBAND/coverage/batch"],
    ids=["one-chromosome", "batch"],
)
def test_a_bed_read_in_the_text_format_is_refused_before_anything_is_read(client, path: str) -> None:
    test_client, reads = client
    response = test_client.get(path, params={"chrom": "1", "format": "text"})
    assert response.status_code == 422, response.text
    assert [error["loc"] for error in response.json()["detail"]] == [["query", "format"]]
    assert reads == []


def test_the_bed_reads_declare_json_their_only_format() -> None:
    paths = app.openapi()["paths"]
    for path in ("/api/bed/{sample_id}/{bed_type}", "/api/bed/{sample_id}/{bed_type}/batch"):
        operation = paths[path]["get"]
        (parameter,) = [p for p in operation["parameters"] if p["name"] == "format"]
        assert parameter["schema"].get("enum", [parameter["schema"].get("const")]) == ["json"]
        assert parameter["schema"].get("default") == "json"
        assert list(operation["responses"]["200"]["content"]) == ["application/json"]
