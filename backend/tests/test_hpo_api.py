from dataclasses import dataclass
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError

from backend.app.core.postgres import get_postgres_session
from backend.app.main import app
from backend.app.routers import admin as admin_router
from backend.app.routers import families as families_router
from backend.app.routers import hpo as hpo_router
from backend.app.services.access_control import CurrentUser


@dataclass
class _FakeFamilyContext:
    family_uuid: str = "11111111-1111-1111-1111-111111111111"
    family_id: str = "FAM1"


class _FakeSession:
    def __init__(self) -> None:
        self.rolled_back = False

    async def rollback(self) -> None:
        self.rolled_back = True


@pytest.fixture()
def hpo_api_client(monkeypatch: pytest.MonkeyPatch):
    original_overrides = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True

    user = CurrentUser(
        id="user1",
        username="viewer@example.com",
        email="viewer@example.com",
        role="viewer",
        created_at=datetime.now(timezone.utc),
    )

    async def override_get_postgres_session():
        yield _FakeSession()

    async def override_get_current_user():
        return user

    async def override_get_current_admin_user():
        return user.model_copy(update={"role": "admin"})

    app.dependency_overrides[get_postgres_session] = override_get_postgres_session
    app.dependency_overrides[admin_router.get_current_admin_user] = override_get_current_admin_user
    app.dependency_overrides[hpo_router.get_current_user] = override_get_current_user
    app.dependency_overrides[families_router.get_current_user] = override_get_current_user
    app.dependency_overrides[families_router.get_current_admin_user] = override_get_current_admin_user

    with TestClient(app) as client:
        yield client, monkeypatch

    app.dependency_overrides = original_overrides


def test_hpo_search_and_detail_endpoints(hpo_api_client) -> None:
    client, monkeypatch = hpo_api_client

    async def fake_search(*args, **kwargs):
        return [
            {
                "hpo_id": "HP:0001250",
                "label": "Seizure",
                "definition": "A seizure phenotype.",
                "is_obsolete": False,
            }
        ]

    async def fake_details(*args, **kwargs):
        return {
            "hpo_id": "HP:0001250",
            "label": "Seizure",
            "definition": "A seizure phenotype.",
            "is_obsolete": False,
            "replaced_by": None,
            "release_version": "test",
            "release_date": None,
            "synonyms": ["Epileptic seizure"],
            "parents": [],
            "children": [],
        }

    monkeypatch.setattr(hpo_router, "search_hpo_terms", fake_search)
    monkeypatch.setattr(hpo_router, "get_hpo_term_details", fake_details)

    search_response = client.get("/api/hpo/search?q=seizure")
    detail_response = client.get("/api/hpo/HP:0001250")

    assert search_response.status_code == 200
    assert search_response.json()[0]["hpo_id"] == "HP:0001250"
    assert detail_response.status_code == 200
    assert detail_response.json()["synonyms"] == ["Epileptic seizure"]


def test_admin_hpo_terms_endpoint(hpo_api_client) -> None:
    client, monkeypatch = hpo_api_client

    async def fake_admin_terms(*args, **kwargs):
        return [
            {
                "hpo_id": "HP:0001250",
                "label": "Seizure",
                "definition": "A seizure phenotype.",
                "is_obsolete": False,
                "replaced_by": None,
                "release_version": "test",
                "release_date": None,
                "synonyms": ["Epileptic seizure"],
                "parents": [],
                "children": [],
                "parent_count": 0,
                "child_count": 0,
            }
        ]

    monkeypatch.setattr(admin_router, "list_hpo_admin_terms", fake_admin_terms)

    response = client.get("/api/admin/hpo/terms?q=seizure")

    assert response.status_code == 200
    assert response.json()[0]["hpo_id"] == "HP:0001250"


def test_family_hpo_annotation_endpoints(hpo_api_client) -> None:
    client, monkeypatch = hpo_api_client
    now = datetime.now(timezone.utc)
    annotation = {
        "id": "22222222-2222-2222-2222-222222222222",
        "sample_id": "PROBAND",
        "hpo_id": "HP:0001250",
        "label": "Seizure",
        "status": "present",
        "onset": None,
        "evidence": None,
        "source": "manual",
        "note": None,
        "created_at": now,
        "updated_at": now,
    }

    async def fake_context(*args, **kwargs):
        return _FakeFamilyContext()

    async def fake_list(*args, **kwargs):
        return [annotation]

    async def fake_create(*args, **kwargs):
        return annotation

    async def fake_query(*args, **kwargs):
        return {
            "hpo_id": "HP:0001250",
            "include_descendants": True,
            "sample_ids": ["PROBAND"],
            "annotations": [annotation],
        }

    monkeypatch.setattr(families_router, "build_family_metadata_context", fake_context)
    monkeypatch.setattr(families_router, "list_family_hpo_annotations", fake_list)
    monkeypatch.setattr(families_router, "create_individual_hpo_annotation", fake_create)
    monkeypatch.setattr(families_router, "query_family_hpo_annotations", fake_query)

    list_response = client.get("/api/families/FAM1/hpo")
    create_response = client.post(
        "/api/families/FAM1/members/PROBAND/hpo",
        json={"hpo_id": "HP:0001250", "status": "present"},
    )
    query_response = client.get("/api/families/FAM1/hpo/query?hpo_id=HP:0001250")

    assert list_response.status_code == 200
    assert list_response.json()[0]["sample_id"] == "PROBAND"
    assert create_response.status_code == 200
    assert create_response.json()["hpo_id"] == "HP:0001250"
    assert query_response.status_code == 200
    assert query_response.json()["sample_ids"] == ["PROBAND"]


