import pytest

from backend.app.services import clickhouse_variant_storage as cvs


@pytest.mark.asyncio
async def test_count_family_small_variants_by_family_builds_scoped_group_by(monkeypatch) -> None:
    captured: dict = {}

    async def fake_ensure(assembly_name):
        return None

    async def fake_execute(query, params=None, data=None):
        captured["query"] = query
        captured["params"] = params
        return [("fam-1", 15), ("fam-2", 7)]

    monkeypatch.setattr(cvs, "ensure_clickhouse_variant_tables", fake_ensure)
    monkeypatch.setattr(cvs, "_execute", fake_execute)

    out = await cvs.count_family_small_variants_by_family(
        "GRCh38",
        family_project_pairs=[("fam-1", "p1"), ("fam-2", "p2")],
        families_without_project=["fam-3"],
    )

    assert out == {"fam-1": 15, "fam-2": 7}
    query = captured["query"]
    # Exact per-family project scope via tuple-IN, plus an unscoped OR term.
    assert "(family_guid, project_guid) IN %(family_project_pairs)s" in query
    assert "family_guid IN %(families_without_project)s" in query
    assert "GROUP BY family_guid" in query
    # Counts via the exact nested count(), not approximate count(DISTINCT).
    assert "GROUP BY family_guid, key" in query
    # tuple-of-tuples so clickhouse-connect renders ClickHouse tuple-IN syntax.
    assert captured["params"]["family_project_pairs"] == (("fam-1", "p1"), ("fam-2", "p2"))
    assert captured["params"]["families_without_project"] == ("fam-3",)


@pytest.mark.asyncio
async def test_count_family_by_family_skips_query_when_no_scope(monkeypatch) -> None:
    calls = {"execute": 0}

    async def fake_ensure(assembly_name):
        return None

    async def fake_execute(query, params=None, data=None):
        calls["execute"] += 1
        return []

    monkeypatch.setattr(cvs, "ensure_clickhouse_variant_tables", fake_ensure)
    monkeypatch.setattr(cvs, "_execute", fake_execute)

    out = await cvs.count_family_structural_variants_by_family(
        "GRCh38", family_project_pairs=[], families_without_project=[]
    )
    assert out == {}
    assert calls["execute"] == 0


def _capture_storage_writes(monkeypatch) -> list[tuple[str, object]]:
    """Record every ClickHouse statement a storage call issues (query, params-or-data)."""
    issued: list[tuple[str, object]] = []

    async def fake_ensure(assembly_name):
        return None

    async def fake_execute(query, params=None, data=None):
        issued.append((" ".join(query.split()), data if data is not None else params))
        return []

    monkeypatch.setattr(cvs, "ensure_clickhouse_variant_tables", fake_ensure)
    monkeypatch.setattr(cvs, "_execute", fake_execute)
    return issued


def _data_version_bumps(issued, family_uuid="fam-1"):
    return [
        payload
        for query, payload in issued
        if query.startswith("INSERT INTO") and "SNV_INDEL/family_data_version" in query
        and payload and payload[0][0] == family_uuid
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("source", [None, "clair3"])
async def test_small_variant_delete_bumps_the_family_data_version_last(monkeypatch, source) -> None:
    # #509: every storage-level mutation stamps a new data version AFTER the write, so the
    # prioritised-ranking cache can never serve a ranking over the deleted variants.
    issued = _capture_storage_writes(monkeypatch)
    await cvs.delete_family_small_variants("GRCh38", "fam-1", source=source)
    assert len(_data_version_bumps(issued)) == 1
    assert "family_data_version" in issued[-1][0]
    assert any(query.startswith("ALTER TABLE") and "DELETE" in query for query, _ in issued[:-1])


@pytest.mark.asyncio
async def test_small_variant_summary_refresh_bumps_the_family_data_version(monkeypatch) -> None:
    # The refresh follows every re-insert and the snapshot restore, so it stands for both.
    issued = _capture_storage_writes(monkeypatch)
    await cvs.refresh_family_small_variant_summaries("GRCh38", "fam-1")
    assert len(_data_version_bumps(issued)) == 1
    assert "family_data_version" in issued[-1][0]


@pytest.mark.asyncio
async def test_small_variant_insert_bumps_the_version_only_when_entries_were_written(monkeypatch) -> None:
    issued = _capture_storage_writes(monkeypatch)
    monkeypatch.setattr(cvs, "_small_variant_entry_rows", lambda *a, **k: ([], [], [], [], []))
    await cvs.insert_small_variant_records("GRCh38", "fam-1", ["p1"], [])
    assert _data_version_bumps(issued) == []

    entry_row = ("row",)
    monkeypatch.setattr(
        cvs, "_small_variant_entry_rows", lambda *a, **k: ([], [entry_row], [], [], [])
    )
    await cvs.insert_small_variant_records("GRCh38", "fam-1", ["p1"], [])
    assert len(_data_version_bumps(issued)) == 1
    assert "family_data_version" in issued[-1][0]


@pytest.mark.asyncio
async def test_each_bump_writes_a_fresh_random_token(monkeypatch) -> None:
    issued = _capture_storage_writes(monkeypatch)
    await cvs.bump_family_small_variant_data_version("GRCh38", "fam-1")
    await cvs.bump_family_small_variant_data_version("GRCh38", "fam-1")
    tokens = [payload[0][1] for payload in _data_version_bumps(issued)]
    assert len(tokens) == 2 and tokens[0] != tokens[1]
    assert all(0 <= token < 2**63 for token in tokens)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rows", "expected"),
    [([(3, 12345)], "3:12345"), ([(0, None)], "0:0"), ([], "0:0")],
)
async def test_family_data_version_is_the_token_count_and_sum(monkeypatch, rows, expected) -> None:
    captured: dict = {}

    async def fake_ensure(assembly_name):
        return None

    async def fake_execute(query, params=None, data=None):
        captured["query"] = " ".join(query.split())
        captured["params"] = params
        return rows

    monkeypatch.setattr(cvs, "ensure_clickhouse_variant_tables", fake_ensure)
    monkeypatch.setattr(cvs, "_execute", fake_execute)

    assert await cvs.get_family_small_variant_data_version("GRCh38", "fam-1") == expected
    assert "SELECT count(), sum(token)" in captured["query"]
    assert "SNV_INDEL/family_data_version" in captured["query"]
    assert captured["params"] == {"family_guid": "fam-1"}


