"""Sign-in through the organisation's OAuth 2.0 server, such as the OAuth application Ghent
University's ICT registers (SEC-4).

The browser comes back from the OAuth server to the app's /login page with an authorization
code, and ``POST /api/auth/oauth/session`` redeems it with the browser's PKCE code verifier at
the token endpoint, for CoGA's client (its ID and secret) and redirect address, then reads the
user from the user-info endpoint with the access token. The user info must be a JSON object of
reasonable size that carries the user's object ID in the organisation's Entra ID; the account
is bound to that ID. A first sign-in makes an account that waits for an administrator's
approval (403, the administrators are told); an active account gets a session token. A refused
code answers 401 and counts against the source address, which is throttled. With the OAuth
sign-in configured there is no password sign-in or sign-up, and a token this server issued
signs in only an account bound to an object ID, or the emergency administrator while
``AZURE_ADMIN_OVERRIDE`` is on (flagged in the request audit).

The settings refuse a partial OAuth configuration, OAuth beside Microsoft or CAS sign-in, an
address that is not HTTPS (outside development), a redirect that is not the app's /login page,
and a placeholder client secret outside development.

The OAuth server is replaced by an httpx mock transport, and the account store as in
``test_microsoft_sign_in_routes.py``. The users, codes, secrets and addresses are synthetic.
"""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import pytest

from backend.app.core import oauth
from backend.app.core.azure import AzureIdentity
from backend.app.core.config import Settings, settings
from backend.app.routers import auth as auth_router
from backend.app.services.access_control import CurrentUser
from backend.app.services.user_accounts import OAUTH_SIGN_IN
from backend.tests import test_microsoft_sign_in_routes as _routes
from backend.tests.test_microsoft_sign_in_routes import (
    EMERGENCY,
    LOCAL_VIEWER,
    _PHRASE,
    _Store,
    _account,
    _bearer,
    _current_user,
    _rows,
)

# The account store, the audit rows and the client of the Microsoft sign-in route tests.
store = _routes.store
audit_rows = _routes.audit_rows
client = _routes.client

_AUTHORIZE = "https://login.example.org/oauth/authorize"
_TOKEN = "https://login.example.org/oauth/token"
_USERINFO = "https://login.example.org/oauth/userinfo"
_REDIRECT = "https://coga.example.org/login"
_CLIENT_ID = "coga-test-client"
# Built at run time: a secret-shaped literal would trip the secret scan.
_CLIENT_SECRET = base64.urlsafe_b64encode(bytes(range(24))).decode()
_CODE = "c0de-" + "a1b2c3d4" * 4
_VERIFIER = "v" * 43
_ACCESS_TOKEN = "at-" + "f00d" * 8
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _info(**fields: Any) -> dict[str, Any]:
    return {
        "sub": "acolleague",
        "azureObjectId": str(uuid4()),
        "mail": "a.colleague@example.org",
        "givenName": "A",
        "surname": "Colleague",
        **fields,
    }


@pytest.fixture()
def oauth_on(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "oauth_authorize_url": _AUTHORIZE,
        "oauth_token_url": _TOKEN,
        "oauth_userinfo_url": _USERINFO,
        "oauth_redirect_url": _REDIRECT,
        "oauth_client_id": _CLIENT_ID,
        "oauth_client_secret": _CLIENT_SECRET,
        "oauth_scope": "openid  profile",
    }.items():
        monkeypatch.setattr(settings, name, value)


def _server(info: dict[str, Any] | None = None, *, token_answer: httpx.Response | None = None, info_answer: httpx.Response | None = None):
    """An OAuth server: the token endpoint and the user-info endpoint."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return token_answer or httpx.Response(200, json={"access_token": _ACCESS_TOKEN, "token_type": "Bearer"})
        if request.url.path == "/oauth/userinfo":
            return info_answer or httpx.Response(200, json=info if info is not None else _info())
        return httpx.Response(404)

    return handle


def _serve(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
    """Answer the OAuth server's requests with ``handler``; returns the requests made."""
    seen: list[httpx.Request] = []
    real = _REAL_ASYNC_CLIENT

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(oauth.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(record), **kw))
    return seen


# --------------------------------------------------------------------------- #
# Reading the user info
# --------------------------------------------------------------------------- #