def test_family_member_management_endpoints(hpo_api_client) -> None:
    client, monkeypatch = hpo_api_client
    now = datetime.now(timezone.utc)
    impact = {
        "sample_id": "PROBAND",
        "pedigree_references": {"as_child": 2},
        "data_counts": {"small_variants": 12, "haplotype": 4},
        "affected_analysis_scopes": ["vcf-genotypes", "haplotype-phasing"],
        "stale_analysis_scopes": ["segregation", "haplotypes"],
        "warnings": ["This individual has linked genotype calls in imported variant data."],
        "destructive": True,
        "requires_manual_recalculation": True,
    }
    annotation = {
        "id": "22222222-2222-2222-2222-222222222222",
        "sample_id": "PROBAND",
        "hpo_id": "HP:0001250",
        "label": "Seizure",
        "definition": "A seizure phenotype.",
        "status": "present",
        "onset": None,
        "evidence": None,
        "source": "manual",
        "note": None,
        "created_at": now,
        "updated_at": now,
    }
    member = {
        "sample_id": "PROBAND",
        "role": "proband",
        "affected": True,
        "sex": "female",
        "clinical_status": "affected",
        "carrier_status": "unknown",
        "carrier_type": None,
        "carrier_evidence": {},
        "active": True,
    }
    family = {
        "_id": "11111111-1111-1111-1111-111111111111",
        "family_id": "FAM1",
        "created_at": now,
        "members": [member],
        "relationships": [],
        "structure_version": {"version": 2, "structure_hash": "abc", "metadata": {}},
        "pedigree": None,
        "roi": None,
        "projects": [],
        "metadata": {},
    }

    async def fake_detail(*args, **kwargs):
        return {
            "member": member,
            "father_id": "FATHER",
            "mother_id": "MOTHER",
            "hpo_annotations": [annotation],
            "impact": impact,
        }

    async def fake_update(*args, **kwargs):
        return {
            "family": family,
            "member": member,
            "father_id": "FATHER",
            "mother_id": "MOTHER",
            "impact": impact,
            "warnings": ["Relationship-dependent analyses should be rerun."],
            "stale_analysis_scopes": ["segregation"],
            "data_counts": impact["data_counts"],
            "cleared_data_counts": {},
        }

    async def fake_batch_update(*args, **kwargs):
        return {
            "family": family,
            "warnings": ["Metadata-only update; raw genomic datasets preserved."],
            "stale_analysis_scopes": ["segregation"],
            "data_counts": {},
            "cleared_data_counts": {},
        }

    async def fake_delete(*args, **kwargs):
        return {
            "family": family,
            "impact": impact,
            "warnings": ["Removed family members are inactive."],
            "stale_analysis_scopes": ["sample-data"],
            "data_counts": impact["data_counts"],
            "cleared_data_counts": {},
        }

    monkeypatch.setattr(families_router, "get_family_member_detail_for_user", fake_detail)
    monkeypatch.setattr(families_router, "update_family_member_for_admin", fake_update)
    monkeypatch.setattr(families_router, "update_family_members_batch_for_admin", fake_batch_update)
    monkeypatch.setattr(families_router, "delete_family_member_for_admin", fake_delete)

    detail_response = client.get("/api/families/FAM1/members/PROBAND")
    update_response = client.put(
        "/api/families/FAM1/members/PROBAND",
        json={"carrier_status": "carrier", "father_id": "FATHER", "mother_id": "MOTHER"},
    )
    batch_response = client.put(
        "/api/families/FAM1/members/batch",
        json={
            "updates": [
                {
                    "sample_id": "PROBAND",
                    "carrier_status": "carrier",
                    "father_id": "FATHER",
                    "mother_id": "MOTHER",
                }
            ]
        },
    )
    delete_response = client.delete("/api/families/FAM1/members/PROBAND?confirm=true")

    assert detail_response.status_code == 200
    assert detail_response.json()["hpo_annotations"][0]["definition"] == "A seizure phenotype."
    assert update_response.status_code == 200
    assert update_response.json()["member"]["sample_id"] == "PROBAND"
    assert batch_response.status_code == 200
    assert batch_response.json()["cleared_data_counts"] == {}
    assert delete_response.status_code == 200
    assert delete_response.json()["warnings"] == ["Removed family members are inactive."]


def test_family_member_batch_reports_missing_metadata_schema(hpo_api_client) -> None:
    client, monkeypatch = hpo_api_client

    async def fake_batch_update(*args, **kwargs):
        del args, kwargs
        raise DBAPIError(
            "SELECT",
            {},
            Exception('relation "family_structure_versions" does not exist'),
        )

    monkeypatch.setattr(families_router, "update_family_members_batch_for_admin", fake_batch_update)

    response = client.put(
        "/api/families/FAM1/members/batch",
        json={"updates": [{"sample_id": "PROBAND", "carrier_status": "carrier"}]},
    )

    assert response.status_code == 503
    assert "Family metadata database schema is not available" in response.json()["detail"]
