from __future__ import annotations

import pytest

from backend.app.services.clickhouse_family_variants import SmallVariantCall, SmallVariantRecord
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
    assert status["expected_table_count"] == 10
    assert status["existing_table_count"] == 3
    assert status["small_variant_rows"] == 5000
    assert status["structural_variant_rows"] == 1200
    assert status["pending_mutations"] == 2
    assert "GRCh38/SNV_INDEL/variants/annotation_index" in status["missing_tables"]
    assert "GRCh38/SNV_INDEL/family_variant_summary" in status["missing_tables"]
    # The retired project_gt_stats/gt_stats cascade is no longer expected, so it is
    # not reported as missing.
    assert "GRCh38/SNV_INDEL/gt_stats" not in status["missing_tables"]
    assert "GRCh38/SNV_INDEL/project_gt_stats" not in status["missing_tables"]
    assert any(
        table["name"] == "GRCh38/SV/entries" and table["pending_mutations"] == 2
        for table in status["tables"]
    )


@pytest.mark.asyncio
async def test_count_family_small_variants_by_sample_counts_non_reference_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_ensure(assembly_name: str) -> None:
        assert assembly_name == "GRCh38"

    async def fake_execute(
        query: str,
        params: dict[str, object] | None = None,
        data=None,
    ):
        captured["query"] = query
        captured["params"] = params
        return [("embryo-1", 7)]

    monkeypatch.setattr(
        clickhouse_variant_storage,
        "ensure_clickhouse_variant_tables",
        fake_ensure,
    )
    monkeypatch.setattr(clickhouse_variant_storage, "_execute", fake_execute)

    counts = await clickhouse_variant_storage.count_family_small_variants_by_sample(
        "GRCh38",
        "family-1",
        sample_ids=["embryo-1"],
        project_ids=["project-1"],
    )

    assert counts == {"embryo-1": 7}
    assert "ARRAY JOIN `calls.sampleId` AS sample_id, `calls.gt` AS gt" in str(captured["query"])
    assert captured["params"] == {
        "family_guid": "family-1",
        "sample_ids": ("embryo-1",),
        "project_ids": ("project-1",),
    }


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
    assert len(executed_queries) == 10
    assert all("OPTIMIZE TABLE" in query for query in executed_queries)
    assert all("FINAL" in query for query in executed_queries)
    assert not any("_mv" in query for query in executed_queries)
    # The retired project_gt_stats/gt_stats aggregates are no longer optimized.
    assert not any("gt_stats" in query for query in executed_queries)


@pytest.mark.asyncio
async def test_insert_small_variant_records_uses_compact_annotations_and_gene_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed: list[tuple[str, list[tuple[object, ...]] | None]] = []

    async def fake_ensure(assembly_name: str) -> None:
        assert assembly_name == "GRCh38"

    async def fake_execute(
        query: str,
        params: dict[str, object] | None = None,
        data=None,
    ):
        executed.append((query, data))
        return []

    monkeypatch.setattr(
        clickhouse_variant_storage,
        "ensure_clickhouse_variant_tables",
        fake_ensure,
    )
    monkeypatch.setattr(clickhouse_variant_storage, "_execute", fake_execute)

    record = SmallVariantRecord(
        variant_key=None,
        variant_id="1-100-A-G",
        chr="1",
        start=100,
        end=100,
        ref="A",
        alt="G",
        source="glimpse2",
        rsid=None,
        filters=[],
        gene_symbols=["APC"],
        annotations=[{"gene": "APC", "gene_id": "ENSG00000134982", "impact": "HIGH"}],
        calls=[SmallVariantCall(sample="sample-1", gt="0/1", gq=None, dp=None, af=[], ad=[], ps=None)],
    )

    await clickhouse_variant_storage.insert_small_variant_records(
        "GRCh38",
        "family-1",
        ["project-1", "project-2"],
        [record],
    )

    annotation_query, annotation_data = next(
        (query, data)
        for query, data in executed
        if "INSERT INTO coga.`GRCh38/SNV_INDEL/variants/annotations`" in query
    )
    entry_data = next(
        data
        for query, data in executed
        if "INSERT INTO coga.`GRCh38/SNV_INDEL/entries`" in query
    )
    gene_index_data = next(
        data
        for query, data in executed
        if "INSERT INTO coga.`GRCh38/SNV_INDEL/variants/gene_index`" in query
    )

    assert "annotation_json" not in annotation_query
    assert annotation_data is not None
    assert len(annotation_data) == 1
    assert entry_data is not None
    assert len(entry_data) == 2
    assert gene_index_data is not None
    assert {row[4] for row in gene_index_data} == {"apc", "ensg00000134982"}


