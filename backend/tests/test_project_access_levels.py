"""Project access levels (SEC-1): an analyst reads and changes a project's records, a reader
only reads them.

Each project member is an analyst or a reader (``project_users.access_level``); an admin has
every right in every project. ``get_current_user`` records whether a request changes
something (its method, unless the route is marked ``changes_only_own_data``), and the family,
sample and project checkpoints refuse a reader's change with a 403 that says why, after the
visibility check, so a record outside the caller's projects still answers like an unknown one.
A reader may save their own filter presets, and may not read a family's clinical audit trail.

The requests here go through the real ``get_current_user`` with a signed token and through
the real checkpoints; only the user lookup, the family rows, the database session and the
routes' services are replaced. ``test_every_change_route_is_classified`` keeps the catalogue
of routes that change something; the e2e sweep (``e2e/test_e2e_project_reader_role.py``)
asks each of them on real Postgres. All IDs are synthetic.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Iterator

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from backend.app import dependencies
from backend.app.core.config import settings
from backend.app.core.postgres import get_postgres_session
from backend.app.dependencies import create_access_token
from backend.app.main import app
from backend.app.middleware import request_logging
from backend.app.routers import auth as auth_router
from backend.app.routers import families as families_router
from backend.app.routers import families_small_variants as small_variants_router
from backend.app.routers import families_structural_variants as structural_variants_router
from backend.app.services import metadata_service
from backend.app.services.access_control import (
    AUDIT_TRAIL_DETAIL,
    READ_ONLY_DETAIL,
    CurrentUser,
    RecordNotVisible,
    request_changes_records,
)
from backend.app.services.ped_service import _resolve_accessible_project_id

PROJECT_A = "00000000-0000-4000-8000-00000000000a"
PROJECT_B = "00000000-0000-4000-8000-00000000000b"
PROJECT_C = "00000000-0000-4000-8000-00000000000c"
# An analyst in A who reads B (a mixed member), a reader in B only, and an admin.
MIXED, READER, ADMIN = "mixed@example.org", "reader@example.org", "admin@example.org"
FAMILIES = {
    "FAM_A": [PROJECT_A],
    "FAM_B": [PROJECT_B],
    "FAM_AB": [PROJECT_A, PROJECT_B],
    "FAM_C": [PROJECT_C],
}


def _member(email: str, role: str, projects: list[str], readers: list[str]) -> CurrentUser:
    return CurrentUser(
        id=f"user-{email.split('@')[0]}",
        username=email,
        email=email,
        role=role,
        projects=projects,
        metadata_project_ids=projects,
        reader_project_ids=readers,
        created_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
    )


def _users() -> dict[str, CurrentUser]:
    return {
        MIXED: _member(MIXED, "viewer", [PROJECT_A, PROJECT_B], [PROJECT_B]),
        READER: _member(READER, "viewer", [PROJECT_B], [PROJECT_B]),
        ADMIN: _member(ADMIN, "admin", [], []),
    }


def _changing(user: CurrentUser) -> CurrentUser:
    user.changes_records = True
    return user


class _Rows:
    """What an empty database answers: no rows, no value (but the project a lookup names)."""

    rowcount = 0

    def __init__(self, value: Any = None) -> None:
        self.value = value

    def mappings(self) -> "_Rows":
        return self

    def all(self) -> list[Any]:
        return []

    def first(self) -> None:
        return None

    def scalar_one_or_none(self) -> Any:
        return self.value


class _EmptySession:
    async def execute(self, statement: Any, params: dict[str, Any] | None = None, **_kwargs: Any) -> _Rows:
        # The Family Builder looks its project up: every project exists here.
        if "FROM projects WHERE id" in str(statement):
            return _Rows((params or {}).get("project_id"))
        return _Rows()

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def _family_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fetch(session: Any, *, family_identifiers: list[str] | None = None, **_kwargs: Any) -> list[dict]:
        return [
            {"id": f"uuid-{family_id}", "family_id": family_id, "project_ids": FAMILIES[family_id], "metadata": {}}
            for family_id in family_identifiers or []
            if family_id in FAMILIES
        ]

    async def fetch_sample(session: Any, sample_identifier: str) -> dict | None:
        family_id = sample_identifier.removesuffix("_S1")
        if family_id not in FAMILIES:
            return None
        return {"id": f"uuid-{sample_identifier}", "sample_id": sample_identifier, "family_project_ids": FAMILIES[family_id]}

    monkeypatch.setattr(metadata_service, "_fetch_family_rows", fetch)
    monkeypatch.setattr(metadata_service, "_fetch_sample_access_mapping", fetch_sample)


# --------------------------------------------------------------------------- #
# The checkpoints
# --------------------------------------------------------------------------- #


def _family(user: CurrentUser, family_id: str) -> Any:
    return asyncio.run(metadata_service.get_accessible_family_mapping(None, family_id, user))


def _sample(user: CurrentUser, sample_id: str) -> Any:
    return asyncio.run(metadata_service.get_accessible_sample_mapping(None, sample_id, user))


def _refusal(call: Any) -> HTTPException:
    with pytest.raises(HTTPException) as refused:
        call()
    return refused.value


def test_a_reader_may_read_a_family_of_their_project_but_not_change_it(monkeypatch: pytest.MonkeyPatch) -> None:
    _family_rows(monkeypatch)
    reader = _users()[READER]

    assert _family(reader, "FAM_B")["family_id"] == "FAM_B"
    refused = _refusal(lambda: _family(_changing(reader), "FAM_B"))
    assert (refused.status_code, refused.detail) == (403, READ_ONLY_DETAIL)


def test_a_family_outside_the_readers_projects_still_answers_like_an_unknown_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The visibility check comes first: the 403 tells only a member that the family exists.
    _family_rows(monkeypatch)
    reader = _changing(_users()[READER])

    hidden, unknown = _refusal(lambda: _family(reader, "FAM_C")), _refusal(lambda: _family(reader, "NO_FAMILY"))

    assert isinstance(hidden, RecordNotVisible)
    assert (hidden.status_code, hidden.detail) == (unknown.status_code, unknown.detail) == (404, "Family not found")


def test_a_mixed_member_changes_where_they_are_an_analyst(monkeypatch: pytest.MonkeyPatch) -> None:
    _family_rows(monkeypatch)
    mixed = _changing(_users()[MIXED])

    assert _family(mixed, "FAM_A")["family_id"] == "FAM_A"
    # A family of two projects is changed by an analyst of either.
    assert _family(mixed, "FAM_AB")["family_id"] == "FAM_AB"
    assert _refusal(lambda: _family(mixed, "FAM_B")).detail == READ_ONLY_DETAIL


@pytest.mark.parametrize("role", ["admin", "superuser"])
def test_an_admin_changes_every_family(monkeypatch: pytest.MonkeyPatch, role: str) -> None:
    _family_rows(monkeypatch)
    admin = _changing(_member(ADMIN, role, [PROJECT_B], [PROJECT_B]))
    for family_id in FAMILIES:
        assert _family(admin, family_id)["family_id"] == family_id


def test_a_reader_may_read_a_sample_of_their_project_but_not_change_it(monkeypatch: pytest.MonkeyPatch) -> None:
    _family_rows(monkeypatch)
    reader = _users()[READER]

    assert _sample(reader, "FAM_B_S1")["sample_id"] == "FAM_B_S1"
    assert _refusal(lambda: _sample(_changing(reader), "FAM_B_S1")).detail == READ_ONLY_DETAIL
    assert _refusal(lambda: _sample(_changing(reader), "FAM_C_S1")).status_code == 404
    assert _sample(_changing(_users()[MIXED]), "FAM_AB_S1")["sample_id"] == "FAM_AB_S1"


class _ProjectSession:
    def __init__(self, project_id: str) -> None:
        self.project_id = project_id

    async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        project_id = self.project_id

        class _Found:
            def scalar_one_or_none(self) -> str:
                return project_id

        return _Found()


def test_a_reader_may_not_add_a_family_to_their_project(monkeypatch: pytest.MonkeyPatch) -> None:
    # The Family Builder's project checkpoint.
    reader = _changing(_users()[READER])
    refused = _refusal(lambda: asyncio.run(_resolve_accessible_project_id(_ProjectSession(PROJECT_B), reader, PROJECT_B)))
    assert (refused.status_code, refused.detail) == (403, READ_ONLY_DETAIL)

    mixed = _changing(_users()[MIXED])
    assert asyncio.run(_resolve_accessible_project_id(_ProjectSession(PROJECT_A), mixed, PROJECT_A)) == PROJECT_A
    assert _refusal(
        lambda: asyncio.run(_resolve_accessible_project_id(_ProjectSession(PROJECT_B), mixed, PROJECT_B))
    ).detail == READ_ONLY_DETAIL


# --------------------------------------------------------------------------- #
# Through the app: the real get_current_user decides whether a request changes something
# --------------------------------------------------------------------------- #


@pytest.fixture()
def api(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """A client whose requests are signed in by the real ``get_current_user`` and reach the
    real family checkpoint. Records the calls that reach the routes' services."""
    original_overrides = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True
    monkeypatch.setattr(settings, "azure_client_id", None)
    monkeypatch.setattr(settings, "azure_tenant_id", None)
    monkeypatch.setattr(settings, "secret_key", "x" * 40)
    users = _users()

    async def user_by_email(session: Any, email: str) -> CurrentUser | None:
        # A fresh object per request, as the database gives.
        user = users.get(email)
        return user.model_copy() if user is not None else None

    reached: list[tuple[str, dict[str, Any]]] = []
    now = datetime(2026, 10, 14, tzinfo=timezone.utc)
    annotation = {
        "id": "00000000-0000-4000-8000-0000000000e1",
        "sample_id": "FAM_A_S1",
        "hpo_id": "HP:0001250",
        "label": "Seizure",
        "definition": None,
        "status": "present",
        "onset": None,
        "evidence": None,
        "source": "manual",
        "note": None,
        "created_at": now,
        "updated_at": now,
    }
    preset = {
        "id": "00000000-0000-4000-8000-0000000000e2",
        "owner": READER,
        "name": "mine",
        "description": None,
        "filters": {},
        "sample_filters": {},
        "sample_templates": {},
        "created_at": now,
        "updated_at": now,
    }

    def recorder(name: str, result: Any = None):
        async def record(*_args: Any, **kwargs: Any) -> Any:
            reached.append((name, kwargs))
            return result

        return record

    async def no_precompute(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def no_audit_row(_payload: Any) -> None:
        return None

    async def empty_session():
        yield _EmptySession()

    _family_rows(monkeypatch)
    monkeypatch.setattr(dependencies, "get_current_user_by_email", user_by_email)
    monkeypatch.setattr(request_logging, "write_audit_log_event", no_audit_row)
    monkeypatch.setattr(families_router, "precompute_family_ranking_safe", no_precompute)
    monkeypatch.setattr(families_router, "create_individual_hpo_annotation", recorder("create_hpo", annotation))
    monkeypatch.setattr(families_router, "update_individual_hpo_annotation", recorder("update_hpo", annotation))
    monkeypatch.setattr(families_router, "delete_individual_hpo_annotation", recorder("delete_hpo"))
    monkeypatch.setattr(families_router, "list_family_hpo_annotations", recorder("list_hpo", []))
    monkeypatch.setattr(small_variants_router, "save_small_variant_filter_preset_record", recorder("save_preset", preset))
    monkeypatch.setattr(
        structural_variants_router,
        "save_structural_variant_filter_preset_record",
        recorder("save_sv_preset", {**preset, "family_id": "uuid-FAM_B", "scope": "family"}),
    )
    monkeypatch.setattr(auth_router, "delete_small_variant_filter_preset_for_owner", recorder("delete_my_preset"))
    app.dependency_overrides[get_postgres_session] = empty_session
    with TestClient(app) as client:
        yield {"client": client, "reached": reached}
    app.dependency_overrides = original_overrides


def _as(email: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token({'sub': email})}"}


_HPO_CHANGES = [
    ("post", "/api/families/{family}/members/{family}_S1/hpo", {"hpo_id": "HP:0001250"}),
    ("put", "/api/families/{family}/hpo/00000000-0000-4000-8000-0000000000e1", {"status": "absent"}),
    ("delete", "/api/families/{family}/hpo/00000000-0000-4000-8000-0000000000e1", None),
]


@pytest.mark.parametrize(("method", "path", "body"), _HPO_CHANGES)
def test_a_reader_is_refused_a_phenotype_change_with_403_and_why(api: dict, method: str, path: str, body: Any) -> None:
    kwargs = {"json": body} if body is not None else {}
    response = api["client"].request(method, path.format(family="FAM_B"), headers=_as(READER), **kwargs)

    assert (response.status_code, response.json()) == (403, {"detail": READ_ONLY_DETAIL})
    assert api["reached"] == []


@pytest.mark.parametrize(("method", "path", "body"), _HPO_CHANGES)
def test_an_analyst_and_an_admin_change_phenotypes_and_the_change_names_its_author(
    api: dict, method: str, path: str, body: Any
) -> None:
    kwargs = {"json": body} if body is not None else {}
    for email, family_id in ((MIXED, "FAM_A"), (ADMIN, "FAM_B")):
        response = api["client"].request(method, path.format(family=family_id), headers=_as(email), **kwargs)
        assert response.status_code in (200, 204), response.text

    # Each change reached the service with the family's ID and its author, for the audit event.
    assert [(call["family_identifier"], call["user"].email) for _name, call in api["reached"]] == [
        ("FAM_A", MIXED),
        ("FAM_B", ADMIN),
    ]


def test_a_reader_reads_the_phenotypes_of_their_family(api: dict) -> None:
    response = api["client"].get("/api/families/FAM_B/hpo", headers=_as(READER))
    assert (response.status_code, response.json()) == (200, [])


def test_a_mixed_member_is_refused_only_where_they_are_a_reader(api: dict) -> None:
    path = "/api/families/{}/members/{}_S1/hpo"
    answers = {
        family_id: api["client"].post(path.format(family_id, family_id), json={"hpo_id": "HP:0001250"}, headers=_as(MIXED)).status_code
        for family_id in ("FAM_A", "FAM_AB", "FAM_B", "FAM_C")
    }
    assert answers == {"FAM_A": 200, "FAM_AB": 200, "FAM_B": 403, "FAM_C": 404}


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("put", "/api/families/FAM_B/metadata", {"status_key": None}),
        ("put", "/api/families/FAM_B/small-variants/1-2000-C-T/review", {"note": "x"}),
        ("put", "/api/families/FAM_B/structural-variants/sv1/review", {"note": "x"}),
        ("post", "/api/families/FAM_B/report/sign-out", None),
        ("post", "/api/ped/manual", {"family_id": "NEW_FAM", "project_id": PROJECT_B, "members": [{"sample_id": "NEW_S1"}]}),
    ],
)
def test_a_reader_is_refused_reviews_sign_out_family_edits_and_new_families(
    api: dict, method: str, path: str, body: Any
) -> None:
    kwargs = {"json": body} if body is not None else {}
    response = api["client"].request(method, path, headers=_as(READER), **kwargs)
    assert (response.status_code, response.json()) == (403, {"detail": READ_ONLY_DETAIL})