def test_the_user_info_names_the_user_by_object_id_with_the_e_mail_address_and_name(oauth_on: None) -> None:
    object_id = str(uuid4()).upper()

    identity = oauth.identity_from_user_info(_info(azureObjectId=object_id))

    assert identity == AzureIdentity(
        object_id=object_id.lower(),
        preferred_username=None,
        email="a.colleague@example.org",
        given_name="A",
        family_name="Colleague",
        name="A Colleague",
    )


def test_a_field_is_read_from_an_attributes_object_and_a_list_gives_its_first_value(oauth_on: None) -> None:
    object_id = str(uuid4())
    info = {"id": "acolleague", "attributes": {"azureObjectId": [object_id], "mail": ["", "first@example.org", "second@example.org"]}}

    identity = oauth.identity_from_user_info(info)

    assert (identity.object_id, identity.email, identity.name) == (object_id, "first@example.org", None)


def test_the_field_names_follow_the_settings(oauth_on: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "oauth_object_id_claim", "oid")
    monkeypatch.setattr(settings, "oauth_email_claim", "email")
    object_id = str(uuid4())

    identity = oauth.identity_from_user_info({"oid": object_id, "email": "b@example.org"})

    assert (identity.object_id, identity.email) == (object_id, "b@example.org")


@pytest.mark.parametrize("object_id", [None, "", "not-a-uuid", 42])
def test_user_info_without_an_object_id_is_refused(oauth_on: None, object_id: Any) -> None:
    info = _info(azureObjectId=object_id)
    if object_id is None:
        del info["azureObjectId"]
    with pytest.raises(oauth.OAuthError, match=r"no object ID \(azureObjectId\)"):
        oauth.identity_from_user_info(info)


# --------------------------------------------------------------------------- #
# Redeeming the code
# --------------------------------------------------------------------------- #


