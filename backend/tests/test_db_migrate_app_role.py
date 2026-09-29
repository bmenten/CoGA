"""The migration enables the restricted role's login without the password reaching SQL.

``python -m app.db_migrate`` runs as the table owner. With ``POSTGRES_APP_PASSWORD`` set it
also lets ``coga_app`` log in (docs/db-runtime-role-runbook.md), so the switch to the
restricted role needs no hand-run SQL against a database that, on Google Cloud, has only a
private address. The password is turned into a SCRAM-SHA-256 verifier first, as ``psql``'s
``\\password`` does, so the plaintext never reaches the server or its logs.

The real login is proven by ``integration/test_app_boots_as_restricted_role.py``.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac

import pytest

from app import db_migrate
from app.db_migrate import enable_app_role_login, scram_sha256_verifier


def _decode(verifier: str) -> tuple[int, bytes, bytes, bytes]:
    scheme, rest = verifier.split("$", 1)
    assert scheme == "SCRAM-SHA-256"
    head, tail = rest.split("$", 1)
    iterations, salt = head.split(":", 1)
    stored, server = tail.split(":", 1)
    return int(iterations), base64.b64decode(salt), base64.b64decode(stored), base64.b64decode(server)


def test_the_verifier_reproduces_the_rfc_7677_exchange() -> None:
    # RFC 7677 section 3: user "user", password "pencil". A verifier is right when the
    # published client proof opens with its stored half and it signs the published reply.
    nonce = "rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0"
    salt_b64 = "W22ZaJ0SNY7soEsUEjb6gQ=="
    auth_message = (
        f"n=user,r=rOprNGfwEbeRWgbNEkqO,r={nonce},s={salt_b64},i=4096,c=biws,r={nonce}"
    ).encode()
    published_proof = base64.b64decode("dHzbZapWIk4jUhN+Ute9ytag9zjfMHgsqmmiz7AndVQ=")
    published_reply = "6rriTRBi23WpRR/wtup+mMhUZUn/dB5nLTJRsjl95G4="

    verifier = scram_sha256_verifier("pencil", salt=base64.b64decode(salt_b64))
    iterations, salt, stored, server = _decode(verifier)

    assert (iterations, salt) == (4096, base64.b64decode(salt_b64))
    client_signature = hmac.new(stored, auth_message, hashlib.sha256).digest()
    recovered = bytes(a ^ b for a, b in zip(published_proof, client_signature))
    assert hashlib.sha256(recovered).digest() == stored
    reply = hmac.new(server, auth_message, hashlib.sha256).digest()
    assert base64.b64encode(reply).decode() == published_reply


def test_each_verifier_gets_a_fresh_salt() -> None:
    first, second = scram_sha256_verifier("same-password"), scram_sha256_verifier("same-password")
    assert first != second
    assert _decode(first)[1] != _decode(second)[1]
    assert len(_decode(first)[1]) == 16


@pytest.mark.parametrize("password", ["", "café-password", "tab\there", "line\nbreak"])
def test_passwords_postgres_would_normalise_are_refused(password: str) -> None:
    # SASLprep is the identity only for printable ASCII; anything else could be stored
    # under a verifier that never matches what the API later sends.
    with pytest.raises(ValueError, match="printable ASCII"):
        scram_sha256_verifier(password)


class _RecordingConnection:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict | None]] = []

    async def execute(self, statement, parameters=None):
        self.statements.append((str(statement), parameters))

    async def exec_driver_sql(self, statement, parameters=None):
        self.statements.append((statement, parameters))


class _RecordingEngine:
    def __init__(self) -> None:
        self.connection = _RecordingConnection()

    def begin(self):
        engine = self

        class _Transaction:
            async def __aenter__(self):
                return engine.connection

            async def __aexit__(self, *exc):
                return False

        return _Transaction()


def test_the_password_reaches_postgres_only_as_a_bound_verifier(monkeypatch) -> None:
    engine = _RecordingEngine()
    monkeypatch.setattr(db_migrate, "get_postgres_engine", lambda: engine)
    password = "coga-app-unit-not-a-real-secret"

    asyncio.run(enable_app_role_login(password))

    statements = engine.connection.statements
    assert len(statements) == 2
    (set_sql, set_params), (alter_sql, alter_params) = statements
    # The verifier is a bind parameter of set_config ...
    assert "set_config('coga.app_role_verifier', :verifier, true)" in set_sql
    assert set_params is not None
    assert set_params["verifier"].startswith("SCRAM-SHA-256$4096:")
    # ... which Postgres quotes into ALTER ROLE itself; the SQL text is fixed.
    assert "ALTER ROLE coga_app WITH LOGIN PASSWORD" in alter_sql
    assert "quote_literal(current_setting('coga.app_role_verifier'))" in alter_sql
    assert alter_params is None
    # The plaintext is nowhere: not in either statement, not in any parameter.
    for sql, params in statements:
        assert password not in sql
        assert password not in repr(params)


def _stub_migration(monkeypatch) -> list[str]:
    calls: list[str] = []

    async def record(name: str, *args) -> None:
        calls.append(name if not args else f"{name}:{args[0]}")

    monkeypatch.setattr(db_migrate, "wait_for_postgres", lambda: record("wait"))
    monkeypatch.setattr(db_migrate, "init_postgres_schema", lambda: record("schema"))
    monkeypatch.setattr(db_migrate, "init_postgres_admin_user", lambda: record("admin"))
    monkeypatch.setattr(db_migrate, "enable_app_role_login", lambda pw: record("login", pw))
    monkeypatch.setattr(db_migrate, "close_postgres_engine", lambda: record("close"))
    return calls


def test_the_migration_leaves_the_role_alone_without_a_password(monkeypatch) -> None:
    calls = _stub_migration(monkeypatch)
    monkeypatch.setattr(db_migrate.settings, "postgres_app_password", "")

    asyncio.run(db_migrate.run_schema_migrations())

    assert calls == ["wait", "schema", "admin", "close"]


def test_the_migration_enables_the_login_after_the_schema(monkeypatch) -> None:
    calls = _stub_migration(monkeypatch)
    monkeypatch.setattr(db_migrate.settings, "postgres_app_password", "pw-from-secret-manager")

    asyncio.run(db_migrate.run_schema_migrations())

    # After the schema (which creates the role) and before the engine is released.
    assert calls == ["wait", "schema", "admin", "login:pw-from-secret-manager", "close"]