@pytest.mark.asyncio
async def test_insert_small_variant_records_stores_site_qual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed: list[tuple[str, list[tuple[object, ...]] | None]] = []

    async def fake_ensure(assembly_name: str) -> None:
        assert assembly_name == "GRCh38"

    async def fake_execute(
        query: str,
        params: dict[str, object] | None = None,
        data=None,
    ):
        executed.append((query, data))
        return []

    monkeypatch.setattr(
        clickhouse_variant_storage,
        "ensure_clickhouse_variant_tables",
        fake_ensure,
    )
    monkeypatch.setattr(clickhouse_variant_storage, "_execute", fake_execute)

    record = SmallVariantRecord(
        variant_key=None,
        variant_id="1-100-A-G",
        chr="1",
        start=100,
        end=100,
        ref="A",
        alt="G",
        source="clair3",
        rsid=None,
        filters=[],
        gene_symbols=["APC"],
        annotations=[],
        calls=[SmallVariantCall(sample="sample-1", gt="0/1", gq=None, dp=None, af=[], ad=[], ps=None)],
        qual=42.5,
    )

    await clickhouse_variant_storage.insert_small_variant_records(
        "GRCh38",
        "family-1",
        ["project-1"],
        [record],
    )

    entry_query, entry_data = next(
        (query, data)
        for query, data in executed
        if "INSERT INTO coga.`GRCh38/SNV_INDEL/entries`" in query
    )

    assert "qual," in entry_query
    assert entry_data is not None
    # qual is the variant-level column inserted immediately after `filters`.
    assert entry_data[0][18] == pytest.approx(42.5)


@pytest.mark.asyncio
async def test_insert_small_variant_records_chunks_large_table_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed: list[tuple[str, int]] = []

    async def fake_ensure(assembly_name: str) -> None:
        assert assembly_name == "GRCh38"

    async def fake_execute(
        query: str,
        params: dict[str, object] | None = None,
        data=None,
    ):
        assert data is not None
        executed.append((query, len(data)))
        return []

    monkeypatch.setattr(
        clickhouse_variant_storage,
        "ensure_clickhouse_variant_tables",
        fake_ensure,
    )
    monkeypatch.setattr(clickhouse_variant_storage, "_execute", fake_execute)
    monkeypatch.setattr(clickhouse_variant_storage, "_SMALL_VARIANT_DETAIL_INSERT_ROWS", 2)
    monkeypatch.setattr(clickhouse_variant_storage, "_SMALL_VARIANT_ENTRY_INSERT_ROWS", 2)
    monkeypatch.setattr(clickhouse_variant_storage, "_SMALL_VARIANT_ANNOTATION_INSERT_ROWS", 2)
    monkeypatch.setattr(clickhouse_variant_storage, "_SMALL_VARIANT_INDEX_INSERT_ROWS", 2)
    monkeypatch.setattr(clickhouse_variant_storage, "_SMALL_VARIANT_GENE_INDEX_INSERT_ROWS", 2)

    records = [
        SmallVariantRecord(
            variant_key=None,
            variant_id=f"1-{100 + index}-A-G",
            chr="1",
            start=100 + index,
            end=100 + index,
            ref="A",
            alt="G",
            source="glimpse2",
            rsid=None,
            filters=[],
            gene_symbols=[f"GENE{index}"],
            annotations=[],
            calls=[SmallVariantCall(sample="sample-1", gt="0/1", gq=None, dp=None, af=[], ad=[], ps=None)],
        )
        for index in range(3)
    ]

    await clickhouse_variant_storage.insert_small_variant_records(
        "GRCh38",
        "family-1",
        ["project-1"],
        records,
    )

    def sizes_for(table_fragment: str) -> list[int]:
        return [size for query, size in executed if table_fragment in query]

    assert sizes_for("variants/details") == [2, 1]
    assert sizes_for("SNV_INDEL/entries") == [2, 1]
    assert sizes_for("variants/annotations") == [2, 1]
    assert sizes_for("variants/annotation_index") == [2, 1]
    assert sizes_for("variants/gene_index") == [2, 1]


