"""Each role check in the services answers as the user's role says (services/access_control.py).

Each check asks ``access_control`` by what it guards, rather than comparing the role with the
literal ``"admin"``, which would refuse a superuser the routes let in: a superuser's and an
admin's (``has_superuser_rights``: every project, gene panels, variant tags, import jobs) or an
admin's alone (``is_admin_user``: overwriting a family, the user list). These tests hold the
roles that have the right to the same answer, and the roles below them to the refusal, except
that a project outside the user's own answers 404 "Project not found", like one that does not
exist (REQ-SEC-001).
"""

from __future__ import annotations

import ast
import pathlib
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app.core.postgres import get_postgres_session
from backend.app.dependencies import get_current_user
from backend.app.main import app
from backend.app.routers import auth as auth_router
from backend.app.schemas import SmallVariantTagDefinitionCreate, SmallVariantTagDefinitionUpdate
from backend.app.services import (
    family_package_jobs,
    gene_metadata_service,
    panel_metadata_service,
    ped_service,
    small_variant_review_tags,
)
from backend.app.services.access_control import CurrentUser

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"
# The roles with every project, gene panels, variant tags and import jobs.
SUPERUSER_ROLES = ["admin", "superuser"]
# The roles below them: each reads its own projects only, and configures nothing.
BELOW_SUPERUSER = ["viewer", "analyst", "supervisor"]
PROJECT = "11111111-1111-1111-1111-111111111111"
JOB_ID = "22222222-2222-2222-2222-222222222222"


def _user(role: str, projects: list[str] | None = None) -> CurrentUser:
    return CurrentUser(
        id="u1",
        username="someone",
        email="someone@example.org",
        role=role,
        metadata_project_ids=projects or [],
        created_at=datetime(2026, 9, 29, tzinfo=timezone.utc),
    )


class _Result:
    def __init__(self, rows: list[dict[str, Any]] | None = None, scalar: Any = None) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def mappings(self) -> "_Result":
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class _Session:
    """Answers each statement with the next queued result and records the SQL."""

    def __init__(self, *results: _Result) -> None:
        self._results = list(results)
        self.statements: list[str] = []
        self.params: list[Any] = []

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        self.statements.append(str(statement))
        self.params.append(params)
        return self._results.pop(0)

    async def rollback(self) -> None:
        return None


ROLE_NAMES = {"viewer", "analyst", "supervisor", "superuser", "admin"}


def test_no_check_compares_a_role_with_a_literal_role() -> None:
    # access_control.py names the roles once; every other check asks it.
    offenders = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if path.name == "access_control.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Compare):
                continue
            operands = [node.left, *node.comparators]
            names_a_role = any(
                (isinstance(operand, ast.Attribute) and operand.attr == "role")
                or (isinstance(operand, ast.Name) and operand.id == "role")
                for operand in operands
            )
            if names_a_role and any(
                isinstance(operand, ast.Constant) and operand.value in ROLE_NAMES for operand in operands
            ):
                offenders.append(f"{path.relative_to(APP_DIR)}:{node.lineno}")
    assert offenders == [], "ask access_control (is_admin_user, has_superuser_rights, ...): " + ", ".join(offenders)


# --- gene panels ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", SUPERUSER_ROLES)
def test_a_superuser_and_an_admin_may_change_gene_panels(role: str) -> None:
    panel_metadata_service._ensure_admin(_user(role))


@pytest.mark.parametrize("role", BELOW_SUPERUSER)
def test_the_roles_below_superuser_may_not_change_gene_panels(role: str) -> None:
    with pytest.raises(HTTPException) as refused:
        panel_metadata_service._ensure_admin(_user(role))
    assert refused.value.status_code == 403


# --- gene profile --------------------------------------------------------------------------


class _PastTheProjectCheck(Exception):
    """The gene profile went on to its next step: the project check let the user through."""


