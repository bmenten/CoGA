"""The mail to the user a family is assigned to.

A save of a family's *Assigned to* that names someone new, other than the user saving it, tells
that user by e-mail through the mail server ``SMTP_HOST`` names (services/family_assignment_mail.py,
services/outgoing_mail.py); which saves hand an assignment on is pinned in
``test_case_assignee_eligibility.py``. These pin that:

* the mail goes from ``SMTP_FROM`` (``ADMIN_EMAIL`` when it is not set) to the assignee alone,
  names the family by its ID, who assigned it and when, on lines a value cannot break, and links
  the family's page with its ID encoded when CoGA knows its own address (the CAS service URL);
  its subject names no family;
* without ``SMTP_HOST``, or without one plain address for the assignee, no mail goes out, and
  the log says so without the address or the family; a mail the server refuses is logged with
  the error's type only;
* ``PUT /api/families/{family_id}/metadata`` sends the assignment the save hands on after its
  answer, and nothing when the save hands none on.

The mail server is replaced by a fake; the addresses and family IDs are synthetic.
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
from backend.app.routers import families as families_router
from backend.app.services import family_assignment_mail, outgoing_mail
from backend.app.services.access_control import CurrentUser
from backend.app.services.family_assignment_mail import FamilyAssignment

ASSIGNMENT = FamilyAssignment(
    family_id="FAM_1/2#B",
    assignee_email="ana.lyst@example.org",
    assignee_name="Ana Lyst",
    assigned_by="Ada Admin",
    assigned_at=datetime(2026, 11, 11, 9, 30, tzinfo=timezone.utc),
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
        raise smtplib.SMTPRecipientsRefused({"ana.lyst@example.org": (550, b"no mailbox for ana.lyst@example.org")})


def _configure(monkeypatch: pytest.MonkeyPatch, smtp: type = _FakeSmtp, **env: Any) -> None:
    _FakeSmtp.sent = []
    _FakeSmtp.calls = []
    monkeypatch.setattr(outgoing_mail, "settings", Settings(_env_file=None, APP_ENV="test", **env))
    monkeypatch.setattr(outgoing_mail.smtplib, "SMTP", smtp)


def _assignment(**changes: Any) -> FamilyAssignment:
    return FamilyAssignment(**{**ASSIGNMENT.__dict__, **changes})


def test_the_mail_names_the_family_and_links_its_page(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(
        monkeypatch,
        SMTP_HOST="smtprelay.example.org",
        SMTP_PORT="25",
        SMTP_STARTTLS="true",
        SMTP_FROM="coga@example.org",
        ADMIN_EMAIL="emergency@example.org",
        CAS_SERVER_URL="https://login.example.org",
        CAS_SERVICE_URL="https://coga.example.org/login",
    )

    assert family_assignment_mail.notify_family_assignment(ASSIGNMENT) is True

    (message,) = _FakeSmtp.sent
    assert [call for call, _ in _FakeSmtp.calls] == ["connect", "starttls", "send"]
    assert _FakeSmtp.calls[0] == ("connect", ("smtprelay.example.org", 25, 10))
    assert (message["From"], message["To"]) == ("coga@example.org", "ana.lyst@example.org")
    assert message["Subject"] == "CoGA: a family is assigned to you"
    body = message.get_content()
    assert body.startswith("Dear Ana Lyst,\n")
    assert "Family: FAM_1/2#B\n" in body
    assert "Assigned by: Ada Admin\n" in body
    assert "Assigned on: 2026-11-11 09:30 UTC\n" in body
    # The ID encoded as one path segment, as the frontend's routePath encodes it.
    assert "Open the family:\nhttps://coga.example.org/families/FAM_1%2F2%23B\n" in body


def test_without_its_own_address_the_mail_says_where_to_find_the_family(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, SMTP_HOST="smtprelay.example.org", ADMIN_EMAIL="emergency@example.org")

    family_assignment_mail.notify_family_assignment(
        _assignment(family_id="FAM1", assigned_at=datetime(2026, 11, 11, 9, 30))
    )

    (message,) = _FakeSmtp.sent
    assert message["From"] == "emergency@example.org"
    body = message.get_content()
    assert "Open it in CoGA: Families, then FAM1.\n" in body
    assert "Assigned on: 2026-11-11 09:30 UTC\n" in body
    assert "http" not in body


def test_a_value_cannot_add_a_line_or_a_header_to_the_mail(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, SMTP_HOST="smtprelay.example.org")

    family_assignment_mail.notify_family_assignment(
        _assignment(assignee_name="Ana\r\nBcc: someone@example.org", assigned_by="Ada\nAdmin" + "x" * 300)
    )

    (message,) = _FakeSmtp.sent
    assert message["Bcc"] is None
    lines = message.get_content().splitlines()
    assert lines[0] == "Dear Ana  Bcc: someone@example.org,"
    (by,) = [line for line in lines if line.startswith("Assigned by: ")]
    assert by.startswith("Assigned by: Ada Admin") and len(by) == len("Assigned by: ") + 200


@pytest.mark.parametrize(
    ("env", "address", "logged", "level"),
    [
        ({}, ASSIGNMENT.assignee_email, "SMTP_HOST not set: no mail about the family assignment", logging.INFO),
        (
            {"SMTP_HOST": "smtprelay.example.org"},
            "ana.lyst@example.org, other@example.org",
            "no mail about the family assignment: the assignee has no plain e-mail address",
            logging.WARNING,
        ),
        (
            {"SMTP_HOST": "smtprelay.example.org"},
            "",
            "no mail about the family assignment: the assignee has no plain e-mail address",
            logging.WARNING,
        ),
    ],
    ids=["no-mail-server", "two-addresses", "no-address"],
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

    with caplog.at_level(logging.INFO, logger=family_assignment_mail.__name__):
        assert family_assignment_mail.notify_family_assignment(_assignment(assignee_email=address)) is False

    assert _FakeSmtp.calls == []
    (record,) = [r for r in caplog.records if r.name == family_assignment_mail.__name__]
    assert (record.getMessage(), record.levelno) == (logged, level)


def test_a_refused_mail_is_logged_with_the_error_type_only(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _configure(monkeypatch, smtp=_RefusingSmtp, SMTP_HOST="smtprelay.example.org")

    with caplog.at_level(logging.INFO, logger=family_assignment_mail.__name__):
        assert family_assignment_mail.notify_family_assignment(ASSIGNMENT) is False

    (record,) = [r for r in caplog.records if r.name == family_assignment_mail.__name__]
    assert record.levelno == logging.ERROR
    assert record.getMessage() == (
        "the mail about the family assignment was not sent: The mail server could not take the "
        "mail (SMTPRecipientsRefused)."
    )
    assert "example.org" not in record.getMessage() and "FAM_1" not in record.getMessage()


def test_a_sent_mail_is_logged_without_the_address_or_the_family(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _configure(monkeypatch, SMTP_HOST="smtprelay.example.org")

    with caplog.at_level(logging.INFO, logger=family_assignment_mail.__name__):
        family_assignment_mail.notify_family_assignment(ASSIGNMENT)

    (record,) = [r for r in caplog.records if r.name == family_assignment_mail.__name__]
    assert record.getMessage() == "the mail about the family assignment went to its assignee"


# --- the route -------------------------------------------------------------------------------


class _FakeSession:
    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def _family_payload() -> dict[str, Any]:
    return {
        "_id": "11111111-1111-1111-1111-111111111111",
        "family_id": "FAM1",
        "created_at": datetime.now(timezone.utc),
        "members": [],
        "relationships": [],
        "projects": [],
        "metadata": {},
        "status": None,
        "assigned_to": None,
        "reviewed_by": None,
    }


@pytest.fixture()
def route(monkeypatch: pytest.MonkeyPatch):
    """The route over a stubbed save that hands on what ``handed`` holds; ``told`` collects
    what the route sends."""
    original = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True
    handed: list[FamilyAssignment] = []
    told: list[FamilyAssignment] = []

    async def session():
        yield _FakeSession()

    async def save(_session: Any, *, family_id: str, update: Any, user: Any, on_assignment: Any) -> dict[str, Any]:
        for assignment in handed:
            on_assignment(assignment)
        return _family_payload()

    app.dependency_overrides[get_postgres_session] = session
    app.dependency_overrides[families_router.get_current_user] = lambda: CurrentUser(
        id="00000000-0000-4000-8000-000000000001",
        username="ada.admin",
        email="ada.admin@example.org",
        role="admin",
        created_at=datetime.now(timezone.utc),
    )
    monkeypatch.setattr(families_router, "update_family_metadata_for_user", save)
    monkeypatch.setattr(families_router, "notify_family_assignment", told.append)
    try:
        with TestClient(app) as client:
            yield client, handed, told
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original)


def test_the_route_mails_the_assignment_the_save_hands_on(route: Any) -> None:
    client, handed, told = route
    handed.append(ASSIGNMENT)

    response = client.put(
        "/api/families/FAM1/metadata", json={"assigned_to": "00000000-0000-4000-8000-000000000002"}
    )

    assert response.status_code == 200
    assert told == [ASSIGNMENT]


def test_the_route_mails_no_one_when_the_save_hands_nothing_on(route: Any) -> None:
    client, _handed, told = route

    response = client.put("/api/families/FAM1/metadata", json={"status_key": None})

    assert response.status_code == 200
    assert told == []