@pytest.mark.asyncio
async def test_rebuild_small_variant_gene_index_is_explicit_batched_maintenance(
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
        return {"assembly_name": assembly_name, "health": "ready"}

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

    status = await clickhouse_variant_storage.rebuild_small_variant_gene_index("GRCh38")

    assert status["assembly_name"] == "GRCh38"
    joined = " ".join(executed_queries)
    # The live gene_index is never TRUNCATEd (the old empty-window approach):
    # the index is rebuilt into a shadow table and atomically swapped in.
    assert not any(query.startswith("TRUNCATE") for query in executed_queries)
    assert any(
        "CREATE TABLE coga.`GRCh38/SNV_INDEL/variants/gene_index_rebuild`" in query
        for query in executed_queries
    )
    assert any(
        "INSERT INTO coga.`GRCh38/SNV_INDEL/variants/gene_index_rebuild`" in query
        for query in executed_queries
    )
    assert "SELECT DISTINCT" in joined
    assert "arrayConcat(gene_symbols, gene_ids)" in joined
    # Validated (CHECK TABLE) before the atomic swap.
    assert any(query.strip().startswith("CHECK TABLE") for query in executed_queries)
    assert any("EXCHANGE TABLES" in query for query in executed_queries)


@pytest.mark.asyncio
async def test_refresh_family_small_variant_summaries_rebuilds_family_and_sample_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed: list[tuple[str, dict[str, object] | None]] = []

    async def fake_ensure(assembly_name: str) -> None:
        assert assembly_name == "GRCh38"

    async def fake_execute(
        query: str,
        params: dict[str, object] | None = None,
        data=None,
    ):
        executed.append((query, params))
        return []

    monkeypatch.setattr(
        clickhouse_variant_storage,
        "ensure_clickhouse_variant_tables",
        fake_ensure,
    )
    monkeypatch.setattr(clickhouse_variant_storage, "_execute", fake_execute)

    await clickhouse_variant_storage.refresh_family_small_variant_summaries(
        "GRCh38",
        "family-1",
    )

    assert len(executed) == 4
    assert "family_variant_summary" in executed[0][0]
    assert "DELETE WHERE family_guid = %(family_guid)s" in executed[0][0]
    assert "family_sample_variant_summary" in executed[1][0]
    assert "countDistinctIf(key, length(ref) = 1 AND length(alt) = 1)" in executed[2][0]
    # Per-project scoping: both summaries must group by project_guid so per-project
    # counts never aggregate across the projects a family belongs to.
    assert "GROUP BY family_guid, project_guid" in executed[2][0]
    assert "countDistinctIf(key, gt NOT IN ('', '.', './.', '.|.', '0/0', '0|0'))" in executed[3][0]
    assert "countDistinctIf(key, gt IN ('0/1', '1/0', '0|1', '1|0'))" in executed[3][0]
    assert "GROUP BY family_guid, project_guid, sample_id" in executed[3][0]
    assert "project_guid" in executed[3][0]
    # The summary is a diagnostic count, so both rebuild queries exclude imputed
    # callsets (glimpse2/shapeit) — matching the live-fallback query and the default
    # per-family variant list. The delete queries stay unscoped by source.
    assert "lowerUTF8(source) NOT IN %(imputed_sources)s" in executed[2][0]
    assert "lowerUTF8(source) NOT IN %(imputed_sources)s" in executed[3][0]
    assert "lowerUTF8(source)" not in executed[0][0]
    assert all(params["family_guid"] == "family-1" for _query, params in executed)
    assert executed[2][1]["imputed_sources"] == ("glimpse2", "shapeit")


@pytest.mark.asyncio
async def test_delete_family_small_variants_scopes_entries_to_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed: list[tuple[str, dict[str, object] | None]] = []

    async def fake_ensure(assembly_name: str) -> None:
        assert assembly_name == "GRCh38"

    async def fake_execute(query, params=None, data=None):
        executed.append((query, params))
        return []

    monkeypatch.setattr(
        clickhouse_variant_storage, "ensure_clickhouse_variant_tables", fake_ensure
    )
    monkeypatch.setattr(clickhouse_variant_storage, "_execute", fake_execute)

    await clickhouse_variant_storage.delete_family_small_variants(
        "GRCh38", "family-1", source="glimpse2"
    )

    # Source-scoped: exactly one DELETE, against entries only, filtered by source, so
    # re-importing glimpse2 cannot touch the clair3 rows. The summary tables are left
    # for the caller's refresh to rebuild from the surviving entries.
    assert len(executed) == 1
    query, params = executed[0]
    assert "entries" in query
    assert "AND source = %(source)s" in query
    assert "family_variant_summary" not in query
    assert params == {"family_guid": "family-1", "source": "glimpse2"}


@pytest.mark.asyncio
async def test_delete_family_small_variants_without_source_clears_all_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed: list[tuple[str, dict[str, object] | None]] = []

    async def fake_ensure(assembly_name: str) -> None:
        assert assembly_name == "GRCh38"

    async def fake_execute(query, params=None, data=None):
        executed.append((query, params))
        return []

    monkeypatch.setattr(
        clickhouse_variant_storage, "ensure_clickhouse_variant_tables", fake_ensure
    )
    monkeypatch.setattr(clickhouse_variant_storage, "_execute", fake_execute)

    await clickhouse_variant_storage.delete_family_small_variants("GRCh38", "family-1")

    # Unscoped: clears entries and both summary tables, with no source filter.
    assert len(executed) == 3
    assert "entries" in executed[0][0]
    assert "family_variant_summary" in executed[1][0]
    assert "family_sample_variant_summary" in executed[2][0]
    assert all("source = %(source)s" not in query for query, _params in executed)


@pytest.mark.asyncio
async def test_count_family_small_variants_scopes_to_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed: list[tuple[str, dict[str, object] | None]] = []

    async def fake_ensure(assembly_name: str) -> None:
        assert assembly_name == "GRCh38"

    async def fake_execute(query, params=None, data=None):
        executed.append((query, params))
        return [[7]]

    monkeypatch.setattr(
        clickhouse_variant_storage, "ensure_clickhouse_variant_tables", fake_ensure
    )
    monkeypatch.setattr(clickhouse_variant_storage, "_execute", fake_execute)

    count = await clickhouse_variant_storage.count_family_small_variants(
        "GRCh38", "family-1", source="clair3"
    )

    assert count == 7
    query, params = executed[0]
    assert "source = %(source)s" in query
    assert params["source"] == "clair3"


# --- durable removal of the never-read project_gt_stats/gt_stats cascade --------


@pytest.mark.asyncio
async def test_drop_legacy_gt_stats_aggregates_drops_views_then_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[str] = []

    async def fake_execute(query: str, params=None, data=None):
        recorded.append(" ".join(query.split()))
        return None

    monkeypatch.setattr(clickhouse_variant_storage, "_execute", fake_execute)

    await clickhouse_variant_storage._drop_legacy_gt_stats_aggregates("coga", "GRCh38")

    # Views are dropped before their target tables so inserts into `entries` stop
    # fanning out before the SummingMergeTree targets disappear.
    assert recorded == [
        "DROP TABLE IF EXISTS coga.`GRCh38/SNV_INDEL/entries_to_project_gt_stats_mv` SYNC",
        "DROP TABLE IF EXISTS coga.`GRCh38/SNV_INDEL/project_gt_stats_to_gt_stats_mv` SYNC",
        "DROP TABLE IF EXISTS coga.`GRCh38/SNV_INDEL/gt_stats` SYNC",
        "DROP TABLE IF EXISTS coga.`GRCh38/SNV_INDEL/project_gt_stats` SYNC",
    ]


def test_expected_variant_tables_exclude_gt_stats_cascade() -> None:
    names = {
        name
        for _vt, _kind, name in clickhouse_variant_storage._expected_clickhouse_variant_tables(
            "GRCh38"
        )
    }
    for dead in (
        "GRCh38/SNV_INDEL/gt_stats",
        "GRCh38/SNV_INDEL/project_gt_stats",
        "GRCh38/SNV_INDEL/entries_to_project_gt_stats_mv",
        "GRCh38/SNV_INDEL/project_gt_stats_to_gt_stats_mv",
    ):
        assert dead not in names


@pytest.mark.asyncio
async def test_ensure_variant_tables_drops_cascade_and_never_recreates_gt_stats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[str] = []

    async def fake_execute(query: str, params=None, data=None):
        q = " ".join(query.split())
        recorded.append(q)
        # Legacy family-summary probe: report it already has project_guid so that
        # migration is a no-op and does not add noise to the recorded statements.
        if "countIf(name = 'project_guid')" in q:
            return [(1,)]
        return None

    monkeypatch.setattr(clickhouse_variant_storage, "_execute", fake_execute)
    # Bypass the process-lifetime "already ensured" cache so the body runs.
    monkeypatch.setattr(clickhouse_variant_storage, "_ensured_variant_table_assemblies", set())

    await clickhouse_variant_storage.ensure_clickhouse_variant_tables("GRCh38")

    # The dead cascade is dropped...
    assert any(
        q == "DROP TABLE IF EXISTS coga.`GRCh38/SNV_INDEL/entries_to_project_gt_stats_mv` SYNC"
        for q in recorded
    )
    assert any(q == "DROP TABLE IF EXISTS coga.`GRCh38/SNV_INDEL/gt_stats` SYNC" for q in recorded)
    # ...and never recreated by the CREATE pass that follows.
    assert not any(q.startswith("CREATE TABLE") and "/gt_stats`" in q for q in recorded)
    assert not any(q.startswith("CREATE TABLE") and "/project_gt_stats`" in q for q in recorded)
    assert not any("CREATE MATERIALIZED VIEW" in q for q in recorded)
    # Sanity: the live tables we keep are still created.
    assert any(q.startswith("CREATE TABLE") and "/SNV_INDEL/entries`" in q for q in recorded)
