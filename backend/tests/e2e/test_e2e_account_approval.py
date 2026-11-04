"""An account that waits for approval: the administrators' mail, activation, archive, deletion (E2E).

A sign-up (and a first CAS sign-in) makes an inactive account that was never active
(``users.activated_at`` empty). Every active administrator is told by e-mail; an administrator
activates it, archives it, or deletes it when it is not relevant (SEC-4).

On real Postgres, through the app with real sign-ins:

* a sign-up makes an inactive account that waits for approval, and the mail about it goes to
  every active administrator and not to an inactive one; the *Users* page reads who it reaches,
  and a test mail goes to the same addresses;
* an account activated once can no longer be deleted (409); archived, it is inactive, cannot
  sign in and cannot be activated until it is restored (409), and restored it stays inactive
  with its first activation's time; the database refuses an archived account that is active;
* an account waiting for approval is deleted (204), leaves the account list and is gone (404
  after), and the deletion is in the request audit with the account it deleted; one that a
  record names (a gene panel it made) is refused (409) and kept;
* the administrator can neither archive nor delete their own account.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it. The mail
server is a fake; the values are synthetic, and what the test creates is removed at the end
(the append-only audit events stay, as every e2e test's do).
"""

from __future__ import annotations

import random
from typing import Any
from uuid import uuid4

import pytest

from backend.tests.e2e._harness import ensure_postgres_schema

pytestmark = pytest.mark.integration


class _FakeSmtp:
    sent: list[Any] = []

    def __init__(self, host: str, port: int = 0, timeout: float | None = None) -> None:
        self.host = host

    def __enter__(self) -> "_FakeSmtp":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def send_message(self, message: Any) -> None:
        _FakeSmtp.sent.append(message)


