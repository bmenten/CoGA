-- DB privilege separation: the restricted runtime role `coga_app`, locked out of the mutations
-- that would let it bypass the append-only and hash-chain controls.
--
-- This is the role the application SHOULD connect as. It closes the owner-bypass gap that the
-- hash chains cannot detect: a NON-owner role cannot `ALTER TABLE ... DISABLE TRIGGER`, cannot
-- `SET session_replication_role` (superuser-only), and — with UPDATE/DELETE revoked below —
-- cannot rewrite or remove audit rows, signed reports or the hash-chain columns. So a runtime
-- attacker cannot re-chain an interior edit, which a self-recomputing OWNER could.
--
-- `coga_app` is created NOLOGIN: the application connects as the table OWNER until a DSN flip
-- grants the role LOGIN and a password (docs/db-runtime-role-runbook.md). The migration step
-- always runs as the OWNER (coga_app cannot create or alter tables — that is the point).
--
-- RESIDUAL RISK: coga_app KEEPS INSERT (the app appends audit rows + signed reports), and the
-- block-mutation triggers fire on UPDATE/DELETE only — so coga_app can still *append* a
-- self-consistent (forged) chain link (chain_row_hash is a public SHA-256). What it cannot do
-- is rewrite/delete an existing row or re-chain an interior edit. Append-forgery is covered by
-- application-layer attribution and the external chain-head anchor (integrity_anchors).
--
-- CREATE ROLE is guarded, as the role is cluster-wide and may exist already (another database
-- on the same server, or a concurrent instance).

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'coga_app') THEN
        BEGIN
            CREATE ROLE coga_app NOLOGIN;
        EXCEPTION WHEN duplicate_object THEN
            -- A concurrent instance won the race (the NOT EXISTS check is not atomic and
            -- the whole schema load runs in one tx); the role now exists, which is all we
            -- need. Swallow it so the rest of the schema-load transaction survives.
            NULL;
        END;
    END IF;
END
$$;

-- Baseline application CRUD on the current schema + future owner-created objects.
GRANT USAGE ON SCHEMA public TO coga_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO coga_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO coga_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO coga_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO coga_app;

-- The schema-migration ledger and the baselines' digest (alembic_version and schema_baselines,
-- 04_traceability.sql): only the migration step writes them, as the owner. coga_app may read
-- which revision and which baselines the database is at, never record either.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON alembic_version, schema_baselines FROM coga_app;

-- Append-only tables: INSERT + SELECT only. Revoking UPDATE/DELETE means the runtime role
-- cannot rewrite or remove existing audit rows, signed reports, integrity anchors, the QC
-- cut-off history, the pedigree's structure versions, the UI-event log or hash-chain
-- columns.
-- TRUNCATE is never granted by GRANT SELECT,INSERT,UPDATE,DELETE in the first place (so the
-- REVOKE of it is belt-and-suspenders against a future hand-grant) — important because
-- TRUNCATE would bypass the BEFORE UPDATE/DELETE triggers. The ON DELETE SET NULL carve-outs
-- still work: a referential action does not require the deleting role to hold UPDATE on the
-- referencing table, so account and family deletion continue to function.
--
-- NOTE: any FUTURE append-only table MUST repeat this REVOKE — the default privileges
-- above otherwise grant the new table UPDATE/DELETE to coga_app. The integration test
-- test_app_role_privileges.py fails for a table with a *_block_mutation trigger that is
-- missing here.
REVOKE UPDATE, DELETE, TRUNCATE ON
    audit_log_events, clinical_audit_events, report_signouts, integrity_anchors,
    qc_threshold_changes, family_structure_versions, ui_events
    FROM coga_app;
REVOKE UPDATE, DELETE, TRUNCATE ON import_delivery_revisions FROM coga_app;
REVOKE UPDATE, DELETE, TRUNCATE ON ancestry_reference_panels, ancestry_results, ancestry_sample_choices FROM coga_app;
REVOKE UPDATE, DELETE, TRUNCATE ON ancestry_files, ancestry_file_scans FROM coga_app;
