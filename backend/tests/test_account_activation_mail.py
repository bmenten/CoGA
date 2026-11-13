"""The mail to the user whose account an administrator activates.

A change on the *Users* page that makes an inactive account active tells the account's user by
e-mail through the mail server ``SMTP_HOST`` names (services/account_activation_mail.py,
services/outgoing_mail.py). These pin that:

* the mail goes from ``SMTP_FROM`` to the account's address alone, says it can sign in, who
  activated it and how it signs in, on lines a value cannot break, and links CoGA's sign-in
  page when CoGA knows its own address (the CAS service URL);
* without ``SMTP_HOST``, or without one plain address for the account, no mail goes out, and
  the log says so without the address; a mail the server refuses is logged with the error's
  type only;
* ``PATCH /api/auth/users/{user_id}`` sends the mail after its answer when the change makes the
  account active, and none for any other change.

The mail server is replaced by a fake; the addresses are synthetic.
"""

from __future__ import annotations

import logging
import smtplib
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.core.postgres import get_postgres_session
from backend.app.main import app
from backend.app.routers import auth as auth_router
from backend.app.schemas import UserRead
from backend.app.services import account_activation_mail, outgoing_mail
from backend.app.services.access_control import CurrentUser
from backend.app.services.account_activation_mail import AccountActivation

ACTIVATION = AccountActivation(
    email="new.colleague@example.org",
    name="New Colleague",
    sign_in="cas",
    activated_by="Ada Admin",
)


class _FakeSmtp:
    sent: list[Any] = []
    calls: list[tuple[str, Any]] = []

    def __init__(self, host: str, port: int = 0, timeout: float | None = None) -> None:
        _FakeSmtp.calls.append(("connect", (host, port, timeout)))

    def __enter__(self) -> "_FakeSmtp":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def starttls(self, context: Any = None) -> None:
        _FakeSmtp.calls.append(("starttls", context is not None))

    def login(self, user: str, password: str) -> None:
        _FakeSmtp.calls.append(("login", user))

    def send_message(self, message: Any) -> None:
        _FakeSmtp.calls.append(("send", message["To"]))
        _FakeSmtp.sent.append(message)


class _RefusingSmtp(_FakeSmtp):
    def send_message(self, message: Any) -> None:
        # The server's answer quotes the address, as a refused recipient's does.
        raise smtplib.SMTPRecipientsRefused(
            {"new.colleague@example.org": (550, b"no mailbox for new.colleague@example.org")}
        )


def _configure(monkeypatch: pytest.MonkeyPatch, smtp: type = _FakeSmtp, **env: Any) -> None:
    _FakeSmtp.sent = []
    _FakeSmtp.calls = []
    monkeypatch.setattr(outgoing_mail, "settings", Settings(_env_file=None, APP_ENV="test", **env))
    monkeypatch.setattr(outgoing_mail.smtplib, "SMTP", smtp)


def _activation(**changes: Any) -> AccountActivation:
    return AccountActivation(**{**ACTIVATION.__dict__, **changes})


def test_the_mail_says_the_account_can_sign_in_and_links_the_sign_in_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(
        monkeypatch,
        SMTP_HOST="smtprelay.example.org",
        SMTP_STARTTLS="true",
        SMTP_FROM="coga@example.org",
        ADMIN_EMAIL="emergency@example.org",
        CAS_SERVER_URL="https://login.example.org",
        CAS_SERVICE_URL="https://coga.example.org/login",
    )

    assert account_activation_mail.notify_account_activation(ACTIVATION) is True

    (message,) = _FakeSmtp.sent
    assert [call for call, _ in _FakeSmtp.calls] == ["connect", "starttls", "send"]
    assert (message["From"], message["To"]) == ("coga@example.org", "new.colleague@example.org")
    assert message["Subject"] == "CoGA: your account is active"
    body = message.get_content()
    assert body.startswith("Dear New Colleague,\n")
    assert "An administrator activated your CoGA account: you can sign in now.\n" in body
    assert "Account: new.colleague@example.org\n" in body
    assert "Activated by: Ada Admin\n" in body
    assert "Sign in with: the organisation's sign-in (CAS)\n" in body
    assert "Sign in at:\nhttps://coga.example.org/login\n" in body


def test_without_its_own_address_the_mail_says_where_to_sign_in(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, SMTP_HOST="smtprelay.example.org", ADMIN_EMAIL="emergency@example.org")

    account_activation_mail.notify_account_activation(_activation(name="", sign_in="password"))

    (message,) = _FakeSmtp.sent
    assert message["From"] == "emergency@example.org"
    body = message.get_content()
    # Without a name, the account's address.
    assert body.startswith("Dear new.colleague@example.org,\n")
    assert "Sign in with: this e-mail address and the password you chose when you signed up\n" in body
    assert "Sign in to CoGA at the address where you asked for the account.\n" in body
    assert "http" not in body


def test_a_value_cannot_break_a_line_of_the_mail(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, SMTP_HOST="smtprelay.example.org")

    account_activation_mail.notify_account_activation(
        _activation(name="New\nColleague", activated_by="Ada\r\nBcc: x@example.org")
    )

    body = _FakeSmtp.sent[0].get_content()
    assert body.startswith("Dear New Colleague,\n")
    assert "Activated by: Ada  Bcc: x@example.org\n" in body
    assert "\nBcc:" not in body


