from __future__ import annotations

import pytest

from backend.app.services import clickhouse_variant_storage


@pytest.mark.asyncio
async def test_list_clickhouse_variant_assemblies_dedupes_table_prefixes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_execute(
        query: str,
        params: dict[str, object] | None = None,
        data=None,
    ):
        assert "FROM system.tables" in query
        return [
            ("GRCh38/SNV_INDEL/entries",),
            ("GRCh38/SV/entries",),
            ("GRCh37/SNV_INDEL/entries",),
        ]

    monkeypatch.setattr(clickhouse_variant_storage, "_execute", fake_execute)

    assemblies = await clickhouse_variant_storage.list_clickhouse_variant_assemblies()

    assert assemblies == ["GRCh37", "GRCh38"]


@pytest.mark.asyncio
async def test_get_clickhouse_variant_storage_status_reports_missing_tables_and_mutations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_execute(
        query: str,
        params: dict[str, object] | None = None,
        data=None,
    ):
        if "FROM system.tables" in query:
            return [
                ("GRCh38/SNV_INDEL/entries", "CollapsingMergeTree"),
                ("GRCh38/SNV_INDEL/variants/details", "ReplacingMergeTree"),
                ("GRCh38/SV/entries", "CollapsingMergeTree"),
            ]
        if "FROM system.parts" in query:
            return [
                ("GRCh38/SNV_INDEL/entries", 5000, 250_000),
                ("GRCh38/SNV_INDEL/variants/details", 5000, 175_000),
                ("GRCh38/SV/entries", 1200, 64_000),
            ]
        if "FROM system.mutations" in query:
            return [("GRCh38/SV/entries", 2)]
        raise AssertionError(f"Unexpected query: {query}")

    monkeypatch.setattr(clickhouse_variant_storage, "_execute", fake_execute)

    status = await clickhouse_variant_storage.get_clickhouse_variant_storage_status("GRCh38")

    assert status["assembly_name"] == "GRCh38"
    assert status["health"] == "missing"
    assert status["expected_table_count"] == 11
    assert status["existing_table_count"] == 3
    assert status["small_variant_rows"] == 5000
    assert status["structural_variant_rows"] == 1200
    assert status["pending_mutations"] == 2
    assert "GRCh38/SNV_INDEL/key_lookup" in status["missing_tables"]
    assert any(
        table["name"] == "GRCh38/SV/entries" and table["pending_mutations"] == 2
        for table in status["tables"]
    )


@pytest.mark.asyncio
async def test_optimize_clickhouse_variant_tables_skips_materialized_views(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed_queries: list[str] = []

    async def fake_ensure(assembly_name: str) -> None:
        assert assembly_name == "GRCh38"

    async def fake_execute(
        query: str,
        params: dict[str, object] | None = None,
        data=None,
    ):
        executed_queries.append(query)
        return []

    async def fake_status(assembly_name: str) -> dict[str, object]:
        return {"assembly_name": assembly_name, "health": "ready", "tables": []}

    monkeypatch.setattr(
        clickhouse_variant_storage,
        "ensure_clickhouse_variant_tables",
        fake_ensure,
    )
    monkeypatch.setattr(clickhouse_variant_storage, "_execute", fake_execute)
    monkeypatch.setattr(
        clickhouse_variant_storage,
        "get_clickhouse_variant_storage_status",
        fake_status,
    )

    status = await clickhouse_variant_storage.optimize_clickhouse_variant_tables(
        "GRCh38",
        final=True,
    )

    assert status["assembly_name"] == "GRCh38"
    assert len(executed_queries) == 9
    assert all("OPTIMIZE TABLE" in query for query in executed_queries)
    assert all("FINAL" in query for query in executed_queries)
    assert not any("_mv" in query for query in executed_queries)
