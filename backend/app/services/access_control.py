"""Who is asking, and what they may see: the signed-in user and the project-scoped rules.

The many routers and services that need only the user model and the access rules read them
from here, without depending on the metadata layer. The rules that need the database (``get_accessible_family_mapping``, ``get_accessible_sample_mapping``) stay with the
queries they guard, in ``metadata_service``.

Two things decide what a user may do with a record:

- Their **global role** (``users.role``, ``USER_ROLES``), lowest first; each has every right of
  the roles below it. A *viewer* only reads the records of their projects. An *analyst* also
  analyses them: reviews, tags, classifications, phenotypes, workflow fields, new families. A
  *supervisor* also signs an analysis out (TF-06 H15). A *superuser* also has every project,
  member or not, and the configuration the analyses read: gene panels, variant tags, filter
  presets, sequencing QC thresholds, and package import. An *admin* also has the rest of the
  administration: accounts, projects, reference data, data deletion, audit.
- Their **access level** in each project they are a member of: an analyst reads and
  changes the project's records, a reader only reads them. The level can only take rights
  away: a viewer changes nothing in any project, whatever their level there.

A request that changes something (any method but GET, HEAD and OPTIONS) is refused a viewer
and a reader at the checkpoint that decides whether the user may see the family, sample or
project it names (``require_change_access``), so no route can leave the check out. A route
whose change touches only the caller's own data is marked ``changes_only_own_data``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Iterable, Literal, TypeVar

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, EmailStr, Field

# The global roles, lowest first: the CHECK on users.role, in the same order. Each has every
# right of the roles before it (``has_role``).
UserRole = Literal["viewer", "analyst", "supervisor", "superuser", "admin"]
USER_ROLES: tuple[UserRole, ...] = ("viewer", "analyst", "supervisor", "superuser", "admin")
ROLE_VIEWER: UserRole = "viewer"
ROLE_ANALYST: UserRole = "analyst"
ROLE_SUPERVISOR: UserRole = "supervisor"
ROLE_SUPERUSER: UserRole = "superuser"
ROLE_ADMIN: UserRole = "admin"

# The roles that hold a right: its lowest role and every role above it.
CHANGING_ROLES = frozenset(USER_ROLES[USER_ROLES.index(ROLE_ANALYST) :])
SIGN_OUT_ROLES = frozenset(USER_ROLES[USER_ROLES.index(ROLE_SUPERVISOR) :])
# The roles with every project, member or not, and the configuration the analyses read.
SUPERUSER_ROLES = frozenset(USER_ROLES[USER_ROLES.index(ROLE_SUPERUSER) :])
# The role with the whole administration.
ADMIN_ROLES = frozenset({ROLE_ADMIN})

# A project member's access level, stored per membership (``project_users.access_level``).
ProjectAccessLevel = Literal["analyst", "reader"]
ANALYST: ProjectAccessLevel = "analyst"
READER: ProjectAccessLevel = "reader"

# The methods that only read; a request with any other method changes something.
READING_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# What a reader, and a viewer, is told when a request of theirs would change a record, and
# when they ask for the audit trail.
READ_ONLY_DETAIL = (
    "Read-only access: you are a reader in this project, so you can view and export its "
    "records but not change them."
)
VIEWER_READ_ONLY_DETAIL = (
    "Read-only access: your role is viewer, so you can view and export records but not "
    "change them."
)
AUDIT_TRAIL_DETAIL = (
    "Read-only access: the clinical audit trail is for analysts and administrators, and you "
    "are a reader in this family's project."
)
VIEWER_AUDIT_TRAIL_DETAIL = (
    "Read-only access: the clinical audit trail is for analysts and administrators, and your "
    "role is viewer."
)
# What an analyst is told who asks to sign an analysis out (TF-06 H15).
SIGN_OUT_ROLE_DETAIL = (
    "Signing out needs the supervisor role: ask a supervisor of this family's project to sign "
    "out the analysis."
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
    # The Microsoft object ID of an account that signs in with Microsoft; None for a
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
    # When the account was first active (None while it waits for approval), and when it was
    # archived (None when it is not).
    activated_at: datetime | None = None
    archived_at: datetime | None = None
    # When the account last signed out (None before its first logout): ``get_current_user``
    # refuses every token issued to it before then. Not part of what is shown of the user.
    sessions_ended_at: datetime | None = Field(default=None, exclude=True)

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


def has_role(user: CurrentUser, role: UserRole) -> bool:
    """Whether the user's global role is ``role`` or a higher one. An unknown role has none."""
    return user.role in USER_ROLES[USER_ROLES.index(role) :]


def is_admin_user(user: CurrentUser) -> bool:
    """Whether the user has the whole administration: an admin."""
    return user.role in ADMIN_ROLES


def has_superuser_rights(user: CurrentUser) -> bool:
    """Whether the user has every project, member or not, and the configuration the analyses
    read (gene panels, variant tags, filter presets, sequencing QC thresholds, package import):
    a superuser or an admin."""
    return user.role in SUPERUSER_ROLES


