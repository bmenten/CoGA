"""The family-scoped variant storage (``clickhouse_variant_storage``): the statements it sends
ClickHouse (unit, a fake of ClickHouse at the storage's ``_execute``).

- Setting an assembly's tables up only creates them, from their final definition (#679);
  ``SNV_INDEL/entries`` holds each call's FILTER values and caller metrics, and a stored table
  without them is refused.
- The storage status (missing tables, running mutations), the assembly listing, ``OPTIMIZE``
  and the explicit, batched rebuild of the gene index.
- Counting: per family in one query, scoped to each family's projects; per sample, the
  non-reference calls; scoped to a callset source.
- Writing: an insert writes compact annotations, the gene index and the site QUAL, in chunks
  when large, and VEP's MANE Select and MANE Plus Clinical flags per transcript and per variant
  on every assembly (CLIN-5); the family and sample summaries are rebuilt; a delete is scoped
  to its source, or clears every table without one, and is synchronous.
- Every small-variant mutation stamps a new family data version after it (#509), so the
  prioritised-ranking cache never serves a ranking over changed variants; every SV insert and
  delete, a per-sample SV replace and a snapshot restore stamp a new SV data version (the SV to
  gene index of the "second hit" badge is keyed on it). A version is read as the token count
  and sum.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, NamedTuple

import pytest

from backend.app.services import clickhouse_variant_storage as cvs
from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord
from backend.app.services.genotypes import genotype_vocabulary
from backend.app.services.variant_annotation_parser import (
    AnnotationHeaderState,
    extract_small_variant_annotations,
    update_annotation_header_state,
)


class _Sent(NamedTuple):
    query: str
    params: dict[str, Any] | None
    data: Any


class _ClickHouse:
    """What the storage sends ClickHouse: every statement in ``sent``, as sent, and each
    assembly whose tables it set up first in ``ensured``. ``answer(query)`` gives the rows a
    statement reads (none by default)."""

    def __init__(self) -> None:
        self.sent: list[_Sent] = []
        self.ensured: list[str] = []
        self.answer: Callable[[str], Any] = lambda _query: []

    async def execute(self, query: str, params: dict[str, Any] | None = None, data: Any = None) -> Any:
        self.sent.append(_Sent(query, params, data))
        return self.answer(query)

    async def ensure(self, assembly_name: str) -> None:
        self.ensured.append(assembly_name)

    def statements(self) -> list[str]:
        """Each statement sent, its whitespace collapsed."""
        return [" ".join(sent.query.split()) for sent in self.sent]


@pytest.fixture
def clickhouse(monkeypatch: pytest.MonkeyPatch) -> _ClickHouse:
    """ClickHouse faked at the storage's edge, the assembly's tables taken as set up."""
    fake = _ClickHouse()
    monkeypatch.setattr(cvs, "_execute", fake.execute)
    monkeypatch.setattr(cvs, "ensure_clickhouse_variant_tables", fake.ensure)
    return fake


@pytest.fixture
def clickhouse_tables(monkeypatch: pytest.MonkeyPatch) -> _ClickHouse:
    """ClickHouse faked at the storage's edge, the tables set up as written: the process's
    "already set up" record emptied, so the setup runs."""
    fake = _ClickHouse()
    monkeypatch.setattr(cvs, "_execute", fake.execute)
    monkeypatch.setattr(cvs, "_ensured_variant_table_assemblies", set())
    return fake


def _only_grch38(clickhouse: _ClickHouse) -> None:
    assert clickhouse.ensured and set(clickhouse.ensured) == {"GRCh38"}


_SMALL_VERSION = "SNV_INDEL/family_data_version"
_SV_VERSION = "SV/family_data_version"


def _version_bumps(clickhouse: _ClickHouse, version_table: str, family_uuid: str = "fam-1") -> list[Any]:
    """The rows of each stamp of ``family_uuid``'s data version in ``version_table``."""
    return [
        sent.data
        for sent in clickhouse.sent
        if sent.query.startswith("INSERT INTO")
        and f"/{version_table}" in sent.query
        and sent.data
        and sent.data[0][0] == family_uuid
    ]


def _record(variant_id: str, *, source: str = "clair3", **fields: Any) -> SmallVariantRecord:
    chrom, pos, ref, alt = variant_id.split("-")
    values: dict[str, Any] = {
        "rsid": None,
        "filters": [],
        "gene_symbols": [],
        "annotations": [],
        "calls": [SmallVariantCall(sample="sample-1", gt="0/1", gq=None, dp=None, af=[], ad=[], ps=None)],
    }
    values.update(fields)
    return SmallVariantRecord(
        variant_key=None,
        variant_id=variant_id,
        chr=chrom,
        start=int(pos),
        end=int(pos),
        ref=ref,
        alt=alt,
        source=source,
        **values,
    )