def test_the_code_is_redeemed_for_coga_s_client_with_the_verifier_then_the_user_is_read(
    oauth_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _serve(monkeypatch, _server())

    identity = asyncio.run(oauth.verify_authorization_code(_CODE, _VERIFIER))

    token_request, info_request = seen
    assert (token_request.method, str(token_request.url)) == ("POST", _TOKEN)
    assert parse_qs(token_request.content.decode()) == {
        "grant_type": ["authorization_code"],
        "code": [_CODE],
        "redirect_uri": [_REDIRECT],
        "code_verifier": [_VERIFIER],
    }
    # client_secret_basic: the client ID and secret in the Authorization header only.
    expected = base64.b64encode(f"{_CLIENT_ID}:{_CLIENT_SECRET}".encode()).decode()
    assert token_request.headers["Authorization"] == f"Basic {expected}"
    assert (info_request.method, str(info_request.url)) == ("GET", _USERINFO)
    assert info_request.headers["Authorization"] == f"Bearer {_ACCESS_TOKEN}"
    assert identity.email == "a.colleague@example.org"


def test_client_secret_post_puts_the_client_in_the_form_and_no_header(oauth_on: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "oauth_token_auth_method", "client_secret_post")
    seen = _serve(monkeypatch, _server())

    asyncio.run(oauth.verify_authorization_code(_CODE, _VERIFIER))

    form = parse_qs(seen[0].content.decode())
    assert (form["client_id"], form["client_secret"]) == ([_CLIENT_ID], [_CLIENT_SECRET])
    assert "Authorization" not in seen[0].headers


def test_a_form_encoded_token_answer_is_read(oauth_on: None, monkeypatch: pytest.MonkeyPatch) -> None:
    answer = httpx.Response(200, content=f"access_token={_ACCESS_TOKEN}&expires=7200".encode(), headers={"Content-Type": "text/plain"})
    seen = _serve(monkeypatch, _server(token_answer=answer))

    asyncio.run(oauth.verify_authorization_code(_CODE, _VERIFIER))

    assert seen[1].headers["Authorization"] == f"Bearer {_ACCESS_TOKEN}"


@pytest.mark.parametrize(
    ("code", "verifier", "reason"),
    [
        ("", _VERIFIER, "not an authorization code"),
        ("abc def", _VERIFIER, "not an authorization code"),
        ("abc&redirect_uri=https://evil.example", _VERIFIER, "not an authorization code"),
        ("a" * 2049, _VERIFIER, "not an authorization code"),
        (_CODE, "short", "not a PKCE code verifier"),
        (_CODE, "v" * 129, "not a PKCE code verifier"),
        (_CODE, "v" * 42 + "!", "not a PKCE code verifier"),
    ],
)
def test_a_value_that_is_no_code_or_verifier_never_reaches_the_server(
    oauth_on: None, monkeypatch: pytest.MonkeyPatch, code: str, verifier: str, reason: str
) -> None:
    seen = _serve(monkeypatch, _server())
    with pytest.raises(oauth.OAuthError, match=reason):
        asyncio.run(oauth.verify_authorization_code(code, verifier))
    assert seen == []


@pytest.mark.parametrize(
    ("token_answer", "info_answer", "reason"),
    [
        (httpx.Response(400, json={"error": "invalid_grant", "error_description": "secret detail"}), None, r"HTTP 400, invalid_grant\)"),
        (httpx.Response(400, json={"error": "Not A Code!"}), None, r"HTTP 400\)"),
        (httpx.Response(302, headers={"Location": "https://elsewhere.example"}), None, "HTTP 302"),
        (httpx.Response(200, json={"token_type": "Bearer"}), None, "no access token"),
        (httpx.Response(200, json={"access_token": _ACCESS_TOKEN, "token_type": "mac"}), None, "no bearer token"),
        (httpx.Response(200, json=["not", "an", "object"]), None, "not a JSON object"),
        (httpx.Response(200, content=b"<html>"), None, "token answer is not JSON"),
        (None, httpx.Response(401), "user-info endpoint answered HTTP 401"),
        (None, httpx.Response(200, content=b"x" * (64 * 1024 + 1)), "user info is too large"),
        (None, httpx.Response(200, content=b"<html>"), "user info is not JSON"),
    ],
)
def test_a_server_answer_coga_does_not_accept_refuses_the_code_without_quoting_it(
    oauth_on: None, monkeypatch: pytest.MonkeyPatch, token_answer: Any, info_answer: Any, reason: str
) -> None:
    _serve(monkeypatch, _server(token_answer=token_answer, info_answer=info_answer))
    with pytest.raises(oauth.OAuthError, match=reason) as refused:
        asyncio.run(oauth.verify_authorization_code(_CODE, _VERIFIER))
    message = str(refused.value)
    for value in (_CODE, _VERIFIER, _ACCESS_TOKEN, _CLIENT_SECRET, "secret detail"):
        assert value not in message


def test_an_unreachable_server_refuses_the_code(oauth_on: None, monkeypatch: pytest.MonkeyPatch) -> None:
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    _serve(monkeypatch, unreachable)
    with pytest.raises(oauth.OAuthError, match="token endpoint could not be reached"):
        asyncio.run(oauth.verify_authorization_code(_CODE, _VERIFIER))


def test_the_browser_signs_in_at_the_authorization_page_for_coga_s_client(oauth_on: None) -> None:
    url = urlsplit(oauth.authorize_url())
    assert (url.scheme, url.netloc, url.path) == ("https", "login.example.org", "/oauth/authorize")
    assert parse_qs(url.query) == {
        "response_type": ["code"],
        "client_id": [_CLIENT_ID],
        "redirect_uri": [_REDIRECT],
        "scope": ["openid profile"],
    }
    assert _CLIENT_SECRET not in oauth.authorize_url()
    assert oauth.logout_url() is None


# --------------------------------------------------------------------------- #
# The settings
# --------------------------------------------------------------------------- #

_OAUTH_VALUES = {
    "OAUTH_AUTHORIZE_URL": _AUTHORIZE,
    "OAUTH_TOKEN_URL": _TOKEN,
    "OAUTH_USERINFO_URL": _USERINFO,
    "OAUTH_REDIRECT_URL": _REDIRECT,
    "OAUTH_CLIENT_ID": _CLIENT_ID,
    "OAUTH_CLIENT_SECRET": _CLIENT_SECRET,
}


def _settings(**values: Any) -> Settings:
    return Settings(_env_file=None, **{"APP_ENV": "test", **values})


def test_an_oauth_configuration_is_all_six_settings() -> None:
    configured = _settings(**_OAUTH_VALUES)
    assert (configured.oauth_enabled, configured.redirect_sign_in_enabled, configured.organisation_sign_in_enabled) == (True, True, True)
    assert _CLIENT_SECRET not in repr(configured)
    blank = _settings(**{name: " " for name in _OAUTH_VALUES})
    assert (blank.oauth_enabled, blank.organisation_sign_in_enabled) == (False, False)


@pytest.mark.parametrize(
    ("values", "reason"),
    [
        ({**_OAUTH_VALUES, "OAUTH_CLIENT_SECRET": ""}, "OAUTH_CLIENT_SECRET is not set"),
        ({"OAUTH_CLIENT_ID": _CLIENT_ID}, "OAUTH_AUTHORIZE_URL"),
        ({**_OAUTH_VALUES, "AZURE_TENANT_ID": str(uuid4()), "AZURE_CLIENT_ID": str(uuid4())}, "one organisation sign-in at a time"),
        ({**_OAUTH_VALUES, "CAS_SERVER_URL": "https://login.example.org", "CAS_SERVICE_URL": _REDIRECT}, "one organisation sign-in at a time"),
        ({**_OAUTH_VALUES, "OAUTH_TOKEN_URL": "ftp://login.example.org/token"}, "https://"),
        ({**_OAUTH_VALUES, "OAUTH_USERINFO_URL": "https://user@login.example.org/userinfo"}, "user name"),
        ({**_OAUTH_VALUES, "OAUTH_AUTHORIZE_URL": f"{_AUTHORIZE}?client_id=other"}, "query string"),
        ({**_OAUTH_VALUES, "OAUTH_REDIRECT_URL": "https://coga.example.org/"}, "/login"),
        ({**_OAUTH_VALUES, "OAUTH_LOGOUT_URL": "ftp://login.example.org/logout"}, "OAUTH_LOGOUT_URL must be an https://"),
        ({**_OAUTH_VALUES, "OAUTH_OBJECT_ID_CLAIM": " "}, "OAUTH_OBJECT_ID_CLAIM cannot be blank"),
        ({**_OAUTH_VALUES, "OAUTH_DISPLAY_NAME": " "}, "OAUTH_DISPLAY_NAME"),
        ({**_OAUTH_VALUES, "OAUTH_TOKEN_AUTH_METHOD": "private_key_jwt"}, "OAUTH_TOKEN_AUTH_METHOD"),
    ],
)
def test_a_wrong_oauth_configuration_is_refused(values: dict[str, str], reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        _settings(**values)


def test_plain_http_serves_a_local_test_server_in_development_only() -> None:
    values = {
        **_OAUTH_VALUES,
        "OAUTH_AUTHORIZE_URL": "http://localhost:8443/authorize",
        "OAUTH_REDIRECT_URL": "http://localhost:5173/login",
    }
    assert _settings(APP_ENV="development", **values).oauth_enabled
    with pytest.raises(ValueError, match="https://"):
        _settings(APP_ENV="staging", **values)


def test_a_placeholder_client_secret_is_refused_outside_development() -> None:
    with pytest.raises(ValueError, match="OAUTH_CLIENT_SECRET"):
        _settings(APP_ENV="staging", **{**_OAUTH_VALUES, "OAUTH_CLIENT_SECRET": "change-me"})


def test_the_emergency_override_works_beside_the_oauth_sign_in() -> None:
    assert _settings(AZURE_ADMIN_OVERRIDE="true", **_OAUTH_VALUES).azure_admin_override


# --------------------------------------------------------------------------- #
# The routes
# --------------------------------------------------------------------------- #


class _Signer:
    """The account store of the OAuth sign-in: who signed in, the throttle and the notices."""

    def __init__(self, store: _Store) -> None:
        self.store = store
        self.identities: list[AzureIdentity] = []
        self.sources: list[Any] = []
        self.refused = 0
        self.throttled = False
        self.notified: list[str] = []


@pytest.fixture()
def signer(monkeypatch: pytest.MonkeyPatch, store: _Store, oauth_on: None) -> _Signer:
    signer = _Signer(store)

    async def account(_session: Any, identity: AzureIdentity, source: Any) -> tuple[CurrentUser, Any]:
        signer.identities.append(identity)
        signer.sources.append(source)
        existing = next((a for a in store.accounts.values() if a["azure_object_id"] == identity.object_id), None)
        if existing is not None:
            return _current_user(existing), None
        made = _account(identity.email, "viewer", azure_object_id=identity.object_id, is_active=False)
        store.accounts[made["email"]] = made
        return _current_user(made), {"user_id": made["id"], "created": "oauth_sign_in"}

    async def throttle(_session: Any, *, remote_ip: Any) -> Any:
        if signer.throttled:
            return type("State", (), {"retry_after_seconds": 30})()
        return None

    async def refused(_session: Any, *, remote_ip: Any) -> None:
        signer.refused += 1

    async def administrators(_session: Any) -> list[str]:
        return ["the.admin@example.org"]

    def notify(account: Any, recipients: list[str]) -> bool:
        assert recipients == ["the.admin@example.org"]
        assert account.sign_in == "oauth"
        signer.notified.append(account.email)
        return True

    monkeypatch.setattr(auth_router, "microsoft_account_user", account)
    monkeypatch.setattr(auth_router, "get_oauth_throttle_state", throttle)
    monkeypatch.setattr(auth_router, "record_refused_oauth_code", refused)
    monkeypatch.setattr(auth_router, "administrator_addresses", administrators)
    monkeypatch.setattr(auth_router, "notify_account_request", notify)
    return signer


def _verified(monkeypatch: pytest.MonkeyPatch, object_id: str, email: str = "a.colleague@example.org") -> None:
    async def verify(code: str, verifier: str) -> AzureIdentity:
        assert (code, verifier) == (_CODE, _VERIFIER)
        return AzureIdentity(
            object_id=object_id, preferred_username=None, email=email, given_name="A", family_name="Colleague", name="A Colleague"
        )

    monkeypatch.setattr(auth_router.oauth, "verify_authorization_code", verify)


_BODY = {"authorization_code": _CODE, "code_verifier": _VERIFIER}


def test_the_browser_is_told_where_to_sign_in_through_oauth(client: TestClient, oauth_on: None) -> None:
    response = client.get("/api/auth/config")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "microsoft": None,
        "cas": None,
        "oauth": {"display_name": "UGent", "authorize_url": oauth.authorize_url(), "logout_url": None},
    }
    assert _CLIENT_SECRET not in response.text


def test_a_first_oauth_sign_in_makes_an_account_that_waits_for_approval(
    client: TestClient, signer: _Signer, monkeypatch: pytest.MonkeyPatch, audit_rows: list[Any]
) -> None:
    _verified(monkeypatch, str(uuid4()))

    response = client.post("/api/auth/oauth/session", json=_BODY)

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == auth_router.CAS_ACCOUNT_WAITING_DETAIL
    assert signer.sources == [OAUTH_SIGN_IN]
    assert signer.notified == ["a.colleague@example.org"]
    (row,) = _rows(audit_rows, "/api/auth/oauth/session")
    assert row.request_meta["account_change"]["created"] == "oauth_sign_in"


def test_an_account_active_once_is_told_it_is_not_active_rather_than_waiting(
    client: TestClient, signer: _Signer, store: _Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    object_id = str(uuid4())
    store.accounts["a.colleague@example.org"] = _account(
        "a.colleague@example.org",
        "viewer",
        azure_object_id=object_id,
        is_active=False,
        activated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        archived_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    _verified(monkeypatch, object_id)

    response = client.post("/api/auth/oauth/session", json=_BODY)

    assert (response.status_code, response.json()) == (403, {"detail": auth_router.CAS_ACCOUNT_INACTIVE_DETAIL})
    assert signer.notified == []


def test_an_active_account_gets_a_session_that_signs_it_in(
    client: TestClient, signer: _Signer, store: _Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    object_id = str(uuid4())
    store.accounts["a.colleague@example.org"] = _account("a.colleague@example.org", "viewer", azure_object_id=object_id)
    _verified(monkeypatch, object_id)

    response = client.post("/api/auth/oauth/session", json=_BODY)

    assert response.status_code == 200, response.text
    assert response.json()["role"] == "viewer"
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {response.json()['access_token']}"})
    assert me.status_code == 200, me.text
    assert (me.json()["email"], me.json()["sign_in"]) == ("a.colleague@example.org", "oauth")


def test_a_refused_code_answers_401_and_counts_against_the_address(
    client: TestClient, signer: _Signer, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def refuse(_code: str, _verifier: str) -> AzureIdentity:
        raise oauth.OAuthError("the token endpoint refused the code (HTTP 400, invalid_grant)")

    monkeypatch.setattr(auth_router.oauth, "verify_authorization_code", refuse)

    response = client.post("/api/auth/oauth/session", json=_BODY)

    assert response.status_code == 401, response.text
    assert response.json()["detail"] == auth_router.OAUTH_CODE_REFUSED_DETAIL
    assert signer.refused == 1
    assert signer.identities == []
    assert "invalid_grant" in caplog.text
    assert _CODE not in caplog.text and _VERIFIER not in caplog.text


def test_a_throttled_address_is_refused_before_the_server_is_asked(
    client: TestClient, signer: _Signer, monkeypatch: pytest.MonkeyPatch
) -> None:
    signer.throttled = True

    async def verify(_code: str, _verifier: str) -> AzureIdentity:
        raise AssertionError("asked the OAuth server")

    monkeypatch.setattr(auth_router.oauth, "verify_authorization_code", verify)

    response = client.post("/api/auth/oauth/session", json=_BODY)

    assert (response.status_code, response.headers.get("Retry-After")) == (429, "30")


def test_a_body_without_a_verifier_of_pkce_length_is_refused_before_anything_else(client: TestClient, signer: _Signer) -> None:
    response = client.post("/api/auth/oauth/session", json={"authorization_code": _CODE, "code_verifier": "short"})
    assert response.status_code == 422, response.text
    assert signer.identities == []


def test_without_the_oauth_sign_in_the_route_answers_404(client: TestClient) -> None:
    response = client.post("/api/auth/oauth/session", json=_BODY)
    assert response.status_code == 404, response.text


def test_with_oauth_there_is_no_password_sign_in_or_sign_up(client: TestClient, signer: _Signer, store: _Store) -> None:
    login = client.post("/api/auth/login", json={"email": LOCAL_VIEWER["email"], "password": _PHRASE})
    assert (login.status_code, login.json()["detail"]) == (403, auth_router.PASSWORD_SIGN_IN_OFF_DETAIL)
    assert store.looked_up == []

    signup = client.post(
        "/api/auth/signup",
        json={"email": "new@example.org", "password": _PHRASE, "first_name": "N", "last_name": "U", "affiliation": "Lab"},
    )
    assert (signup.status_code, signup.json()["detail"]) == (403, auth_router.SIGNUP_OFF_DETAIL)


def test_with_oauth_a_local_token_signs_in_only_an_account_bound_to_an_object_id(
    client: TestClient, signer: _Signer, store: _Store
) -> None:
    bound = _account("bound@example.org", "viewer", azure_object_id=str(uuid4()))
    store.accounts[bound["email"]] = bound

    assert client.get("/api/auth/me", headers=_bearer(bound["email"])).status_code == 200
    assert client.get("/api/auth/me", headers=_bearer(LOCAL_VIEWER["email"])).status_code == 401
    # The emergency administrator, only while the override is on.
    assert client.get("/api/auth/me", headers=_bearer(EMERGENCY["email"])).status_code == 401


def test_with_oauth_the_emergency_administrator_signs_in_while_the_override_is_on(
    client: TestClient, signer: _Signer, monkeypatch: pytest.MonkeyPatch, audit_rows: list[Any]
) -> None:
    monkeypatch.setattr(settings, "azure_admin_override", True)

    response = client.get("/api/auth/me", headers=_bearer(EMERGENCY["email"]))

    assert response.status_code == 200, response.text
    (row,) = _rows(audit_rows, "/api/auth/me")
    assert row.request_meta.get("emergency_sign_in") is True



def test_with_the_oauth_sign_in_a_bound_account_signs_in_through_oauth(oauth_on: None) -> None:
    from backend.app.services import metadata_service

    row = {
        "id": str(uuid4()),
        "username": "u@example.org",
        "email": "u@example.org",
        "is_active": True,
        "role": "viewer",
        "created_at": datetime.now(timezone.utc),
    }
    assert metadata_service._user_read_from_mapping({**row, "azure_object_id": str(uuid4())}).sign_in == "oauth"
    assert metadata_service._user_read_from_mapping({**row, "azure_object_id": None}).sign_in == "password"


def test_the_approval_mail_links_to_the_users_page_at_the_redirect_s_origin(oauth_on: None) -> None:
    from backend.app.services.account_notifications import users_page_url

    assert users_page_url() == "https://coga.example.org/admin/access/users"


def test_the_request_audit_masks_the_code_and_the_verifier() -> None:
    from backend.app.middleware.request_logging import _sanitize_for_logging

    assert _sanitize_for_logging(_BODY) == {"authorization_code": "***", "code_verifier": "***"}
