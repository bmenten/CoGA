import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException

from backend.app.services import metadata_service
from backend.app.services.access_control import CurrentUser, RecordNotVisible, visible_metadata_project_ids
from backend.app.services.ped_service import (
    _ensure_user_can_replace_existing_families,
    _resolve_accessible_project_id,
)


class _ScalarResult:
    def __init__(self, value: str | None):
        self.value = value

    def scalar_one_or_none(self) -> str | None:
        return self.value


class _ProjectLookupSession:
    def __init__(self, existing_project_id: str | None):
        self.existing_project_id = existing_project_id

    async def execute(self, statement, params):
        return _ScalarResult(self.existing_project_id)


def _user(role: str, project_ids: list[str]) -> CurrentUser:
    return CurrentUser(
        id=str(uuid4()),
        username="viewer@example.com",
        email="viewer@example.com",
        role=role,
        projects=project_ids,
        metadata_project_ids=project_ids,
        created_at=datetime.now(timezone.utc),
    )


def _refusal(call) -> HTTPException:
    with pytest.raises(HTTPException) as refused:
        asyncio.run(call)
    return refused.value


def _answer(refusal: HTTPException) -> tuple[int, object, object]:
    """What the client gets: the status, the body's detail and the headers."""
    return refusal.status_code, refusal.detail, refusal.headers


def _hidden_kind(refusal: HTTPException) -> str | None:
    """What only the request's audit row gets: the kind of record that exists but was hidden."""
    return refusal.kind if isinstance(refusal, RecordNotVisible) else None


def test_resolve_accessible_project_id_requires_viewer_assignment() -> None:
    # Another team's project answers exactly like a project that does not exist (REQ-SEC-001).
    allowed_project_id = str(uuid4())
    hidden_project_id = str(uuid4())
    user = _user("viewer", [allowed_project_id])

    hidden = _refusal(
        _resolve_accessible_project_id(_ProjectLookupSession(hidden_project_id), user, hidden_project_id)
    )
    unknown = _refusal(_resolve_accessible_project_id(_ProjectLookupSession(None), user, str(uuid4())))

    assert _answer(hidden) == _answer(unknown) == (404, "Project not found", None)
    assert (_hidden_kind(hidden), _hidden_kind(unknown)) == ("project", None)


def test_resolve_accessible_project_id_accepts_assigned_project() -> None:
    project_id = str(uuid4())

    resolved_project_id = asyncio.run(
        _resolve_accessible_project_id(
            _ProjectLookupSession(project_id),
            _user("viewer", [project_id]),
            project_id,
        )
    )

    assert resolved_project_id == project_id


def test_viewer_family_project_ids_are_filtered_to_visible_projects() -> None:
    visible_project_id = str(uuid4())
    hidden_project_id = str(uuid4())

    assert visible_metadata_project_ids(
        [hidden_project_id, visible_project_id],
        _user("viewer", [visible_project_id]),
    ) == [visible_project_id]


def test_non_admin_cannot_replace_existing_families() -> None:
    # Replacing existing families/samples is admin-only. This is a role-based
    # policy, not a per-project check — the function does not inspect rows.
    with pytest.raises(HTTPException) as exc_info:
        _ensure_user_can_replace_existing_families(_user("viewer", []))

    assert exc_info.value.status_code == 403


def test_admin_can_replace_existing_families() -> None:
    # An admin is permitted (no exception raised).
    _ensure_user_can_replace_existing_families(_user("admin", []))


# --- PHI access gate: get_accessible_family_mapping / get_accessible_sample_mapping ---------
# The central checkpoint every family/sample endpoint flows through
# (build_family_metadata_context -> get_accessible_family_mapping, and the sample
# equivalent). The cases below cover the cross-user / multi-project / admin scenarios so a
# regression that widens access is caught, and hold a record outside the caller's projects
# to the answer an unknown one gets, so an ID reveals nothing (REQ-SEC-001).


def _families(monkeypatch, *rows: dict) -> None:
    async def fetch(session, *, family_identifiers):
        return [row for row in rows if row["family_id"] in family_identifiers]

    monkeypatch.setattr(metadata_service, "_fetch_family_rows", fetch)


def _samples(monkeypatch, *rows: dict) -> None:
    async def fetch(session, sample_identifier):
        return next((row for row in rows if row["sample_id"] == sample_identifier), None)

    monkeypatch.setattr(metadata_service, "_fetch_sample_access_mapping", fetch)


def _family(user: CurrentUser, family_id: str = "FAM1"):
    return metadata_service.get_accessible_family_mapping(None, family_id, user)


def _sample(user: CurrentUser, sample_id: str = "S1"):
    return metadata_service.get_accessible_sample_mapping(None, sample_id, user)


