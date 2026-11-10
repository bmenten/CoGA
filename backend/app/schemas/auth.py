"""Users, sign-in and sign-up."""

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from .common import (
    ApiId,
)


# A password is the only factor for a local account, so it must be at least 15
# characters (NIST SP 800-63B-4). Sign-up accepted any string, the empty one included.
# bcrypt reads only the first 72 bytes, so no maximum is needed for its cost.
SIGNUP_PASSWORD_MIN_LENGTH = 15


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=SIGNUP_PASSWORD_MIN_LENGTH)
    first_name: str
    last_name: str
    affiliation: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserRead(BaseModel):
    id: ApiId
    username: str
    email: EmailStr
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    affiliation: Optional[str] = None
    # How the account signs in: through the organisation, bound to its object ID in
    # the directory (with Microsoft, or through the organisation's CAS server when that is the
    # sign-in configured), or with a local password.
    sign_in: Literal["microsoft", "cas", "password"] = "password"
    is_active: bool
    role: str
    # The projects the user is a member of, and those among them where they are a reader;
    # in the others they are an analyst.
    projects: List[str] = Field(default_factory=list)
    reader_projects: List[str] = Field(default_factory=list)
    created_at: datetime
    # When the account was first active; None while it waits for an administrator's approval
    # (a sign-up, a first CAS sign-in): only such an account can be deleted.
    activated_at: Optional[datetime] = None
    # When an administrator archived the account (it is then inactive); None otherwise.
    archived_at: Optional[datetime] = None


# The global roles (the CHECK on users.role), lowest first, each with every right of the ones
# before it (services/access_control.py): a viewer reads the records of their projects; an
# analyst also analyses them; a supervisor also signs an analysis out; a superuser also has
# every project and the configuration the analyses read; an admin, the whole administration.
UserRole = Literal["viewer", "analyst", "supervisor", "superuser", "admin"]


class UserUpdate(BaseModel):
    # A field the change does not take is refused (422, naming it), not left out without a
    # word: a change of access an administrator thinks made and that was never read is the
    # worst kind. ``projects``, as the account list names them, is one (``project_ids`` here).
    model_config = ConfigDict(extra="forbid")

    is_active: Optional[bool] = None
    role: Optional[UserRole] = None
    # True archives the account, which deactivates it; False restores it, still inactive.
    archived: Optional[bool] = None
    # The account's projects, as a project's ``user_ids`` and ``reader_user_ids`` name its
    # members: given, the account is a member of exactly these projects; the readers
    # among them are ``reader_project_ids``, and it is an analyst in the others. Without
    # ``reader_project_ids`` each project it stays in keeps its level and one added is an
    # analyst's; a reader project that is not one of its projects is refused (400).
    project_ids: Optional[List[str]] = None
    reader_project_ids: Optional[List[str]] = None


class AccountRequestMailOut(BaseModel):
    """Whether CoGA e-mails the administrators about an account that waits for approval, and
    to whom: every active administrator, when a mail server (SMTP_HOST) is set."""

    enabled: bool
    recipients: List[str] = Field(default_factory=list)


class AccountRequestMailTestOut(BaseModel):
    """The test mail went out to these addresses."""

    recipients: List[str]


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str


class MicrosoftSignInOut(BaseModel):
    """What the browser needs to sign a user in with Microsoft (MSAL): none of it is secret."""

    client_id: str
    authority: str
    api_scope: str


class CasSignInOut(BaseModel):
    """Where the browser signs a user in through the organisation's CAS server, and signs out
    of it: none of it is secret."""

    display_name: str
    login_url: str
    logout_url: str


class SignInConfigOut(BaseModel):
    """How users sign in: with Microsoft when ``microsoft`` is given, through the
    organisation's CAS server when ``cas`` is, else with a password (and then a new user
    signs up)."""

    microsoft: Optional[MicrosoftSignInOut] = None
    cas: Optional[CasSignInOut] = None


class CasTicketIn(BaseModel):
    """The service ticket the CAS server sent the browser back with."""

    ticket: str = Field(min_length=1, max_length=256)


class SignupAck(BaseModel):
    """Generic signup acknowledgement.

    Returned for every signup request regardless of whether the submitted email
    is already registered, so the endpoint cannot be used to enumerate accounts.
    """

    detail: str


class UserRefOut(BaseModel):
    """Minimal user reference used for assignment/reviewer pickers."""

    id: ApiId
    username: str
    email: EmailStr
    first_name: str = ""
    last_name: str = ""
