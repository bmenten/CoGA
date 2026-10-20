"""Postgres connection and schema helpers for CoGA metadata storage."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None
# Cloud SQL connector instance (created lazily inside the async creator, bound to the
# running loop). None unless POSTGRES_USE_CLOUD_SQL_CONNECTOR is set.
_connector = None


async def _cloud_sql_connect():
    """asyncpg connection via the Cloud SQL Python Connector (mTLS + verify-full).

    The connector is created lazily on first use so it binds to the running event
    loop; it is reused across connections and closed in ``close_postgres_engine``."""
    global _connector
    if _connector is None:
        from google.cloud.sql.connector import create_async_connector

        _connector = await create_async_connector()
    return await _connector.connect_async(
        settings.postgres_instance_connection_name,
        "asyncpg",
        user=settings.postgres_user,
        password=settings.postgres_password,
        db=settings.postgres_db,
        ip_type=settings.cloud_sql_ip_type,
    )


def get_postgres_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        if settings.postgres_use_cloud_sql_connector:
            # The connector supplies the asyncpg connection (and the TLS identity),
            # so the URL carries only the dialect/driver, no host or sslmode.
            _engine = create_async_engine(
                "postgresql+asyncpg://",
                future=True,
                async_creator=_cloud_sql_connect,
            )
        else:
            _engine = create_async_engine(
                settings.postgres_dsn,
                future=True,
                connect_args=settings.postgres_connect_args,
            )
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_postgres_sessionmaker() -> async_sessionmaker[AsyncSession]:
    get_postgres_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def get_postgres_session() -> AsyncIterator[AsyncSession]:
    session_factory = get_postgres_sessionmaker()
    async with session_factory() as session:
        yield session


async def wait_for_postgres(max_tries: int = 20, delay: float = 1.0) -> None:
    engine = get_postgres_engine()
    for attempt in range(max_tries):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return
        except Exception:
            if attempt == max_tries - 1:
                raise
            await asyncio.sleep(delay)


def _schema_files() -> list[Path]:
    schema_dir = Path(__file__).resolve().parents[2] / "db" / "schema" / "postgres"
    return sorted(schema_dir.glob("*.sql"))


# A PostgreSQL dollar-quote opener/closer: ``$$`` or ``$tag$``.
_DOLLAR_QUOTE_RE = re.compile(r"\$(?:[A-Za-z_]\w*)?\$")


def _split_sql_script(contents: str) -> list[str]:
    """Split a schema file into statements on ``;``.

    Honours ``--`` line comments and ``$$ ... $$`` / ``$tag$ ... $tag$``
    dollar-quoted bodies, so a semicolon inside a PL/pgSQL function body (e.g. the
    append-only audit trigger) does not truncate the statement. (The schema files
    have no ``--`` inside string/dollar literals, so dropping line comments first
    is safe.)
    """
    text = "\n".join(line.split("--", 1)[0] for line in contents.splitlines())
    statements: list[str] = []
    buf: list[str] = []
    dollar_tag: str | None = None
    i, n = 0, len(text)
    while i < n:
        if dollar_tag is None:
            match = _DOLLAR_QUOTE_RE.match(text, i)
            if match:  # entering a dollar-quoted body
                dollar_tag = match.group(0)
                buf.append(dollar_tag)
                i += len(dollar_tag)
                continue
            if text[i] == ";":
                statement = "".join(buf).strip()
                if statement:
                    statements.append(statement)
                buf = []
                i += 1
                continue
        elif text.startswith(dollar_tag, i):  # closing the dollar-quoted body
            buf.append(dollar_tag)
            i += len(dollar_tag)
            dollar_tag = None
            continue
        buf.append(text[i])
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


async def init_postgres_schema() -> None:
    """Bring the database to this version's schema, as the table owner, in one transaction.

    Until the release candidate this re-applies the baseline files, then runs the Alembic
    revisions the ledger lacks (core/postgres_migrations.py).
    """

    # Imported here: postgres_migrations reads the baseline helpers above from this module.
    from .postgres_migrations import apply_postgres_schema

    engine = get_postgres_engine()
    async with engine.begin() as conn:
        await apply_postgres_schema(conn)


async def verify_postgres_schema_revision() -> str:
    """Refuse to start unless the database is at this version's newest schema revision.

    For a start that does not migrate (``POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP=false``): the
    migration step ran before the rollout, as the owner, and this only reads the ledger, which
    the restricted role may (``check_schema_revision`` in core/postgres_migrations.py).
    """

    from .postgres_migrations import check_schema_revision

    engine = get_postgres_engine()
    async with engine.connect() as conn:
        return await conn.run_sync(check_schema_revision)


async def close_postgres_engine() -> None:
    global _engine, _sessionmaker, _connector
    if _engine is not None:
        await _engine.dispose()
    if _connector is not None:
        await _connector.close_async()
    _engine = None
    _sessionmaker = None
    _connector = None
