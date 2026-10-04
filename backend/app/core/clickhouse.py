"""ClickHouse connection and schema helpers for CoGA variant storage."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
from pathlib import Path
import re
import tempfile
from typing import Any

import clickhouse_connect
from clickhouse_connect.driver.exceptions import ClickHouseError, StreamClosedError, StreamFailureError

from .config import settings

logger = logging.getLogger(__name__)

# Characters not allowed in a ClickHouse dataset key (the assembly-name prefix of
# every variant table path, e.g. "GRCh38/SNV_INDEL/entries").
_CLICKHOUSE_DATASET_DISALLOWED = re.compile(r"[^A-Za-z0-9._-]")


def clickhouse_dataset_key(assembly_name: str) -> str:
    """Map an assembly name to a ClickHouse-safe dataset key.

    Identity for names that are already valid identifiers (e.g. ``GRCh38``), so
    existing tables stay addressable with no migration; every other character is
    replaced with ``_`` so assemblies like ``T2T CHM13v2.0`` can be ingested and
    queried automatically. Every service that builds variant table names MUST
    derive the key through this one function — ingestion and the read paths have
    to agree on the prefix or the data becomes invisible. The output always
    matches ``[A-Za-z0-9._-]+``, so it is safe to interpolate into a table path.
    """
    key = _CLICKHOUSE_DATASET_DISALLOWED.sub("_", (assembly_name or "").strip())
    if not key:
        raise ValueError("Assembly name is empty")
    return key


_async_client: Any | None = None
_client_lock: asyncio.Lock | None = None
_INSERT_QUERY_PATTERN = re.compile(
    # `table` is anchored to start with a non-whitespace char and end on a non-`(`,
    # non-whitespace char so the following `\s*\(` boundary is unambiguous. The old
    # `.+?` overlapped the surrounding `\s+`/`\s*`, giving cubic backtracking on a long
    # `INSERT INTO ` + whitespace/near-miss run (py/polynomial-redos).
    r"^\s*INSERT\s+INTO\s+(?P<table>\S(?:[^(]*[^(\s])?)\s*\((?P<columns>.*?)\)\s*VALUES\s*$",
    re.IGNORECASE | re.DOTALL,
)
_QUALIFIED_TABLE_PATTERN = re.compile(
    r"^(?P<database>[A-Za-z_][A-Za-z0-9_]*)\.`(?P<table>[^`]+)`$",
)
_INSERT_RETRY_ERROR_MARKERS = (
    "can not write request body",
    "cannot write request body",
    "broken pipe",
    "connection aborted",
    "connection reset",
    "remote end closed",
    "server disconnected",
)
# Transient errors where the shared HTTP client is in a bad state (a dropped
# socket, or a session that another concurrent query left locked). Resetting the
# client and retrying once clears these without surfacing a 500 to the user.
_QUERY_RETRY_ERROR_MARKERS = (
    "broken pipe",
    "connection aborted",
    "connection reset",
    "remote end closed",
    "server disconnected",
    "connection refused",
    "session is locked",
    "session_is_locked",
    "timed out",
    "read timeout",
)


def _clickhouse_query_settings() -> dict[str, Any]:
    """Per-query guardrails so broad variant filters degrade gracefully.

    Allowing large GROUP BY / sort / JOIN state to spill to disk keeps a heavy
    query from being killed for memory, and ``max_execution_time`` bounds its
    runtime so one query cannot hang the request worker indefinitely.
    """
    query_settings: dict[str, Any] = {
        "max_execution_time": settings.clickhouse_max_execution_time,
        # Headroom for large gene-panel filters (Mendeliome) inlined into the query.
        "max_query_size": settings.clickhouse_max_query_size,
    }
    spill_bytes = settings.clickhouse_external_spill_bytes
    if spill_bytes > 0:
        query_settings["max_bytes_before_external_group_by"] = spill_bytes
        query_settings["max_bytes_before_external_sort"] = spill_bytes
        query_settings["join_algorithm"] = "auto"
    if settings.clickhouse_max_memory_usage > 0:
        query_settings["max_memory_usage"] = settings.clickhouse_max_memory_usage
    return query_settings


# Resolved-once path to the CA cert written from inline PEM (clickhouse-connect /
# urllib3 want a file path, not PEM content).
_ca_cert_path: str | None = None


def _resolve_clickhouse_ca_cert() -> str | None:
    """Return a filesystem path to the CA cert for server verification, or None.

    ``CLICKHOUSE_CA_CERT`` may be a path (used as-is) or inline PEM content (written
    once to a temp file, cached for the process)."""
    global _ca_cert_path
    raw = settings.clickhouse_ca_cert
    if not raw or not raw.strip():
        return None
    value = raw.strip()
    if "BEGIN CERTIFICATE" not in value:
        return value  # already a path
    if _ca_cert_path is None:
        handle = tempfile.NamedTemporaryFile(
            prefix="coga-clickhouse-ca-", suffix=".crt", delete=False, mode="w"
        )
        handle.write(value + "\n")
        handle.close()
        _ca_cert_path = handle.name
    return _ca_cert_path


async def _create_clickhouse_client() -> Any:
    tls_kwargs: dict[str, Any] = {}
    if settings.clickhouse_secure:
        ca_cert = _resolve_clickhouse_ca_cert()
        if ca_cert:
            tls_kwargs["ca_cert"] = ca_cert
        if settings.clickhouse_server_host_name:
            tls_kwargs["server_host_name"] = settings.clickhouse_server_host_name
    return await clickhouse_connect.get_async_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_http_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database="default",
        # TLS (TF-13 S-2): https/secure when CLICKHOUSE_SECURE is set (use port 8443);
        # plain http otherwise. verify checks the server cert when secure; ca_cert +
        # server_host_name let it verify a private CA when connecting by IP.
        interface="https" if settings.clickhouse_secure else "http",
        secure=settings.clickhouse_secure,
        verify=settings.clickhouse_verify,
        **tls_kwargs,
        # A single client is shared across all requests. Auto-generated session
        # ids make ClickHouse serialize the session and reject concurrent queries
        # with SESSION_IS_LOCKED, which surfaces as intermittent 500s under load
        # (worse for slow/complex queries that hold the session longer). Disabling
        # the session lets concurrent requests run independently over HTTP.
        autogenerate_session_id=False,
        send_receive_timeout=settings.clickhouse_send_receive_timeout,
        settings=_clickhouse_query_settings(),
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
    # CHECK TABLE returns rows (one per part with check_query_single_value_result = 0),
    # but clickhouse-connect runs CHECK as a command even through query(): the rows come
    # back as ONE row of tab-split fields. clickhouse_variant_storage._check_table reads
    # that shape; do not read a CHECK result row by row.
    return first_token in {"SELECT", "SHOW", "DESCRIBE", "DESC", "EXISTS", "WITH", "CHECK"}


def _quote_identifier(name: str) -> str:
    return "`" + name.replace("\\", "\\\\").replace("`", "\\`") + "`"


def _driver_table_name(table: str, database: str | None) -> str:
    """The table as clickhouse-connect's ``insert`` must get it: quoted and qualified.

    The driver takes a name holding a dot for an already qualified ``database.table`` and
    sends it unquoted, ignoring ``database``. The table of an assembly whose dataset key
    has a dot (``T2T_CHM13v2.0/SNV_INDEL/entries``) then reached ClickHouse as broken SQL,
    and no insert into it worked. A quoted, qualified name passes through as it is."""
    if database is None:
        return table
    return f"{_quote_identifier(database)}.{_quote_identifier(table)}"


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
    table, database, columns = _parse_insert_query(query)
    for attempt in range(2):
        client = await get_clickhouse_client()
        try:
            return await client.insert(
                table=_driver_table_name(table, database),
                database=None,
                data=data,
                column_names=columns,
            )
        except Exception as exc:
            if attempt == 0 and _is_retryable_insert_error(exc):
                await reset_clickhouse_client(client)
                await asyncio.sleep(0.25)
                continue
            raise
    return None


def _is_retryable_insert_error(exc: Exception) -> bool:
    message = str(exc).lower()
    if isinstance(exc, (StreamClosedError, StreamFailureError)):
        return True
    if isinstance(exc, ClickHouseError):
        return any(marker in message for marker in _INSERT_RETRY_ERROR_MARKERS)
    return any(marker in message for marker in _INSERT_RETRY_ERROR_MARKERS)


async def reset_clickhouse_client(client: Any | None = None) -> None:
    global _async_client
    async with _get_client_lock():
        target = _async_client
        if client is None or target is client:
            _async_client = None
        else:
            target = client
    if target is not None:
        with suppress(Exception):
            await target.close()


def _is_retryable_query_error(exc: Exception) -> bool:
    message = str(exc).lower()
    if isinstance(exc, (StreamClosedError, StreamFailureError)):
        return True
    return any(marker in message for marker in _QUERY_RETRY_ERROR_MARKERS)


async def execute_clickhouse(query: str, parameters: Any = None) -> Any:
    if isinstance(parameters, list) and _INSERT_QUERY_PATTERN.match(query):
        return await insert_clickhouse(query, parameters)
    returns_rows = _query_returns_rows(query)
    for attempt in range(2):
        client = await get_clickhouse_client()
        try:
            if returns_rows:
                result = await client.query(query, parameters=parameters or {})
                return list(result.result_rows)
            return await client.command(query, parameters=parameters or {})
        except Exception as exc:
            if attempt == 0 and _is_retryable_query_error(exc):
                logger.warning(
                    "ClickHouse query failed with a transient error; resetting "
                    "client and retrying once: %s",
                    exc,
                )
                await reset_clickhouse_client(client)
                await asyncio.sleep(0.25)
                continue
            raise
    return None


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