def test_a_reader_saves_and_deletes_their_own_filter_presets(api: dict) -> None:
    client, headers = api["client"], _as(READER)
    preset_id = "00000000-0000-4000-8000-0000000000e2"
    answers = [
        client.post("/api/families/FAM_B/small-variant-filter-presets", json={"name": "mine"}, headers=headers),
        client.post("/api/families/FAM_B/structural-variant-filter-presets", json={"name": "mine"}, headers=headers),
        client.delete(f"/api/auth/small-variant-filter-presets/{preset_id}", headers=headers),
    ]

    assert [answer.status_code for answer in answers] == [200, 200, 204]
    assert [name for name, _call in api["reached"]] == ["save_preset", "save_sv_preset", "delete_my_preset"]


def test_a_reader_is_refused_the_audit_trail_and_an_analyst_reads_it(api: dict) -> None:
    refused = api["client"].get("/api/families/FAM_B/clinical-audit", headers=_as(READER))
    read = api["client"].get("/api/families/FAM_A/clinical-audit", headers=_as(MIXED))

    assert (refused.status_code, refused.json()) == (403, {"detail": AUDIT_TRAIL_DETAIL})
    assert (read.status_code, read.json()) == (200, {"family_id": "FAM_A", "total": 0, "has_more": False, "events": []})