# --- setting the tables up --------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_variant_tables_only_creates_tables(clickhouse_tables: _ClickHouse) -> None:
    """There is no older schema to upgrade (the data is synthetic), so ensuring an
    assembly's tables issues only the identity check and CREATE TABLE statements: no
    ALTER, DROP or materialized view (#679)."""
    clickhouse_tables.answer = lambda _query: None

    await cvs.ensure_clickhouse_variant_tables("GRCh38")

    ddl = [q for q in clickhouse_tables.statements() if not q.startswith("SELECT")]
    assert ddl and all(q.startswith("CREATE TABLE IF NOT EXISTS") for q in ddl)
    created = {q.split("`")[1] for q in ddl}
    expected = {name for _vt, _kind, name in cvs._expected_clickhouse_variant_tables("GRCh38")}
    assert expected <= created


@pytest.mark.asyncio
async def test_ensure_tables_creates_each_family_data_version_table(clickhouse_tables: _ClickHouse) -> None:
    await cvs.ensure_clickhouse_variant_tables("GRCh38")

    for version_table in ("SNV_INDEL/family_data_version", "SV/family_data_version"):
        ddl = [s for s in clickhouse_tables.statements() if f"GRCh38/{version_table}" in s]
        assert len(ddl) == 1, version_table
        # Plain MergeTree: rows must never collapse, or the (count, sum) fingerprint could
        # return to an earlier value.
        assert "CREATE TABLE IF NOT EXISTS" in ddl[0] and "ENGINE = MergeTree" in ddl[0]


@pytest.mark.asyncio
async def test_entries_hold_each_calls_filter_and_metrics(clickhouse_tables: _ClickHouse) -> None:
    # A one-sample VCF's FILTER and caller metrics (TLOD, ...) are its call's own and are
    # kept per call; a rewrite writes them back with every other column.
    await cvs.ensure_clickhouse_variant_tables("GRCh38")

    [create] = [s for s in clickhouse_tables.statements() if "CREATE TABLE" in s and "SNV_INDEL/entries`" in s]
    assert "`calls.filters` Array(Array(LowCardinality(String)))" in create
    assert "`calls.metrics` Array(Map(LowCardinality(String), Float32))" in create
    assert {"calls.filters", "calls.metrics"} <= set(cvs.SMALL_VARIANT_ENTRY_COLUMNS)


@pytest.mark.asyncio
async def test_two_setups_of_one_assembly_at_once_create_its_tables_once(
    clickhouse_tables: _ClickHouse, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two requests of a fresh process: the second waits for the first's setup and then
    # finds the assembly ready, rather than creating every table again.
    async def slow(query: str, params: Any = None, data: Any = None) -> Any:
        await asyncio.sleep(0)
        return await clickhouse_tables.execute(query, params, data)

    monkeypatch.setattr(cvs, "_execute", slow)

    await asyncio.gather(cvs.ensure_clickhouse_variant_tables("GRCh38"), cvs.ensure_clickhouse_variant_tables("GRCh38"))

    creates = [s for s in clickhouse_tables.statements() if s.startswith("CREATE TABLE")]
    assert creates and len(creates) == len(set(creates))
    assert "GRCh38" in cvs._ensured_variant_table_assemblies
    # Ready: a later call sends nothing.
    sent = len(clickhouse_tables.sent)
    await cvs.ensure_clickhouse_variant_tables("GRCh38")
    assert len(clickhouse_tables.sent) == sent


@pytest.mark.asyncio
async def test_an_entries_table_without_the_per_call_columns_is_refused(clickhouse_tables: _ClickHouse) -> None:
    # A table an earlier version created has no per-call FILTER and metrics columns. It is
    # refused, as a table with an older row identity is, rather than failing later on
    # every read: there is no older schema to upgrade in place.
    clickhouse_tables.answer = lambda query: (
        [("GRCh38/SNV_INDEL/entries", ["key", "calls.sampleId", "calls.ps"])] if "system.columns" in query else []
    )

    with pytest.raises(cvs.ClickHouseStorageIdentityError, match="calls.filters, calls.metrics"):
        await cvs.ensure_clickhouse_variant_tables("GRCh38")
    assert "GRCh38" not in cvs._ensured_variant_table_assemblies


# --- the storage's state and maintenance ------------------------------------------------


@pytest.mark.asyncio
async def test_list_clickhouse_variant_assemblies_dedupes_table_prefixes(clickhouse: _ClickHouse) -> None:
    clickhouse.answer = lambda _query: [
        ("GRCh38/SNV_INDEL/entries",),
        ("GRCh38/SV/entries",),
        ("GRCh37/SNV_INDEL/entries",),
    ]

    assemblies = await cvs.list_clickhouse_variant_assemblies()

    assert assemblies == ["GRCh37", "GRCh38"]
    assert all("FROM system.tables" in sent.query for sent in clickhouse.sent)


@pytest.mark.asyncio
async def test_get_clickhouse_variant_storage_status_reports_missing_tables_and_mutations(
    clickhouse: _ClickHouse,
) -> None:
    def answer(query: str) -> list[tuple[Any, ...]]:
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

    clickhouse.answer = answer

    status = await cvs.get_clickhouse_variant_storage_status("GRCh38")

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
        table["name"] == "GRCh38/SV/entries" and table["pending_mutations"] == 2 for table in status["tables"]
    )


