from __future__ import annotations

from typing import Any

import pytest

from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord
from backend.app.services import clickhouse_variant_storage
from backend.app.services.genotypes import genotype_vocabulary
from backend.app.services.variant_annotation_parser import (
    AnnotationHeaderState,
    extract_small_variant_annotations,
    update_annotation_header_state,
)


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
        # Non-reference means "carries an ALT allele" (#511): a haploid "0" is not counted.
        "gt_alt": genotype_vocabulary("het", "hom_alt"),
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


def _vep_annotations(*entries: str) -> list[dict[str, Any]]:
    """Annotations as the import parses them from a VEP ``CSQ`` with both MANE columns."""
    state = AnnotationHeaderState()
    update_annotation_header_state(
        state,
        '##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from Ensembl'
        " VEP. Format: Allele|Consequence|IMPACT|SYMBOL|Gene|Feature_type|Feature|CANONICAL"
        '|MANE_SELECT|MANE_PLUS_CLINICAL">',
    )
    return extract_small_variant_annotations({"CSQ": ",".join(entries)}, state)


def _inserted_rows(
    executed: list[tuple[str, list[tuple[object, ...]] | None]], table: str
) -> list[dict[str, object]]:
    """The rows written into ``table`` (``variants/annotations`` …), each as {column: value}."""
    query, data = next((query, data) for query, data in executed if f"/{table}` (" in query)
    columns = [column.strip() for column in query.split("(", 1)[1].split(")", 1)[0].split(",")]
    return [dict(zip(columns, row, strict=True)) for row in data or []]


@pytest.mark.asyncio
@pytest.mark.parametrize("assembly", ["GRCh38", "GRCh37", "T2T-CHM13v2.0"])
async def test_insert_small_variant_records_writes_both_mane_flags(
    monkeypatch: pytest.MonkeyPatch, assembly: str
) -> None:
    # CLIN-5: *MANE only* keeps a MANE Select or a MANE Plus Clinical transcript. The family
    # search reads each transcript's flags from the annotations, the Variant Explorer each
    # variant's from the annotation index; the import writes both, on every assembly, from
    # VEP's MANE_SELECT and MANE_PLUS_CLINICAL.
    executed: list[tuple[str, list[tuple[object, ...]] | None]] = []

    async def fake_ensure(assembly_name: str) -> None:
        assert assembly_name == assembly

    async def fake_execute(query: str, params: dict[str, object] | None = None, data=None):
        executed.append((query, data))
        return []

    monkeypatch.setattr(clickhouse_variant_storage, "ensure_clickhouse_variant_tables", fake_ensure)
    monkeypatch.setattr(clickhouse_variant_storage, "_execute", fake_execute)

    def record(variant_id: str, annotations: list[dict[str, Any]]) -> SmallVariantRecord:
        chrom, pos, ref, alt = variant_id.split("-")
        return SmallVariantRecord(
            variant_key=None,
            variant_id=variant_id,
            chr=chrom,
            start=int(pos),
            end=int(pos),
            ref=ref,
            alt=alt,
            source="clair3",
            rsid=None,
            filters=[],
            gene_symbols=[],
            annotations=annotations,
            calls=[SmallVariantCall(sample="sample-1", gt="0/1", gq=None, dp=None, af=[], ad=[], ps=None)],
        )

    await clickhouse_variant_storage.insert_small_variant_records(
        assembly,
        "family-1",
        ["project-1"],
        [
            # The variant's one MANE transcript is MANE Plus Clinical; the canonical one is not MANE.
            record(
                "1-100-A-G",
                _vep_annotations(
                    "G|missense_variant|MODERATE|GENE1|ENSG1|Transcript|ENST11|||NM_PLUS1.1",
                    "G|intron_variant|MODIFIER|GENE1|ENSG1|Transcript|ENST12|YES||",
                ),
            ),
            record(
                "1-200-C-T",
                _vep_annotations("T|missense_variant|MODERATE|GENE2|ENSG2|Transcript|ENST21|YES|NM_SELECT2.1|"),
            ),
            record(
                "1-300-G-A",
                _vep_annotations("A|missense_variant|MODERATE|GENE3|ENSG3|Transcript|ENST31|YES||"),
            ),
        ],
    )

    transcripts = _inserted_rows(executed, "variants/annotations")
    assert {
        row["transcript_id"]: (row["mane_select"], row["mane_plus_clinical"]) for row in transcripts
    } == {
        "ENST11": (False, True),
        "ENST12": (False, False),
        "ENST21": (True, False),
        "ENST31": (False, False),
    }
    index = _inserted_rows(executed, "variants/annotation_index")
    assert {
        row["variantId"]: (row["has_mane_select"], row["has_mane_plus_clinical"]) for row in index
    } == {
        "1-100-A-G": (False, True),
        "1-200-C-T": (True, False),
        "1-300-G-A": (False, False),
    }


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
        if "check_query_single_value_result = 1" in query:
            return [[1]]  # the shadow table passes CHECK TABLE, as the client returns it
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

    # The last statement stamps the family's new data version (#509).
    *executed, bump = executed
    assert "SNV_INDEL/family_data_version" in bump[0]
    assert len(executed) == 4
    assert "family_variant_summary" in executed[0][0]
    assert "DELETE WHERE family_guid = %(family_guid)s" in executed[0][0]
    assert "family_sample_variant_summary" in executed[1][0]
    assert "countDistinctIf(key, length(ref) = 1 AND length(alt) = 1)" in executed[2][0]
    # Per-project scoping: both summaries must group by project_guid so per-project
    # counts never aggregate across the projects a family belongs to.
    assert "GROUP BY family_guid, project_guid" in executed[2][0]
    assert "countDistinctIf(key, (gt IN %(gt_alt)s" in executed[3][0]
    assert "countDistinctIf(key, (gt IN %(gt_het)s" in executed[3][0]
    assert "countDistinctIf(key, (gt IN %(gt_hom)s" in executed[3][0]
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
    # for the caller's refresh to rebuild from the surviving entries. The delete is
    # followed by the data-version stamp (#509).
    *executed, bump = executed
    assert "SNV_INDEL/family_data_version" in bump[0]
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

    # Unscoped: clears entries and both summary tables, with no source filter, then
    # stamps the data version (#509).
    *executed, bump = executed
    assert "SNV_INDEL/family_data_version" in bump[0]
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


# --- the tables are created from their final definition only (#679) ------------


@pytest.mark.asyncio
async def test_ensure_variant_tables_only_creates_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """There is no older schema to upgrade (the data is synthetic), so ensuring an
    assembly's tables issues only the identity check and CREATE TABLE statements: no
    ALTER, DROP or materialized view."""
    recorded: list[str] = []

    async def fake_execute(query: str, params=None, data=None):
        recorded.append(" ".join(query.split()))
        return None

    monkeypatch.setattr(clickhouse_variant_storage, "_execute", fake_execute)
    # Bypass the process-lifetime "already ensured" cache so the body runs.
    monkeypatch.setattr(clickhouse_variant_storage, "_ensured_variant_table_assemblies", set())

    await clickhouse_variant_storage.ensure_clickhouse_variant_tables("GRCh38")

    ddl = [q for q in recorded if not q.startswith("SELECT")]
    assert ddl and all(q.startswith("CREATE TABLE IF NOT EXISTS") for q in ddl)
    created = {q.split("`")[1] for q in ddl}
    expected = {
        name
        for _vt, kind, name in clickhouse_variant_storage._expected_clickhouse_variant_tables("GRCh38")
    }
    assert expected <= created