def test_the_user_says_which_of_their_projects_they_only_read(api: dict) -> None:
    me = api["client"].get("/api/auth/me", headers=_as(MIXED)).json()
    assert (me["projects"], me["reader_projects"]) == ([PROJECT_A, PROJECT_B], [PROJECT_B])


# --------------------------------------------------------------------------- #
# The catalogue of routes that change something
# --------------------------------------------------------------------------- #

# The routes whose change touches only the caller's own data: a reader may call them.
OWN_DATA_ROUTES = {
    ("DELETE", "/api/auth/small-variant-filter-presets/{preset_id}"),
    ("POST", "/api/families/{family_id}/small-variant-filter-presets"),
    ("POST", "/api/families/{family_id}/structural-variant-filter-presets"),
    ("DELETE", "/api/families/{family_id}/structural-variant-filter-presets/{preset_id}"),
    ("POST", "/api/ui-events"),
}
# The routes that change a record any signed-in member may reach: refused a reader at the
# checkpoint of the family or project they name.
CHECKPOINT_ROUTES = {
    ("POST", "/api/ped/manual"),
    ("PUT", "/api/families/{family_id}/metadata"),
    ("POST", "/api/families/{family_id}/members/{sample_id}/hpo"),
    ("PUT", "/api/families/{family_id}/hpo/{annotation_id}"),
    ("DELETE", "/api/families/{family_id}/hpo/{annotation_id}"),
    ("PUT", "/api/families/{family_id}/small-variants/{variant_id}/review"),
    ("PUT", "/api/families/{family_id}/structural-variants/{variant_id}/review"),
    ("POST", "/api/families/{family_id}/report/sign-out"),
}
# Sign-in and sign-up: no one is signed in yet.
PUBLIC_ROUTES = {("POST", "/api/auth/signup"), ("POST", "/api/auth/login"), ("POST", "/api/auth/token")}


