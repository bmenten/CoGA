"""Postgres connection and schema helpers for CoGA metadata storage."""

from __future__ import annotations

import asyncio
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


def get_postgres_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        _engine = create_async_engine(settings.postgres_dsn, future=True)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_postgres_sessionmaker() -> async_sessionmaker[AsyncSession]:
    engine = get_postgres_engine()
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


def _split_sql_script(contents: str) -> list[str]:
    return [statement.strip() for statement in contents.split(";") if statement.strip()]


async def init_postgres_schema() -> None:
    engine = get_postgres_engine()
    async with engine.begin() as conn:
        for path in _schema_files():
            for statement in _split_sql_script(path.read_text()):
                await conn.exec_driver_sql(statement)


async def close_postgres_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