async def _gene_profile_in_project(monkeypatch, user: CurrentUser, *, project_exists: bool = True) -> None:
    async def next_step(session: Any) -> None:
        raise _PastTheProjectCheck

    monkeypatch.setattr(gene_metadata_service, "_get_human_context", next_step)
    found = _Result([{"id": PROJECT, "assembly_id": "a1"}] if project_exists else [])
    await gene_metadata_service.build_gene_profile(
        _Session(found), symbol="GENE1", assembly_id=None, family_id=None, project_id=PROJECT, user=user
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("role", SUPERUSER_ROLES)
async def test_a_superuser_and_an_admin_read_a_gene_profile_in_any_project(monkeypatch, role: str) -> None:
    with pytest.raises(_PastTheProjectCheck):
        await _gene_profile_in_project(monkeypatch, _user(role))


@pytest.mark.asyncio
async def test_a_viewer_reads_gene_profiles_in_their_own_projects_only(monkeypatch) -> None:
    with pytest.raises(_PastTheProjectCheck):
        await _gene_profile_in_project(monkeypatch, _user("viewer", [PROJECT]))
    with pytest.raises(HTTPException) as refused:
        await _gene_profile_in_project(monkeypatch, _user("viewer"))
    with pytest.raises(HTTPException) as unknown:
        await _gene_profile_in_project(monkeypatch, _user("viewer"), project_exists=False)
    # Another team's project answers exactly like one that does not exist (REQ-SEC-001).
    assert (refused.value.status_code, refused.value.detail) == (404, "Project not found")
    assert (unknown.value.status_code, unknown.value.detail) == (404, "Project not found")


@pytest.mark.asyncio
async def test_a_family_gene_profile_reads_the_first_of_its_projects_the_user_sees(monkeypatch) -> None:
    hidden = "33333333-3333-3333-3333-333333333333"
    asked: list[dict[str, Any]] = []

    async def family(session: Any, family_id: str, user: CurrentUser) -> dict[str, Any]:
        return {"id": "family-uuid", "family_id": family_id, "project_ids": [hidden, PROJECT]}

    class _Recording:
        async def execute(self, statement: Any, params: Any = None) -> _Result:
            asked.append(dict(params or {}))
            return _Result([{"id": PROJECT, "assembly_id": "a1"}])

    async def next_step(session: Any) -> None:
        raise _PastTheProjectCheck

    monkeypatch.setattr(gene_metadata_service, "get_accessible_family_mapping", family)
    monkeypatch.setattr(gene_metadata_service, "_get_human_context", next_step)

    with pytest.raises(_PastTheProjectCheck):
        await gene_metadata_service.build_gene_profile(
            _Recording(),  # type: ignore[arg-type]
            symbol="GENE1",
            assembly_id=None,
            family_id="FAM1",
            project_id=None,
            user=_user("viewer", [PROJECT]),
        )

    # It took the family's first project, another team's, and read that project's assembly (SEC-9).
    assert asked == [{"project_id": PROJECT}]


# --- families from a PED file or by hand ---------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("role", SUPERUSER_ROLES)
async def test_a_superuser_and_an_admin_may_create_families_in_any_project(role: str) -> None:
    user = _user(role)
    assert await ped_service._resolve_accessible_project_id(_Session(), user, None) is None
    found = _Session(_Result(scalar=PROJECT))
    assert await ped_service._resolve_accessible_project_id(found, user, PROJECT) == PROJECT


def test_only_an_admin_may_overwrite_existing_families() -> None:
    # Overwriting deletes the family there was: an admin's, as every deletion is.
    ped_service._ensure_user_can_replace_existing_families(_user("admin"))
    for role in ["superuser", *BELOW_SUPERUSER]:
        with pytest.raises(HTTPException) as refused:
            ped_service._ensure_user_can_replace_existing_families(_user(role))
        assert refused.value.status_code == 403


@pytest.mark.asyncio
async def test_a_viewer_needs_a_project_of_their_own_and_may_not_overwrite() -> None:
    viewer = _user("viewer")
    with pytest.raises(HTTPException) as no_project:
        await ped_service._resolve_accessible_project_id(_Session(), viewer, None)
    assert no_project.value.status_code == 400
    with pytest.raises(HTTPException) as not_theirs:
        await ped_service._resolve_accessible_project_id(_Session(_Result(scalar=PROJECT)), viewer, PROJECT)
    # Answered like a project that does not exist (REQ-SEC-001).
    assert (not_theirs.value.status_code, not_theirs.value.detail) == (404, "Project not found")
    with pytest.raises(HTTPException) as overwrite:
        ped_service._ensure_user_can_replace_existing_families(viewer)
    assert overwrite.value.status_code == 403


# --- family package import jobs ------------------------------------------------------------


def _job(requested_by: str) -> dict[str, Any]:
    return {
        "id": JOB_ID,
        "submitted_path": "/imports/FAM1",
        "family_id": "FAM1",
        "project_id": None,
        "status": "queued",
        "dry_run": False,
        "requested_by": requested_by,
        "requested_at": datetime(2026, 9, 29, tzinfo=timezone.utc),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("role", SUPERUSER_ROLES)
async def test_a_superuser_and_an_admin_see_every_import_job(role: str) -> None:
    user = _user(role)
    job = await family_package_jobs.get_family_import_job(
        _Session(_Result([_job("other@example.org")])), job_id=JOB_ID, user=user
    )
    assert job.requested_by == "other@example.org"
    listing = _Session(_Result([]))
    await family_package_jobs.list_family_import_jobs(listing, user=user)
    assert "WHERE" not in listing.statements[0]
    assert listing.params[0] == {"limit": 25}
    with pytest.raises(HTTPException) as unknown:
        await family_package_jobs.get_family_import_job(_Session(_Result([])), job_id=JOB_ID, user=user)
    assert (unknown.value.status_code, unknown.value.detail) == (404, "Family import job not found")


@pytest.mark.asyncio
@pytest.mark.parametrize("role", BELOW_SUPERUSER)
async def test_a_role_below_superuser_reads_no_import_job(role: str) -> None:
    # Every import route needs a superuser (dependencies.get_current_superuser). The service
    # refuses a user below one as the routes do (ENG-23), before it reads anything, their own
    # job included.
    user = _user(role)
    own_job = _Session(_Result([_job(user.email)]))
    with pytest.raises(HTTPException) as refused:
        await family_package_jobs.get_family_import_job(own_job, job_id=JOB_ID, user=user)
    assert (refused.value.status_code, refused.value.detail) == (403, "Superuser access required")
    assert own_job.statements == []
    listing = _Session(_Result([]))
    with pytest.raises(HTTPException) as refused_listing:
        await family_package_jobs.list_family_import_jobs(listing, user=user)
    assert (refused_listing.value.status_code, refused_listing.value.detail) == (403, "Superuser access required")
    assert listing.statements == []


# --- variant tags --------------------------------------------------------------------------


async def _tag_refusals(user: CurrentUser) -> list[int]:
    """The status of each tag change. A built-in tag is refused without a database, so a
    refusal other than 403 means the role check let the user through."""
    calls = [
        small_variant_review_tags.create_small_variant_tag_definition(
            _Session(),
            family_uuid="f1",
            payload=SmallVariantTagDefinitionCreate(label="Secondary finding"),
            user=user,
        ),
        small_variant_review_tags.update_small_variant_tag_definition(
            _Session(),
            family_uuid="f1",
            tag_key="secondary_finding",
            payload=SmallVariantTagDefinitionUpdate(),
            user=user,
        ),
        small_variant_review_tags.delete_small_variant_tag_definition(
            _Session(), family_uuid="f1", tag_key="secondary_finding", user=user
        ),
    ]
    statuses = []
    for call in calls:
        with pytest.raises(HTTPException) as refused:
            await call
        statuses.append(refused.value.status_code)
    return statuses


@pytest.mark.asyncio
@pytest.mark.parametrize("role", SUPERUSER_ROLES)
async def test_a_superuser_and_an_admin_get_past_the_tag_checks(role: str) -> None:
    user = _user(role)
    # Create: the label is a built-in tag's (409); edit and delete: a built-in tag (400).
    assert await _tag_refusals(user) == [409, 400, 400]
    shared = _Session(_Result([{"id": PROJECT}]))
    visible = await small_variant_review_tags._ensure_projects_visible(
        shared, project_ids=[PROJECT], user=user
    )
    assert visible == [PROJECT]


@pytest.mark.asyncio
@pytest.mark.parametrize("role", BELOW_SUPERUSER)
async def test_the_roles_below_superuser_are_refused_by_the_tag_checks(role: str) -> None:
    user = _user(role)
    assert await _tag_refusals(user) == [403, 403, 403]
    with pytest.raises(HTTPException) as refused:
        await small_variant_review_tags._ensure_projects_visible(
            _Session(), project_ids=[PROJECT], user=user
        )
    assert refused.value.status_code == 403


# --- the user list -------------------------------------------------------------------------


@pytest.fixture()
def signed_in_as(monkeypatch: pytest.MonkeyPatch):
    """A client signed in with a role. The real ``get_current_admin_user`` runs on top of
    the overridden ``get_current_user``."""
    original_overrides = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True

    async def no_accounts(session: Any) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(auth_router, "list_user_accounts", no_accounts)

    async def override_get_postgres_session():
        yield _Session()

    def client_for(role: str) -> TestClient:
        async def override_get_current_user() -> CurrentUser:
            return _user(role)

        app.dependency_overrides[get_postgres_session] = override_get_postgres_session
        app.dependency_overrides[get_current_user] = override_get_current_user
        return TestClient(app)

    yield client_for
    app.dependency_overrides = original_overrides


@pytest.mark.parametrize(
    ("role", "status"),
    [("admin", 200), ("superuser", 403), ("supervisor", 403), ("analyst", 403), ("viewer", 403)],
)
def test_the_user_list_is_an_admins_alone(signed_in_as, role: str, status: int) -> None:
    with signed_in_as(role) as client:
        response = client.get("/api/auth/users")
    assert response.status_code == status
