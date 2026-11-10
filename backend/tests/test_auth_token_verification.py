"""Sign-in tokens: the local HS256 JWT, and with Microsoft sign-in its access tokens and the
emergency administrator's local token.

REQ-SEC-003 (TF-09a). Without Azure AD, ``get_current_user`` accepts the token the login
issues (``create_access_token``: HS256, signed with ``SECRET_KEY``, with an expiry and an issue
time) and refuses, with a 401, a token signed with another key or another algorithm, an
unsigned or expired one, one that does not say when it was issued, one that names no user, and
one of an unknown or inactive user.

With Azure AD (Microsoft Entra ID) configured (SEC-4), the bearer token must be a Microsoft
access token for CoGA's API: it signs in the account bound to the token's object ID (made at
the first sign-in), never an account found by an e-mail address; an inactive account is
refused with a 401 that says so. A token this server issued is accepted only with
``AZURE_ADMIN_OVERRIDE`` on, and only for the emergency administrator (the active local
account ``ADMIN_USERNAME`` names, with an administrator's role); every such use is logged and
flagged in the request's audit row (``request_meta.emergency_sign_in``). The override leaves
Microsoft tokens alone.

A logout (``POST /auth/logout``) ends every session of its account: a token issued to the
account before its last logout, CoGA's own (whose ``iat`` is to the microsecond) or Microsoft's,
is refused with the same 401, in each of the three sign-ins; one issued after signs in.

The requests go through the real login route, token code, Microsoft token check and
``get_current_user``. Only the account lookups, the login's database calls and the audit write
are replaced; the directory is a stand-in made at run time (``_microsoft_sign_in_fakes``), so
its keys are no directory's. The accounts are synthetic.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any
import uuid

from fastapi.testclient import TestClient
import jwt
import pytest

from backend.app import dependencies
from backend.app.core.azure import TOKEN_DATED_EARLY_SECONDS, AzureIdentity
from backend.app.core.config import LOCAL_TOKEN_ALGORITHM, Settings, settings
from backend.app.core.postgres import get_postgres_session
from backend.app.main import app
from backend.app.middleware import request_logging
from backend.app.routers import auth as auth_router
from backend.app.services.access_control import CurrentUser
from backend.tests._microsoft_sign_in_fakes import FakeDirectory

# Long enough for HS512 too, so the token signed with another algorithm is made without a
# short-key warning and is refused for its algorithm alone.
_LOCAL_SIGNER = "k" * 64
_OTHER_SIGNER = "o" * 64

ADMIN = "admin@example.com"
SUPERUSER = "superuser@example.com"
VIEWER = "viewer@example.com"
INACTIVE_ADMIN = "inactive-admin@example.com"
EMERGENCY = "emergency-admin@example.com"
INACTIVE_EMERGENCY = "inactive-emergency@example.com"
MICROSOFT_ADMIN = "microsoft-admin@example.com"
SIGNED_OUT = "signed-out@example.com"
SIGNED_OUT_EMERGENCY = "signed-out-emergency@example.com"
# The right password and the stored hash it matches, put together at run time.
_PHRASE = " ".join(("correct", "horse", "battery", "staple"))
_STORED_HASH = "-".join(("stored", "hash"))
_EMERGENCY_USERNAME = "coga-emergency"


def _user(
    email: str,
    role: str,
    *,
    active: bool = True,
    username: str | None = None,
    object_id: str | None = None,
) -> CurrentUser:
    return CurrentUser(
        id=f"user-{email.split('@')[0]}",
        username=username or email,
        email=email,
        role=role,
        is_active=active,
        azure_object_id=object_id,
        created_at=datetime.now(timezone.utc),
    )


_USERS = {
    ADMIN: _user(ADMIN, "admin"),
    SUPERUSER: _user(SUPERUSER, "superuser"),
    VIEWER: _user(VIEWER, "viewer"),
    INACTIVE_ADMIN: _user(INACTIVE_ADMIN, "admin", active=False),
    EMERGENCY: _user(EMERGENCY, "admin", username=_EMERGENCY_USERNAME),
    # The emergency administrator's user name on an account that signs in with Microsoft.
    MICROSOFT_ADMIN: _user(MICROSOFT_ADMIN, "admin", username=_EMERGENCY_USERNAME, object_id=str(uuid.uuid4())),
}
_INACTIVE_EMERGENCY = _user(INACTIVE_EMERGENCY, "admin", active=False, username=_EMERGENCY_USERNAME)
# When the signed-out accounts last logged out: ten minutes ago, so that a Microsoft token issued
# before and one issued after are both within the directory's lifetime and leeway.
_LOGGED_OUT_AT = datetime.now(timezone.utc) - timedelta(minutes=10)
_SIGNED_OUT = _user(SIGNED_OUT, "analyst").model_copy(update={"sessions_ended_at": _LOGGED_OUT_AT})
_SIGNED_OUT_EMERGENCY = _user(SIGNED_OUT_EMERGENCY, "admin", username=_EMERGENCY_USERNAME).model_copy(
    update={"sessions_ended_at": _LOGGED_OUT_AT}
)


class _FakeSession:
    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


@pytest.fixture()
def audit_rows(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """The request audit rows the middleware writes, kept instead of written."""
    rows: list[Any] = []

    async def keep(payload: Any) -> None:
        rows.append(payload)

    monkeypatch.setattr(request_logging, "write_audit_log_event", keep)
    return rows


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch, audit_rows: list[Any]):
    """The app without Azure AD, signing its callers in with the real ``get_current_user``."""
    original_overrides = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True
    monkeypatch.setattr(settings, "secret_key", _LOCAL_SIGNER)
    monkeypatch.setattr(settings, "azure_tenant_id", None)
    monkeypatch.setattr(settings, "azure_client_id", None)
    monkeypatch.setattr(settings, "azure_api_scope", None)
    monkeypatch.setattr(settings, "azure_admin_override", False)
    monkeypatch.setattr(settings, "admin_username", _EMERGENCY_USERNAME)

    users = {
        **_USERS,
        INACTIVE_EMERGENCY: _INACTIVE_EMERGENCY,
        SIGNED_OUT: _SIGNED_OUT,
        SIGNED_OUT_EMERGENCY: _SIGNED_OUT_EMERGENCY,
    }

    async def user_by_email(_session: Any, email: str) -> CurrentUser | None:
        return users.get(email)

    async def account_by_email(_session: Any, email: str) -> dict[str, Any] | None:
        user = users.get(email)
        if user is None:
            return None
        return {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "role": user.role,
            "is_active": user.is_active,
            "azure_object_id": user.azure_object_id,
            "hashed_password": _STORED_HASH,
        }

    async def override_get_postgres_session():
        yield _FakeSession()

    monkeypatch.setattr(dependencies, "get_current_user_by_email", user_by_email)
    monkeypatch.setattr(dependencies, "get_auth_user_mapping_by_email", account_by_email)
    app.dependency_overrides[get_postgres_session] = override_get_postgres_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides = original_overrides


class _Accounts:
    """The accounts bound to object IDs, as ``microsoft_account_user`` finds and makes them."""

    def __init__(self) -> None:
        self.by_object_id: dict[str, CurrentUser] = {}
        self.identities: list[AzureIdentity] = []

    def bind(self, user: CurrentUser) -> str:
        object_id = str(uuid.uuid4())
        self.by_object_id[object_id] = user.model_copy(update={"azure_object_id": object_id})
        return object_id

    async def sign_in(self, _session: Any, identity: AzureIdentity):
        self.identities.append(identity)
        user = self.by_object_id.get(identity.object_id)
        if user is not None:
            return user, None
        user = _user(identity.email or "new@example.org", "viewer", object_id=identity.object_id)
        self.by_object_id[identity.object_id] = user
        return user, {"user_id": user.id, "email": user.email, "created": "microsoft_sign_in"}


@pytest.fixture()
def azure(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> tuple[FakeDirectory, _Accounts]:
    """Microsoft sign-in configured, against a stand-in directory."""
    directory = FakeDirectory().serve(monkeypatch).configure(monkeypatch, settings)
    accounts = _Accounts()
    monkeypatch.setattr(dependencies, "microsoft_account_user", accounts.sign_in)
    return directory, accounts


def _local_token(
    email: str | None,
    *,
    key: str | None = _LOCAL_SIGNER,
    algorithm: str = "HS256",
    expires_in: timedelta = timedelta(minutes=5),
    issued_at: float | None = None,
    dated: bool = True,
) -> str:
    """A token as ``create_access_token`` issues one, issued now unless ``issued_at`` says
    otherwise; ``dated=False`` leaves the issue time out."""
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {"exp": now + expires_in}
    if email is not None:
        payload["sub"] = email
    if dated:
        payload["iat"] = now.timestamp() if issued_at is None else issued_at
    return jwt.encode(payload, key, algorithm=algorithm)


def _me(client: TestClient, token: str):
    return client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})


def _assert_refused(response, detail: str = "Could not validate credentials") -> None:
    assert response.status_code == 401, response.text
    assert response.json() == {"detail": detail}
    assert response.headers["www-authenticate"] == "Bearer"


def _override_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == dependencies.__name__
        and record.levelno == logging.WARNING
        and "azure_admin_override" in record.getMessage()
    ]


def _me_rows(audit_rows: list[Any]) -> list[Any]:
    return [row for row in audit_rows if row.path == "/api/auth/me"]


# --------------------------------------------------------------------------- #
# Without Azure AD: the local HS256 token
# --------------------------------------------------------------------------- #


def test_the_token_issued_at_sign_in_is_hs256_with_an_expiry_and_signs_its_user_in(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def not_throttled(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def account(_session: Any, email: str) -> dict[str, Any] | None:
        user = _USERS.get(email)
        if user is None:
            return None
        return {"email": user.email, "hashed_password": _STORED_HASH, "is_active": True, "role": user.role}

    async def no_op(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(auth_router, "get_login_throttle_state", not_throttled)
    monkeypatch.setattr(auth_router, "get_auth_user_mapping_by_email", account)
    monkeypatch.setattr(auth_router, "record_failed_login", no_op)
    monkeypatch.setattr(auth_router, "clear_login_failures", no_op)
    monkeypatch.setattr(
        auth_router,
        "verify_password",
        lambda plain, hashed: plain == _PHRASE and hashed == _STORED_HASH,
    )

    signed_in_at = datetime.now(timezone.utc)
    login = client.post("/api/auth/login", json={"email": VIEWER, "password": _PHRASE})

    assert login.status_code == 200, login.text
    body = login.json()
    assert body["token_type"] == "bearer" and body["role"] == "viewer"
    token = body["access_token"]
    assert jwt.get_unverified_header(token)["alg"] == "HS256"
    claims = jwt.decode(token, _LOCAL_SIGNER, algorithms=["HS256"])
    assert claims["sub"] == VIEWER
    lifetime = datetime.fromtimestamp(claims["exp"], timezone.utc) - signed_in_at
    expected = timedelta(minutes=settings.access_token_expire_minutes)
    assert expected - timedelta(seconds=30) <= lifetime <= expected + timedelta(seconds=30)
    # When it was issued, to the microsecond, so that a logout can refuse it and not the
    # token of a sign-in in the same second after it.
    assert isinstance(claims["iat"], float)
    assert abs(claims["iat"] - signed_in_at.timestamp()) < 30

    me = _me(client, token)

    assert me.status_code == 200, me.text
    assert (me.json()["email"], me.json()["role"], me.json()["sign_in"]) == (VIEWER, "viewer", "password")


def test_the_local_token_algorithm_is_fixed_not_a_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    # SEC-12: the algorithm CoGA signs and checks its own tokens with is a constant: an
    # ALGORITHM in the environment changes nothing, and the settings read no such variable.
    monkeypatch.setenv("ALGORITHM", "HS512")
    monkeypatch.setattr(settings, "secret_key", _LOCAL_SIGNER)

    assert "algorithm" not in Settings.model_fields
    assert not hasattr(Settings(_env_file=None, APP_ENV="test"), "algorithm")
    assert LOCAL_TOKEN_ALGORITHM == "HS256"
    assert jwt.get_unverified_header(dependencies.create_access_token({"sub": VIEWER}))["alg"] == "HS256"


@pytest.mark.parametrize(
    "make_token",
    [
        pytest.param(lambda: _local_token(VIEWER, key=_OTHER_SIGNER), id="signed-with-another-key"),
        pytest.param(lambda: _local_token(VIEWER, algorithm="HS512"), id="signed-with-another-algorithm"),
        pytest.param(lambda: _local_token(VIEWER, key=None, algorithm="none"), id="unsigned"),
        pytest.param(lambda: _local_token(VIEWER, expires_in=timedelta(seconds=-5)), id="expired"),
        # Not a token create_access_token issued: no logout could refuse it.
        pytest.param(lambda: _local_token(VIEWER, dated=False), id="saying-no-issue-time"),
        pytest.param(lambda: _local_token(VIEWER, issued_at="yesterday"), id="dated-in-words"),  # type: ignore[arg-type]
        pytest.param(lambda: _local_token(None), id="naming-no-user"),
        pytest.param(lambda: _local_token("nobody@example.com"), id="of-an-unknown-user"),
        pytest.param(lambda: _local_token(INACTIVE_ADMIN), id="of-an-inactive-user"),
        pytest.param(lambda: "not-a-token", id="not-a-token"),
    ],
)
def test_a_token_that_is_not_a_valid_local_token_is_refused(client: TestClient, make_token) -> None:
    _assert_refused(_me(client, make_token()))


def test_a_local_token_signs_in_whatever_the_role_without_azure(client: TestClient) -> None:
    # Without Azure AD the local token is the sign-in, for every role, and no request is
    # flagged as an emergency sign-in.
    for email, role in ((VIEWER, "viewer"), (SUPERUSER, "superuser"), (ADMIN, "admin"), (EMERGENCY, "admin")):
        response = _me(client, _local_token(email))
        assert response.status_code == 200, response.text
        assert (response.json()["email"], response.json()["role"]) == (email, role)


def test_without_azure_a_microsoft_token_is_refused(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = FakeDirectory().serve(monkeypatch)
    _assert_refused(_me(client, directory.access_token()))
    assert directory.fetched == []


# --------------------------------------------------------------------------- #
# With Azure AD: Microsoft tokens, and the emergency administrator's local token
# --------------------------------------------------------------------------- #


def test_with_azure_a_microsoft_token_signs_in_the_account_of_its_object_id(
    client: TestClient, azure: tuple[FakeDirectory, _Accounts], audit_rows: list[Any]
) -> None:
    directory, accounts = azure
    object_id = accounts.bind(_USERS[VIEWER])

    # The token's e-mail address names another account: the object ID decides.
    response = _me(client, directory.access_token(object_id, email=ADMIN, preferred_username=ADMIN))

    assert response.status_code == 200, response.text
    assert (response.json()["email"], response.json()["role"], response.json()["sign_in"]) == (
        VIEWER,
        "viewer",
        "microsoft",
    )
    assert [identity.object_id for identity in accounts.identities] == [object_id]
    (row,) = _me_rows(audit_rows)
    assert (row.user_email, row.user_role, row.status_code) == (VIEWER, "viewer", 200)
    assert "account_change" not in row.request_meta and "emergency_sign_in" not in row.request_meta


def test_a_first_microsoft_sign_in_records_the_account_it_made(
    client: TestClient, azure: tuple[FakeDirectory, _Accounts], audit_rows: list[Any]
) -> None:
    directory, accounts = azure
    object_id = str(uuid.uuid4())

    first = _me(client, directory.access_token(object_id, email="new.colleague@example.org"))
    again = _me(client, directory.access_token(object_id, email="new.colleague@example.org"))

    assert first.status_code == 200 and again.status_code == 200, (first.text, again.text)
    assert first.json()["email"] == "new.colleague@example.org" and first.json()["projects"] == []
    made, signed_in = _me_rows(audit_rows)
    assert made.request_meta["account_change"] == {
        "user_id": first.json()["id"],
        "email": "new.colleague@example.org",
        "created": "microsoft_sign_in",
    }
    assert "account_change" not in signed_in.request_meta


def test_an_inactive_microsoft_account_is_told_so(
    client: TestClient, azure: tuple[FakeDirectory, _Accounts]
) -> None:
    directory, accounts = azure
    object_id = accounts.bind(_USERS[INACTIVE_ADMIN])

    _assert_refused(_me(client, directory.access_token(object_id)), dependencies.INACTIVE_MICROSOFT_ACCOUNT_DETAIL)


def test_a_microsoft_token_that_is_not_for_coga_is_refused_and_the_reason_logged(
    client: TestClient,
    azure: tuple[FakeDirectory, _Accounts],
    caplog: pytest.LogCaptureFixture,
) -> None:
    directory, accounts = azure
    token = directory.access_token(accounts.bind(_USERS[VIEWER]), aud=str(uuid.uuid4()))

    with caplog.at_level(logging.WARNING, logger=dependencies.__name__):
        _assert_refused(_me(client, token))

    messages = [record.getMessage() for record in caplog.records if record.name == dependencies.__name__]
    assert messages == ["microsoft sign-in: token refused: refused by the token check (InvalidAudienceError)"]
    assert token not in caplog.text
    assert accounts.identities == []


def test_an_expired_microsoft_token_is_refused_without_a_warning(
    client: TestClient,
    azure: tuple[FakeDirectory, _Accounts],
    caplog: pytest.LogCaptureFixture,
) -> None:
    directory, accounts = azure
    token = directory.access_token(
        accounts.bind(_USERS[VIEWER]), exp=datetime.now(timezone.utc) - timedelta(minutes=5)
    )

    with caplog.at_level(logging.WARNING, logger=dependencies.__name__):
        _assert_refused(_me(client, token))

    assert [record for record in caplog.records if record.name == dependencies.__name__] == []


@pytest.mark.parametrize("email", [ADMIN, SUPERUSER, VIEWER, EMERGENCY])
def test_with_azure_and_no_override_a_local_token_is_refused_even_for_the_emergency_admin(
    client: TestClient,
    azure: tuple[FakeDirectory, _Accounts],
    caplog: pytest.LogCaptureFixture,
    email: str,
) -> None:
    with caplog.at_level(logging.WARNING, logger=dependencies.__name__):
        _assert_refused(_me(client, _local_token(email)))
    assert _override_warnings(caplog) == []


def test_the_override_admits_the_emergency_admins_local_token_flags_and_logs_each_use(
    client: TestClient,
    azure: tuple[FakeDirectory, _Accounts],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    audit_rows: list[Any],
) -> None:
    monkeypatch.setattr(settings, "azure_admin_override", True)

    with caplog.at_level(logging.WARNING, logger=dependencies.__name__):
        first = _me(client, _local_token(EMERGENCY))
        second = _me(client, _local_token(EMERGENCY))

    for response in (first, second):
        assert response.status_code == 200, response.text
        assert (response.json()["email"], response.json()["role"]) == (EMERGENCY, "admin")
    warnings = _override_warnings(caplog)
    assert len(warnings) == 2
    assert all(_USERS[EMERGENCY].id in warning and "role=admin" in warning for warning in warnings)
    assert all(EMERGENCY not in warning for warning in warnings)
    assert [row.request_meta.get("emergency_sign_in") for row in _me_rows(audit_rows)] == [True, True]


@pytest.mark.parametrize(
    "make_token",
    [
        pytest.param(lambda: _local_token(VIEWER), id="a-viewer"),
        pytest.param(lambda: _local_token(ADMIN), id="another-admin"),
        pytest.param(lambda: _local_token(SUPERUSER), id="a-superuser"),
        pytest.param(lambda: _local_token(MICROSOFT_ADMIN), id="the-emergency-name-on-a-microsoft-account"),
        pytest.param(lambda: _local_token(INACTIVE_EMERGENCY), id="the-emergency-admin-deactivated"),
        pytest.param(lambda: _local_token("nobody@example.com"), id="an-unknown-user"),
        pytest.param(lambda: _local_token(EMERGENCY, key=_OTHER_SIGNER), id="signed-with-another-key"),
        pytest.param(lambda: _local_token(EMERGENCY, algorithm="HS512"), id="another-algorithm"),
        pytest.param(lambda: _local_token(EMERGENCY, expires_in=timedelta(seconds=-5)), id="expired"),
        pytest.param(lambda: _local_token(EMERGENCY, key=None, algorithm="none"), id="unsigned"),
        pytest.param(lambda: _local_token(EMERGENCY, dated=False), id="saying-no-issue-time"),
        pytest.param(lambda: _local_token(None), id="naming-no-user"),
    ],
)
def test_the_override_refuses_every_other_local_token(
    client: TestClient,
    azure: tuple[FakeDirectory, _Accounts],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    audit_rows: list[Any],
    make_token,
) -> None:
    monkeypatch.setattr(settings, "azure_admin_override", True)

    with caplog.at_level(logging.WARNING, logger=dependencies.__name__):
        _assert_refused(_me(client, make_token()))

    assert _override_warnings(caplog) == []
    assert [row.request_meta.get("emergency_sign_in") for row in _me_rows(audit_rows)] == [None]


def test_the_override_refuses_the_emergency_admin_whose_account_is_gone_between_its_lookups(
    client: TestClient,
    azure: tuple[FakeDirectory, _Accounts],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    audit_rows: list[Any],
) -> None:
    # The account is read twice, its sign-in record and then the user it signs in: one gone
    # between the two is refused, not signed in as nobody.
    monkeypatch.setattr(settings, "azure_admin_override", True)

    async def gone(_session: Any, _email: str) -> None:
        return None

    monkeypatch.setattr(dependencies, "get_current_user_by_email", gone)

    with caplog.at_level(logging.WARNING, logger=dependencies.__name__):
        _assert_refused(_me(client, _local_token(EMERGENCY)))

    assert _override_warnings(caplog) == []
    assert [row.request_meta.get("emergency_sign_in") for row in _me_rows(audit_rows)] == [None]


@pytest.mark.parametrize("override", [False, True], ids=["without-the-override", "with-the-override"])
def test_with_azure_a_bearer_that_is_no_token_is_refused(
    client: TestClient,
    azure: tuple[FakeDirectory, _Accounts],
    monkeypatch: pytest.MonkeyPatch,
    override: bool,
) -> None:
    # Its header cannot be read, so neither check is picked: refused before the directory's
    # keys are fetched or any account is looked up.
    directory, accounts = azure
    monkeypatch.setattr(settings, "azure_admin_override", override)

    for bearer in ("not-a-token", "a.b.c"):
        _assert_refused(_me(client, bearer))

    assert directory.fetched == [] and accounts.identities == []


def test_the_override_leaves_microsoft_tokens_alone(
    client: TestClient,
    azure: tuple[FakeDirectory, _Accounts],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    audit_rows: list[Any],
) -> None:
    # The emergency rule is the local token's: a Microsoft token signs in a viewer as without it,
    # is not logged as a use of the override, and its request is not flagged.
    directory, accounts = azure
    monkeypatch.setattr(settings, "azure_admin_override", True)

    with caplog.at_level(logging.WARNING, logger=dependencies.__name__):
        response = _me(client, directory.access_token(accounts.bind(_USERS[VIEWER])))

    assert response.status_code == 200, response.text
    assert response.json()["email"] == VIEWER
    assert _override_warnings(caplog) == []
    assert [row.request_meta.get("emergency_sign_in") for row in _me_rows(audit_rows)] == [None]


# --------------------------------------------------------------------------- #
# A logout ends every session of its account
# --------------------------------------------------------------------------- #

_BEFORE = (_LOGGED_OUT_AT - timedelta(minutes=5)).timestamp()
_JUST_BEFORE = _LOGGED_OUT_AT.timestamp() - 0.000_5
_JUST_AFTER = _LOGGED_OUT_AT.timestamp() + 0.000_5
_AFTER = (_LOGGED_OUT_AT + timedelta(minutes=5)).timestamp()


@pytest.mark.parametrize(
    "make_token",
    [
        pytest.param(lambda: _local_token(SIGNED_OUT, issued_at=_BEFORE), id="issued-before"),
        pytest.param(lambda: _local_token(SIGNED_OUT, issued_at=_JUST_BEFORE), id="issued-within-a-millisecond-before"),
    ],
)
def test_a_local_token_issued_before_the_accounts_logout_is_refused(client: TestClient, make_token) -> None:
    _assert_refused(_me(client, make_token()))


@pytest.mark.parametrize("issued_at", [_JUST_AFTER, _AFTER], ids=["within-a-millisecond-after", "after"])
def test_a_local_token_issued_after_the_accounts_logout_signs_in(client: TestClient, issued_at: float) -> None:
    response = _me(client, _local_token(SIGNED_OUT, issued_at=issued_at))

    assert response.status_code == 200, response.text
    assert response.json()["email"] == SIGNED_OUT


def test_a_microsoft_token_issued_before_the_accounts_logout_is_refused_and_one_after_signs_in(
    client: TestClient, azure: tuple[FakeDirectory, _Accounts]
) -> None:
    # Entra ID dates a token (iat) about five minutes before it issues it: a token issued a
    # second after the logout carries an iat almost five minutes before it, and signs in.
    directory, accounts = azure
    object_id = accounts.bind(_SIGNED_OUT)
    early = TOKEN_DATED_EARLY_SECONDS
    logged_out = _LOGGED_OUT_AT.timestamp()

    before = _me(client, directory.access_token(object_id, iat=int(logged_out - early - 60)))
    just_before = _me(client, directory.access_token(object_id, iat=int(logged_out - early) - 1))
    just_after = _me(client, directory.access_token(object_id, iat=int(logged_out - early) + 1))
    after = _me(client, directory.access_token(object_id, iat=int(_AFTER)))

    _assert_refused(before)
    _assert_refused(just_before)
    for response in (just_after, after):
        assert response.status_code == 200, response.text
        assert response.json()["email"] == SIGNED_OUT


def test_the_override_refuses_the_emergency_admins_local_token_issued_before_its_logout_unlogged(
    client: TestClient,
    azure: tuple[FakeDirectory, _Accounts],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    audit_rows: list[Any],
) -> None:
    monkeypatch.setattr(settings, "azure_admin_override", True)

    with caplog.at_level(logging.WARNING, logger=dependencies.__name__):
        before = _me(client, _local_token(SIGNED_OUT_EMERGENCY, issued_at=_BEFORE))
    _assert_refused(before)
    # A refused token is no use of the override: neither logged nor flagged.
    assert _override_warnings(caplog) == []
    assert [row.request_meta.get("emergency_sign_in") for row in _me_rows(audit_rows)] == [None]

    after = _me(client, _local_token(SIGNED_OUT_EMERGENCY, issued_at=_AFTER))
    assert after.status_code == 200, after.text
    assert after.json()["email"] == SIGNED_OUT_EMERGENCY


def test_the_cas_sign_ins_local_token_issued_before_the_accounts_logout_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With CAS the token is CoGA's own, of an account bound to an object ID.
    monkeypatch.setattr(settings, "cas_server_url", "https://cas.example.org/cas")
    monkeypatch.setattr(settings, "cas_service_url", "https://coga.example.org/login")
    bound = _SIGNED_OUT.model_copy(update={"azure_object_id": str(uuid.uuid4())})

    async def user_by_email(_session: Any, email: str) -> CurrentUser | None:
        return bound if email == SIGNED_OUT else None

    monkeypatch.setattr(dependencies, "get_current_user_by_email", user_by_email)

    _assert_refused(_me(client, _local_token(SIGNED_OUT, issued_at=_BEFORE)))
    assert _me(client, _local_token(SIGNED_OUT, issued_at=_AFTER)).status_code == 200


class _EndedSessions:
    """``end_account_sessions`` as the logout calls it, kept instead of written."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, float | None]] = []

    async def __call__(self, _session: Any, *, user_id: str, token_issued_at: float | None = None) -> datetime:
        self.calls.append((user_id, token_issued_at))
        return datetime.now(timezone.utc)