def _routes(routes: Any) -> Iterator[Any]:
    """Every route of the app. FastAPI wraps an included router in an ``_IncludedRouter``,
    whose ``effective_candidates`` are its routes with their full paths and dependencies."""
    for route in routes:
        if hasattr(route, "effective_candidates"):
            yield from _routes(route.effective_candidates())
        elif isinstance(route, APIRoute) or getattr(route, "dependant", None) is not None:
            yield route


def _calls(dependant: Any) -> set[Any]:
    found: set[Any] = set()
    for dependency in dependant.dependencies:
        found.add(dependency.call)
        found |= _calls(dependency)
    return found


def _change_routes() -> dict[tuple[str, str], Any]:
    return {
        (method, route.path_format): route
        for route in _routes(app.routes)
        for method in route.methods or ()
        if method not in ("GET", "HEAD", "OPTIONS")
    }


def test_every_change_route_is_classified() -> None:
    admin, own, checkpoint, public, unknown = set(), set(), set(), set(), set()
    for key, route in _change_routes().items():
        calls = _calls(route.dependant)
        if dependencies.get_current_admin_user in calls:
            admin.add(key)
        elif not request_changes_records(key[0], route.endpoint):
            own.add(key)
        elif dependencies.get_current_user not in calls:
            public.add(key)
        elif key in CHECKPOINT_ROUTES:
            checkpoint.add(key)
        else:
            unknown.add(key)

    # A route that changes something must be an admin's, the caller's own data (marked
    # changes_only_own_data) or refused a reader at a checkpoint: list a new one here once
    # the e2e sweep shows how it answers a reader.
    assert unknown == set()
    assert own == OWN_DATA_ROUTES
    assert checkpoint == CHECKPOINT_ROUTES
    assert public == PUBLIC_ROUTES
    # A floor, so a walk that found no route cannot pass.
    assert len(admin) >= 55, sorted(admin)


