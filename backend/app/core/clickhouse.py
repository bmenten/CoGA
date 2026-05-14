"""ClickHouse connection and schema helpers for CoGA variant storage."""

from __future__ import annotations

import asyncio
from pathlib import Path
import re
from typing import Any

import clickhouse_connect

from .config import settings

_async_client: Any | None = None
_client_lock: asyncio.Lock | None = None
_INSERT_QUERY_PATTERN = re.compile(
    r"^\s*INSERT\s+INTO\s+(?P<table>.+?)\s*\((?P<columns>.*?)\)\s*VALUES\s*$",
    re.IGNORECASE | re.DOTALL,
)
_QUALIFIED_TABLE_PATTERN = re.compile(
    r"^(?P<database>[A-Za-z_][A-Za-z0-9_]*)\.`(?P<table>[^`]+)`$",
)


async def _create_clickhouse_client() -> Any:
    return await clickhouse_connect.get_async_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_http_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database="default",
        interface="http",
    )


def _get_client_lock() -> asyncio.Lock:
    global _client_lock
    if _client_lock is None:
        _client_lock = asyncio.Lock()
    return _client_lock


async def get_clickhouse_client() -> Any:
    global _async_client
    if _async_client is None:
        async with _get_client_lock():
            if _async_client is None:
                _async_client = await _create_clickhouse_client()
    return _async_client


def _query_returns_rows(query: str) -> bool:
    first_token = query.strip().split(None, 1)[0].upper() if query.strip() else ""
    return first_token in {"SELECT", "SHOW", "DESCRIBE", "DESC", "EXISTS", "WITH"}


def _parse_insert_query(query: str) -> tuple[str, str | None, list[str]]:
    match = _INSERT_QUERY_PATTERN.match(query)
    if match is None:
        raise ValueError("ClickHouse insert queries must be written as INSERT INTO <table> (<columns>) VALUES")
    table_expr = " ".join(match.group("table").strip().split())
    database: str | None = None
    table = table_expr
    qualified_match = _QUALIFIED_TABLE_PATTERN.match(table_expr)
    if qualified_match is not None:
        database = qualified_match.group("database")
        table = qualified_match.group("table")
    columns = [
        column.strip().strip("`")
        for column in match.group("columns").split(",")
        if column.strip()
    ]
    return table, database, columns


async def insert_clickhouse(
    query: str,
    data: list[tuple[Any, ...]],
) -> Any:
    if not data:
        return None
    client = await get_clickhouse_client()
    table, database, columns = _parse_insert_query(query)
    return await client.insert(
        table=table,
        database=database,
        data=data,
        column_names=columns,
    )


async def execute_clickhouse(query: str, parameters: Any = None) -> Any:
    if isinstance(parameters, list) and _INSERT_QUERY_PATTERN.match(query):
        return await insert_clickhouse(query, parameters)
    client = await get_clickhouse_client()
    if _query_returns_rows(query):
        result = await client.query(query, parameters=parameters or {})
        return list(result.result_rows)
    return await client.command(query, parameters=parameters or {})


async def close_clickhouse_client() -> None:
    global _async_client
    if _async_client is None:
        return
    client = _async_client
    _async_client = None
    await client.close()


async def wait_for_clickhouse(max_tries: int = 20, delay: float = 1.0) -> None:
    for attempt in range(max_tries):
        try:
            await execute_clickhouse("SELECT 1")
            return
        except Exception:
            if attempt == max_tries - 1:
                raise
            await asyncio.sleep(delay)


def _schema_files() -> list[Path]:
    schema_dir = Path(__file__).resolve().parents[2] / "db" / "schema" / "clickhouse"
    return sorted(schema_dir.glob("*.sql"))


def _split_sql_script(contents: str) -> list[str]:
    return [statement.strip() for statement in contents.split(";") if statement.strip()]


def _render_sql(contents: str) -> str:
    rendered = contents.replace(
        "CREATE DATABASE IF NOT EXISTS coga",
        f"CREATE DATABASE IF NOT EXISTS {settings.clickhouse_database}",
    )
    return rendered.replace("coga.`", f"{settings.clickhouse_database}.`")


async def init_clickhouse_schema() -> None:
    for path in _schema_files():
        rendered = _render_sql(path.read_text())
        for statement in _split_sql_script(rendered):
            await execute_clickhouse(statement)
