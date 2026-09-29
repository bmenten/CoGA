"""Every admin check treats ``superuser`` as an admin, as ``ADMIN_ROLES`` says.

Twelve checks compared the role with the literal ``"admin"``. The admin routes let a
superuser in through ``get_current_admin_user``, and then these checks refused it: editing
variant tags and gene panels, creating a family from a PED file or by hand, reading import
jobs, reading a gene profile outside its projects, and listing the user accounts. Each check
is now ``is_admin_user``. These tests hold a superuser to the same answer as an admin, and a
viewer to the refusal it had.
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
ADMIN_ROLES = ["admin", "superuser"]
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

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        self.statements.append(str(statement))
        return self._results.pop(0)

    async def rollback(self) -> None:
        return None


def test_no_check_compares_a_role_with_the_literal_admin() -> None:
    offenders = []
    for path in sorted(APP_DIR.rglob("*.py")):
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
                isinstance(operand, ast.Constant) and operand.value == "admin" for operand in operands
            ):
                offenders.append(f"{path.relative_to(APP_DIR)}:{node.lineno}")
    assert offenders == [], "use is_admin_user(), which counts superuser too: " + ", ".join(offenders)


# --- gene panels ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", ADMIN_ROLES)
def test_every_admin_role_may_change_gene_panels(role: str) -> None:
    panel_metadata_service._ensure_admin(_user(role))


def test_a_viewer_may_not_change_gene_panels() -> None:
    with pytest.raises(HTTPException) as refused:
        panel_metadata_service._ensure_admin(_user("viewer"))
    assert refused.value.status_code == 403


# --- gene profile --------------------------------------------------------------------------


@pytest.mark.parametrize("role", ADMIN_ROLES)
def test_every_admin_role_reads_a_gene_profile_in_any_project(role: str) -> None:
    gene_metadata_service._ensure_project_access(PROJECT, _user(role))


def test_a_viewer_reads_gene_profiles_in_their_own_projects_only() -> None:
    gene_metadata_service._ensure_project_access(PROJECT, _user("viewer", [PROJECT]))
    with pytest.raises(HTTPException) as refused:
        gene_metadata_service._ensure_project_access(PROJECT, _user("viewer"))
    assert refused.value.status_code == 403


# --- families from a PED file or by hand ---------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ADMIN_ROLES)
async def test_every_admin_role_may_create_families_in_any_project_and_overwrite(role: str) -> None:
    user = _user(role)
    assert await ped_service._resolve_accessible_project_id(_Session(), user, None) is None
    found = _Session(_Result(scalar=PROJECT))
    assert await ped_service._resolve_accessible_project_id(found, user, PROJECT) == PROJECT
    ped_service._ensure_user_can_replace_existing_families(user)


@pytest.mark.asyncio
async def test_a_viewer_needs_a_project_of_their_own_and_may_not_overwrite() -> None:
    viewer = _user("viewer")
    with pytest.raises(HTTPException) as no_project:
        await ped_service._resolve_accessible_project_id(_Session(), viewer, None)
    assert no_project.value.status_code == 400
    with pytest.raises(HTTPException) as not_theirs:
        await ped_service._resolve_accessible_project_id(_Session(_Result(scalar=PROJECT)), viewer, PROJECT)
    assert not_theirs.value.status_code == 403
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
@pytest.mark.parametrize("role", ADMIN_ROLES)
async def test_every_admin_role_sees_every_import_job(role: str) -> None:
    user = _user(role)
    job = await family_package_jobs.get_family_import_job(
        _Session(_Result([_job("other@example.org")])), job_id=JOB_ID, user=user
    )
    assert job.requested_by == "other@example.org"
    listing = _Session(_Result([]))
    await family_package_jobs.list_family_import_jobs(listing, user=user)
    assert "requested_by = :requested_by" not in listing.statements[0]


@pytest.mark.asyncio
async def test_a_viewer_sees_only_their_own_import_jobs() -> None:
    viewer = _user("viewer")
    with pytest.raises(HTTPException) as refused:
        await family_package_jobs.get_family_import_job(
            _Session(_Result([_job("other@example.org")])), job_id=JOB_ID, user=viewer
        )
    assert refused.value.status_code == 403
    listing = _Session(_Result([]))
    await family_package_jobs.list_family_import_jobs(listing, user=viewer)
    assert "requested_by = :requested_by" in listing.statements[0]


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
@pytest.mark.parametrize("role", ADMIN_ROLES)
async def test_every_admin_role_gets_past_the_tag_admin_checks(role: str) -> None:
    user = _user(role)
    # Create: the label is a built-in tag's (409); edit and delete: a built-in tag (400).
    assert await _tag_refusals(user) == [409, 400, 400]
    shared = _Session(_Result([{"id": PROJECT}]))
    visible = await small_variant_review_tags._ensure_projects_visible(
        shared, project_ids=[PROJECT], user=user
    )
    assert visible == [PROJECT]


@pytest.mark.asyncio
async def test_a_viewer_is_refused_by_the_tag_admin_checks() -> None:
    viewer = _user("viewer")
    assert await _tag_refusals(viewer) == [403, 403, 403]
    with pytest.raises(HTTPException) as refused:
        await small_variant_review_tags._ensure_projects_visible(
            _Session(), project_ids=[PROJECT], user=viewer
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


@pytest.mark.parametrize(("role", "status"), [("admin", 200), ("superuser", 200), ("viewer", 403)])
def test_the_user_list_is_open_to_every_admin_role(signed_in_as, role: str, status: int) -> None:
    with signed_in_as(role) as client:
        response = client.get("/api/auth/users")
    assert response.status_code == status
