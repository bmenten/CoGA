"""Out-of-band Postgres schema migration + admin bootstrap.

This is the **owner-privileged** half of the DB privilege separation (P1-3 / P1-4). Schema
DDL — ``CREATE``/``ALTER TABLE``, trigger management, ``GRANT`` — must run as the table
**owner**; the restricted runtime role ``coga_app`` deliberately cannot. Keeping the
migration out of the API's request-serving process lets the app boot as ``coga_app``
(``POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP=false``) without crash-looping on DDL it is not
allowed to run — the coordinated flip described in ``docs/db-runtime-role-runbook.md``.

Run it as a deploy step, before starting the app, using the **owner** DSN::

    python -m backend.app.db_migrate

When ``POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP`` is left at its default (``true``) the app
still self-migrates on startup as the owner — the current single-DSN deployment — and this
module is simply unused. Both paths call the same ``init_postgres_schema`` /
``init_postgres_admin_user`` helpers, so there is one source of truth for the schema apply.

With ``POSTGRES_APP_PASSWORD`` set, the migration also lets ``coga_app`` log in with that
password (``enable_app_role_login``), so the switch needs no hand-run SQL. On Google Cloud the
database has only a private address, and this job is what reaches it as the owner.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import os
from datetime import datetime, timezone

from sqlalchemy import text

from .core.config import settings
from .core.postgres import (
    close_postgres_engine,
    get_postgres_engine,
    init_postgres_schema,
    wait_for_postgres,
)
from .dependencies import get_password_hash


async def init_postgres_admin_user() -> None:
    """Ensure a metadata admin user exists in Postgres.

    Idempotent: returns early if the admin username is already present. Safe to run either
    as the owner (migration path) or as ``coga_app`` (which retains ``INSERT`` on ``users``).
    """

    engine = get_postgres_engine()
    async with engine.begin() as conn:
        existing = await conn.execute(
            text("SELECT id FROM users WHERE username = :username"),
            {"username": settings.admin_username},
        )
        if existing.scalar_one_or_none() is not None:
            return

        await conn.execute(
            text(
                """
                INSERT INTO users (
                    username,
                    hashed_password,
                    role,
                    email,
                    metadata,
                    created_at
                )
                VALUES (
                    :username,
                    :hashed_password,
                    'admin',
                    :email,
                    '{}'::jsonb,
                    :created_at
                )
                """
            ),
            {
                "username": settings.admin_username,
                "hashed_password": get_password_hash(settings.admin_password),
                "email": settings.admin_email,
                "created_at": datetime.now(timezone.utc),
            },
        )


# Postgres' default iteration count for SCRAM-SHA-256 verifiers (``scram_iterations``).
_SCRAM_ITERATIONS = 4096


def scram_sha256_verifier(
    password: str, *, salt: bytes | None = None, iterations: int = _SCRAM_ITERATIONS
) -> str:
    """Return the SCRAM-SHA-256 verifier Postgres stores for ``password`` (RFC 5802, 7677).

    Handing Postgres the verifier rather than the password, as ``psql``'s ``\\password``
    does, keeps the plaintext off the server: out of its logs, and out of the error context
    a failed ``ALTER ROLE`` would log. Postgres runs a password through SASLprep before
    hashing it. That is the identity for printable ASCII, so any other password is refused
    rather than stored under a verifier that would never match it.
    """

    if not password or not all(" " <= character <= "~" for character in password):
        raise ValueError("The coga_app password must be non-empty printable ASCII.")
    salt = os.urandom(16) if salt is None else salt
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("ascii"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()

    def b64(raw: bytes) -> str:
        return base64.b64encode(raw).decode("ascii")

    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


async def enable_app_role_login(password: str) -> None:
    """Let the restricted runtime role ``coga_app`` log in with ``password``.

    Owner-only: ``05_grants.sql`` creates the role without a login, and only its creator (or a
    superuser) may change that. Running it again with another password rotates it.
    ``ALTER ROLE`` takes no bind parameters, so the verifier travels as a parameter of
    ``set_config`` and Postgres quotes it itself; nothing is spliced into SQL text here.
    """

    verifier = scram_sha256_verifier(password)
    engine = get_postgres_engine()
    async with engine.begin() as conn:
        # Transaction-local, so the setting is gone when this block commits.
        await conn.execute(
            text("SELECT set_config('coga.app_role_verifier', :verifier, true)"),
            {"verifier": verifier},
        )
        await conn.exec_driver_sql(
            """
            DO $$
            BEGIN
                EXECUTE 'ALTER ROLE coga_app WITH LOGIN PASSWORD '
                    || quote_literal(current_setting('coga.app_role_verifier'));
            END
            $$
            """
        )


async def run_schema_migrations() -> None:
    """Apply the Postgres schema and seed the admin user, then release the engine.

    With ``POSTGRES_APP_PASSWORD`` set it also enables ``coga_app``'s login with it.

    This is the owner-privileged migration step invoked out-of-band by the deploy pipeline.
    ClickHouse schema init is intentionally left to app startup: ClickHouse connects with its
    own admin credentials (there is no ``coga_app`` equivalent there), so it is not part of
    the Postgres privilege-separation flip.
    """

    try:
        await wait_for_postgres()
        await init_postgres_schema()
        await init_postgres_admin_user()
        if settings.postgres_app_password:
            await enable_app_role_login(settings.postgres_app_password)
    finally:
        await close_postgres_engine()


def main() -> None:
    asyncio.run(run_schema_migrations())


if __name__ == "__main__":
    main()
