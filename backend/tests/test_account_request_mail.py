"""The mail to the administrators about an account that waits for approval.

A sign-up and a first CAS sign-in make an inactive account; every active administrator is told
by e-mail through the mail server ``SMTP_HOST`` names (services/account_notifications.py). These
pin that:

* the mail goes through ``SMTP_HOST``:``SMTP_PORT`` from ``SMTP_FROM`` (``ADMIN_EMAIL`` when it
  is not set) to every administrator given, names the account (name, e-mail address,
  affiliation, how it signs in, when it was made) on lines a value cannot break, and links the
  *Users* page when CoGA knows its own address (the CAS service URL);
* with ``SMTP_STARTTLS`` the connection is encrypted before ``SMTP_USERNAME`` signs in, and
  outside development a user name without STARTTLS is refused at start;
* without ``SMTP_HOST``, or without an administrator, no mail goes out, and the log says so
  without an address; a mail the server refuses is logged with the error's type only, never
  the server's text, which quotes the addresses;
* ``GET /api/auth/account-request-mail`` says whether the mail goes out and to whom, and
  ``POST /api/auth/account-request-mail/test`` sends a test: 409 without a mail server or an
  administrator, 502 with the error's type when the server refuses it.

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
from backend.app.dependencies import get_current_admin_user
from backend.app.main import app
from backend.app.routers import auth as auth_router
from backend.app.services import account_notifications
from backend.app.services.access_control import CurrentUser

ADMINS = ["first.admin@example.org", "second.admin@example.org"]
ACCOUNT = account_notifications.AccountRequest(
    email="new.colleague@example.org",
    first_name="New",
    last_name="Colleague",
    affiliation="Synthetic lab",
    sign_in="cas",
    created_at=datetime(2026, 11, 4, 14, 3, tzinfo=timezone.utc),
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
        # The server's answer quotes the addresses, as a refused recipient's does.
        raise smtplib.SMTPRecipientsRefused(
            {"first.admin@example.org": (550, b"no mailbox for new.colleague@example.org")}
        )


def _configure(monkeypatch: pytest.MonkeyPatch, smtp: type = _FakeSmtp, **env: Any) -> Settings:
    _FakeSmtp.sent = []
    _FakeSmtp.calls = []
    configured = Settings(_env_file=None, APP_ENV="test", **env)
    monkeypatch.setattr(account_notifications, "settings", configured)
    monkeypatch.setattr(account_notifications.smtplib, "SMTP", smtp)
    return configured


def test_the_mail_names_the_account_and_goes_to_every_administrator(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(
        monkeypatch,
        SMTP_HOST="mail.example.org",
        SMTP_PORT="2525",
        SMTP_FROM="coga@example.org",
        ADMIN_EMAIL="emergency@example.org",
        CAS_SERVER_URL="https://login.example.org",
        CAS_SERVICE_URL="https://coga.example.org/login",
    )

    assert account_notifications.notify_account_request(ACCOUNT, ADMINS) is True

    (message,) = _FakeSmtp.sent
    assert _FakeSmtp.calls[0] == ("connect", ("mail.example.org", 2525, 10))
    assert [call for call, _ in _FakeSmtp.calls] == ["connect", "send"]
    assert message["To"] == "first.admin@example.org, second.admin@example.org"
    assert message["From"] == "coga@example.org"
    assert message["Subject"] == "CoGA: a new account waits for approval"
    body = message.get_content()
    assert "Name: New Colleague\n" in body
    assert "E-mail address: new.colleague@example.org\n" in body
    assert "Affiliation: Synthetic lab\n" in body
    assert "Made by: the organisation's sign-in (CAS)\n" in body
    assert "Made on: 2026-11-04 14:03 UTC\n" in body
    assert "https://coga.example.org/admin/access/users" in body


def test_without_its_own_address_the_mail_names_the_page_and_comes_from_admin_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, SMTP_HOST="mail.example.org", ADMIN_EMAIL="emergency@example.org")

    account_notifications.notify_account_request(
        account_notifications.AccountRequest(
            email="signed.up@example.org",
            first_name="",
            last_name="",
            affiliation="",
            sign_in="password",
            created_at=datetime(2026, 11, 4, 14, 3),
        ),
        ADMINS[:1],
    )

    (message,) = _FakeSmtp.sent
    assert _FakeSmtp.calls[0] == ("connect", ("mail.example.org", 25, 10))
    assert message["From"] == "emergency@example.org"
    body = message.get_content()
    assert "Name: —\n" in body
    assert "Made by: a sign-up with a password\n" in body
    assert "under Administration > Users" in body
    assert "http" not in body


def test_a_value_cannot_add_a_line_or_a_header_to_the_mail(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, SMTP_HOST="mail.example.org")

    account_notifications.notify_account_request(
        account_notifications.AccountRequest(
            email="odd@example.org",
            first_name="Odd\r\nBcc: someone@example.org",
            last_name="Name Line",
            affiliation="x" * 500,
            sign_in="cas",
            created_at=ACCOUNT.created_at,
        ),
        ADMINS,
    )

    (message,) = _FakeSmtp.sent
    assert message["Bcc"] is None
    lines = message.get_content().splitlines()
    (name,) = [line for line in lines if line.startswith("Name: ")]
    assert name == "Name: Odd  Bcc: someone@example.org Name Line"
    (affiliation,) = [line for line in lines if line.startswith("Affiliation: ")]
    assert len(affiliation) == len("Affiliation: ") + 200


def test_starttls_comes_before_the_sign_in_to_the_mail_server(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(
        monkeypatch,
        SMTP_HOST="mail.example.org",
        SMTP_PORT="587",
        SMTP_STARTTLS="true",
        SMTP_USERNAME="coga-relay",
        SMTP_PASSWORD="-".join(("synthetic", "relay", "phrase")),
    )

    account_notifications.notify_account_request(ACCOUNT, ADMINS)

    assert [call for call, _ in _FakeSmtp.calls] == ["connect", "starttls", "login", "send"]
    assert ("starttls", True) in _FakeSmtp.calls
    assert ("login", "coga-relay") in _FakeSmtp.calls


def test_outside_development_a_mail_server_user_needs_starttls() -> None:
    relay = {"APP_ENV": "production", "SMTP_HOST": "mail.example.org", "SMTP_USERNAME": "coga-relay"}
    with pytest.raises(ValueError, match="SMTP_STARTTLS"):
        Settings(_env_file=None, **relay)
    # With STARTTLS the mail settings pass; what refuses this start is the missing secrets.
    with pytest.raises(ValueError) as other:
        Settings(_env_file=None, SMTP_STARTTLS="true", **relay)
    assert "SMTP_STARTTLS" not in str(other.value)
    assert "weak secrets" in str(other.value)


@pytest.mark.parametrize(
    ("env", "recipients", "logged", "level"),
    [
        ({}, ADMINS, "SMTP_HOST not set: no mail about the account waiting for approval", logging.INFO),
        (
            {"SMTP_HOST": "mail.example.org"},
            [],
            "no active administrator to tell about the account waiting for approval",
            logging.WARNING,
        ),
    ],
    ids=["no-mail-server", "no-administrator"],
)
def test_no_mail_goes_out_without_a_mail_server_or_an_administrator(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    env: dict[str, str],
    recipients: list[str],
    logged: str,
    level: int,
) -> None:
    monkeypatch.delenv("SMTP_HOST", raising=False)
    _configure(monkeypatch, **env)

    with caplog.at_level(logging.INFO, logger=account_notifications.__name__):
        assert account_notifications.notify_account_request(ACCOUNT, recipients) is False

    assert _FakeSmtp.calls == []
    (record,) = [r for r in caplog.records if r.name == account_notifications.__name__]
    assert (record.getMessage(), record.levelno) == (logged, level)


def test_a_refused_mail_is_logged_with_the_error_type_only(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _configure(monkeypatch, smtp=_RefusingSmtp, SMTP_HOST="mail.example.org")

    with caplog.at_level(logging.INFO, logger=account_notifications.__name__):
        assert account_notifications.notify_account_request(ACCOUNT, ADMINS) is False

    (record,) = [r for r in caplog.records if r.name == account_notifications.__name__]
    assert record.levelno == logging.ERROR
    assert record.getMessage() == (
        "the mail about the account waiting for approval was not sent: The mail server could not "
        "take the mail (SMTPRecipientsRefused)."
    )
    assert "example.org" not in record.getMessage()


# --- the routes ------------------------------------------------------------------------------


class _FakeSession:
    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


@pytest.fixture()
def admin_client(monkeypatch: pytest.MonkeyPatch):
    original = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True

    async def session():
        yield _FakeSession()

    async def administrators(_session: Any) -> list[str]:
        return list(ADMINS)

    app.dependency_overrides[get_postgres_session] = session
    app.dependency_overrides[get_current_admin_user] = lambda: CurrentUser(
        id="00000000-0000-4000-8000-000000000001",
        username="first.admin",
        email=ADMINS[0],
        role="admin",
        created_at=datetime.now(timezone.utc),
    )
    monkeypatch.setattr(auth_router, "administrator_addresses", administrators)
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original)


def test_the_users_page_is_told_whether_the_mail_goes_out_and_to_whom(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SMTP_HOST", raising=False)
    _configure(monkeypatch)
    off = admin_client.get("/api/auth/account-request-mail")
    _configure(monkeypatch, SMTP_HOST="mail.example.org")
    on = admin_client.get("/api/auth/account-request-mail")

    assert (off.status_code, off.json()) == (200, {"enabled": False, "recipients": ADMINS})
    assert (on.status_code, on.json()) == (200, {"enabled": True, "recipients": ADMINS})


def test_a_test_mail_goes_to_every_administrator(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, SMTP_HOST="mail.example.org")

    response = admin_client.post("/api/auth/account-request-mail/test")

    assert (response.status_code, response.json()) == (200, {"recipients": ADMINS})
    (message,) = _FakeSmtp.sent
    assert message["Subject"] == "CoGA: test of the account-request mail"
    assert message["To"] == ", ".join(ADMINS)


def test_a_test_mail_is_refused_without_a_mail_server_or_an_administrator(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SMTP_HOST", raising=False)
    _configure(monkeypatch)
    no_server = admin_client.post("/api/auth/account-request-mail/test")

    async def nobody(_session: Any) -> list[str]:
        return []

    _configure(monkeypatch, SMTP_HOST="mail.example.org")
    monkeypatch.setattr(auth_router, "administrator_addresses", nobody)
    no_admin = admin_client.post("/api/auth/account-request-mail/test")

    assert (no_server.status_code, no_server.json()) == (409, {"detail": auth_router.NO_MAIL_SERVER_DETAIL})
    assert (no_admin.status_code, no_admin.json()) == (409, {"detail": auth_router.NO_ADMIN_ADDRESS_DETAIL})
    assert _FakeSmtp.calls == []


def test_a_refused_test_mail_says_the_error_type_only(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch, smtp=_RefusingSmtp, SMTP_HOST="mail.example.org")

    response = admin_client.post("/api/auth/account-request-mail/test")

    assert response.status_code == 502
    assert response.json() == {
        "detail": "The mail server could not take the mail (SMTPRecipientsRefused)."
    }