@pytest.mark.asyncio
async def test_optimize_clickhouse_variant_tables_skips_materialized_views(
    clickhouse: _ClickHouse, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_status(assembly_name: str) -> dict[str, object]:
        return {"assembly_name": assembly_name, "health": "ready", "tables": []}

    monkeypatch.setattr(cvs, "get_clickhouse_variant_storage_status", fake_status)

    status = await cvs.optimize_clickhouse_variant_tables("GRCh38", final=True)

    assert status["assembly_name"] == "GRCh38"
    queries = [sent.query for sent in clickhouse.sent]
    assert len(queries) == 10
    assert all("OPTIMIZE TABLE" in query for query in queries)
    assert all("FINAL" in query for query in queries)
    assert not any("_mv" in query for query in queries)
    _only_grch38(clickhouse)

    # Without ``final`` the same tables, merged as ClickHouse sees fit.
    clickhouse.sent.clear()
    await cvs.optimize_clickhouse_variant_tables("GRCh38")
    assert [sent.query for sent in clickhouse.sent] == [query.removesuffix(" FINAL") for query in queries]


@pytest.mark.asyncio
async def test_rebuild_small_variant_gene_index_is_explicit_batched_maintenance(
    clickhouse: _ClickHouse, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The shadow table passes CHECK TABLE, as the client returns it.
    clickhouse.answer = lambda query: [[1]] if "check_query_single_value_result = 1" in query else []

    async def fake_status(assembly_name: str) -> dict[str, object]:
        return {"assembly_name": assembly_name, "health": "ready"}

    monkeypatch.setattr(cvs, "get_clickhouse_variant_storage_status", fake_status)

    status = await cvs.rebuild_small_variant_gene_index("GRCh38")

    assert status["assembly_name"] == "GRCh38"
    queries = [sent.query for sent in clickhouse.sent]
    joined = " ".join(queries)
    # The live gene_index is never TRUNCATEd (the old empty-window approach):
    # the index is rebuilt into a shadow table and atomically swapped in.
    assert not any(query.startswith("TRUNCATE") for query in queries)
    assert any("CREATE TABLE coga.`GRCh38/SNV_INDEL/variants/gene_index_rebuild`" in query for query in queries)
    assert any("INSERT INTO coga.`GRCh38/SNV_INDEL/variants/gene_index_rebuild`" in query for query in queries)
    assert "SELECT DISTINCT" in joined
    assert "arrayConcat(gene_symbols, gene_ids)" in joined
    # Validated (CHECK TABLE) before the atomic swap.
    assert any(query.strip().startswith("CHECK TABLE") for query in queries)
    assert any("EXCHANGE TABLES" in query for query in queries)
    _only_grch38(clickhouse)


# --- counting ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_count_family_small_variants_by_family_builds_scoped_group_by(clickhouse: _ClickHouse) -> None:
    clickhouse.answer = lambda _query: [("fam-1", 15), ("fam-2", 7)]

    out = await cvs.count_family_small_variants_by_family(
        "GRCh38",
        family_project_pairs=[("fam-1", "p1"), ("fam-2", "p2")],
        families_without_project=["fam-3"],
    )

    assert out == {"fam-1": 15, "fam-2": 7}
    [(query, params, _data)] = clickhouse.sent
    # Exact per-family project scope via tuple-IN, plus an unscoped OR term.
    assert "(family_guid, project_guid) IN %(family_project_pairs)s" in query
    assert "family_guid IN %(families_without_project)s" in query
    assert "GROUP BY family_guid" in query
    # Counts via the exact nested count(), not approximate count(DISTINCT).
    assert "GROUP BY family_guid, key" in query
    # tuple-of-tuples so clickhouse-connect renders ClickHouse tuple-IN syntax.
    assert params["family_project_pairs"] == (("fam-1", "p1"), ("fam-2", "p2"))
    assert params["families_without_project"] == ("fam-3",)
    # A family's small-variant count is of its direct calls: the imputed callsets are not
    # counted, as in the family's summary.
    assert "lowerUTF8(source) NOT IN %(exclude_sources)s" in query
    assert params["exclude_sources"] == ("glimpse2", "shapeit")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pairs", "without_project", "scope", "left_out"),
    [
        (
            [("fam-1", "p1"), (" ", "p2")],
            [],
            "(family_guid, project_guid) IN %(family_project_pairs)s",
            "families_without_project",
        ),
        ([], ["fam-3", "fam-3", " "], "family_guid IN %(families_without_project)s", "family_project_pairs"),
    ],
    ids=["projects-only", "families-without-a-project-only"],
)
async def test_a_structural_count_by_family_names_only_the_scopes_it_was_given(
    clickhouse: _ClickHouse, pairs, without_project, scope: str, left_out: str
) -> None:
    clickhouse.answer = lambda _query: [("fam-1", 4)]

    out = await cvs.count_family_structural_variants_by_family(
        "GRCh38", family_project_pairs=pairs, families_without_project=without_project
    )

    assert out == {"fam-1": 4}
    [(query, params, _data)] = clickhouse.sent
    assert scope in query and left_out not in query and left_out not in params
    # A blank family is no scope, and a family named twice is counted once; SVs have no
    # imputed callset to leave out.
    assert params.get("family_project_pairs", (("fam-1", "p1"),)) == (("fam-1", "p1"),)
    assert params.get("families_without_project", ("fam-3",)) == ("fam-3",)
    assert "exclude_sources" not in query and "exclude_sources" not in params


@pytest.mark.asyncio
async def test_count_family_by_family_skips_query_when_no_scope(clickhouse: _ClickHouse) -> None:
    out = await cvs.count_family_structural_variants_by_family(
        "GRCh38", family_project_pairs=[], families_without_project=[]
    )
    assert out == {}
    assert clickhouse.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "count", [cvs.count_family_small_variants_by_sample, cvs.count_family_structural_variants_by_sample],
    ids=["small-variants", "structural-variants"],
)
async def test_a_count_by_sample_of_no_sample_sends_no_query(clickhouse: _ClickHouse, count: Any) -> None:
    assert await count("GRCh38", "family-1", sample_ids=["", "  "]) == {}
    assert clickhouse.sent == [] and clickhouse.ensured == []