def test_the_logout_ends_the_sessions_of_the_signed_in_account_whatever_its_role(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, audit_rows: list[Any]
) -> None:
    ended = _EndedSessions()
    monkeypatch.setattr(auth_router, "end_account_sessions", ended)
    token = dependencies.create_access_token({"sub": VIEWER})
    issued = jwt.decode(token, _LOCAL_SIGNER, algorithms=["HS256"])["iat"]

    response = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})

    # A viewer too: ending one's own sessions changes no record. The token it was made with
    # is named, so that it is refused too.
    assert response.status_code == 204, response.text
    assert response.content == b""
    assert ended.calls == [(_USERS[VIEWER].id, issued)]
    (row,) = [row for row in audit_rows if row.path == "/api/auth/logout"]
    assert (row.status_code, row.user_id) == (204, _USERS[VIEWER].id)


def test_a_logout_with_a_microsoft_token_names_the_latest_time_microsoft_issued_it(
    client: TestClient, azure: tuple[FakeDirectory, _Accounts], monkeypatch: pytest.MonkeyPatch
) -> None:
    directory, accounts = azure
    ended = _EndedSessions()
    monkeypatch.setattr(auth_router, "end_account_sessions", ended)
    iat = int(datetime.now(timezone.utc).timestamp()) - TOKEN_DATED_EARLY_SECONDS + 2

    token = directory.access_token(accounts.bind(_USERS[VIEWER]), iat=iat)
    response = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 204, response.text
    assert ended.calls == [(_USERS[VIEWER].id, iat + TOKEN_DATED_EARLY_SECONDS)]


