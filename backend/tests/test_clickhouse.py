from __future__ import annotations

import threading

import pytest

from backend.app.core import clickhouse


class _RecordingClient:
    def __init__(self, label: str) -> None:
        self.label = label
        self.calls: list[tuple[str, object]] = []

    def execute(self, query: str, parameters=None):
        self.calls.append((query, parameters))
        return [(self.label, query, parameters)]

    def disconnect(self) -> None:
        return None


def test_get_clickhouse_client_is_thread_local(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[_RecordingClient] = []

    def fake_create_clickhouse_client():
        client = _RecordingClient(f"client-{len(created) + 1}")
        created.append(client)
        return client

    monkeypatch.setattr(clickhouse, "_thread_local", threading.local())
    monkeypatch.setattr(clickhouse, "_client_registry", set())
    monkeypatch.setattr(clickhouse, "_create_clickhouse_client", fake_create_clickhouse_client)

    main_client = clickhouse.get_clickhouse_client()
    assert clickhouse.get_clickhouse_client() is main_client

    worker_client: _RecordingClient | None = None

    def worker() -> None:
        nonlocal worker_client
        worker_client = clickhouse.get_clickhouse_client()

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert worker_client is not None
    assert worker_client is not main_client
    assert created == [main_client, worker_client]


@pytest.mark.asyncio
async def test_execute_clickhouse_uses_thread_local_client(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _RecordingClient("client-1")

    monkeypatch.setattr(clickhouse, "_thread_local", threading.local())
    monkeypatch.setattr(clickhouse, "_client_registry", set())
    monkeypatch.setattr(clickhouse, "_create_clickhouse_client", lambda: client)

    rows = await clickhouse.execute_clickhouse("SELECT 1", {"side": "left"})

    assert rows == [("client-1", "SELECT 1", {"side": "left"})]
    assert client.calls == [("SELECT 1", {"side": "left"})]
