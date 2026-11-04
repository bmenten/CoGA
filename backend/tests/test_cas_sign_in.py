"""Sign-in through the organisation's CAS server, such as login.ugent.be (SEC-4).

The browser comes back from the CAS server to the app's /login page with a service ticket, and
``POST /api/auth/cas/session`` checks it with the CAS server (``/serviceValidate``, for CoGA's
own service). The answer must be a CAS answer, of reasonable size and without a document type
declaration, that names the user and carries the user's object ID in the organisation's Entra
ID; the account is bound to that ID. A first sign-in makes an account that waits for an
administrator's approval (403, the administrator is told); an active account gets a session
token. A refused ticket answers 401 and counts against the source address, which is
throttled. With the CAS sign-in configured there is no password sign-in or sign-up, and a
token this server issued signs in only an account bound to an object ID, or the emergency
administrator while ``AZURE_ADMIN_OVERRIDE`` is on (flagged in the request audit).

The settings refuse half a CAS configuration, a CAS sign-in beside Microsoft's, an address
that is not HTTPS (outside development), and a service that is not the app's /login page.

The CAS server is replaced by an httpx mock transport, and the account store as in
``test_microsoft_sign_in_routes.py``. The users, tickets and addresses are synthetic.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import pytest

from backend.app.core import cas
from backend.app.core.azure import AzureIdentity
from backend.app.core.config import Settings, settings
from backend.app.routers import auth as auth_router
from backend.app.services.access_control import CurrentUser
from backend.app.services.user_accounts import CAS_SIGN_IN
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

_SERVER = "https://login.example.org"
_SERVICE = "https://coga.example.org/login"
_CAS = "http://www.yale.edu/tp/cas"
_TICKET = "ST-" + "a1b2c3d4" * 4
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _answer(user: str = "acolleague", **attributes: str | list[str]) -> bytes:
    values = {
        "azureObjectId": str(uuid4()),
        "mail": "a.colleague@example.org",
        "givenName": "A",
        "surname": "Colleague",
        **attributes,
    }
    lines = []
    for name, value in values.items():
        for item in value if isinstance(value, list) else [value]:
            lines.append(f"<cas:{name}>{item}</cas:{name}>")
    return (
        f'<cas:serviceResponse xmlns:cas="{_CAS}"><cas:authenticationSuccess>'
        f"<cas:user>{user}</cas:user><cas:attributes>{''.join(lines)}</cas:attributes>"
        "</cas:authenticationSuccess></cas:serviceResponse>"
    ).encode()


@pytest.fixture()
def cas_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cas_server_url", _SERVER)
    monkeypatch.setattr(settings, "cas_service_url", _SERVICE)


def _serve(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
    """Answer the CAS server's requests with ``handler``; returns the requests made."""
    seen: list[httpx.Request] = []
    real = _REAL_ASYNC_CLIENT

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(cas.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(record), **kw))
    return seen


# --------------------------------------------------------------------------- #
# Reading the CAS server's answer
# --------------------------------------------------------------------------- #


def test_an_answer_names_the_user_by_object_id_with_the_e_mail_address_and_name(cas_on: None) -> None:
    object_id = str(uuid4()).upper()

    identity = cas.identity_from_answer(_answer(azureObjectId=object_id))

    assert identity == AzureIdentity(
        object_id=object_id.lower(),
        preferred_username="acolleague",
        email="a.colleague@example.org",
        given_name="A",
        family_name="Colleague",
        name="A Colleague",
    )


def test_a_repeated_attribute_gives_its_first_value(cas_on: None) -> None:
    identity = cas.identity_from_answer(_answer(mail=["first@example.org", "second@example.org"]))
    assert identity.email == "first@example.org"


@pytest.mark.parametrize("object_id", [None, "", "not-a-uuid"])
def test_an_answer_without_an_object_id_is_refused(cas_on: None, object_id: str | None) -> None:
    if object_id is None:
        # The attribute is not released at all.
        body = _answer().replace(b"cas:azureObjectId>", b"cas:other>")
    else:
        body = _answer(azureObjectId=object_id)
    with pytest.raises(cas.CasError, match="no object ID"):
        cas.identity_from_answer(body)


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (
            f'<cas:serviceResponse xmlns:cas="{_CAS}"><cas:authenticationFailure code="INVALID_TICKET">'
            "Ticket ST-1 not recognized</cas:authenticationFailure></cas:serviceResponse>".encode(),
            r"refused the ticket \(INVALID_TICKET\)",
        ),
        (b"<html>not CAS</html>", "not a CAS service response"),
        (b"not xml at all <", "not XML"),
        (
            b'<!DOCTYPE r [<!ENTITY a "aaaa">]><cas:serviceResponse xmlns:cas="http://www.yale.edu/tp/cas"/>',
            "document type",
        ),
        (f'<cas:serviceResponse xmlns:cas="{_CAS}"/>'.encode(), "no sign-in"),
        (b"x" * (64 * 1024 + 1), "too large"),
    ],
)
def test_an_answer_that_is_no_successful_cas_answer_is_refused(cas_on: None, body: bytes, reason: str) -> None:
    with pytest.raises(cas.CasError, match=reason) as refused:
        cas.identity_from_answer(body)
    # The reason never quotes the answer.
    assert "not recognized" not in str(refused.value)


