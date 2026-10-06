from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app.core.postgres import get_postgres_session
from backend.app.main import app
from backend.app.routers import admin as admin_router
from backend.app.routers import families as families_router
from backend.app.routers import families_nipt as families_nipt_router
from backend.app.routers import lookups as lookups_router
from backend.app.services.access_control import CurrentUser
from backend.app.services.nipt_analysis import FetalFractionEstimate
from backend.app.services.nipt_service import NiptGeneTargets, NiptVariantsResult
from backend.app.services.nipt_target_coverage import TargetCoverageRow


class _FakeSession:
    async def rollback(self) -> None:
        return None


@pytest.fixture()
def family_metadata_client(monkeypatch: pytest.MonkeyPatch):
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
    app.dependency_overrides[families_router.get_current_user] = override_get_current_user
    app.dependency_overrides[families_router.get_current_admin_user] = override_get_current_admin_user
    app.dependency_overrides[lookups_router.get_current_user] = override_get_current_user
    app.dependency_overrides[admin_router.get_current_admin_user] = override_get_current_admin_user

    with TestClient(app) as client:
        yield client, monkeypatch

    app.dependency_overrides = original_overrides


def _family_payload() -> dict:
    return {
        "_id": "11111111-1111-1111-1111-111111111111",
        "family_id": "FAM1",
        "created_at": datetime.now(timezone.utc),
        "members": [],
        "relationships": [],
        "projects": [],
        "metadata": {},
        "status": {"key": "solved", "label": "Solved", "color": "#1f9d57"},
        "assigned_to": {
            "id": "u1",
            "username": "ann",
            "email": "ann@example.com",
            "first_name": "Ann",
            "last_name": "Lee",
        },
        "reviewed_by": None,
    }


