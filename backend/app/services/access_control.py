"""Who is asking, and what they may see: the signed-in user and the project-scoped rules (#528).

Split out of ``metadata_service`` so that the many routers and services that need only the user
model and the access rules do not depend on the metadata layer. The rules that need the
database (``get_accessible_family_mapping``, ``get_accessible_sample_mapping``) stay with the
queries they guard, in ``metadata_service``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, EmailStr, Field

ADMIN_ROLES = {"admin", "superuser"}


class CurrentUser(BaseModel):
    id: str
    username: str
    email: EmailStr
    first_name: str = ""
    last_name: str = ""
    affiliation: str = ""
    is_active: bool = True
    role: str
    projects: list[str] = Field(default_factory=list)
    metadata_project_ids: list[str] = Field(default_factory=list)
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
