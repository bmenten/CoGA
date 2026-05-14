from __future__ import annotations

import pytest

from backend.app.core import clickhouse


class _QueryResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.result_rows = rows


class _RecordingAsyncClient:
    def __init__(self, label: str = "client-1") -> None:
        self.label = label
        self.closed = False
        self.queries: list[tuple[str, object]] = []
        self.commands: list[tuple[str, object]] = []
        self.inserts: list[tuple[str, str | None, list[tuple[object, ...]], list[str]]] = []

    async def query(self, query: str, parameters=None):
        self.queries.append((query, parameters))
        return _QueryResult([(self.label, query, parameters)])

    async def command(self, query: str, parameters=None):
        self.commands.append((query, parameters))
        return {"command": query, "parameters": parameters}

    async def insert(self, *, table: str, database: str | None, data, column_names: list[str]):
        self.inserts.append((table, database, list(data), column_names))
        return {"inserted": len(data)}

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_get_clickhouse_client_reuses_async_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[_RecordingAsyncClient] = []

    async def fake_create_clickhouse_client():
        client = _RecordingAsyncClient(f"client-{len(created) + 1}")
        created.append(client)
        return client

    monkeypatch.setattr(clickhouse, "_async_client", None)
    monkeypatch.setattr(clickhouse, "_client_lock", None)
    monkeypatch.setattr(clickhouse, "_create_clickhouse_client", fake_create_clickhouse_client)

    first_client = await clickhouse.get_clickhouse_client()
    second_client = await clickhouse.get_clickhouse_client()

    assert first_client is second_client
    assert created == [first_client]


@pytest.mark.asyncio
async def test_execute_clickhouse_routes_select_to_query(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _RecordingAsyncClient()

    monkeypatch.setattr(clickhouse, "_async_client", client)
    monkeypatch.setattr(clickhouse, "_client_lock", None)

    rows = await clickhouse.execute_clickhouse("SELECT 1", {"side": "left"})

    assert rows == [("client-1", "SELECT 1", {"side": "left"})]
    assert client.queries == [("SELECT 1", {"side": "left"})]
    assert client.commands == []


@pytest.mark.asyncio
async def test_execute_clickhouse_routes_ddl_to_command(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _RecordingAsyncClient()

    monkeypatch.setattr(clickhouse, "_async_client", client)
    monkeypatch.setattr(clickhouse, "_client_lock", None)

    result = await clickhouse.execute_clickhouse("CREATE TABLE example (id UInt64)")

    assert result == {"command": "CREATE TABLE example (id UInt64)", "parameters": {}}
    assert client.commands == [("CREATE TABLE example (id UInt64)", {})]
    assert client.queries == []


@pytest.mark.asyncio
async def test_execute_clickhouse_routes_insert_to_insert_api(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _RecordingAsyncClient()

    monkeypatch.setattr(clickhouse, "_async_client", client)
    monkeypatch.setattr(clickhouse, "_client_lock", None)

    result = await clickhouse.execute_clickhouse(
        "INSERT INTO coga.`GRCh38/SNV_INDEL/entries` (key, variantId, `calls.sampleId`) VALUES",
        [(1, "v1", ["S1"])],
    )

    assert result == {"inserted": 1}
    assert client.inserts == [
        (
            "GRCh38/SNV_INDEL/entries",
            "coga",
            [(1, "v1", ["S1"])],
            ["key", "variantId", "calls.sampleId"],
        )
    ]
    assert client.queries == []
    assert client.commands == []


@pytest.mark.asyncio
async def test_close_clickhouse_client_closes_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _RecordingAsyncClient()

    monkeypatch.setattr(clickhouse, "_async_client", client)

    await clickhouse.close_clickhouse_client()

    assert client.closed is True
    assert clickhouse._async_client is None