@pytest.mark.asyncio
async def test_structural_variant_counts_are_scoped_to_their_source_and_projects(clickhouse: _ClickHouse) -> None:
    clickhouse.answer = lambda query: [("PROBAND", 3)] if "ARRAY JOIN" in query else [[5]]

    total = await cvs.count_family_structural_variants("GRCh38", "family-1", project_ids=["p1"], source="sniffles")
    by_sample = await cvs.count_family_structural_variants_by_sample(
        "GRCh38", "family-1", sample_ids=["PROBAND", "PROBAND"], project_ids=["p1"], source="sniffles"
    )

    assert (total, by_sample) == (5, {"PROBAND": 3})
    [(total_query, total_params, _), (sample_query, sample_params, _)] = clickhouse.sent
    for query in (total_query, sample_query):
        assert "sign = 1" in query
        assert "project_guid IN %(project_ids)s" in query and "source = %(source)s" in query
    assert total_params == {"family_guid": "family-1", "project_ids": ("p1",), "source": "sniffles"}
    assert sample_params == {
        "family_guid": "family-1",
        "sample_ids": ("PROBAND",),
        "project_ids": ("p1",),
        "source": "sniffles",
    }
    _only_grch38(clickhouse)


@pytest.mark.asyncio
async def test_the_samples_a_callset_holds_calls_of_are_read_from_its_stored_rows(clickhouse: _ClickHouse) -> None:
    # A per-sample upload without overwrite is refused for a sample whose calls its callset
    # already holds: it asks which samples those are, by sample id or by the uuid a call may
    # be stored under.
    clickhouse.answer = lambda _query: [("PROBAND",), ("uuid-mother",), ("",), (None,)]

    samples = await cvs.family_small_variant_call_samples("GRCh38", "family-1", source="mito")

    assert samples == {"PROBAND", "uuid-mother"}
    [(query, params, _data)] = clickhouse.sent
    query = " ".join(query.split())
    assert "SELECT DISTINCT sample_id FROM coga.`GRCh38/SNV_INDEL/entries`" in query
    assert "ARRAY JOIN `calls.sampleId` AS sample_id" in query
    assert "WHERE family_guid = %(family_guid)s AND sign = 1 AND source = %(source)s" in query
    assert params == {"family_guid": "family-1", "source": "mito"}
    _only_grch38(clickhouse)


