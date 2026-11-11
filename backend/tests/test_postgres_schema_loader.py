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


def test_split_ignores_a_semicolon_in_a_line_comment() -> None:
    # Comments are dropped before the split, so a baseline's comment may hold a semicolon.
    sql = """
    -- a note; with a semicolon
    CREATE TABLE a (
        id int  -- the key; never null
    );
    """
    assert _split_sql_script(sql) == ["CREATE TABLE a (\n        id int  \n    )"]


def test_traceability_immutable_trigger_parses_with_dollar_quoted_body() -> None:
    # The append-only audit immutability (block-mutation function + trigger) lives in
    # 04_traceability.sql. Its dollar-quoted PL/pgSQL body has internal semicolons
    # (RAISE EXCEPTION) that must not split the statement.
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


def test_the_tag_definitions_baseline_holds_no_update_of_rows() -> None:
    # The baselines make an empty database: they define the tables, their defaults and checks,
    # and update no row. Project-scoped tags rest on the scope column's DEFAULT 'global' and its
    # CHECK constraints.
    path = (
        Path(__file__).resolve().parents[1]
        / "db" / "schema" / "postgres" / "03_assay.sql"
    )
    stmts = _split_sql_script(path.read_text())
    kinds = [s.split()[0].upper() for s in stmts]
    assert "UPDATE" not in kinds
    joined = "\n".join(stmts)
    # The scope column, its constraints and the project-links table are there.
    assert "scope text DEFAULT 'global'::text NOT NULL" in joined
    assert "small_variant_tag_definitions_scope_check" in joined
    assert "small_variant_tag_definitions_scope_project_check" in joined
    assert (
        "CREATE TABLE IF NOT EXISTS small_variant_tag_definition_project_links" in joined
    )