# --------------------------------------------------------------------------- #
# Who is a reader: the project's members and their levels
# --------------------------------------------------------------------------- #


class _CapturingSession:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.commits = 0

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Rows:
        self.statements.append((" ".join(str(statement).split()), dict(params or {})))
        return _Rows()

    async def commit(self) -> None:
        self.commits += 1

    def inserted_levels(self) -> dict[str, str]:
        return {
            params["user_id"]: params["access_level"]
            for sql, params in self.statements
            if sql.startswith("INSERT INTO project_users")
        }


def test_each_member_is_stored_with_their_level() -> None:
    session = _CapturingSession()
    asyncio.run(metadata_service._set_project_user_links(session, PROJECT_B, ["u1", "u2", "u3"], ["u2"]))

    assert session.statements[0][0].startswith("DELETE FROM project_users")
    assert session.inserted_levels() == {"u1": "analyst", "u2": "reader", "u3": "analyst"}


def test_a_reader_who_is_no_member_is_refused_before_anything_is_written() -> None:
    session = _CapturingSession()
    refused = _refusal(lambda: asyncio.run(metadata_service._set_project_user_links(session, PROJECT_B, ["u1"], ["u2"])))

    assert refused.status_code == 400
    assert session.statements == []


def _project_update(monkeypatch: pytest.MonkeyPatch, **changes: Any) -> dict[str, str]:
    """The levels an update of project B stores; B has members u1 (analyst) and u2 (reader)."""
    existing = {
        "id": PROJECT_B,
        "name": "B",
        "description": None,
        "species_id": "species",
        "assembly_id": "assembly",
        "metadata": {},
        "user_ids": ["u1", "u2"],
        "reader_user_ids": ["u2"],
    }

    async def project(session: Any, project_id: str) -> dict[str, Any]:
        return existing

    async def known_users(session: Any, user_ids: list[str]) -> list[str]:
        return list(dict.fromkeys(user_ids))

    monkeypatch.setattr(metadata_service, "_fetch_project_mapping", project)
    monkeypatch.setattr(metadata_service, "_validate_project_user_ids", known_users)
    session = _CapturingSession()
    asyncio.run(
        metadata_service.update_project_record(
            session,
            project_id=PROJECT_B,
            name=None,
            description=None,
            species_id=None,
            assembly_id=None,
            user_ids=changes.get("user_ids"),
            reader_user_ids=changes.get("reader_user_ids"),
        )
    )
    assert session.commits == 1
    return session.inserted_levels()