async def _exercise(run: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from backend.app.core.config import settings
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.dependencies import get_password_hash
    from backend.app.main import app
    from backend.tests.e2e import _harness

    password = f"approval-password-{run}"
    emails = {
        "signup": f"e2e-approval-signup-{run}@example.com",
        "approved": f"e2e-approval-approved-{run}@example.com",
        "panel": f"e2e-approval-panel-{run}@example.com",
        "admin": f"e2e-approval-admin-{run}@example.com",
        "old_admin": f"e2e-approval-old-admin-{run}@example.com",
    }
    panel_name = f"e2e-approval-panel-{run}"

    await ensure_postgres_schema()
    sessionmaker = get_postgres_sessionmaker()
    ids: dict[str, str] = {}
    async with sessionmaker() as session:
        admin_user, _project, _assembly = await _harness.ensure_e2e_project(session)
        ids["harness_admin"] = admin_user.id
        hashed = get_password_hash(password)
        # Two accounts waiting for approval, as a sign-up makes them; an active and an inactive
        # administrator besides the harness's.
        for key, role, active, activated in (
            ("approved", "viewer", False, False),
            ("panel", "viewer", False, False),
            ("admin", "admin", True, True),
            ("old_admin", "admin", False, True),
        ):
            ids[key] = (
                await session.execute(
                    text(
                        """
                        INSERT INTO users (username, hashed_password, role, email, is_active, activated_at)
                        VALUES (:email, :hashed, :role, :email, :active,
                                CASE WHEN :activated THEN now() ELSE NULL END)
                        RETURNING id::text
                        """
                    ),
                    {"email": emails[key], "hashed": hashed, "role": role, "active": active, "activated": activated},
                )
            ).scalar_one()
        # A record that names an account waiting for approval: a gene panel it made.
        await session.execute(
            text("INSERT INTO gene_panels (name, created_by) VALUES (:name, CAST(:id AS uuid))"),
            {"name": panel_name, "id": ids["panel"]},
        )
        await session.commit()

    out: dict[str, Any] = {"emails": emails, "ids": ids, "admin_email": settings.admin_email}
    # A source address of its own, so that the sign-up throttle counts this run alone.
    client_address = (f"192.0.2.{random.randint(1, 254)}", 40000)
    transport = ASGITransport(app=app, raise_app_exceptions=False, client=client_address)
    try:
        async with AsyncClient(transport=transport, base_url="http://e2e") as admin:
            admin.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(admin)}"

            # --- a sign-up waits for approval, and the administrators are told -----------------
            async with AsyncClient(transport=transport, base_url="http://e2e") as anonymous:
                signup = await anonymous.post(
                    "/api/auth/signup",
                    json={
                        "email": emails["signup"],
                        "password": password,
                        "first_name": "New",
                        "last_name": "Colleague",
                        "affiliation": "Synthetic lab",
                    },
                )
            out["signup"] = signup.status_code
            out["signup_mail"] = [
                (message["To"], message.get_content()) for message in _FakeSmtp.sent
            ]
            accounts = {account["email"]: account for account in (await admin.get("/api/auth/users")).json()}
            ids["signup"] = accounts[emails["signup"]]["id"]
            out["signup_account"] = {
                key: accounts[emails["signup"]][key] for key in ("is_active", "role", "activated_at", "archived_at")
            }
            out["signup_created_at"] = accounts[emails["signup"]]["created_at"]
            mail = await admin.get("/api/auth/account-request-mail")
            out["mail_status"] = (mail.status_code, mail.json())
            test_mail = await admin.post("/api/auth/account-request-mail/test")
            out["test_mail"] = (test_mail.status_code, test_mail.json(), _FakeSmtp.sent[-1]["To"])

            # --- activated once: archived, never deleted ---------------------------------------
            approved = f"/api/auth/users/{ids['approved']}"
            activated = await admin.patch(approved, json={"is_active": True})
            out["activated"] = (activated.status_code, activated.json()["is_active"], activated.json()["activated_at"])
            out["delete_activated"] = (await admin.delete(approved)).json()
            archived = await admin.patch(approved, json={"archived": True})
            out["archived"] = (
                archived.status_code,
                archived.json()["is_active"],
                archived.json()["archived_at"] is not None,
            )
            async with AsyncClient(transport=transport, base_url="http://e2e") as archived_client:
                login = await archived_client.post(
                    "/api/auth/login", json={"email": emails["approved"], "password": password}
                )
                out["archived_login"] = (login.status_code, login.json())
            activate_archived = await admin.patch(approved, json={"is_active": True})
            out["activate_archived"] = (activate_archived.status_code, activate_archived.json())
            restored = await admin.patch(approved, json={"archived": False})
            out["restored"] = (
                restored.status_code,
                restored.json()["is_active"],
                restored.json()["archived_at"],
                restored.json()["activated_at"] == activated.json()["activated_at"],
            )
            out["delete_restored"] = (await admin.delete(approved)).status_code

            # --- waiting for approval: deleted, unless a record names it ------------------------
            panel_delete = await admin.delete(f"/api/auth/users/{ids['panel']}")
            out["delete_named"] = (panel_delete.status_code, panel_delete.json())
            deleted = await admin.delete(f"/api/auth/users/{ids['signup']}")
            out["deleted"] = deleted.status_code
            out["listed_after"] = emails["signup"] in {
                account["email"] for account in (await admin.get("/api/auth/users")).json()
            }
            out["deleted_again"] = (await admin.delete(f"/api/auth/users/{ids['signup']}")).status_code

            # --- not one's own account ---------------------------------------------------------
            own = f"/api/auth/users/{ids['harness_admin']}"
            out["archive_own"] = (await admin.patch(own, json={"archived": True})).status_code
            out["delete_own"] = (await admin.delete(own)).status_code

        async with sessionmaker() as session:
            out["rows"] = {
                str(row.email): (row.is_active, row.activated_at is not None, row.archived_at is not None)
                for row in (
                    await session.execute(
                        text(
                            "SELECT email, is_active, activated_at, archived_at FROM users "
                            "WHERE email = ANY(:emails)"
                        ),
                        {"emails": list(emails.values())},
                    )
                ).all()
            }
            out["audit_delete"] = [
                (row.status_code, row.user_email, row.account_change)
                for row in (
                    await session.execute(
                        text(
                            """
                            SELECT status_code, user_email, request_meta->'account_change' AS account_change
                            FROM audit_log_events
                            WHERE method = 'DELETE' AND path = :path
                            ORDER BY created_at, id
                            """
                        ),
                        {"path": f"/api/auth/users/{ids['signup']}"},
                    )
                ).all()
            ]
        # The database holds an archived account inactive, whatever writes it.
        try:
            async with sessionmaker() as session:
                await session.execute(
                    text("UPDATE users SET is_active = true, archived_at = now() WHERE id = CAST(:id AS uuid)"),
                    {"id": ids["old_admin"]},
                )
                await session.commit()
            out["archived_active_stored"] = True
        except IntegrityError:
            out["archived_active_stored"] = False
    finally:
        async with sessionmaker() as session:
            await session.execute(text("DELETE FROM gene_panels WHERE name = :name"), {"name": panel_name})
            await session.execute(text("DELETE FROM users WHERE email = ANY(:emails)"), {"emails": list(emails.values())})
            await session.commit()
    return out