def test_update_family_metadata_endpoint_allows_any_user(family_metadata_client) -> None:
    client, monkeypatch = family_metadata_client
    captured: dict = {}

    async def fake_update(session, *, family_id, update, user):
        captured["family_id"] = family_id
        captured["update"] = update.model_dump(exclude_unset=True)
        captured["role"] = user.role
        return _family_payload()

    monkeypatch.setattr(families_router, "update_family_metadata_for_user", fake_update)

    response = client.put(
        "/api/families/FAM1/metadata",
        json={"status_key": "solved", "assigned_to": "u1", "reviewed_by": None},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"]["label"] == "Solved"
    assert body["assigned_to"]["id"] == "u1"
    assert body["reviewed_by"] is None
    # Editable by a plain (non-admin) signed-in user, and the partial update is
    # forwarded verbatim.
    assert captured["role"] == "viewer"
    assert captured["family_id"] == "FAM1"
    assert captured["update"] == {"status_key": "solved", "assigned_to": "u1", "reviewed_by": None}


def test_list_family_statuses_endpoint(family_metadata_client) -> None:
    client, monkeypatch = family_metadata_client

    async def fake_list(session, *, include_inactive=False):
        assert include_inactive is False
        return [
            {
                "id": "st1",
                "key": "solved",
                "label": "Solved",
                "description": None,
                "color": "#1f9d57",
                "sort_order": 10,
                "is_active": True,
            }
        ]

    monkeypatch.setattr(lookups_router, "list_family_statuses", fake_list)

    response = client.get("/api/family-statuses")
    assert response.status_code == 200
    body = response.json()
    assert [item["key"] for item in body] == ["solved"]


def test_download_family_pedigree_endpoint(family_metadata_client) -> None:
    client, monkeypatch = family_metadata_client

    async def fake_build(session, *, family_id):
        assert family_id == "FAM1"
        return "FAM1 KID 0 0 1 2\n"

    monkeypatch.setattr(admin_router, "build_pedigree_text", fake_build)

    response = client.get("/api/admin/families/FAM1/ped")

    assert response.status_code == 200
    assert response.text == "FAM1 KID 0 0 1 2\n"
    assert response.headers["content-type"].startswith("text/plain")
    assert 'filename="FAM1.ped"' in response.headers["content-disposition"]


def test_list_users_endpoint(family_metadata_client) -> None:
    client, monkeypatch = family_metadata_client

    async def fake_users(session):
        return [
            {"id": "u1", "username": "ann", "email": "ann@example.com", "first_name": "Ann", "last_name": "Lee"},
            {"id": "u2", "username": "bob", "email": "bob@example.com", "first_name": "Bob", "last_name": "Ng"},
        ]

    monkeypatch.setattr(lookups_router, "list_assignable_users", fake_users)

    response = client.get("/api/users")
    assert response.status_code == 200
    body = response.json()
    assert {item["id"] for item in body} == {"u1", "u2"}


def test_admin_family_status_crud(family_metadata_client) -> None:
    client, monkeypatch = family_metadata_client
    deleted: dict = {}

    def _status(key: str, label: str) -> dict:
        return {
            "id": "st1",
            "key": key,
            "label": label,
            "description": None,
            "color": "#5b6b79",
            "sort_order": 500,
            "is_active": True,
        }

    async def fake_list(session, *, include_inactive=False):
        assert include_inactive is True
        return [_status("solved", "Solved")]

    async def fake_create(session, *, payload, user):
        assert user.role == "admin"
        return _status("pending", payload.label)

    async def fake_update(session, *, key, payload):
        return _status(key, payload.label or "Solved")

    async def fake_delete(session, *, key):
        deleted["key"] = key

    monkeypatch.setattr(admin_router, "list_family_statuses", fake_list)
    monkeypatch.setattr(admin_router, "create_family_status", fake_create)
    monkeypatch.setattr(admin_router, "update_family_status", fake_update)
    monkeypatch.setattr(admin_router, "delete_family_status", fake_delete)

    list_response = client.get("/api/admin/family-statuses")
    assert list_response.status_code == 200
    assert list_response.json()[0]["key"] == "solved"

    create_response = client.post("/api/admin/family-statuses", json={"label": "Pending"})
    assert create_response.status_code == 201
    assert create_response.json()["label"] == "Pending"

    update_response = client.put("/api/admin/family-statuses/solved", json={"label": "Resolved"})
    assert update_response.status_code == 200
    assert update_response.json()["label"] == "Resolved"

    delete_response = client.delete("/api/admin/family-statuses/solved")
    assert delete_response.status_code == 204
    assert deleted["key"] == "solved"


@pytest.mark.parametrize(
    "path",
    [
        "/api/families/FAM1/small-variants",
        "/api/families/FAM1/structural-variants",
        "/api/families/FAM1/nipt/variants",
    ],
)
@pytest.mark.parametrize("bad_page_size", [10_000_001, -1])
def test_variant_page_size_is_bounded(family_metadata_client, path, bad_page_size) -> None:
    client, _monkeypatch = family_metadata_client

    # An out-of-range page_size is rejected by request validation before the handler
    # runs, so no ClickHouse read or hydration is triggered by an oversized page.
    response = client.get(path, params={"page_size": bad_page_size})

    assert response.status_code == 422
    assert "page_size" in response.text


@pytest.mark.parametrize(
    ("capped", "expected"),
    [(True, (True, 5000)), (False, (False, None))],
)
def test_the_nipt_variant_page_says_when_its_list_is_capped(
    family_metadata_client, capped, expected
) -> None:
    # The NIPT page and report need the cap to tell a cut list from a complete one; the
    # page carried only the total, which past the cap looked exact.
    client, monkeypatch = family_metadata_client

    async def fake_variants(_session, **_kwargs):
        return NiptVariantsResult(
            fetal_fraction=FetalFractionEstimate(
                ff=0.1,
                ff_computed=0.1,
                ff_median=0.1,
                ci_low=0.09,
                ci_high=0.11,
                n_sites=40,
                method="category7_pooled",
                low_confidence=False,
            ),
            total=1234,
            variants=[],
            total_is_estimated=capped,
            count_limit=5000 if capped else None,
        )

    monkeypatch.setattr(families_nipt_router, "get_family_nipt_variants", fake_variants)

    response = client.get("/api/families/FAM1/nipt/variants", params={"panel_id": "panel-1"})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1234
    assert (body["total_is_estimated"], body["count_limit"]) == expected


def test_the_coverage_page_opens_a_gene_s_targets(family_metadata_client) -> None:
    # A row of the NIPT coverage page, opened: every target of the gene, each saying whether
    # it is weak by the rule the NIPT page counts with (REQ-NIPT-006).
    client, monkeypatch = family_metadata_client
    seen: dict[str, object] = {}

    async def fake_gene_targets(_session, *, family_id, user, gene, project_id=None):
        seen.update(family_id=family_id, gene=gene, project_id=project_id)
        return NiptGeneTargets(
            gene=gene,
            critical_mean_depth=300.0,
            rows=[
                TargetCoverageRow(chrom="7", start=0, end=100, gene="GENEA", attribute="GENEA;NM_1;1", mean=1500.0,
                                  proportion_covered=100.0),
                TargetCoverageRow(chrom="7", start=200, end=300, gene="GENEA", attribute="GENEA;NM_1;2", mean=250.0,
                                  proportion_covered=100.0),
                TargetCoverageRow(chrom="7", start=400, end=500, gene="GENEA", attribute="GENEA;NM_1;3", mean=1200.0,
                                  proportion_covered=97.5),
            ],
        )

    monkeypatch.setattr(families_nipt_router, "get_family_nipt_gene_targets", fake_gene_targets)

    response = client.get("/api/families/FAM1/nipt/coverage/targets", params={"gene": "GENEA", "project_id": "p1"})

    assert response.status_code == 200
    body = response.json()
    assert seen == {"family_id": "FAM1", "gene": "GENEA", "project_id": "p1"}
    assert body["critical_mean_depth"] == 300.0
    assert [(target["start"], target["weak"]) for target in body["targets"]] == [
        (0, False),
        (200, True),  # mean below 300x
        (400, True),  # a base without coverage
    ]


def test_the_gene_targets_need_a_gene(family_metadata_client) -> None:
    client, _monkeypatch = family_metadata_client

    response = client.get("/api/families/FAM1/nipt/coverage/targets")

    assert response.status_code == 422
