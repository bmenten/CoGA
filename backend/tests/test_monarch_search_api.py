"""Router tests for the admin Monarch search and refresh endpoints."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app.core.postgres import get_postgres_session
from backend.app.main import app
from backend.app.routers import admin as admin_router
from backend.app.services.access_control import CurrentUser


class _FakeSession:
    rollbacks = 0

    async def rollback(self) -> None:
        type(self).rollbacks += 1


@pytest.fixture()
def monarch_admin_client(monkeypatch: pytest.MonkeyPatch):
    original_overrides = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True

    admin_user = CurrentUser(
        id="admin1",
        username="admin@example.com",
        email="admin@example.com",
        role="admin",
        created_at=datetime.now(timezone.utc),
    )

    async def override_get_postgres_session():
        yield _FakeSession()

    async def override_get_current_admin_user():
        return admin_user

    app.dependency_overrides[get_postgres_session] = override_get_postgres_session
    app.dependency_overrides[admin_router.get_current_admin_user] = (
        override_get_current_admin_user
    )

    with TestClient(app) as client:
        yield client, monkeypatch

    app.dependency_overrides = original_overrides


def test_monarch_search_returns_diseases_with_links(monarch_admin_client) -> None:
    client, monkeypatch = monarch_admin_client

    captured: dict[str, object] = {}

    async def fake_search(session, *, query, limit):
        captured["query"] = query
        captured["limit"] = limit
        return {
            "query": query,
            "total": 1,
            "diseases": [
                {
                    "mondo_id": "MONDO:0007739",
                    "disease_label": "Huntington disease",
                    "match_type": "disease",
                    "gene_count": 1,
                    "genes": [
                        {
                            "gene_symbol": "HTT",
                            "hgnc_id": "HGNC:4851",
                            "predicate": "causes",
                            "causal": True,
                        }
                    ],
                    "phenotype_count": 2,
                    "matched_phenotype_count": 1,
                    "phenotypes": [
                        {
                            "hpo_id": "HP:0002072",
                            "phenotype_label": "Chorea",
                            "matched": True,
                        }
                    ],
                }
            ],
            "gene_overview": {
                "total": 1,
                "genes": [
                    {
                        "gene_symbol": "HTT",
                        "hgnc_id": "HGNC:4851",
                        "causal": True,
                        "disease_count": 1,
                    }
                ],
            },
        }

    monkeypatch.setattr(admin_router, "search_monarch_associations", fake_search)

    response = client.get("/api/admin/monarch/search?q=huntington&limit=10")

    assert response.status_code == 200
    body = response.json()
    assert captured == {"query": "huntington", "limit": 10}
    assert body["total"] == 1
    disease = body["diseases"][0]
    assert disease["mondo_id"] == "MONDO:0007739"
    assert disease["match_type"] == "disease"
    assert disease["genes"][0]["gene_symbol"] == "HTT"
    assert disease["genes"][0]["causal"] is True
    assert disease["phenotypes"][0]["hpo_id"] == "HP:0002072"
    assert body["gene_overview"]["total"] == 1
    assert body["gene_overview"]["genes"][0]["gene_symbol"] == "HTT"
    assert body["gene_overview"]["genes"][0]["disease_count"] == 1


def test_monarch_search_rejects_out_of_range_limit(monarch_admin_client) -> None:
    client, _ = monarch_admin_client

    response = client.get("/api/admin/monarch/search?q=seizure&limit=500")

    assert response.status_code == 422


_REFRESH_SUMMARY = {
    "release_version": "2026-04-01",
    "files_loaded": 4,
    "gene_disease_pairs": 13300,
    "genes": 5470,
    "diseases": 8910,
    "causal_pairs": 7250,
    "disease_phenotype_pairs": 246000,
    "phenotype_diseases": 11240,
    "phenotypes": 9010,
    "excluded_phenotype_pairs": 0,
    "completed_at": "2026-04-15T12:00:00Z",
    "duration_seconds": 2.5,
}


def _patch_refresh(monkeypatch, regenerate) -> None:
    async def fake_refresh(session):
        return dict(_REFRESH_SUMMARY)

    monkeypatch.setattr(admin_router, "refresh_monarch", fake_refresh)
    monkeypatch.setattr(admin_router, "regenerate_mendeliome", regenerate)


def test_monarch_refresh_reports_the_regenerated_mendeliome(monarch_admin_client) -> None:
    client, monkeypatch = monarch_admin_client

    async def regenerate(session, user):
        return None

    _patch_refresh(monkeypatch, regenerate)
    response = client.post("/api/admin/monarch/refresh")

    assert response.status_code == 200
    body = response.json()
    assert body["release_version"] == "2026-04-01"
    assert body["mendeliome_regenerated"] is True
    assert body["mendeliome_error"] is None


def test_a_failed_mendeliome_regeneration_is_reported_not_hidden(monarch_admin_client) -> None:
    # #514: the refresh itself succeeded, so it must not fail — but the generated
    # Mendeliome panel still reflects the previous release, and the admin is told.
    client, monkeypatch = monarch_admin_client

    async def regenerate(session, user):
        raise RuntimeError("gene_info lookup timed out")

    _patch_refresh(monkeypatch, regenerate)
    _FakeSession.rollbacks = 0
    response = client.post("/api/admin/monarch/refresh")

    assert response.status_code == 200
    body = response.json()
    assert body["release_version"] == "2026-04-01"
    assert body["mendeliome_regenerated"] is False
    # The exception type only: its message can carry SQL or row values.
    assert body["mendeliome_error"] == "RuntimeError"
    # The half-built panel version is discarded rather than left on the session.
    assert _FakeSession.rollbacks == 1