def test_the_ticket_is_checked_for_coga_s_own_service(cas_on: None, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _serve(monkeypatch, lambda _request: httpx.Response(200, content=_answer()))

    identity = asyncio.run(cas.verify_service_ticket(_TICKET))

    (request,) = seen
    assert (request.url.scheme, request.url.host, request.url.path) == ("https", "login.example.org", "/serviceValidate")
    assert dict(request.url.params) == {"service": _SERVICE, "ticket": _TICKET}
    assert identity.email == "a.colleague@example.org"


@pytest.mark.parametrize("ticket", ["", "PT-123", "ST-", "ST-abc def", "ST-abc&service=https://evil.example", "ST-" + "a" * 251])
def test_a_value_that_is_no_service_ticket_never_reaches_the_cas_server(
    cas_on: None, monkeypatch: pytest.MonkeyPatch, ticket: str
) -> None:
    seen = _serve(monkeypatch, lambda _request: httpx.Response(200, content=_answer()))
    with pytest.raises(cas.CasError, match="not a CAS service ticket"):
        asyncio.run(cas.verify_service_ticket(ticket))
    assert seen == []


def test_a_cas_server_that_answers_an_error_or_cannot_be_reached_refuses_the_ticket(
    cas_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve(monkeypatch, lambda _request: httpx.Response(302, headers={"Location": "https://elsewhere.example"}))
    with pytest.raises(cas.CasError, match="HTTP 302"):
        asyncio.run(cas.verify_service_ticket(_TICKET))

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    _serve(monkeypatch, unreachable)
    with pytest.raises(cas.CasError, match="could not be reached"):
        asyncio.run(cas.verify_service_ticket(_TICKET))


def test_the_browser_signs_in_at_the_cas_login_page_for_coga_s_service(cas_on: None) -> None:
    url = urlsplit(cas.login_url())
    assert (url.scheme, url.netloc, url.path) == ("https", "login.example.org", "/login")
    assert parse_qs(url.query) == {"service": [_SERVICE]}
    assert cas.logout_url() == "https://login.example.org/logout"


# --------------------------------------------------------------------------- #
# The settings
# --------------------------------------------------------------------------- #


def _settings(**values: Any) -> Settings:
    return Settings(_env_file=None, **{"APP_ENV": "test", **values})


def test_a_cas_configuration_is_both_addresses_and_trims_the_server_s_slash() -> None:
    configured = _settings(CAS_SERVER_URL=f"{_SERVER}/", CAS_SERVICE_URL=_SERVICE)
    assert (configured.cas_server_url, configured.cas_enabled, configured.organisation_sign_in_enabled) == (
        _SERVER,
        True,
        True,
    )
    blank = _settings(CAS_SERVER_URL=" ", CAS_SERVICE_URL="")
    assert (blank.cas_enabled, blank.organisation_sign_in_enabled) == (False, False)


@pytest.mark.parametrize(
    ("values", "reason"),
    [
        ({"CAS_SERVER_URL": _SERVER}, "needs both"),
        ({"CAS_SERVICE_URL": _SERVICE}, "needs both"),
        (
            {"CAS_SERVER_URL": _SERVER, "CAS_SERVICE_URL": _SERVICE, "AZURE_TENANT_ID": str(uuid4()), "AZURE_CLIENT_ID": str(uuid4())},
            "one organisation sign-in at a time",
        ),
        ({"CAS_SERVER_URL": "ftp://login.example.org", "CAS_SERVICE_URL": _SERVICE}, "https://"),
        ({"CAS_SERVER_URL": "https://", "CAS_SERVICE_URL": _SERVICE}, "https://"),
        ({"CAS_SERVER_URL": "https://user@login.example.org", "CAS_SERVICE_URL": _SERVICE}, "user name"),
        ({"CAS_SERVER_URL": _SERVER, "CAS_SERVICE_URL": f"{_SERVICE}?next=/"}, "query string"),
        ({"CAS_SERVER_URL": _SERVER, "CAS_SERVICE_URL": "https://coga.example.org/"}, "/login"),
    ],
)
def test_a_wrong_cas_configuration_is_refused(values: dict[str, str], reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        _settings(**values)


def test_plain_http_serves_a_local_test_server_in_development_only() -> None:
    values = {"CAS_SERVER_URL": "http://localhost:8443", "CAS_SERVICE_URL": "http://localhost:5173/login"}
    assert _settings(APP_ENV="development", **values).cas_enabled
    with pytest.raises(ValueError, match="https://"):
        _settings(APP_ENV="staging", **values)


def test_the_emergency_override_needs_an_organisation_sign_in() -> None:
    assert _settings(CAS_SERVER_URL=_SERVER, CAS_SERVICE_URL=_SERVICE, AZURE_ADMIN_OVERRIDE="true").azure_admin_override
    with pytest.raises(ValueError, match="AZURE_ADMIN_OVERRIDE"):
        _settings(AZURE_ADMIN_OVERRIDE="true")


# --------------------------------------------------------------------------- #
# The routes
# --------------------------------------------------------------------------- #


class _Signer:
    """The account store of the CAS sign-in: who signed in, the throttle and the notices."""

    def __init__(self, store: _Store) -> None:
        self.store = store
        self.identities: list[AzureIdentity] = []
        self.sources: list[Any] = []
        self.refused = 0
        self.throttled = False
        self.notified: list[str] = []


@pytest.fixture()
def signer(monkeypatch: pytest.MonkeyPatch, store: _Store, cas_on: None) -> _Signer:
    signer = _Signer(store)

    async def account(_session: Any, identity: AzureIdentity, source: Any) -> tuple[CurrentUser, Any]:
        signer.identities.append(identity)
        signer.sources.append(source)
        existing = next((a for a in store.accounts.values() if a["azure_object_id"] == identity.object_id), None)
        if existing is not None:
            return _current_user(existing), None
        made = _account(identity.email, "viewer", azure_object_id=identity.object_id, is_active=False)
        store.accounts[made["email"]] = made
        return _current_user(made), {"user_id": made["id"], "created": "cas_sign_in"}

    async def throttle(_session: Any, *, remote_ip: Any) -> Any:
        if signer.throttled:
            return type("State", (), {"retry_after_seconds": 30})()
        return None

    async def refused(_session: Any, *, remote_ip: Any) -> None:
        signer.refused += 1

    monkeypatch.setattr(auth_router, "microsoft_account_user", account)
    monkeypatch.setattr(auth_router, "get_cas_throttle_state", throttle)
    monkeypatch.setattr(auth_router, "record_refused_cas_ticket", refused)
    async def administrators(_session: Any) -> list[str]:
        return ["the.admin@example.org"]

    def notify(account: Any, recipients: list[str]) -> bool:
        assert recipients == ["the.admin@example.org"]
        assert account.sign_in == "cas"
        signer.notified.append(account.email)
        return True

    monkeypatch.setattr(auth_router, "administrator_addresses", administrators)
    monkeypatch.setattr(auth_router, "notify_account_request", notify)
    return signer


def _verified(monkeypatch: pytest.MonkeyPatch, object_id: str, email: str = "a.colleague@example.org") -> None:
    async def verify(ticket: str) -> AzureIdentity:
        assert ticket == _TICKET
        return AzureIdentity(
            object_id=object_id, preferred_username="acolleague", email=email, given_name="A", family_name="Colleague", name="A Colleague"
        )

    monkeypatch.setattr(auth_router.cas, "verify_service_ticket", verify)


def test_the_browser_is_told_where_to_sign_in_through_cas(client: TestClient, cas_on: None) -> None:
    response = client.get("/api/auth/config")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "microsoft": None,
        "cas": {
            "display_name": "UGent",
            "login_url": cas.login_url(),
            "logout_url": "https://login.example.org/logout",
        },
    }


def test_a_first_cas_sign_in_makes_an_account_that_waits_for_approval(
    client: TestClient, signer: _Signer, monkeypatch: pytest.MonkeyPatch, audit_rows: list[Any]
) -> None:
    object_id = str(uuid4())
    _verified(monkeypatch, object_id)

    response = client.post("/api/auth/cas/session", json={"ticket": _TICKET})

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == auth_router.CAS_ACCOUNT_WAITING_DETAIL
    assert signer.sources == [CAS_SIGN_IN]
    assert signer.notified == ["a.colleague@example.org"]
    (row,) = _rows(audit_rows, "/api/auth/cas/session")
    assert row.request_meta["account_change"]["created"] == "cas_sign_in"


def test_an_account_active_once_is_told_it_is_not_active_rather_than_waiting(
    client: TestClient, signer: _Signer, store: _Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Deactivated or archived since it was active: no approval is pending, and nobody is mailed.
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

    response = client.post("/api/auth/cas/session", json={"ticket": _TICKET})

    assert (response.status_code, response.json()) == (403, {"detail": auth_router.CAS_ACCOUNT_INACTIVE_DETAIL})
    assert signer.notified == []


def test_an_active_account_gets_a_session_that_signs_it_in(
    client: TestClient, signer: _Signer, store: _Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    object_id = str(uuid4())
    store.accounts["a.colleague@example.org"] = _account("a.colleague@example.org", "viewer", azure_object_id=object_id)
    _verified(monkeypatch, object_id)

    response = client.post("/api/auth/cas/session", json={"ticket": _TICKET})

    assert response.status_code == 200, response.text
    assert response.json()["role"] == "viewer"
    assert signer.notified == []
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {response.json()['access_token']}"})
    assert me.status_code == 200, me.text
    assert (me.json()["email"], me.json()["sign_in"]) == ("a.colleague@example.org", "cas")


def test_a_refused_ticket_answers_401_and_counts_against_the_address(
    client: TestClient, signer: _Signer, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def refuse(_ticket: str) -> AzureIdentity:
        raise cas.CasError("the CAS server refused the ticket (INVALID_TICKET)")

    monkeypatch.setattr(auth_router.cas, "verify_service_ticket", refuse)

    response = client.post("/api/auth/cas/session", json={"ticket": _TICKET})

    assert response.status_code == 401, response.text
    assert response.json()["detail"] == auth_router.CAS_TICKET_REFUSED_DETAIL
    assert signer.refused == 1
    assert signer.identities == []
    assert _TICKET not in caplog.text


def test_a_throttled_address_is_refused_before_the_cas_server_is_asked(
    client: TestClient, signer: _Signer, monkeypatch: pytest.MonkeyPatch
) -> None:
    signer.throttled = True
    asked: list[str] = []

    async def verify(ticket: str) -> AzureIdentity:
        asked.append(ticket)
        raise AssertionError("asked the CAS server")

    monkeypatch.setattr(auth_router.cas, "verify_service_ticket", verify)

    response = client.post("/api/auth/cas/session", json={"ticket": _TICKET})

    assert (response.status_code, response.headers.get("Retry-After")) == (429, "30")
    assert asked == []


def test_without_the_cas_sign_in_the_route_answers_404(client: TestClient) -> None:
    response = client.post("/api/auth/cas/session", json={"ticket": _TICKET})
    assert response.status_code == 404, response.text


def test_with_cas_there_is_no_password_sign_in_or_sign_up(client: TestClient, signer: _Signer, store: _Store) -> None:
    login = client.post("/api/auth/login", json={"email": LOCAL_VIEWER["email"], "password": _PHRASE})
    assert (login.status_code, login.json()["detail"]) == (403, auth_router.PASSWORD_SIGN_IN_OFF_DETAIL)
    assert store.looked_up == []

    signup = client.post(
        "/api/auth/signup",
        json={"email": "new@example.org", "password": _PHRASE, "first_name": "N", "last_name": "U", "affiliation": "Lab"},
    )
    assert (signup.status_code, signup.json()["detail"]) == (403, auth_router.SIGNUP_OFF_DETAIL)


def test_with_cas_a_local_token_signs_in_only_an_account_bound_to_an_object_id(
    client: TestClient, signer: _Signer, store: _Store
) -> None:
    bound = _account("bound@example.org", "viewer", azure_object_id=str(uuid4()))
    store.accounts[bound["email"]] = bound

    assert client.get("/api/auth/me", headers=_bearer(bound["email"])).status_code == 200
    assert client.get("/api/auth/me", headers=_bearer(LOCAL_VIEWER["email"])).status_code == 401
    # The emergency administrator, only while the override is on.
    assert client.get("/api/auth/me", headers=_bearer(EMERGENCY["email"])).status_code == 401


def test_with_cas_the_emergency_administrator_signs_in_while_the_override_is_on(
    client: TestClient, signer: _Signer, monkeypatch: pytest.MonkeyPatch, audit_rows: list[Any]
) -> None:
    monkeypatch.setattr(settings, "azure_admin_override", True)

    response = client.get("/api/auth/me", headers=_bearer(EMERGENCY["email"]))

    assert response.status_code == 200, response.text
    (row,) = _rows(audit_rows, "/api/auth/me")
    assert row.request_meta.get("emergency_sign_in") is True