@pytest.mark.parametrize(
    ("env", "address", "logged", "level"),
    [
        ({}, "new.colleague@example.org", "SMTP_HOST not set: no mail about the account activation", logging.INFO),
        (
            {"SMTP_HOST": "smtprelay.example.org"},
            "a@example.org, b@example.org",
            "no mail about the account activation: the account has no plain e-mail address",
            logging.WARNING,
        ),
    ],
    ids=["no-mail-server", "two-addresses"],
)
def test_no_mail_goes_out_without_a_mail_server_or_a_plain_address(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    env: dict[str, str],
    address: str,
    logged: str,
    level: int,
) -> None:
    monkeypatch.delenv("SMTP_HOST", raising=False)
    _configure(monkeypatch, **env)

    with caplog.at_level(logging.INFO, logger=account_activation_mail.__name__):
        assert account_activation_mail.notify_account_activation(_activation(email=address)) is False

    assert _FakeSmtp.calls == []
    (record,) = [r for r in caplog.records if r.name == account_activation_mail.__name__]
    assert (record.getMessage(), record.levelno) == (logged, level)


def test_a_refused_mail_is_logged_with_the_error_type_only(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _configure(monkeypatch, smtp=_RefusingSmtp, SMTP_HOST="smtprelay.example.org")

    with caplog.at_level(logging.INFO, logger=account_activation_mail.__name__):
        assert account_activation_mail.notify_account_activation(ACTIVATION) is False

    (record,) = [r for r in caplog.records if r.name == account_activation_mail.__name__]
    assert record.levelno == logging.ERROR
    assert record.getMessage() == (
        "the mail about the account activation was not sent: The mail server could not take the "
        "mail (SMTPRecipientsRefused)."
    )
    assert "example.org" not in record.getMessage()


def test_a_sent_mail_is_logged_without_the_address(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _configure(monkeypatch, SMTP_HOST="smtprelay.example.org")

    with caplog.at_level(logging.INFO, logger=account_activation_mail.__name__):
        account_activation_mail.notify_account_activation(ACTIVATION)

    (record,) = [r for r in caplog.records if r.name == account_activation_mail.__name__]
    assert record.getMessage() == "the mail about the account activation went to its user"


# --- the route -------------------------------------------------------------------------------

_ACCOUNT_ID = "00000000-0000-4000-8000-000000000002"


class _FakeSession:
    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def _account(**changes: Any) -> UserRead:
    return UserRead(
        **{
            "id": _ACCOUNT_ID,
            "username": "new.colleague@example.org",
            "email": "new.colleague@example.org",
            "first_name": "New",
            "last_name": "Colleague",
            "sign_in": "cas",
            "is_active": True,
            "role": "viewer",
            "created_at": datetime(2026, 11, 12, 8, 0, tzinfo=timezone.utc),
            "activated_at": datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc),
            **changes,
        }
    )


@pytest.fixture()
def route(monkeypatch: pytest.MonkeyPatch):
    """The route over a stubbed account change that answers what ``answer`` holds; ``told``
    collects what the route sends."""
    original = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True
    answer: dict[str, Any] = {}
    told: list[AccountActivation] = []

    async def session():
        yield _FakeSession()

    async def change(_session: Any, **_kwargs: Any) -> tuple[UserRead, dict[str, dict[str, Any]]]:
        return answer["account"], answer["changes"]

    async def target(_session: Any, _user_id: str) -> dict[str, Any]:
        return {"id": _ACCOUNT_ID, "role": "viewer", "is_active": True, "archived_at": None}

    app.dependency_overrides[get_postgres_session] = session
    app.dependency_overrides[auth_router.get_current_admin_user] = lambda: CurrentUser(
        id="00000000-0000-4000-8000-000000000001",
        username="ada.admin",
        email="ada.admin@example.org",
        first_name="Ada",
        last_name="Admin",
        role="admin",
        created_at=datetime.now(timezone.utc),
    )
    monkeypatch.setattr(auth_router, "update_user_account", change)
    monkeypatch.setattr(auth_router, "get_user_account_mapping", target)
    monkeypatch.setattr(auth_router, "notify_account_activation", told.append)
    try:
        with TestClient(app) as client:
            yield client, answer, told
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original)


def test_the_route_mails_the_account_it_makes_active(route: Any) -> None:
    client, answer, told = route
    answer.update(account=_account(), changes={"is_active": {"from": False, "to": True}})

    response = client.patch(f"/api/auth/users/{_ACCOUNT_ID}", json={"is_active": True})

    assert response.status_code == 200
    assert told == [
        AccountActivation(
            email="new.colleague@example.org",
            name="New Colleague",
            sign_in="cas",
            activated_by="Ada Admin",
        )
    ]


@pytest.mark.parametrize(
    ("body", "account", "changes"),
    [
        # Already active: nothing changed.
        ({"is_active": True}, {}, {}),
        ({"role": "analyst"}, {"role": "analyst"}, {"role": {"from": "viewer", "to": "analyst"}}),
        ({"is_active": False}, {"is_active": False}, {"is_active": {"from": True, "to": False}}),
        (
            {"archived": False},
            {"is_active": False},
            {"archived": {"from": True, "to": False}},
        ),
    ],
    ids=["already-active", "role", "deactivated", "restored"],
)
def test_the_route_mails_no_one_for_any_other_change(
    route: Any, body: dict[str, Any], account: dict[str, Any], changes: dict[str, Any]
) -> None:
    client, answer, told = route
    answer.update(account=_account(**account), changes=changes)

    response = client.patch(f"/api/auth/users/{_ACCOUNT_ID}", json=body)

    assert response.status_code == 200
    assert told == []
