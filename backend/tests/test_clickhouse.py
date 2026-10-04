from __future__ import annotations

from pathlib import Path

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
            "`coga`.`GRCh38/SNV_INDEL/entries`",
            None,
            [(1, "v1", ["S1"])],
            ["key", "variantId", "calls.sampleId"],
        )
    ]
    assert client.queries == []
    assert client.commands == []


@pytest.mark.asyncio
async def test_an_insert_into_a_dotted_assemblys_table_reaches_the_driver_quoted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The T2T assembly's dataset key keeps its dot (T2T_CHM13v2.0). clickhouse-connect
    # takes a table name holding a dot for "database.table" and sends it unquoted, so the
    # split name made every T2T insert a syntax error (DESCRIBE TABLE T2T_CHM13v2.0/...).
    from clickhouse_connect.driver.binding import _qualified_table

    client = _RecordingAsyncClient()
    monkeypatch.setattr(clickhouse, "_async_client", client)
    monkeypatch.setattr(clickhouse, "_client_lock", None)

    await clickhouse.execute_clickhouse(
        "INSERT INTO coga.`T2T_CHM13v2.0/SNV_INDEL/entries` (key) VALUES",
        [(1,)],
    )

    [(table, database, _data, _columns)] = client.inserts
    assert (table, database) == ("`coga`.`T2T_CHM13v2.0/SNV_INDEL/entries`", None)
    # What the driver itself makes of it: the name, intact.
    assert _qualified_table(table, database) == "`coga`.`T2T_CHM13v2.0/SNV_INDEL/entries`"
    # The split name the driver used to get came out bare.
    assert _qualified_table("T2T_CHM13v2.0/SNV_INDEL/entries", "coga") == "T2T_CHM13v2.0/SNV_INDEL/entries"


def test_an_identifier_is_quoted_with_its_backticks_escaped() -> None:
    assert clickhouse._quote_identifier("GRCh38/SV/entries") == "`GRCh38/SV/entries`"
    assert clickhouse._quote_identifier("odd`name") == "`odd\\`name`"
    assert clickhouse._driver_table_name("t", None) == "t"


@pytest.mark.asyncio
async def test_execute_clickhouse_retries_insert_after_request_body_write_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingInsertClient(_RecordingAsyncClient):
        async def insert(self, *, table: str, database: str | None, data, column_names: list[str]):
            self.inserts.append((table, database, list(data), column_names))
            raise RuntimeError("Network Error: [Errno None] Can not write request body")

    created: list[_RecordingAsyncClient] = []

    async def fake_create_clickhouse_client():
        if not created:
            client = _FailingInsertClient("client-1")
        else:
            client = _RecordingAsyncClient(f"client-{len(created) + 1}")
        created.append(client)
        return client

    monkeypatch.setattr(clickhouse, "_async_client", None)
    monkeypatch.setattr(clickhouse, "_client_lock", None)
    monkeypatch.setattr(clickhouse, "_create_clickhouse_client", fake_create_clickhouse_client)

    result = await clickhouse.execute_clickhouse(
        "INSERT INTO coga.`GRCh38/SNV_INDEL/entries` (key, variantId, `calls.sampleId`) VALUES",
        [(1, "v1", ["S1"])],
    )

    assert result == {"inserted": 1}
    assert len(created) == 2
    assert created[0].closed is True
    assert created[1].inserts == [
        (
            "`coga`.`GRCh38/SNV_INDEL/entries`",
            None,
            [(1, "v1", ["S1"])],
            ["key", "variantId", "calls.sampleId"],
        )
    ]


@pytest.mark.asyncio
async def test_execute_clickhouse_retries_query_after_session_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _SessionLockedClient(_RecordingAsyncClient):
        async def query(self, query: str, parameters=None):
            self.queries.append((query, parameters))
            raise RuntimeError("Session is locked by a concurrent client")

    created: list[_RecordingAsyncClient] = []

    async def fake_create_clickhouse_client():
        if not created:
            client = _SessionLockedClient("client-1")
        else:
            client = _RecordingAsyncClient(f"client-{len(created) + 1}")
        created.append(client)
        return client

    monkeypatch.setattr(clickhouse, "_async_client", None)
    monkeypatch.setattr(clickhouse, "_client_lock", None)
    monkeypatch.setattr(clickhouse, "_create_clickhouse_client", fake_create_clickhouse_client)

    rows = await clickhouse.execute_clickhouse("SELECT 1", {"side": "left"})

    assert rows == [("client-2", "SELECT 1", {"side": "left"})]
    assert len(created) == 2
    assert created[0].closed is True


@pytest.mark.asyncio
async def test_execute_clickhouse_does_not_retry_non_transient_query_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _SyntaxErrorClient(_RecordingAsyncClient):
        async def query(self, query: str, parameters=None):
            self.queries.append((query, parameters))
            raise RuntimeError("Code: 62. DB::Exception: Syntax error")

    created: list[_RecordingAsyncClient] = []

    async def fake_create_clickhouse_client():
        client = _SyntaxErrorClient(f"client-{len(created) + 1}")
        created.append(client)
        return client

    monkeypatch.setattr(clickhouse, "_async_client", None)
    monkeypatch.setattr(clickhouse, "_client_lock", None)
    monkeypatch.setattr(clickhouse, "_create_clickhouse_client", fake_create_clickhouse_client)

    with pytest.raises(RuntimeError, match="Syntax error"):
        await clickhouse.execute_clickhouse("SELECT bad", {})

    assert len(created) == 1