def test_an_update_without_a_reader_list_keeps_each_members_level(monkeypatch: pytest.MonkeyPatch) -> None:
    # A member added is an analyst; the ones that stay keep their level.
    assert _project_update(monkeypatch, user_ids=["u1", "u2", "u3"]) == {"u1": "analyst", "u2": "reader", "u3": "analyst"}
    # A reader removed from the project is no reader of it any more.
    assert _project_update(monkeypatch, user_ids=["u1"]) == {"u1": "analyst"}
    # Only the levels changed.
    assert _project_update(monkeypatch, reader_user_ids=["u1"]) == {"u1": "reader", "u2": "analyst"}


def test_the_family_and_the_project_say_whether_the_caller_only_reads_them() -> None:
    users = _users()
    row = {"id": "uuid-FAM_B", "family_id": "FAM_B", "created_at": datetime(2026, 10, 14, tzinfo=timezone.utc)}

    def read_only(email: str, project_ids: list[str]) -> bool:
        return metadata_service._family_out_from_rows(row, [], project_ids, users[email]).read_only

    assert read_only(READER, [PROJECT_B]) is True
    assert read_only(MIXED, [PROJECT_B]) is True
    assert read_only(MIXED, [PROJECT_A, PROJECT_B]) is False
    assert read_only(ADMIN, [PROJECT_B]) is False


def test_the_project_list_says_which_projects_the_caller_only_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {"id": project_id, "name": name, "species_id": "s", "assembly_id": "a", "user_ids": ["u1"], "reader_user_ids": readers}
        for project_id, name, readers in ((PROJECT_A, "A", []), (PROJECT_B, "B", ["u1"]))
    ]

    async def projects(session: Any, *, project_ids: list[str] | None = None) -> list[dict[str, Any]]:
        return rows

    async def no_families(session: Any, project_ids: list[str], user: CurrentUser) -> tuple[dict, dict]:
        return {}, {}

    monkeypatch.setattr(metadata_service, "_fetch_project_mappings", projects)
    monkeypatch.setattr(metadata_service, "_fetch_project_family_rows", no_families)
    dashboards = asyncio.run(metadata_service.list_project_dashboards(None, _users()[MIXED]))

    assert [(project.name, project.read_only, project.reader_user_ids) for project in dashboards] == [
        ("A", False, []),
        ("B", True, ["u1"]),
    ]


def test_the_signed_in_user_and_the_user_list_carry_the_reader_projects(monkeypatch: pytest.MonkeyPatch) -> None:
    mapping = {
        "id": "u1",
        "username": READER,
        "email": READER,
        "role": "viewer",
        "is_active": True,
        "created_at": datetime(2026, 10, 14, tzinfo=timezone.utc),
        "metadata_project_ids": [PROJECT_A, PROJECT_B],
        "reader_project_ids": [PROJECT_B],
    }

    async def by_email(session: Any, email: str) -> dict[str, Any]:
        return mapping

    monkeypatch.setattr(metadata_service, "_fetch_user_mapping_by_email", by_email)
    user = asyncio.run(metadata_service.get_current_user_by_email(None, READER))

    assert user is not None
    assert (user.metadata_project_ids, user.reader_project_ids) == ([PROJECT_A, PROJECT_B], [PROJECT_B])
    # Signed in, the user changes nothing until get_current_user says the request does.
    assert user.changes_records is False
    listed = metadata_service._user_read_from_mapping(mapping)
    assert (listed.projects, listed.reader_projects) == ([PROJECT_A, PROJECT_B], [PROJECT_B])