@pytest.mark.asyncio
async def test_ensure_tables_creates_the_family_data_version_table(monkeypatch) -> None:
    statements: list[str] = []

    async def fake_execute(query, params=None, data=None):
        statements.append(" ".join(query.split()))
        return []

    monkeypatch.setattr(cvs, "_execute", fake_execute)
    monkeypatch.setattr(cvs, "_ensured_variant_table_assemblies", set())

    await cvs.ensure_clickhouse_variant_tables("GRCh38")

    ddl = [s for s in statements if "SNV_INDEL/family_data_version" in s]
    assert len(ddl) == 1
    # Plain MergeTree: rows must never collapse, or the (count, sum) fingerprint could
    # return to an earlier value.
    assert "CREATE TABLE IF NOT EXISTS" in ddl[0] and "ENGINE = MergeTree" in ddl[0]


@pytest.mark.asyncio
async def test_entries_hold_each_calls_filter_and_metrics(monkeypatch) -> None:
    # A one-sample VCF's FILTER and caller metrics (TLOD, ...) are its call's own and are
    # kept per call; a rewrite writes them back with every other column.
    statements: list[str] = []

    async def fake_execute(query, params=None, data=None):
        statements.append(" ".join(query.split()))
        return []

    monkeypatch.setattr(cvs, "_execute", fake_execute)
    monkeypatch.setattr(cvs, "_ensured_variant_table_assemblies", set())

    await cvs.ensure_clickhouse_variant_tables("GRCh38")

    [create] = [s for s in statements if "CREATE TABLE" in s and "SNV_INDEL/entries`" in s]
    assert "`calls.filters` Array(Array(LowCardinality(String)))" in create
    assert "`calls.metrics` Array(Map(LowCardinality(String), Float32))" in create
    assert {"calls.filters", "calls.metrics"} <= set(cvs.SMALL_VARIANT_ENTRY_COLUMNS)


@pytest.mark.asyncio
async def test_an_entries_table_without_the_per_call_columns_is_refused(monkeypatch) -> None:
    # A table an earlier version created has no per-call FILTER and metrics columns. It is
    # refused, as a table with an older row identity is, rather than failing later on
    # every read: there is no older schema to upgrade in place.
    async def fake_execute(query, params=None, data=None):
        if "system.columns" in query:
            return [("GRCh38/SNV_INDEL/entries", ["key", "calls.sampleId", "calls.ps"])]
        return []

    monkeypatch.setattr(cvs, "_execute", fake_execute)
    monkeypatch.setattr(cvs, "_ensured_variant_table_assemblies", set())

    with pytest.raises(cvs.ClickHouseStorageIdentityError, match="calls.filters, calls.metrics"):
        await cvs.ensure_clickhouse_variant_tables("GRCh38")
    assert "GRCh38" not in cvs._ensured_variant_table_assemblies


