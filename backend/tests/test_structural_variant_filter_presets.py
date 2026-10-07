"""Structural-variant filter presets: the route's body and answer carry the preset's scope.

An SV preset is its owner's, for the family it is saved in (``scope: 'family'``, what the SV
page sends) or, reusable, for every family they can open (``scope: 'global'``). The save route
read its body as a small-variant preset, which has no scope since small-variant presets became
their owner's (#681), and answered 500 whatever it was sent; the list served the presets
without their scope. Each request here runs through the route with its real body and answer
models and the real service, on a session that answers the preset statements as Postgres
would; ``e2e/test_e2e_sv_filter_presets_are_saved.py`` runs the same on real Postgres.
"""

from __future__ import annotations

import json
import types
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app.core.postgres import get_postgres_session
from backend.app.dependencies import get_current_user
from backend.app.main import app
from backend.app.routers import families_structural_variants as sv_router
from backend.app.services.access_control import CurrentUser

FAMILY_UUID = "00000000-0000-0000-0000-00000000f001"
PRESET_ID = "00000000-0000-0000-0000-0000000000a1"
PRESETS = "/api/families/FAM1/structural-variant-filter-presets"
USER = CurrentUser(
    id="u1",
    username="reviewer",
    email="reviewer@example.org",
    role="viewer",
    created_at=datetime(2026, 10, 7, tzinfo=timezone.utc),
)
# The body the SV page sends on Save (FamilyStructuralVariantsPage, buildStructuralPresetPayload).
SAVED_SEARCH = {
    "filters": {"type": "DEL", "max_population_af": "0.01"},
    "sample_filters": {"S1": {"gt": ["0/1", "1/0", "0|1", "1|0"], "qual": "", "read_support": "4", "filter": ""}},
    "sample_templates": {"S1": "proband"},
}


class _Result:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self) -> "_Result":
        return self

    def first(self) -> dict | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict]:
        return list(self._rows)


class _Session:
    """Records each statement with its parameters and answers the preset statements: the
    save with the row it stores, the list with ``listed``."""

    def __init__(self, listed: list[dict] | None = None) -> None:
        self.listed = listed or []
        self.executed: list[tuple[str, dict]] = []
        self.commits = 0

    async def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        params = dict(params or {})
        self.executed.append((sql, params))
        if sql.startswith("INSERT INTO structural_variant_filter_presets"):
            return _Result(
                [
                    {
                        "id": PRESET_ID,
                        "family_id": params["family_id"],
                        "scope": params["scope"],
                        "owner": params["owner"],
                        "name": params["name"],
                        "description": params["description"],
                        "filters": json.loads(params["filters_json"]),
                        "sample_filters": json.loads(params["sample_filters_json"]),
                        "sample_templates": json.loads(params["sample_templates_json"]),
                        "created_at": params["now"],
                        "updated_at": params["now"],
                    }
                ]
            )
        if sql.startswith("SELECT") and "FROM structural_variant_filter_presets" in sql:
            return _Result(self.listed)
        raise AssertionError(f"unexpected statement: {sql}")

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None


@pytest.fixture()
def client_for(monkeypatch: pytest.MonkeyPatch):
    """A client of the app as ``USER``, on the given session, for the family ``FAM1``."""
    original_overrides = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True

    async def _context(session, *, family_identifier, user, project_id=None):
        assert family_identifier == "FAM1"
        return types.SimpleNamespace(family_uuid=FAMILY_UUID, family_id="FAM1")

    monkeypatch.setattr(sv_router, "build_family_metadata_context", _context)

    async def _user() -> CurrentUser:
        return USER

    def _make(session: _Session) -> TestClient:
        async def _session():
            yield session

        app.dependency_overrides[get_postgres_session] = _session
        app.dependency_overrides[get_current_user] = _user
        return TestClient(app)

    yield _make
    app.dependency_overrides = original_overrides


def _saved_params(session: _Session) -> dict:
    inserts = [params for sql, params in session.executed if sql.startswith("INSERT INTO")]
    assert len(inserts) == 1, session.executed
    return inserts[0]