def role_changes_records(user: CurrentUser) -> bool:
    """Whether the user's global role lets them change records at all: an analyst or higher.
    A viewer only reads, whatever their access level in a project."""
    return user.role in CHANGING_ROLES


def may_sign_out(user: CurrentUser) -> bool:
    """Whether the user's global role lets them sign an analysis out: a supervisor or higher
    (TF-06 H15). In a family's project they must also be allowed to change it."""
    return user.role in SIGN_OUT_ROLES


def user_metadata_project_ids(user: CurrentUser) -> list[str]:
    return _unique_strings(getattr(user, "metadata_project_ids", []))


def visible_metadata_project_ids(project_ids: Iterable[Any] | None, user: CurrentUser) -> list[str]:
    """The given projects the user may see: all of them for a superuser or an admin, else
    their own."""
    normalized_project_ids = _unique_strings(project_ids)
    if has_superuser_rights(user):
        return normalized_project_ids
    allowed_project_ids = set(user_metadata_project_ids(user))
    return [project_id for project_id in normalized_project_ids if project_id in allowed_project_ids]


def user_can_access_metadata_projects(project_ids: Iterable[Any] | None, user: CurrentUser) -> bool:
    """Whether the user may see a record linked to these projects: a superuser or an admin
    always, anyone else when they share at least one of them.

    A caller that finds a record the user may not see raises ``RecordNotVisible``: the same 404
    an unknown record gets, so an ID tells nobody whether a record outside their projects exists.
    """
    if has_superuser_rights(user):
        return True
    return bool(set(_unique_strings(project_ids)).intersection(user_metadata_project_ids(user)))


def user_analyst_project_ids(user: CurrentUser) -> list[str]:
    """The member projects where the user may change records: every one where they are no
    reader, none for a viewer."""
    if not role_changes_records(user):
        return []
    readers = set(_unique_strings(getattr(user, "reader_project_ids", [])))
    return [project_id for project_id in user_metadata_project_ids(user) if project_id not in readers]


def user_can_change_metadata_projects(project_ids: Iterable[Any] | None, user: CurrentUser) -> bool:
    """Whether the user may change a record linked to these projects: a superuser or an admin
    always, a viewer never, anyone else when they are an analyst in at least one of them.

    A record linked to several projects may be changed by an analyst of any one of them, as it
    may be seen by a member of any one of them.
    """
    if has_superuser_rights(user):
        return True
    return bool(set(_unique_strings(project_ids)).intersection(user_analyst_project_ids(user)))


def user_can_sign_out_metadata_projects(project_ids: Iterable[Any] | None, user: CurrentUser) -> bool:
    """Whether the user may sign out an analysis of a family linked to these projects: they
    may change it, and their role is supervisor or higher (TF-06 H15)."""
    return may_sign_out(user) and user_can_change_metadata_projects(project_ids, user)


class ReadOnlyAccess(HTTPException):
    """The 403 for a viewer or a reader who asks for something only an analyst or an admin
    may do."""

    def __init__(self, detail: str = READ_ONLY_DETAIL) -> None:
        super().__init__(status_code=403, detail=detail)


def require_change_access(project_ids: Iterable[Any] | None, user: CurrentUser) -> None:
    """Refuse a change to a record of these projects by a user who only reads them: a viewer,
    or a reader of the projects.

    The family, sample and project checkpoints call this once they have found that the user
    may see the record, so a viewer or reader is told why (403) while a record they may not
    see still answers like an unknown one (404). It does nothing for a request that only reads.
    """
    if getattr(user, "changes_records", False) and not user_can_change_metadata_projects(project_ids, user):
        raise ReadOnlyAccess(READ_ONLY_DETAIL if role_changes_records(user) else VIEWER_READ_ONLY_DETAIL)


def require_sign_out_authority(user: CurrentUser) -> None:
    """Refuse a sign-out to a user whose role is below supervisor (TF-06 H15).

    Called once the family's checkpoint has let the user change the family, so a family they
    may not see still answers 404 and a viewer or reader is still told they only read."""
    if not may_sign_out(user):
        raise HTTPException(status_code=403, detail=SIGN_OUT_ROLE_DETAIL)


def require_audit_trail_access(project_ids: Iterable[Any] | None, user: CurrentUser) -> None:
    """Refuse a family's clinical audit trail to a viewer, and to a reader in its projects."""
    if not user_can_change_metadata_projects(project_ids, user):
        raise ReadOnlyAccess(AUDIT_TRAIL_DETAIL if role_changes_records(user) else VIEWER_AUDIT_TRAIL_DETAIL)


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
    names ``kind`` ("family", "sample", "project", or another user's "filter_preset") in
    ``request_meta.record_hidden``, so the audit still shows who asked for records outside
    their projects (REQ-SEC-001).
    """

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(status_code=404, detail=detail)
        self.kind = kind