def _sv_data_version_bumps(issued, family_uuid="fam-1"):
    return [
        payload
        for query, payload in issued
        if query.startswith("INSERT INTO") and "SV/family_data_version" in query
        and payload and payload[0][0] == family_uuid
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("source", [None, "needlr"])
async def test_structural_variant_delete_bumps_the_sv_data_version_last(monkeypatch, source) -> None:
    # The SV→gene index (the small-variant "second hit" badge) is keyed on this version, so
    # an admin delete or the delete half of a per-sample upload must move it.
    issued = _capture_storage_writes(monkeypatch)
    await cvs.delete_family_structural_variants("GRCh38", "fam-1", source=source)
    assert len(_sv_data_version_bumps(issued)) == 1
    assert "SV/family_data_version" in issued[-1][0]
    assert any(query.startswith("ALTER TABLE") and "DELETE" in query for query, _ in issued[:-1])
    # The small-variant version is a different family of data and stays put.
    assert _data_version_bumps(issued) == []


@pytest.mark.asyncio
async def test_structural_variant_insert_bumps_the_version_only_when_entries_were_written(
    monkeypatch,
) -> None:
    issued = _capture_storage_writes(monkeypatch)
    monkeypatch.setattr(cvs, "_structural_variant_entry_rows", lambda *a, **k: ([], [], []))
    await cvs.insert_structural_variant_records("GRCh38", "fam-1", ["p1"], [])
    assert _sv_data_version_bumps(issued) == []

    monkeypatch.setattr(
        cvs, "_structural_variant_entry_rows", lambda *a, **k: ([("detail",)], [("lookup",)], [("entry",)])
    )
    await cvs.insert_structural_variant_records("GRCh38", "fam-1", ["p1"], [])
    assert len(_sv_data_version_bumps(issued)) == 1
    assert "SV/family_data_version" in issued[-1][0]


@pytest.mark.asyncio
async def test_a_per_sample_sv_replace_moves_the_sv_data_version(monkeypatch) -> None:
    # The per-sample SV upload (and the admin per-sample SV delete) rewrite the family's
    # SVs through replace_family_structural_variants.
    issued = _capture_storage_writes(monkeypatch)
    monkeypatch.setattr(
        cvs, "_structural_variant_entry_rows", lambda *a, **k: ([("detail",)], [("lookup",)], [("entry",)])
    )
    await cvs.replace_family_structural_variants("GRCh38", "fam-1", ["p1"], ["record"], source="sniffles")
    assert len(_sv_data_version_bumps(issued)) == 2  # the delete, then the insert
    assert "SV/family_data_version" in issued[-1][0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rows", "expected"),
    [([(2, 777)], "2:777"), ([(0, None)], "0:0"), ([], "0:0")],
)
async def test_sv_data_version_is_the_token_count_and_sum(monkeypatch, rows, expected) -> None:
    captured: dict = {}

    async def fake_ensure(assembly_name):
        return None

    async def fake_execute(query, params=None, data=None):
        captured["query"] = " ".join(query.split())
        captured["params"] = params
        return rows

    monkeypatch.setattr(cvs, "ensure_clickhouse_variant_tables", fake_ensure)
    monkeypatch.setattr(cvs, "_execute", fake_execute)

    assert await cvs.get_family_structural_variant_data_version("GRCh38", "fam-1") == expected
    assert "SELECT count(), sum(token)" in captured["query"]
    assert "SV/family_data_version" in captured["query"]
    assert captured["params"] == {"family_guid": "fam-1"}


@pytest.mark.asyncio
async def test_ensure_tables_creates_the_sv_data_version_table(monkeypatch) -> None:
    statements: list[str] = []

    async def fake_execute(query, params=None, data=None):
        statements.append(" ".join(query.split()))
        return []

    monkeypatch.setattr(cvs, "_execute", fake_execute)
    monkeypatch.setattr(cvs, "_ensured_variant_table_assemblies", set())

    await cvs.ensure_clickhouse_variant_tables("GRCh38")

    ddl = [s for s in statements if "SV/family_data_version" in s]
    assert len(ddl) == 1
    assert "CREATE TABLE IF NOT EXISTS" in ddl[0] and "ENGINE = MergeTree" in ddl[0]


@pytest.mark.asyncio
async def test_restoring_a_family_snapshot_moves_the_sv_data_version(monkeypatch) -> None:
    # A failed overwrite import rolls the family's SV rows back by rewriting the tables
    # directly; an index built from the half-imported SVs in the meantime must not survive.
    from backend.app.services import clickhouse_family_snapshot as snapshot_module

    executed: list[str] = []
    bumped: list[tuple[str, str]] = []

    async def fake_execute(query, params=None):
        executed.append(" ".join(query.split()))
        if "FROM system.tables" in query:  # the restore's check: every backup table is there
            return [(name,) for name in params["names"]]
        return []

    async def fake_refresh(assembly_name, family_uuid):
        return None

    async def fake_bump(assembly_name, family_uuid):
        bumped.append((assembly_name, family_uuid))

    monkeypatch.setattr(snapshot_module, "execute_clickhouse", fake_execute)
    monkeypatch.setattr(snapshot_module, "refresh_family_small_variant_summaries", fake_refresh)
    monkeypatch.setattr(
        snapshot_module, "bump_family_structural_variant_data_version", fake_bump, raising=False
    )
    snapshot = snapshot_module.FamilyClickHouseSnapshot(
        assembly_name="GRCh38",
        family_uuid="fam-1",
        token="t",
        specs=snapshot_module._snapshot_specs("GRCh38", "t"),
    )
    await snapshot_module.restore_family_clickhouse_state(snapshot)
    assert bumped == [("GRCh38", "fam-1")]