@pytest.fixture(scope="module")
def ran() -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.app.services import account_notifications
    from backend.tests.e2e import _harness

    run = uuid4().hex[:12]
    _FakeSmtp.sent = []
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(settings, "smtp_host", "mail.e2e.invalid")
        patch.setattr(account_notifications.smtplib, "SMTP", _FakeSmtp)
        return _harness.run_async(lambda: _exercise(run))


def test_a_sign_up_waits_for_approval_and_every_active_administrator_is_told(ran: dict[str, Any]) -> None:
    emails = ran["emails"]
    assert ran["signup"] == 202
    assert ran["signup_account"] == {"is_active": False, "role": "viewer", "activated_at": None, "archived_at": None}
    assert ran["signup_created_at"]
    ((to, body),) = ran["signup_mail"]
    recipients = to.split(", ")
    assert {emails["admin"], ran["admin_email"]} <= set(recipients)
    assert emails["old_admin"] not in recipients
    assert f"E-mail address: {emails['signup']}\n" in body
    assert "Name: New Colleague\n" in body
    assert "Made by: a sign-up with a password\n" in body


def test_the_users_page_reads_who_is_told_and_a_test_mail_reaches_them(ran: dict[str, Any]) -> None:
    status, mail = ran["mail_status"]
    assert status == 200 and mail["enabled"] is True
    assert ran["emails"]["admin"] in mail["recipients"]
    assert ran["emails"]["old_admin"] not in mail["recipients"]
    test_status, test_body, test_to = ran["test_mail"]
    assert (test_status, test_body["recipients"]) == (200, mail["recipients"])
    assert test_to == ", ".join(mail["recipients"])


def test_an_account_activated_once_is_archived_and_never_deleted(ran: dict[str, Any]) -> None:
    status, active, activated_at = ran["activated"]
    assert (status, active) == (200, True) and activated_at
    assert ran["delete_activated"] == {
        "detail": "Only an account that was never activated can be deleted. Archive this one instead."
    }
    assert ran["archived"] == (200, False, True)
    assert ran["archived_login"] == (403, {"detail": "User not active"})
    assert ran["activate_archived"] == (
        409,
        {"detail": "This account is archived: restore it from the archive before activating it."},
    )
    # Restored, it is inactive and keeps the time it was first activated.
    assert ran["restored"] == (200, False, None, True)
    assert ran["delete_restored"] == 409
    assert ran["rows"][ran["emails"]["approved"]] == (False, True, False)


def test_an_account_waiting_for_approval_is_deleted_and_the_audit_names_it(ran: dict[str, Any]) -> None:
    emails = ran["emails"]
    assert ran["deleted"] == 204
    assert ran["listed_after"] is False
    assert ran["deleted_again"] == 404
    assert emails["signup"] not in ran["rows"]
    ((status, user_email, change),) = ran["audit_delete"][:1]
    assert (status, user_email) == (204, ran["admin_email"])
    assert change["deleted"] is True
    assert (change["user_id"], change["email"], change["name"]) == (ran["ids"]["signup"], emails["signup"], "New Colleague")
    assert change["sign_in"] == "password"
    assert [row[0] for row in ran["audit_delete"]] == [204, 404]


def test_an_account_a_record_names_is_kept(ran: dict[str, Any]) -> None:
    assert ran["delete_named"] == (
        409,
        {"detail": "CoGA keeps records that name this account, so it cannot be deleted. Archive it instead."},
    )
    assert ran["rows"][ran["emails"]["panel"]] == (False, False, False)


def test_the_administrator_neither_archives_nor_deletes_their_own_account(ran: dict[str, Any]) -> None:
    assert (ran["archive_own"], ran["delete_own"]) == (403, 403)


def test_the_database_refuses_an_archived_account_that_is_active(ran: dict[str, Any]) -> None:
    assert ran["archived_active_stored"] is False
