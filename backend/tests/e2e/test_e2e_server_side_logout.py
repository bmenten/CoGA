"""A logout ends every session of its account on the server (E2E).

A logout does more than make the browser forget its token, which would leave a copy of it, or
the token of the account's session on another device, valid until it expired (two hours).
``POST /api/auth/logout`` records when the account signed out (``users.sessions_ended_at``) and
``get_current_user`` refuses every token issued to the account before then.

On real Postgres, through the app with real password sign-ins:

* two sign-ins of one account (two devices) each sign in; a logout with one of them (204) ends
  both: each is refused (401) at its next request, and the account records when;
* a sign-in after the logout signs in at once, and a later logout ends it too, at a later time;
* a time set ahead of this server's clock (by another instance's) refuses every token issued
  before it, and a logout here never moves it back;
* another account's session goes on;
* a logout with a token that is refused ends nothing (401);
* each logout is in the request audit, under its account.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it. The accounts
are synthetic and removed at the end (the append-only audit events stay, as every e2e test's do).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import random
from typing import Any
from uuid import uuid4

import pytest

from backend.tests.e2e._harness import ensure_postgres_schema

pytestmark = pytest.mark.integration


async def _exercise(run: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.dependencies import get_password_hash
    from backend.app.main import app
    from backend.app.services.metadata_service import end_account_sessions

    password = f"logout-password-{run}"
    emails = {
        "analyst": f"e2e-logout-analyst-{run}@example.com",
        "other": f"e2e-logout-other-{run}@example.com",
    }

    await ensure_postgres_schema()
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        hashed = get_password_hash(password)
        for email in emails.values():
            await session.execute(
                text(
                    "INSERT INTO users (username, hashed_password, role, email, is_active) "
                    "VALUES (:email, :hashed, 'analyst', :email, true)"
                ),
                {"email": email, "hashed": hashed},
            )
        await session.commit()

    async def ended_at() -> datetime | None:
        async with sessionmaker() as session:
            return (
                await session.execute(
                    text("SELECT sessions_ended_at FROM users WHERE email = :email"),
                    {"email": emails["analyst"]},
                )
            ).scalar_one()

    out: dict[str, Any] = {"emails": emails}
    # A source address of its own, so that the sign-in throttle counts this run alone.
    client_address = (f"198.51.100.{random.randint(1, 254)}", 40000)
    transport = ASGITransport(app=app, raise_app_exceptions=False, client=client_address)
    try:
        async with AsyncClient(transport=transport, base_url="http://e2e") as client:

            async def sign_in(email: str) -> str:
                response = await client.post("/api/auth/login", json={"email": email, "password": password})
                assert response.status_code == 200, response.text
                return response.json()["access_token"]

            async def me(token: str) -> int:
                response = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
                return response.status_code

            async def logout(token: str) -> int:
                response = await client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
                return response.status_code

            # --- two devices, one logout ------------------------------------------------------
            laptop, desk = await sign_in(emails["analyst"]), await sign_in(emails["analyst"])
            colleague = await sign_in(emails["other"])
            out["before"] = (await me(laptop), await me(desk), await ended_at())
            out["logout"] = await logout(desk)
            out["first_end"] = await ended_at()
            out["after"] = (await me(laptop), await me(desk), await me(colleague))
            out["logout_with_ended_token"] = await logout(laptop)

            # --- a sign-in after the logout, and a later logout -------------------------------
            again = await sign_in(emails["analyst"])
            out["again"] = await me(again)
            out["second_logout"] = await logout(again)
            out["second_end"] = await ended_at()
            out["again_after"] = await me(again)

            # --- a time ahead of this server's clock (another instance's) stays -----------------
            ahead = datetime.now(timezone.utc) + timedelta(hours=1)
            async with sessionmaker() as session:
                other_id = (
                    await session.execute(
                        text("UPDATE users SET sessions_ended_at = :ahead WHERE email = :email RETURNING id::text"),
                        {"ahead": ahead, "email": emails["other"]},
                    )
                ).scalar_one()
                await session.commit()
            # The colleague's token was issued before that time: refused, as every token
            # issued before the account's last logout is.
            out["colleague_after_ahead"] = await me(colleague)
            # A logout on this server, whose clock is behind that time, does not move it back.
            async with sessionmaker() as session:
                out["ended_returned"] = await end_account_sessions(session, user_id=other_id)
            out["ahead"] = ahead

        async with sessionmaker() as session:
            out["ahead_kept"] = (
                await session.execute(
                    text("SELECT sessions_ended_at FROM users WHERE email = :email"),
                    {"email": emails["other"]},
                )
            ).scalar_one()
            out["audit"] = [
                (row.status_code, row.user_email)
                for row in (
                    await session.execute(
                        text(
                            """
                            SELECT status_code, user_email
                            FROM audit_log_events
                            WHERE method = 'POST' AND path = '/api/auth/logout'
                              AND user_email = ANY(:emails)
                            ORDER BY created_at, id
                            """
                        ),
                        {"emails": list(emails.values())},
                    )
                ).all()
            ]
    finally:
        async with sessionmaker() as session:
            await session.execute(text("DELETE FROM users WHERE email = ANY(:emails)"), {"emails": list(emails.values())})
            await session.commit()
    return out


@pytest.fixture(scope="module")
def ran() -> dict[str, Any]:
    from backend.tests.e2e import _harness

    return _harness.run_async(lambda: _exercise(uuid4().hex[:12]))


def test_a_logout_ends_every_session_of_the_account_and_no_other(ran: dict[str, Any]) -> None:
    assert ran["before"] == (200, 200, None)
    assert ran["logout"] == 204
    assert ran["first_end"] is not None
    # Both devices' tokens are refused; the colleague's session goes on.
    assert ran["after"] == (401, 401, 200)
    # A token the logout ended cannot log out again: it is refused like any other.
    assert ran["logout_with_ended_token"] == 401


def test_a_sign_in_after_the_logout_signs_in_and_a_later_logout_ends_it(ran: dict[str, Any]) -> None:
    assert ran["again"] == 200
    assert ran["second_logout"] == 204
    assert ran["second_end"] > ran["first_end"]
    assert ran["again_after"] == 401


def test_a_logout_time_ahead_of_the_clock_refuses_every_earlier_token_and_stays(ran: dict[str, Any]) -> None:
    assert ran["colleague_after_ahead"] == 401
    assert ran["ended_returned"] == ran["ahead"]
    assert ran["ahead_kept"] == ran["ahead"]


def test_each_logout_is_in_the_request_audit_under_its_account(ran: dict[str, Any]) -> None:
    analyst = ran["emails"]["analyst"]
    # The two that ended sessions, each under the account; the refused one signed no one in,
    # so its row names no account.
    assert ran["audit"] == [(204, analyst), (204, analyst)]