@pytest.mark.asyncio
async def test_count_family_small_variants_by_sample_counts_non_reference_calls(clickhouse: _ClickHouse) -> None:
    clickhouse.answer = lambda _query: [("embryo-1", 7)]

    counts = await cvs.count_family_small_variants_by_sample(
        "GRCh38",
        "family-1",
        sample_ids=["embryo-1"],
        project_ids=["project-1"],
    )

    assert counts == {"embryo-1": 7}
    [(query, params, _data)] = clickhouse.sent
    assert "ARRAY JOIN `calls.sampleId` AS sample_id, `calls.gt` AS gt" in query
    assert params == {
        "family_guid": "family-1",
        "sample_ids": ("embryo-1",),
        "project_ids": ("project-1",),
        # Non-reference means "carries an ALT allele" (#511): a haploid "0" is not counted.
        "gt_alt": genotype_vocabulary("het", "hom_alt"),
    }
    _only_grch38(clickhouse)


@pytest.mark.asyncio
async def test_count_family_small_variants_scopes_to_source(clickhouse: _ClickHouse) -> None:
    clickhouse.answer = lambda _query: [[7]]

    count = await cvs.count_family_small_variants("GRCh38", "family-1", source="clair3")

    assert count == 7
    query, params, _data = clickhouse.sent[0]
    assert "source = %(source)s" in query
    assert params["source"] == "clair3"
    _only_grch38(clickhouse)


# --- writing small variants -------------------------------------------------------------


@pytest.mark.asyncio
async def test_insert_small_variant_records_uses_compact_annotations_and_gene_index(clickhouse: _ClickHouse) -> None:
    record = _record(
        "1-100-A-G",
        source="glimpse2",
        gene_symbols=["APC"],
        annotations=[{"gene": "APC", "gene_id": "ENSG00000134982", "impact": "HIGH"}],
    )

    await cvs.insert_small_variant_records("GRCh38", "family-1", ["project-1", "project-2"], [record])

    annotation_query, annotation_data = next(
        (sent.query, sent.data)
        for sent in clickhouse.sent
        if "INSERT INTO coga.`GRCh38/SNV_INDEL/variants/annotations`" in sent.query
    )
    entry_data = next(sent.data for sent in clickhouse.sent if "INSERT INTO coga.`GRCh38/SNV_INDEL/entries`" in sent.query)
    gene_index_data = next(
        sent.data for sent in clickhouse.sent if "INSERT INTO coga.`GRCh38/SNV_INDEL/variants/gene_index`" in sent.query
    )

    assert "annotation_json" not in annotation_query
    assert annotation_data is not None
    assert len(annotation_data) == 1
    assert entry_data is not None
    assert len(entry_data) == 2
    assert gene_index_data is not None
    assert {row[4] for row in gene_index_data} == {"apc", "ensg00000134982"}
    _only_grch38(clickhouse)


@pytest.mark.asyncio
async def test_insert_small_variant_records_stores_site_qual(clickhouse: _ClickHouse) -> None:
    record = _record("1-100-A-G", gene_symbols=["APC"], qual=42.5)

    await cvs.insert_small_variant_records("GRCh38", "family-1", ["project-1"], [record])

    entry_query, entry_data = next(
        (sent.query, sent.data) for sent in clickhouse.sent if "INSERT INTO coga.`GRCh38/SNV_INDEL/entries`" in sent.query
    )
    assert "qual," in entry_query
    assert entry_data is not None
    # qual is the variant-level column inserted immediately after `filters`.
    columns = cvs.SMALL_VARIANT_ENTRY_COLUMNS
    assert columns.index("qual") == columns.index("filters") + 1
    assert entry_data[0][columns.index("qual")] == pytest.approx(42.5)


@pytest.mark.asyncio
async def test_insert_small_variant_records_chunks_large_table_payloads(
    clickhouse: _ClickHouse, monkeypatch: pytest.MonkeyPatch
) -> None:
    for limit in (
        "_SMALL_VARIANT_DETAIL_INSERT_ROWS",
        "_SMALL_VARIANT_ENTRY_INSERT_ROWS",
        "_SMALL_VARIANT_ANNOTATION_INSERT_ROWS",
        "_SMALL_VARIANT_INDEX_INSERT_ROWS",
        "_SMALL_VARIANT_GENE_INDEX_INSERT_ROWS",
    ):
        monkeypatch.setattr(cvs, limit, 2)
    records = [_record(f"1-{100 + index}-A-G", source="glimpse2", gene_symbols=[f"GENE{index}"]) for index in range(3)]

    await cvs.insert_small_variant_records("GRCh38", "family-1", ["project-1"], records)

    # Every statement is an insert of rows.
    assert all(sent.data is not None for sent in clickhouse.sent)

    def sizes_for(table_fragment: str) -> list[int]:
        return [len(sent.data) for sent in clickhouse.sent if table_fragment in sent.query]

    assert sizes_for("variants/details") == [2, 1]
    assert sizes_for("SNV_INDEL/entries") == [2, 1]
    assert sizes_for("variants/annotations") == [2, 1]
    assert sizes_for("variants/annotation_index") == [2, 1]
    assert sizes_for("variants/gene_index") == [2, 1]


