-- P1-3 (DB privilege separation — single-role-fallback): create the restricted runtime
-- role `coga_app` and lock it out of the mutations that would let it bypass the
-- append-only + hash-chain controls.
--
-- This is the role the application SHOULD connect as. It closes the owner-bypass gap that
-- the P1-4 hash chain cannot detect: a NON-owner role cannot `ALTER TABLE ... DISABLE
-- TRIGGER`, cannot `SET session_replication_role` (superuser-only), and — with UPDATE/
-- DELETE revoked below — cannot rewrite or remove audit rows, signed reports or the
-- hash-chain columns. So a determined runtime attacker can no longer re-chain an interior
-- edit (which a self-recomputing OWNER still could).
--
-- SHIPPED IN FALLBACK MODE: `coga_app` is created NOLOGIN and the application still
-- connects as the table OWNER until a coordinated DSN flip grants it LOGIN + a password
-- (see docs/db-runtime-role-runbook.md). This migration is therefore additive and changes
-- no runtime behaviour. Migrations must keep running as the OWNER (coga_app cannot create
-- or alter tables — that is the point).
--
-- RESIDUAL RISK: coga_app KEEPS INSERT (the app appends audit rows + signed reports), and
-- the block-mutation triggers fire on UPDATE/DELETE only — so coga_app can still *append* a
-- self-consistent (forged) chain link (chain_row_hash is a public SHA-256). What it cannot
-- do is rewrite/delete an existing row or re-chain an interior edit. Append-forgery is
-- covered by application-layer attribution + the deferred external chain-head anchor.
--
-- Idempotent: the schema loader re-runs every file on startup; CREATE ROLE is guarded and
-- GRANT/REVOKE are naturally idempotent.

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

-- Append-only tables: INSERT + SELECT only. Revoking UPDATE/DELETE means the runtime role
-- cannot rewrite or remove existing audit rows, signed reports, integrity anchors, the QC
-- cut-off history or hash-chain columns.
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
    qc_threshold_changes
    FROM coga_app;
