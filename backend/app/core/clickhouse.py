"""ClickHouse connection and schema helpers for CoGA variant storage."""

from __future__ import annotations

import asyncio
from pathlib import Path
from threading import Lock, local
from typing import Any

from clickhouse_driver import Client

from .config import settings

_client_registry: set[Client] = set()
_client_registry_lock = Lock()
_thread_local = local()


def _create_clickhouse_client() -> Client:
    return Client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        user=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database="default",
    )


def get_clickhouse_client() -> Client:
    client = getattr(_thread_local, "client", None)
    if client is None:
        client = _create_clickhouse_client()
        _thread_local.client = client
        with _client_registry_lock:
            _client_registry.add(client)
    return client


def _execute_clickhouse(query: str, parameters: Any = None) -> Any:
    client = get_clickhouse_client()
    if parameters is None:
        return client.execute(query)
    return client.execute(query, parameters)


async def execute_clickhouse(query: str, parameters: Any = None) -> Any:
    return await asyncio.to_thread(_execute_clickhouse, query, parameters)


def _disconnect_clickhouse_clients() -> None:
    with _client_registry_lock:
        clients = list(_client_registry)
        _client_registry.clear()
    for client in clients:
        client.disconnect()
    if hasattr(_thread_local, "client"):
        delattr(_thread_local, "client")


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


async def close_clickhouse_client() -> None:
    if _client_registry:
        await asyncio.to_thread(_disconnect_clickhouse_clients)