def test_a_logout_with_a_refused_token_ends_nothing(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    ended = _EndedSessions()

    monkeypatch.setattr(auth_router, "end_account_sessions", ended)

    for token in (
        _local_token(SIGNED_OUT, issued_at=_BEFORE),
        _local_token(VIEWER, expires_in=timedelta(seconds=-5)),
        "not-a-token",
    ):
        _assert_refused(client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"}))
    _assert_refused(client.post("/api/auth/logout"), detail="Not authenticated")
    assert ended.calls == []


class _LogoutSession:
    """A session that answers the logout's UPDATE … RETURNING with the time it was given."""

    def __init__(self, stored: datetime | None = None) -> None:
        self.stored = stored
        self.params: dict[str, Any] = {}
        self.commits = 0

    async def execute(self, _statement: Any, params: dict[str, Any]) -> Any:
        self.params = dict(params)
        ended = params["ended"] if self.stored is None else max(self.stored, params["ended"])

        class _Result:
            def scalar_one(self) -> datetime:
                return ended

        return _Result()

    async def commit(self) -> None:
        self.commits += 1


def test_the_sessions_end_now_and_after_the_token_the_logout_was_made_with() -> None:
    import asyncio

    from backend.app.services.metadata_service import end_account_sessions

    now = datetime.now(timezone.utc)
    # A CoGA token, issued before now: the sessions end now.
    plain = _LogoutSession()
    ended = asyncio.run(end_account_sessions(plain, user_id="u-1", token_issued_at=now.timestamp() - 60))
    assert now <= ended <= datetime.now(timezone.utc)
    assert plain.params["user_id"] == "u-1" and plain.commits == 1
    # A Microsoft token whose latest issue time is a little after now (Entra dated it less
    # than five minutes early): the sessions end just after it, so it is refused too.
    later = now + timedelta(seconds=90)
    ahead = _LogoutSession()
    ended = asyncio.run(end_account_sessions(ahead, user_id="u-1", token_issued_at=later.timestamp()))
    assert later < ended <= later + timedelta(milliseconds=1)
