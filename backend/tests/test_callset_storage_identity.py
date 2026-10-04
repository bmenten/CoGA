"""A stored variant row is one callset's, and startup refuses tables that forget it (unit).

A small variant's storage key is the variant (``hash(assembly, variantId)``), shared by
every family and callset so they all find the one annotation. A family's ``entries`` rows
were sorted by ``(project, family, position, key)`` only, so a clair3 row and a GLIMPSE2
row for one variant had the same sort key, and the CollapsingMergeTree kept one of them
when ClickHouse merged the parts. The row identity now ends with the callset (``source``).

An SV's key hashed only the family and the variant id, and a per-sample upload's id
(``chrom-start-end-type---``) names no caller, so a Sniffles and a Spectre call at the same
coordinates shared a key, an ``entries`` sort key, a ``variants/details`` row and a
``key_lookup`` row. The key now hashes the source too; the variant id is unchanged, since
reviews, drift snapshots, the ranking cache and the SV gene index attach by it.

A table created with an older sort key is detected: startup and the table ensure refuse it
with a message saying what to do. ``e2e/test_e2e_callset_storage_identity.py`` shows the
same on real ClickHouse, with the part merge that used to drop the direct call.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.app import main as app_main
from backend.app.services import clickhouse_small_variants
from backend.app.services import clickhouse_variant_storage as cvs
from backend.app.services.clickhouse_variant_ids import (
    small_variant_key,
    structural_variant_key,
)
from backend.app.services.clickhouse_variant_queries import IMPUTED_SMALL_VARIANT_SOURCES
from backend.app.services.clickhouse_variant_records import (
    SmallVariantCall,
    SmallVariantRecord,
    StructuralVariantCall,
    StructuralVariantRecord,
)
from backend.app.services.clickhouse_variant_rows import (
    _small_variant_entry_rows,
    _structural_variant_entry_rows,
)

_OLD_SMALL_KEY = "project_guid, family_guid, xpos, key"
_NEW_SMALL_KEY = "project_guid, family_guid, xpos, key, source"
_OLD_SV_KEY = "project_guid, family_guid, svType, chrom, start, key"
_NEW_SV_KEY = "project_guid, family_guid, svType, chrom, start, key, source"
_OLD_LOOKUP_KEY = "family_guid, variantId"
_NEW_LOOKUP_KEY = "family_guid, variantId, source"


def _small(source: str, gt: str) -> SmallVariantRecord:
    return SmallVariantRecord(
        variant_key=None,
        variant_id="1-3000-G-A",
        chr="1",
        start=3000,
        end=3000,
        ref="G",
        alt="A",
        source=source,
        rsid=None,
        filters=[],
        gene_symbols=["GENE_DENOVO"],
        annotations=[{"gene": "GENE_DENOVO", "impact": "MODERATE"}],
        calls=[SmallVariantCall(sample="PROBAND", gt=gt, gq=None, dp=None, af=[], ad=[], ps=None)],
    )


def _sv(source: str, *, variant_key: int | None = None) -> StructuralVariantRecord:
    return StructuralVariantRecord(
        variant_key=variant_key,
        variant_id="1-30000-31000-DEL---",
        chr="1",
        start=30000,
        end=31000,
        sv_type="DEL",
        source=source,
        remote_chr=None,
        remote_start=None,
        remote_end=None,
        sv_len=-1000,
        filters=["PASS"],
        gene_symbols=[],
        annotations=[],
        calls=[StructuralVariantCall(sample="PROBAND", gt="0/1", qual=40.0, read_support=9, filter="PASS")],
    )


def _capture_ddl(monkeypatch, *, tables: list[tuple[str, str]] | None = None) -> list[str]:
    """Run the real table ensure over a fake ClickHouse whose ``system.tables`` lists
    ``tables`` (name, sorting_key); return every statement it issued."""
    statements: list[str] = []

    async def fake_execute(query, params=None, data=None):
        text = " ".join(query.split())
        statements.append(text)
        if "FROM system.tables" in text and "sorting_key" in text:
            names = set((params or {}).get("names") or ())
            return [row for row in tables or [] if row[0] in names]
        return []

    monkeypatch.setattr(cvs, "_execute", fake_execute)
    monkeypatch.setattr(cvs, "_ensured_variant_table_assemblies", set())
    return statements


def _create_statement(statements: list[str], table: str) -> str:
    matches = [s for s in statements if s.startswith("CREATE TABLE IF NOT EXISTS") and f"`{table}`" in s]
    assert len(matches) == 1, matches
    return matches[0]


# --- The row identity in the table definitions -------------------------------------------


@pytest.mark.asyncio
async def test_small_variant_entries_are_sorted_by_variant_and_callset(monkeypatch) -> None:
    statements = _capture_ddl(monkeypatch)
    await cvs.ensure_clickhouse_variant_tables("GRCh38")
    ddl = _create_statement(statements, "GRCh38/SNV_INDEL/entries")
    # Before the fix: ORDER BY (project_guid, family_guid, xpos, key).
    assert f"ORDER BY ({_NEW_SMALL_KEY})" in ddl
    assert "ENGINE = CollapsingMergeTree(sign)" in ddl


@pytest.mark.asyncio
async def test_structural_variant_entries_are_sorted_by_variant_and_source(monkeypatch) -> None:
    statements = _capture_ddl(monkeypatch)
    await cvs.ensure_clickhouse_variant_tables("GRCh38")
    ddl = _create_statement(statements, "GRCh38/SV/entries")
    assert f"ORDER BY ({_NEW_SV_KEY})" in ddl
    assert "ENGINE = CollapsingMergeTree(sign)" in ddl


@pytest.mark.asyncio
async def test_sv_key_lookup_keeps_one_row_per_variant_and_source(monkeypatch) -> None:
    statements = _capture_ddl(monkeypatch)
    await cvs.ensure_clickhouse_variant_tables("GRCh38")
    ddl = _create_statement(statements, "GRCh38/SV/key_lookup")
    assert "`source` LowCardinality(String)" in ddl
    assert f"ORDER BY ({_NEW_LOOKUP_KEY})" in ddl


# --- The keys -------------------------------------------------------------------------------


def test_a_small_variant_has_one_key_whatever_the_callset() -> None:
    direct = _small("clair3", "0/1")
    imputed = _small("glimpse2", "1|1")
    _details, entries, _annotations, _index, _genes = _small_variant_entry_rows(
        "GRCh38", "fam-1", ["p1"], [direct, imputed]
    )
    # key, variantId and source are the first, second and thirteenth entry columns.
    assert [(row[0], row[1], row[12]) for row in entries] == [
        (small_variant_key("GRCh38", "1-3000-G-A"), "1-3000-G-A", "clair3"),
        (small_variant_key("GRCh38", "1-3000-G-A"), "1-3000-G-A", "glimpse2"),
    ]


def test_an_sv_key_names_the_source_and_the_variant_id_does_not() -> None:
    sniffles = structural_variant_key("GRCh38", "fam-1", "1-30000-31000-DEL---", source="sniffles")
    spectre = structural_variant_key("GRCh38", "fam-1", "1-30000-31000-DEL---", source="spectre")
    assert sniffles != spectre
    # Stable: the same call always gets the same key.
    assert sniffles == structural_variant_key("GRCh38", "fam-1", "1-30000-31000-DEL---", source="sniffles")
    assert structural_variant_key("GRCh38", "fam-2", "1-30000-31000-DEL---", source="sniffles") != sniffles

    details, lookups, entries = _structural_variant_entry_rows(
        "GRCh38", "fam-1", ["p1"], [_sv("sniffles"), _sv("spectre")]
    )
    # Before the fix both rows got one key, so the details, the lookup and the entries of
    # the two callers collided.
    assert [row[0] for row in details] == [sniffles, spectre]
    assert [row[0] for row in entries] == [sniffles, spectre]
    assert [row[1] for row in entries] == ["1-30000-31000-DEL---", "1-30000-31000-DEL---"]
    assert [row[9] for row in entries] == ["sniffles", "spectre"]
    assert lookups == [
        ("fam-1", "1-30000-31000-DEL---", "sniffles", sniffles),
        ("fam-1", "1-30000-31000-DEL---", "spectre", spectre),
    ]


def test_a_stored_sv_row_written_back_keeps_its_key() -> None:
    # A rewrite (per-sample upload, admin delete) writes stored rows back under their key.
    details, lookups, entries = _structural_variant_entry_rows(
        "GRCh38", "fam-1", ["p1"], [_sv("sniffles", variant_key=12345)]
    )
    assert details[0][0] == entries[0][0] == lookups[0][3] == 12345


@pytest.mark.asyncio
async def test_the_key_lookup_insert_names_the_source(monkeypatch) -> None:
    issued: list[tuple[str, Any]] = []

    async def fake_execute(query, params=None, data=None):
        issued.append((" ".join(query.split()), data))
        return []

    async def fake_ensure(_assembly):
        return None

    monkeypatch.setattr(cvs, "_execute", fake_execute)
    monkeypatch.setattr(cvs, "ensure_clickhouse_variant_tables", fake_ensure)
    await cvs.insert_structural_variant_records("GRCh38", "fam-1", ["p1"], [_sv("spectre")])
    lookup = [(query, data) for query, data in issued if "SV/key_lookup" in query]
    assert len(lookup) == 1
    query, data = lookup[0]
    assert "(family_guid, variantId, source, key)" in query
    assert data == [
        (
            "fam-1",
            "1-30000-31000-DEL---",
            "spectre",
            structural_variant_key("GRCh38", "fam-1", "1-30000-31000-DEL---", source="spectre"),
        )
    ]


# --- Detecting a table with an older row identity ----------------------------------------------


@pytest.mark.asyncio
async def test_ensure_accepts_tables_with_the_current_row_identity(monkeypatch) -> None:
    statements = _capture_ddl(
        monkeypatch,
        tables=[
            ("GRCh38/SNV_INDEL/entries", _NEW_SMALL_KEY),
            ("GRCh38/SV/entries", _NEW_SV_KEY),
            # ClickHouse may quote an identifier; the check reads the columns, not the text.
            ("GRCh38/SV/key_lookup", "`family_guid`, `variantId`, `source`"),
        ],
    )
    await cvs.ensure_clickhouse_variant_tables("GRCh38")
    assert any(s.startswith("CREATE TABLE IF NOT EXISTS") for s in statements)
    assert "GRCh38" in cvs._ensured_variant_table_assemblies


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("table", "old_key", "new_key"),
    [
        ("GRCh38/SNV_INDEL/entries", _OLD_SMALL_KEY, _NEW_SMALL_KEY),
        ("GRCh38/SV/entries", _OLD_SV_KEY, _NEW_SV_KEY),
        ("GRCh38/SV/key_lookup", _OLD_LOOKUP_KEY, _NEW_LOOKUP_KEY),
    ],
)
async def test_ensure_refuses_a_table_with_an_older_row_identity(
    monkeypatch, table, old_key, new_key
) -> None:
    statements = _capture_ddl(monkeypatch, tables=[(table, old_key)])
    with pytest.raises(cvs.ClickHouseStorageIdentityError) as excinfo:
        await cvs.ensure_clickhouse_variant_tables("GRCh38")
    message = str(excinfo.value)
    assert f"`{table}`" in message
    assert f"({old_key})" in message and f"({new_key})" in message
    assert "re-import" in message and "docs/database.md" in message
    # Nothing is created or altered next to the refused table, and the assembly is not
    # marked ready: the next call checks again and refuses again.
    assert not [s for s in statements if s.startswith(("CREATE", "ALTER", "DROP"))]
    assert "GRCh38" not in cvs._ensured_variant_table_assemblies
    with pytest.raises(cvs.ClickHouseStorageIdentityError):
        await cvs.ensure_clickhouse_variant_tables("GRCh38")


@pytest.mark.asyncio
async def test_startup_check_refuses_an_older_table_of_any_assembly(monkeypatch) -> None:
    tables = [
        ("GRCh38/SNV_INDEL/entries", _NEW_SMALL_KEY),
        ("GRCh38/SV/entries", _NEW_SV_KEY),
        ("GRCh38/SV/key_lookup", _NEW_LOOKUP_KEY),
        ("T2T_CHM13v2.0/SNV_INDEL/entries", _OLD_SMALL_KEY),
        ("T2T_CHM13v2.0/SV/entries", _OLD_SV_KEY),
    ]
    queries: list[dict[str, Any]] = []

    async def fake_execute(query, params=None, data=None):
        text = " ".join(query.split())
        if "FROM system.tables" in text and "sorting_key" in text:
            queries.append(dict(params or {}))
            names = set((params or {}).get("names") or ())
            return [row for row in tables if row[0] in names]
        raise AssertionError(f"unexpected query {text}")

    async def fake_assemblies():
        return ["GRCh38", "T2T_CHM13v2.0"]

    monkeypatch.setattr(cvs, "_execute", fake_execute)
    monkeypatch.setattr(cvs, "list_clickhouse_variant_assemblies", fake_assemblies)
    with pytest.raises(cvs.ClickHouseStorageIdentityError) as excinfo:
        await cvs.verify_clickhouse_variant_storage_identity()
    message = str(excinfo.value)
    assert "`T2T_CHM13v2.0/SNV_INDEL/entries`" in message
    assert "`T2T_CHM13v2.0/SV/entries`" in message
    assert "GRCh38/" not in message
    # One bound query over every assembly's tables, no text interpolated into the SQL.
    assert len(queries) == 1
    assert "GRCh38/SNV_INDEL/entries" in queries[0]["names"]
    assert "T2T_CHM13v2.0/SV/key_lookup" in queries[0]["names"]


@pytest.mark.asyncio
async def test_startup_check_passes_on_current_tables_and_ignores_import_snapshots(monkeypatch) -> None:
    # A package import's backup copies (``<assembly>/SNAPSHOT/<token>/…``) are never served;
    # one left behind by a crash must not stop the start.
    tables = [
        ("GRCh38/SNV_INDEL/entries", _NEW_SMALL_KEY),
        ("GRCh38/SV/entries", _NEW_SV_KEY),
        ("GRCh38/SNAPSHOT/abc123/SNV_INDEL/entries", _OLD_SMALL_KEY),
        ("GRCh38/SNAPSHOT/abc123/SV/entries", _OLD_SV_KEY),
    ]

    async def fake_execute(query, params=None, data=None):
        names = set((params or {}).get("names") or ())
        if "system.columns" in query:
            # The current entries table has every column, the per-call ones included.
            return [(name, list(cvs.SMALL_VARIANT_ENTRY_COLUMNS)) for name in sorted(names)]
        return [row for row in tables if row[0] in names]

    async def fake_assemblies():
        return ["GRCh38"]

    monkeypatch.setattr(cvs, "_execute", fake_execute)
    monkeypatch.setattr(cvs, "list_clickhouse_variant_assemblies", fake_assemblies)
    await cvs.verify_clickhouse_variant_storage_identity()


@pytest.mark.asyncio
async def test_startup_check_needs_no_query_without_variant_tables(monkeypatch) -> None:
    async def fake_execute(query, params=None, data=None):
        raise AssertionError("no query expected")

    async def fake_assemblies():
        return []

    monkeypatch.setattr(cvs, "_execute", fake_execute)
    monkeypatch.setattr(cvs, "list_clickhouse_variant_assemblies", fake_assemblies)
    await cvs.verify_clickhouse_variant_storage_identity()


# --- Startup runs the check before anything is served or written -------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def step(self, name: str, *, raises: Exception | None = None):
        async def _step(*_args, **_kwargs):
            self.calls.append(name)
            if raises is not None:
                raise raises

        return _step


def _patch_startup(monkeypatch, recorder: _Recorder, *, identity_error: Exception | None) -> None:
    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    for name in (
        "wait_for_postgres",
        "init_postgres_schema",
        "init_postgres_admin_user",
        "start_audit_log_worker",
        "start_ui_event_worker",
        "seed_builtin_repeat_catalog",
        "ensure_human_grch38_reference_on_startup",
        "ensure_human_t2t_reference_on_startup",
        "ensure_hpo_ontology_on_startup",
        "seed_builtin_reference_tracks",
        "queue_startup_gene_reference_refresh_if_needed",
        "wait_for_clickhouse",
        "init_clickhouse_schema",
        "start_clickhouse_integrity_monitor",
        "stop_gene_reference_worker",
        "stop_family_package_import_worker",
        "stop_clickhouse_integrity_monitor",
        "stop_audit_log_worker",
        "stop_ui_event_worker",
        "close_clickhouse_client",
        "close_postgres_engine",
    ):
        monkeypatch.setattr(app_main, name, recorder.step(name))
    monkeypatch.setattr(
        app_main,
        "verify_clickhouse_variant_storage_identity",
        recorder.step("verify_clickhouse_variant_storage_identity", raises=identity_error),
        raising=False,
    )
    monkeypatch.setattr(app_main, "gene_reference_refresh_worker", recorder.step("gene_reference_refresh_worker"))
    monkeypatch.setattr(app_main, "family_package_import_worker", recorder.step("family_package_import_worker"))
    monkeypatch.setattr(app_main, "get_postgres_sessionmaker", lambda: _Session)
    monkeypatch.setattr(app_main.settings, "family_import_worker_count", 1)


class _App:
    class state:  # mirrors FastAPI's app.state attribute
        skip_startup_tasks = False


@pytest.mark.asyncio
async def test_startup_checks_the_row_identity_right_after_the_clickhouse_schema(monkeypatch) -> None:
    recorder = _Recorder()
    _patch_startup(monkeypatch, recorder, identity_error=None)
    async with app_main.lifespan(_App()):
        started = list(recorder.calls)
    assert "verify_clickhouse_variant_storage_identity" in started
    check = started.index("verify_clickhouse_variant_storage_identity")
    assert started[check - 1] == "init_clickhouse_schema"
    assert started.index("start_clickhouse_integrity_monitor") > check


@pytest.mark.asyncio
async def test_startup_stops_when_a_table_has_an_older_row_identity(monkeypatch) -> None:
    recorder = _Recorder()
    _patch_startup(
        monkeypatch,
        recorder,
        identity_error=cvs.ClickHouseStorageIdentityError("old row identity"),
    )
    with pytest.raises(cvs.ClickHouseStorageIdentityError):
        async with app_main.lifespan(_App()):
            pytest.fail("the app must not serve")  # pragma: no cover
    # No integrity sweep, gene refresh or package import starts on the refused tables.
    assert "start_clickhouse_integrity_monitor" not in recorder.calls
    assert "family_package_import_worker" not in recorder.calls
    assert "gene_reference_refresh_worker" not in recorder.calls


# --- One variant, several callsets: the per-variant lookup reads the direct call ---------------


@pytest.mark.asyncio
async def test_the_family_record_of_a_variant_is_its_direct_call(monkeypatch) -> None:
    # Reviews (the compound-het genotype checks) and the drift check read one record per
    # variant id. With a clair3 and a GLIMPSE2 row both stored, an unordered LIMIT 1
    # returned either; the direct call must win, then the callset name.
    captured: dict[str, Any] = {}

    async def fake_execute(query, params=None):
        captured["query"] = " ".join(query.split())
        captured["params"] = params
        return []

    monkeypatch.setattr(clickhouse_small_variants, "execute_clickhouse", fake_execute)
    await clickhouse_small_variants.get_small_variant_family_record(
        assembly_name="GRCh38", family_guid="fam-1", variant_id="1-3000-G-A"
    )
    query = captured["query"]
    assert "ORDER BY lowerUTF8(e.source) IN %(imputed_sources)s, e.source LIMIT 1" in query
    assert captured["params"] == {
        "family_guid": "fam-1",
        "variant_id": "1-3000-G-A",
        "imputed_sources": tuple(IMPUTED_SMALL_VARIANT_SOURCES),
    }