def test_viewer_cannot_access_family_in_unassigned_project(monkeypatch) -> None:
    # The core IDOR guard: a viewer passing a family_id whose project they are not a member
    # of is refused exactly as for a family that does not exist.
    owner_project = str(uuid4())
    intruder = _user("viewer", [str(uuid4())])
    _families(monkeypatch, {"id": "f1", "family_id": "FAM1", "project_ids": [owner_project]})

    hidden, unknown = _refusal(_family(intruder)), _refusal(_family(intruder, "NO_SUCH_FAMILY"))

    assert _answer(hidden) == _answer(unknown) == (404, "Family not found", None)
    assert (_hidden_kind(hidden), _hidden_kind(unknown)) == ("family", None)


def test_viewer_can_access_family_sharing_one_assigned_project(monkeypatch) -> None:
    # A family linked to several projects is visible if the viewer is a member of at least
    # one of them.
    shared = str(uuid4())
    row = {"id": "f1", "family_id": "FAM1", "project_ids": [str(uuid4()), shared]}
    _families(monkeypatch, row)

    assert asyncio.run(_family(_user("viewer", [shared]))) == row


def test_viewer_with_no_projects_is_denied(monkeypatch) -> None:
    viewer = _user("viewer", [])
    _families(monkeypatch, {"id": "f1", "family_id": "FAM1", "project_ids": [str(uuid4())]})

    assert _answer(_refusal(_family(viewer))) == (404, "Family not found", None)
    assert _answer(_refusal(_family(viewer, "NO_SUCH_FAMILY"))) == (404, "Family not found", None)


def test_a_family_in_no_project_is_hidden_from_a_viewer_and_shown_to_an_admin(monkeypatch) -> None:
    row = {"id": "f1", "family_id": "FAM1", "project_ids": []}
    _families(monkeypatch, row)

    assert _answer(_refusal(_family(_user("viewer", [str(uuid4())])))) == (404, "Family not found", None)
    assert asyncio.run(_family(_user("admin", []))) == row


def test_admin_bypasses_project_scoping(monkeypatch) -> None:
    # Admins are not project-scoped: access is granted even with no project assignments and
    # a family in an arbitrary project.
    row = {"id": "f1", "family_id": "FAM1", "project_ids": [str(uuid4())]}
    _families(monkeypatch, row)

    assert asyncio.run(_family(_user("admin", []))) == row


def test_viewer_cannot_access_sample_whose_family_is_in_an_unassigned_project(monkeypatch) -> None:
    # A sample is seen through its family's projects; outside them it answers like an
    # unknown sample.
    family_project = str(uuid4())
    _samples(monkeypatch, {"id": "s1", "sample_id": "S1", "family_project_ids": [family_project]})
    intruder = _user("viewer", [str(uuid4())])

    hidden, unknown = _refusal(_sample(intruder)), _refusal(_sample(intruder, "NO_SUCH_SAMPLE"))

    assert _answer(hidden) == _answer(unknown) == (404, "Sample not found", None)
    assert (_hidden_kind(hidden), _hidden_kind(unknown)) == ("sample", None)
    assert asyncio.run(_sample(_user("viewer", [family_project])))["id"] == "s1"
    assert asyncio.run(_sample(_user("admin", [])))["id"] == "s1"


def test_a_hidden_family_gets_an_unknown_ones_response_and_only_its_audit_row_says_so(monkeypatch) -> None:
    # Through the app: the response is made by the handler every 404 goes through, and the
    # request audit (request_logging) names the hidden record in request_meta.
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.postgres import get_postgres_session
    from backend.app.dependencies import get_current_user
    from backend.app.main import app
    from backend.app.middleware import request_logging

    _families(monkeypatch, {"id": "f1", "family_id": "FAM1", "project_ids": [str(uuid4())]})
    audited: list = []

    async def capture(payload) -> None:
        audited.append(payload)

    async def no_session():
        yield None

    async def a_viewer() -> CurrentUser:
        return _user("viewer", [str(uuid4())])

    monkeypatch.setattr(request_logging, "write_audit_log_event", capture)
    monkeypatch.setitem(app.dependency_overrides, get_postgres_session, no_session)
    monkeypatch.setitem(app.dependency_overrides, get_current_user, a_viewer)

    async def ask() -> list:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return [await client.get(f"/api/families/{family_id}") for family_id in ("FAM1", "NO_SUCH_FAMILY")]

    hidden, unknown = asyncio.run(ask())

    assert hidden.json() == {"detail": "Family not found"}
    assert (hidden.status_code, hidden.content, hidden.headers.items()) == (
        unknown.status_code,
        unknown.content,
        unknown.headers.items(),
    )
    assert [payload.status_code for payload in audited] == [404, 404]
    assert [payload.request_meta.get("record_hidden") for payload in audited] == ["family", None]


def test_admin_sees_all_project_ids_while_viewer_sees_only_assigned() -> None:
    p_assigned, p_foreign = str(uuid4()), str(uuid4())
    family_projects = [p_assigned, p_foreign]

    assert visible_metadata_project_ids(family_projects, _user("admin", [])) == family_projects
    assert visible_metadata_project_ids(family_projects, _user("viewer", [p_assigned])) == [p_assigned]
    assert visible_metadata_project_ids(family_projects, _user("viewer", [])) == []