@pytest.mark.asyncio
async def test_close_clickhouse_client_closes_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _RecordingAsyncClient()

    monkeypatch.setattr(clickhouse, "_async_client", client)

    await clickhouse.close_clickhouse_client()

    assert client.closed is True
    assert clickhouse._async_client is None


# --- TLS to ClickHouse (TF-13 S-2) ------------------------------------------

def _capture_get_async_client(monkeypatch):
    captured: dict = {}

    async def fake_get_async_client(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(clickhouse.clickhouse_connect, "get_async_client", fake_get_async_client)
    return captured


@pytest.mark.asyncio
async def test_create_client_writes_inline_ca_pem_and_verifies(monkeypatch):
    captured = _capture_get_async_client(monkeypatch)
    monkeypatch.setattr(clickhouse, "_ca_cert_path", None)
    pem = "-----BEGIN CERTIFICATE-----\nMIIBdummybase64\n-----END CERTIFICATE-----"
    monkeypatch.setattr(clickhouse.settings, "clickhouse_secure", True)
    monkeypatch.setattr(clickhouse.settings, "clickhouse_verify", True)
    monkeypatch.setattr(clickhouse.settings, "clickhouse_ca_cert", pem)
    monkeypatch.setattr(clickhouse.settings, "clickhouse_server_host_name", "coga-clickhouse")

    await clickhouse._create_clickhouse_client()

    assert captured["interface"] == "https"
    assert captured["secure"] is True
    assert captured["verify"] is True
    assert captured["server_host_name"] == "coga-clickhouse"
    ca_path = captured["ca_cert"]
    assert "MIIBdummybase64" in Path(ca_path).read_text()


@pytest.mark.asyncio
async def test_create_client_uses_ca_cert_path_as_is(monkeypatch, tmp_path):
    captured = _capture_get_async_client(monkeypatch)
    monkeypatch.setattr(clickhouse, "_ca_cert_path", None)
    ca_file = tmp_path / "ca.crt"
    ca_file.write_text("-----BEGIN CERTIFICATE-----\nX\n-----END CERTIFICATE-----\n")
    monkeypatch.setattr(clickhouse.settings, "clickhouse_secure", True)
    monkeypatch.setattr(clickhouse.settings, "clickhouse_ca_cert", str(ca_file))
    monkeypatch.setattr(clickhouse.settings, "clickhouse_server_host_name", None)

    await clickhouse._create_clickhouse_client()

    assert captured["ca_cert"] == str(ca_file)
    assert "server_host_name" not in captured


@pytest.mark.asyncio
async def test_create_client_plain_http_has_no_tls_kwargs(monkeypatch):
    captured = _capture_get_async_client(monkeypatch)
    monkeypatch.setattr(clickhouse.settings, "clickhouse_secure", False)
    monkeypatch.setattr(clickhouse.settings, "clickhouse_ca_cert", "ignored-when-plain")

    await clickhouse._create_clickhouse_client()

    assert captured["interface"] == "http"
    assert captured["secure"] is False
    assert "ca_cert" not in captured
    assert "server_host_name" not in captured


@pytest.mark.parametrize(
    "query,table,database,columns",
    [
        ("INSERT INTO t (a, b) VALUES", "t", None, ["a", "b"]),
        ("  insert into db.`tbl` (`x`,`y`) values  ", "tbl", "db", ["x", "y"]),
        ("INSERT INTO   my_table   (c1, c2, c3)   VALUES", "my_table", None, ["c1", "c2", "c3"]),
        ("INSERT INTO t () VALUES", "t", None, []),
        ("INSERT INTO t(a) VALUES", "t", None, ["a"]),
    ],
)
def test_parse_insert_query_matches_valid_inserts(query, table, database, columns) -> None:
    parsed_table, parsed_db, parsed_cols = clickhouse._parse_insert_query(query)
    assert (parsed_table, parsed_db, parsed_cols) == (table, database, columns)


@pytest.mark.parametrize(
    "query",
    ["SELECT * FROM t", "INSERT INTO t (a) VALUES (1,2)", "INSERTINTO t (a) VALUES"],
)
def test_parse_insert_query_rejects_non_insert_forms(query) -> None:
    with pytest.raises(ValueError):
        clickhouse._parse_insert_query(query)


def test_insert_pattern_is_not_redos_vulnerable() -> None:
    # The old `(?P<table>.+?)` overlapped the surrounding whitespace and took minutes on
    # this input; the anchored `table` group must resolve it in well under a second.
    import time

    adversarial = "INSERT INTO " + " " * 20_000
    start = time.perf_counter()
    result = clickhouse._INSERT_QUERY_PATTERN.match(adversarial)
    elapsed = time.perf_counter() - start
    assert result is None
    assert elapsed < 1.0
