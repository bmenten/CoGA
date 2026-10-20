"""Who is asking, and what they may see: the signed-in user and the project-scoped rules (#528).

Split out of ``metadata_service`` so that the many routers and services that need only the user
model and the access rules do not depend on the metadata layer. The rules that need the
database (``get_accessible_family_mapping``, ``get_accessible_sample_mapping``) stay with the
queries they guard, in ``metadata_service``.

Each project member has an access level (SEC-1): an analyst reads and changes the project's
records, a reader only reads them. An administrator (``ADMIN_ROLES``) has every right in every
project, member or not. A request that changes something (any method but GET, HEAD and
OPTIONS) is refused a reader at the checkpoint that decides whether the user may see the
family, sample or project it names (``require_change_access``), so no route can leave the check
out. A route whose change touches only the caller's own data is marked
``changes_only_own_data``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Iterable, Literal, TypeVar

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, EmailStr, Field

ADMIN_ROLES = {"admin", "superuser"}

# A project member's access level, stored per membership (``project_users.access_level``).
ProjectAccessLevel = Literal["analyst", "reader"]
ANALYST: ProjectAccessLevel = "analyst"
READER: ProjectAccessLevel = "reader"

# The methods that only read; a request with any other method changes something.
READING_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# What a reader is told when a request of theirs would change a record, and when they ask for
# the audit trail.
READ_ONLY_DETAIL = (
    "Read-only access: you are a reader in this project, so you can view and export its "
    "records but not change them."
)
AUDIT_TRAIL_DETAIL = (
    "Read-only access: the clinical audit trail is for analysts and administrators, and you "
    "are a reader in this family's project."
)

_OWN_DATA_MARK = "__coga_changes_only_own_data__"

_Endpoint = TypeVar("_Endpoint", bound=Callable[..., Any])


class CurrentUser(BaseModel):
    id: str
    username: str
    email: EmailStr
    first_name: str = ""
    last_name: str = ""
    affiliation: str = ""
    # The Microsoft object ID of an account that signs in with Microsoft (SEC-4); None for a
    # local account.
    azure_object_id: str | None = None
    is_active: bool = True
    role: str
    projects: list[str] = Field(default_factory=list)
    # Every project the user is a member of: they may read its records.
    metadata_project_ids: list[str] = Field(default_factory=list)
    # The member projects where the user is a reader; in the others they are an analyst.
    reader_project_ids: list[str] = Field(default_factory=list)
    # Whether the request the user is signed in for changes something: ``get_current_user``
    # sets it from the request's method and route (``request_changes_records``), and the
    # family, sample and project checkpoints then refuse a reader (``require_change_access``).
    changes_records: bool = Field(default=False, exclude=True)
    created_at: datetime

    model_config = ConfigDict(arbitrary_types_allowed=True)


def _unique_strings(values: Iterable[Any] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        if value is None:
            continue
        text_value = str(value)
        if text_value and text_value not in result:
            result.append(text_value)
    return result


def is_admin_user(user: CurrentUser) -> bool:
    return user.role in ADMIN_ROLES


def user_metadata_project_ids(user: CurrentUser) -> list[str]:
    return _unique_strings(getattr(user, "metadata_project_ids", []))


def visible_metadata_project_ids(project_ids: Iterable[Any] | None, user: CurrentUser) -> list[str]:
    """The given projects the user may see: all of them for an admin, else their own."""
    normalized_project_ids = _unique_strings(project_ids)
    if is_admin_user(user):
        return normalized_project_ids
    allowed_project_ids = set(user_metadata_project_ids(user))
    return [project_id for project_id in normalized_project_ids if project_id in allowed_project_ids]


def user_can_access_metadata_projects(project_ids: Iterable[Any] | None, user: CurrentUser) -> bool:
    """Whether the user may see a record linked to these projects: an admin always, anyone else
    when they share at least one of them.

    A caller that finds a record the user may not see raises ``RecordNotVisible``: the same 404
    an unknown record gets, so an ID tells nobody whether a record outside their projects exists.
    """
    if is_admin_user(user):
        return True
    return bool(set(_unique_strings(project_ids)).intersection(user_metadata_project_ids(user)))


def user_analyst_project_ids(user: CurrentUser) -> list[str]:
    """The member projects where the user is an analyst: every one where they are no reader."""
    readers = set(_unique_strings(getattr(user, "reader_project_ids", [])))
    return [project_id for project_id in user_metadata_project_ids(user) if project_id not in readers]


def user_can_change_metadata_projects(project_ids: Iterable[Any] | None, user: CurrentUser) -> bool:
    """Whether the user may change a record linked to these projects: an admin always, a member
    when they are an analyst in at least one of them (SEC-1).

    A record linked to several projects may be changed by an analyst of any one of them, as it
    may be seen by a member of any one of them.
    """
    if is_admin_user(user):
        return True
    return bool(set(_unique_strings(project_ids)).intersection(user_analyst_project_ids(user)))


class ReadOnlyAccess(HTTPException):
    """The 403 for a reader who asks for something only an analyst or an admin may do."""

    def __init__(self, detail: str = READ_ONLY_DETAIL) -> None:
        super().__init__(status_code=403, detail=detail)


def require_change_access(project_ids: Iterable[Any] | None, user: CurrentUser) -> None:
    """Refuse a change to a record of these projects by a user who only reads them (SEC-1).

    The family, sample and project checkpoints call this once they have found that the user
    may see the record, so a reader is told why (403) while a record they may not see still
    answers like an unknown one (404). It does nothing for a request that only reads.
    """
    if getattr(user, "changes_records", False) and not user_can_change_metadata_projects(project_ids, user):
        raise ReadOnlyAccess()


def require_audit_trail_access(project_ids: Iterable[Any] | None, user: CurrentUser) -> None:
    """Refuse a family's clinical audit trail to a user who is a reader in its projects."""
    if not user_can_change_metadata_projects(project_ids, user):
        raise ReadOnlyAccess(AUDIT_TRAIL_DETAIL)


def changes_only_own_data(endpoint: _Endpoint) -> _Endpoint:
    """Mark a route whose change touches only the caller's own data: their filter presets, the
    events of their own use of the interface. A reader may call it.

    Every other route that changes something is refused a reader at the checkpoint of the
    record it names (``require_change_access``).
    """
    setattr(endpoint, _OWN_DATA_MARK, True)
    return endpoint


def request_changes_records(method: str, endpoint: object | None) -> bool:
    """Whether a request changes something a reader may not change: any method but GET, HEAD
    and OPTIONS, unless the route is marked ``changes_only_own_data``."""
    if method.upper() in READING_METHODS:
        return False
    return not getattr(endpoint, _OWN_DATA_MARK, False)


class RecordNotVisible(HTTPException):
    """The 404 for a record that exists outside the caller's projects.

    The client gets the answer an unknown record gets, byte for byte: ``main`` hands this to
    FastAPI's own HTTPException handler. Only the request's audit row tells the two apart: it
    names ``kind`` ("family", "sample" or "project") in ``request_meta.record_hidden``, so the
    audit still shows who asked for records outside their projects (REQ-SEC-001).
    """

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(status_code=404, detail=detail)
        self.kind = kind