@pytest.mark.asyncio
async def test_an_insert_of_no_rows_sends_no_statement(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, Any]] = []

    async def execute_clickhouse(query: str, payload: Any) -> list[Any]:
        sent.append((query, payload))
        return []

    monkeypatch.setattr(cvs, "execute_clickhouse", execute_clickhouse)

    assert await cvs._execute("INSERT INTO t (a) VALUES", data=[]) is None
    await cvs._execute_insert_chunks("INSERT INTO t (a) VALUES", [], chunk_size=2)
    assert sent == []
    # Rows go as a list, a read with its parameters (none: an empty dict).
    await cvs._execute("INSERT INTO t (a) VALUES", data=((1,), (2,)))
    await cvs._execute("SELECT 1")
    assert sent == [("INSERT INTO t (a) VALUES", [(1,), (2,)]), ("SELECT 1", {})]
    # A chunk size that would never end the insert is refused before anything is sent.
    with pytest.raises(ValueError, match="chunk size must be positive"):
        await cvs._execute_insert_chunks("INSERT INTO t (a) VALUES", [(1,)], chunk_size=0)
    assert len(sent) == 2


@pytest.mark.asyncio
async def test_a_replace_without_a_record_deletes_the_sources_calls_and_writes_none(
    clickhouse: _ClickHouse, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_rows_expected(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("no SV row is built without a record")

    monkeypatch.setattr(cvs, "_structural_variant_entry_rows", no_rows_expected)

    await cvs.replace_family_structural_variants("GRCh38", "fam-1", ["p1"], [], source="sniffles")

    assert not any(sent.query.startswith("INSERT INTO") and "/SV/entries" in sent.query for sent in clickhouse.sent)
    assert [sent.params for sent in clickhouse.sent if _synchronous_delete(sent.query)][0] == {
        "family_guid": "fam-1",
        "source": "sniffles",
    }
    assert len(_version_bumps(clickhouse, _SV_VERSION)) == 1


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


def _inserted_rows(clickhouse: _ClickHouse, table: str) -> list[dict[str, object]]:
    """The rows written into ``table`` (``variants/annotations`` …), each as {column: value}."""
    query, data = next((sent.query, sent.data) for sent in clickhouse.sent if f"/{table}` (" in sent.query)
    columns = [column.strip() for column in query.split("(", 1)[1].split(")", 1)[0].split(",")]
    return [dict(zip(columns, row, strict=True)) for row in data or []]


@pytest.mark.asyncio
@pytest.mark.parametrize("assembly", ["GRCh38", "GRCh37", "T2T-CHM13v2.0"])
async def test_insert_small_variant_records_writes_both_mane_flags(clickhouse: _ClickHouse, assembly: str) -> None:
    # CLIN-5: *MANE only* keeps a MANE Select or a MANE Plus Clinical transcript. The family
    # search reads each transcript's flags from the annotations, the Variant Explorer each
    # variant's from the annotation index; the import writes both, on every assembly, from
    # VEP's MANE_SELECT and MANE_PLUS_CLINICAL.
    await cvs.insert_small_variant_records(
        assembly,
        "family-1",
        ["project-1"],
        [
            # The variant's one MANE transcript is MANE Plus Clinical; the canonical one is not MANE.
            _record(
                "1-100-A-G",
                annotations=_vep_annotations(
                    "G|missense_variant|MODERATE|GENE1|ENSG1|Transcript|ENST11|||NM_PLUS1.1",
                    "G|intron_variant|MODIFIER|GENE1|ENSG1|Transcript|ENST12|YES||",
                ),
            ),
            _record(
                "1-200-C-T",
                annotations=_vep_annotations("T|missense_variant|MODERATE|GENE2|ENSG2|Transcript|ENST21|YES|NM_SELECT2.1|"),
            ),
            _record(
                "1-300-G-A",
                annotations=_vep_annotations("A|missense_variant|MODERATE|GENE3|ENSG3|Transcript|ENST31|YES||"),
            ),
        ],
    )

    assert clickhouse.ensured and set(clickhouse.ensured) == {assembly}
    transcripts = _inserted_rows(clickhouse, "variants/annotations")
    assert {row["transcript_id"]: (row["mane_select"], row["mane_plus_clinical"]) for row in transcripts} == {
        "ENST11": (False, True),
        "ENST12": (False, False),
        "ENST21": (True, False),
        "ENST31": (False, False),
    }
    index = _inserted_rows(clickhouse, "variants/annotation_index")
    assert {row["variantId"]: (row["has_mane_select"], row["has_mane_plus_clinical"]) for row in index} == {
        "1-100-A-G": (False, True),
        "1-200-C-T": (True, False),
        "1-300-G-A": (False, False),
    }


@pytest.mark.asyncio
async def test_refresh_family_small_variant_summaries_rebuilds_family_and_sample_counts(
    clickhouse: _ClickHouse,
) -> None:
    await cvs.refresh_family_small_variant_summaries("GRCh38", "family-1")

    # The last statement stamps the family's new data version (#509), the refresh standing
    # for every re-insert and for the snapshot restore.
    *executed, bump = [(sent.query, sent.params) for sent in clickhouse.sent]
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
    _only_grch38(clickhouse)


def _synchronous_delete(query: str) -> bool:
    """An ``ALTER TABLE … DELETE`` that returns once the rows are gone (AGENTS.md, "Deletes
    are synchronous"): the ``entries`` readers filter ``sign = 1`` without ``FINAL``."""
    query = " ".join(query.split())
    return query.startswith("ALTER TABLE") and " DELETE WHERE " in query and query.endswith("SETTINGS mutations_sync = 1")


@pytest.mark.asyncio
async def test_delete_family_small_variants_scopes_entries_to_source(clickhouse: _ClickHouse) -> None:
    await cvs.delete_family_small_variants("GRCh38", "family-1", source="glimpse2")

    # Source-scoped: exactly one DELETE, against entries only, filtered by source, so
    # re-importing glimpse2 cannot touch the clair3 rows. The summary tables are left
    # for the caller's refresh to rebuild from the surviving entries. The delete is
    # followed by the data-version stamp (#509), the family's one.
    *executed, bump = clickhouse.sent
    assert bump.query.startswith("INSERT INTO") and "SNV_INDEL/family_data_version" in bump.query
    assert bump.data[0][0] == "family-1"
    assert len(executed) == 1
    query, params, _data = executed[0]
    assert _synchronous_delete(query)
    assert "entries" in query
    assert "AND source = %(source)s" in query
    assert "family_variant_summary" not in query
    assert params == {"family_guid": "family-1", "source": "glimpse2"}
    _only_grch38(clickhouse)


@pytest.mark.asyncio
async def test_delete_family_small_variants_without_source_clears_all_tables(clickhouse: _ClickHouse) -> None:
    await cvs.delete_family_small_variants("GRCh38", "family-1")

    # Unscoped: clears entries and both summary tables, with no source filter, then
    # stamps the data version (#509), the family's one.
    *executed, bump = clickhouse.sent
    assert bump.query.startswith("INSERT INTO") and "SNV_INDEL/family_data_version" in bump.query
    assert bump.data[0][0] == "family-1"
    assert len(executed) == 3
    assert "entries" in executed[0].query
    assert "family_variant_summary" in executed[1].query
    assert "family_sample_variant_summary" in executed[2].query
    assert all(_synchronous_delete(sent.query) for sent in executed)
    assert all("source = %(source)s" not in sent.query for sent in executed)
    assert all(sent.params == {"family_guid": "family-1"} for sent in executed)


# --- the family data versions (#509) ----------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("source", [None, "needlr"])
async def test_structural_variant_delete_bumps_the_sv_data_version_last(clickhouse: _ClickHouse, source) -> None:
    # The SV→gene index (the small-variant "second hit" badge) is keyed on this version, so
    # an admin delete or the delete half of a per-sample upload must move it.
    await cvs.delete_family_structural_variants("GRCh38", "fam-1", source=source)

    assert len(_version_bumps(clickhouse, _SV_VERSION)) == 1
    assert _SV_VERSION in clickhouse.sent[-1].query
    assert any(_synchronous_delete(sent.query) for sent in clickhouse.sent[:-1])
    # The small-variant version is a different family of data and stays put.
    assert _version_bumps(clickhouse, _SMALL_VERSION) == []


@pytest.mark.asyncio
async def test_small_variant_summary_refresh_bumps_the_family_data_version(clickhouse: _ClickHouse) -> None:
    # The refresh follows every re-insert and the snapshot restore, so it stands for both.
    await cvs.refresh_family_small_variant_summaries("GRCh38", "fam-1")

    assert len(_version_bumps(clickhouse, _SMALL_VERSION)) == 1
    assert _SMALL_VERSION in clickhouse.sent[-1].query


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("insert", "row_builder", "no_entry", "an_entry", "version_table"),
    [
        (
            cvs.insert_small_variant_records,
            "_small_variant_entry_rows",
            ([], [], [], [], []),
            ([], [("row",)], [], [], []),
            _SMALL_VERSION,
        ),
        (
            cvs.insert_structural_variant_records,
            "_structural_variant_entry_rows",
            ([], [], []),
            ([("detail",)], [("lookup",)], [("entry",)]),
            _SV_VERSION,
        ),
    ],
    ids=["small-variants", "structural-variants"],
)
async def test_an_insert_bumps_the_version_only_when_entries_were_written(
    clickhouse: _ClickHouse,
    monkeypatch: pytest.MonkeyPatch,
    insert: Any,
    row_builder: str,
    no_entry: tuple[Any, ...],
    an_entry: tuple[Any, ...],
    version_table: str,
) -> None:
    monkeypatch.setattr(cvs, row_builder, lambda *a, **k: no_entry)
    await insert("GRCh38", "fam-1", ["p1"], [])
    assert _version_bumps(clickhouse, version_table) == []

    monkeypatch.setattr(cvs, row_builder, lambda *a, **k: an_entry)
    await insert("GRCh38", "fam-1", ["p1"], [])
    assert len(_version_bumps(clickhouse, version_table)) == 1
    assert version_table in clickhouse.sent[-1].query


@pytest.mark.asyncio
async def test_each_bump_writes_a_fresh_random_token(clickhouse: _ClickHouse) -> None:
    await cvs.bump_family_small_variant_data_version("GRCh38", "fam-1")
    await cvs.bump_family_small_variant_data_version("GRCh38", "fam-1")

    tokens = [payload[0][1] for payload in _version_bumps(clickhouse, _SMALL_VERSION)]
    assert len(tokens) == 2 and tokens[0] != tokens[1]
    assert all(0 <= token < 2**63 for token in tokens)


@pytest.mark.asyncio
async def test_a_per_sample_sv_replace_moves_the_sv_data_version(
    clickhouse: _ClickHouse, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The per-sample SV upload (and the admin per-sample SV delete) rewrite the family's
    # SVs through replace_family_structural_variants.
    monkeypatch.setattr(
        cvs, "_structural_variant_entry_rows", lambda *a, **k: ([("detail",)], [("lookup",)], [("entry",)])
    )

    await cvs.replace_family_structural_variants("GRCh38", "fam-1", ["p1"], ["record"], source="sniffles")

    assert len(_version_bumps(clickhouse, _SV_VERSION)) == 2  # the delete, then the insert
    assert _SV_VERSION in clickhouse.sent[-1].query


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("read_version", "version_table"),
    [
        (cvs.get_family_small_variant_data_version, _SMALL_VERSION),
        (cvs.get_family_structural_variant_data_version, _SV_VERSION),
    ],
    ids=["small-variants", "structural-variants"],
)
@pytest.mark.parametrize(
    ("rows", "expected"),
    [([(3, 12345)], "3:12345"), ([(0, None)], "0:0"), ([], "0:0")],
    ids=["stamped", "no-token-sum", "no-row"],
)
async def test_a_family_data_version_is_the_token_count_and_sum(
    clickhouse: _ClickHouse, read_version: Any, version_table: str, rows: list[Any], expected: str
) -> None:
    clickhouse.answer = lambda _query: rows

    assert await read_version("GRCh38", "fam-1") == expected
    [(query, params, _data)] = clickhouse.sent
    query = " ".join(query.split())
    assert "SELECT count(), sum(token)" in query
    assert f"GRCh38/{version_table}" in query
    assert params == {"family_guid": "fam-1"}
    _only_grch38(clickhouse)


@pytest.mark.asyncio
async def test_restoring_a_family_snapshot_moves_the_sv_data_version(monkeypatch: pytest.MonkeyPatch) -> None:
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
    monkeypatch.setattr(snapshot_module, "bump_family_structural_variant_data_version", fake_bump, raising=False)
    snapshot = snapshot_module.FamilyClickHouseSnapshot(
        assembly_name="GRCh38",
        family_uuid="fam-1",
        token="t",
        specs=snapshot_module._snapshot_specs("GRCh38", "t"),
    )
    await snapshot_module.restore_family_clickhouse_state(snapshot)
    assert bumped == [("GRCh38", "fam-1")]
