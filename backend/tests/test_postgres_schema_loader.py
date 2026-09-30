from __future__ import annotations

from pathlib import Path

from backend.app.core.postgres import _split_sql_script


def test_split_preserves_dollar_quoted_function_body() -> None:
    # A PL/pgSQL function body has semicolons that must not split the statement.
    sql = """
    CREATE TABLE t (id int);  -- trailing comment
    CREATE OR REPLACE FUNCTION f() RETURNS trigger AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'append-only; no deletes';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    DROP TRIGGER IF EXISTS x ON t;
    """
    stmts = _split_sql_script(sql)
    assert len(stmts) == 3
    assert stmts[0].startswith("CREATE TABLE")
    fn = stmts[1]
    assert fn.startswith("CREATE OR REPLACE FUNCTION")
    assert "RAISE EXCEPTION 'append-only; no deletes'" in fn  # internal ; kept
    assert fn.rstrip().endswith("LANGUAGE plpgsql")
    assert stmts[2].startswith("DROP TRIGGER")


def test_split_handles_tagged_dollar_quote() -> None:
    sql = "CREATE FUNCTION g() RETURNS text AS $body$ SELECT 'a;b'; $body$ LANGUAGE sql; SELECT 1;"
    stmts = _split_sql_script(sql)
    assert len(stmts) == 2
    assert "SELECT 'a;b';" in stmts[0] and stmts[0].rstrip().endswith("LANGUAGE sql")
    assert stmts[1] == "SELECT 1"


def test_split_plain_statements_unchanged() -> None:
    stmts = _split_sql_script("CREATE TABLE a (id int);\nCREATE TABLE b (id int);\n")
    assert stmts == ["CREATE TABLE a (id int)", "CREATE TABLE b (id int)"]


def test_traceability_immutable_trigger_parses_with_dollar_quoted_body() -> None:
    # The append-only audit immutability (block-mutation function + trigger) lives in the
    # consolidated 04_traceability.sql. Its dollar-quoted PL/pgSQL body has internal
    # semicolons (RAISE EXCEPTION) that must not split the statement — the bug this guards.
    path = (
        Path(__file__).resolve().parents[1]
        / "db" / "schema" / "postgres" / "04_traceability.sql"
    )
    stmts = _split_sql_script(path.read_text())
    fn = next(
        s
        for s in stmts
        if s.startswith("CREATE OR REPLACE FUNCTION audit_log_events_block_mutation")
    )
    assert "append-only" in fn  # internal ; inside the RAISE EXCEPTION body is kept
    assert fn.rstrip().endswith("LANGUAGE plpgsql")
    assert any(
        s.startswith("DROP TRIGGER IF EXISTS audit_log_events_immutable") for s in stmts
    )
    assert any(s.startswith("CREATE TRIGGER audit_log_events_immutable") for s in stmts)


def test_tag_scope_consolidation_has_no_destructive_backfill_update() -> None:
    # Regression guard (#255): init_postgres_schema() replays every statement on every
    # restart (no migration ledger), so a one-shot UPDATE normalizing rows back to
    # scope='global'/project_id=NULL would silently clobber every legitimately created
    # project-scoped tag on each boot. The project-scoped-tag definition — now folded into
    # the consolidated 03_assay.sql — must contain no such UPDATE; the inline column
    # DEFAULT 'global' + CHECK constraints already cover fresh DBs.
    path = (
        Path(__file__).resolve().parents[1]
        / "db" / "schema" / "postgres" / "03_assay.sql"
    )
    stmts = _split_sql_script(path.read_text())
    kinds = [s.split()[0].upper() for s in stmts]
    assert "UPDATE" not in kinds
    joined = "\n".join(stmts)
    # The folded scope column + constraints + project-links table must be present.
    assert "scope text DEFAULT 'global'::text NOT NULL" in joined
    assert "small_variant_tag_definitions_scope_check" in joined
    assert "small_variant_tag_definitions_scope_project_check" in joined
    assert (
        "CREATE TABLE IF NOT EXISTS small_variant_tag_definition_project_links" in joined
    )