def test_the_body_the_sv_page_sends_saves_a_preset_for_the_family(client_for) -> None:
    session = _Session()
    with client_for(session) as client:
        response = client.post(
            PRESETS, json={"name": " Rare deletions ", "description": " DEL only ", "scope": "family", **SAVED_SEARCH}
        )
    assert response.status_code == 200, response.text
    params = _saved_params(session)
    assert (params["family_id"], params["scope"], params["owner"]) == (FAMILY_UUID, "family", "reviewer")
    assert (params["name"], params["description"]) == ("Rare deletions", "DEL only")
    assert json.loads(params["filters_json"]) == SAVED_SEARCH["filters"]
    assert json.loads(params["sample_filters_json"]) == SAVED_SEARCH["sample_filters"]
    assert json.loads(params["sample_templates_json"]) == SAVED_SEARCH["sample_templates"]
    assert session.commits == 1
    body = response.json()
    assert {key: body[key] for key in ("_id", "family_id", "scope", "owner", "name", "description")} == {
        "_id": PRESET_ID,
        "family_id": FAMILY_UUID,
        "scope": "family",
        "owner": "reviewer",
        "name": "Rare deletions",
        "description": "DEL only",
    }
    assert {key: body[key] for key in SAVED_SEARCH} == SAVED_SEARCH


def test_a_reusable_preset_is_saved_for_no_family(client_for) -> None:
    session = _Session()
    with client_for(session) as client:
        response = client.post(PRESETS, json={"name": "Every family", "scope": "global", **SAVED_SEARCH})
    assert response.status_code == 200, response.text
    params = _saved_params(session)
    assert (params["family_id"], params["scope"]) == (None, "global")
    assert (response.json()["family_id"], response.json()["scope"]) == (None, "global")


def test_a_preset_saved_without_a_scope_is_for_the_family(client_for) -> None:
    session = _Session()
    with client_for(session) as client:
        response = client.post(PRESETS, json={"name": "Default"})
    assert response.status_code == 200, response.text
    params = _saved_params(session)
    assert (params["family_id"], params["scope"]) == (FAMILY_UUID, "family")
    assert (params["filters_json"], params["sample_filters_json"], params["sample_templates_json"]) == (
        "{}",
        "{}",
        "{}",
    )


@pytest.mark.parametrize(
    "body,status",
    [
        ({"name": "Unknown scope", "scope": "project"}, 422),
        ({"name": "No family", "scope": None}, 422),
        ({"name": "   ", "scope": "family"}, 400),
        ({"name": "", "scope": "family"}, 422),
    ],
)
def test_a_body_the_route_cannot_save_is_refused_before_any_statement(client_for, body, status) -> None:
    session = _Session()
    with client_for(session) as client:
        response = client.post(PRESETS, json=body)
    assert response.status_code == status, response.text
    assert session.executed == []
    assert session.commits == 0


def test_the_list_serves_each_presets_scope_and_family(client_for) -> None:
    stamp = datetime(2026, 10, 7, tzinfo=timezone.utc)
    rows = [
        {
            "id": PRESET_ID,
            "family_id": FAMILY_UUID,
            "scope": "family",
            "owner": "reviewer",
            "name": "Rare deletions",
            "description": None,
            "filters": SAVED_SEARCH["filters"],
            "sample_filters": {},
            "sample_templates": {},
            "created_at": stamp,
            "updated_at": stamp,
        },
        {
            "id": "00000000-0000-0000-0000-0000000000a2",
            "family_id": None,
            "scope": "global",
            "owner": "reviewer",
            "name": "Every family",
            "description": "reusable",
            "filters": {},
            "sample_filters": {},
            "sample_templates": {},
            "created_at": stamp,
            "updated_at": stamp,
        },
    ]
    session = _Session(listed=rows)
    with client_for(session) as client:
        response = client.get(PRESETS)
    assert response.status_code == 200, response.text
    assert [(preset["_id"], preset["scope"], preset["family_id"]) for preset in response.json()] == [
        (PRESET_ID, "family", FAMILY_UUID),
        ("00000000-0000-0000-0000-0000000000a2", "global", None),
    ]
    assert [params for _sql, params in session.executed] == [{"family_id": FAMILY_UUID, "owner": "reviewer"}]
