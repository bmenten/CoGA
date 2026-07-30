-- 04_traceability.sql — Import-provenance + append-only, hash-chained clinical audit + integrity

-- ---------------------------------------------------------------------------
-- audit_log_events — HTTP access audit trail (append-only)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    user_email TEXT,
    user_role TEXT,
    method TEXT NOT NULL,
    route_path TEXT,
    path TEXT NOT NULL,
    query_string TEXT,
    status_code INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    remote_ip TEXT,
    user_agent TEXT,
    referer TEXT,
    protocol TEXT,
    request_body JSONB,
    request_meta JSONB NOT NULL DEFAULT '{}'::jsonb,
    db_update JSONB,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_log_events_created_at
    ON audit_log_events (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_log_events_user_email
    ON audit_log_events (user_email);

CREATE INDEX IF NOT EXISTS idx_audit_log_events_route_path
    ON audit_log_events (route_path);

-- Make the access audit trail append-only at the database level so the record
-- of who-accessed-what-when cannot be quietly altered or erased -- a baseline
-- expectation for a PHI system. The application only ever INSERTs here, so this
-- does not affect normal operation.
--
-- DELETE is blocked outright. UPDATE is blocked too, with one carve-out: the
-- `user_id` foreign key is `ON DELETE SET NULL`, so deleting a user account
-- triggers a cascade UPDATE that nulls `user_id` on their audit rows. We allow
-- exactly that nulling (and nothing else) so account removal still works; the
-- denormalised user_email / user_role columns preserve the actor's identity.
CREATE OR REPLACE FUNCTION audit_log_events_block_mutation() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'audit_log_events is append-only; DELETE is not permitted';
    END IF;
    -- UPDATE: permit only the ON DELETE SET NULL user unlink (user_id -> NULL,
    -- every other column unchanged). Compare the rest of the row column-agnostically
    -- so new columns stay protected automatically.
    IF NEW.user_id IS NOT NULL
        OR (to_jsonb(NEW) - 'user_id') IS DISTINCT FROM (to_jsonb(OLD) - 'user_id') THEN
        RAISE EXCEPTION 'audit_log_events is append-only; UPDATE is not permitted';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_log_events_immutable ON audit_log_events;
CREATE TRIGGER audit_log_events_immutable
    BEFORE UPDATE OR DELETE ON audit_log_events
    FOR EACH ROW EXECUTE FUNCTION audit_log_events_block_mutation();

-- ---------------------------------------------------------------------------
-- ui_events — client-side UI interaction events
-- ---------------------------------------------------------------------------
-- Client-side UI interaction events (button/link clicks, in-app navigation).
-- Complements audit_log_events. That table captures every HTTP request the
-- backend handles. This one captures interactions that never reach the backend,
-- so platform usage can be inspected and optimised. Values are masked client- and
-- server-side (identifiers reduced to id, query strings to keys) by
-- routers/ui_events.py before storage.
-- NOTE: the schema runner splits files on the SQL statement separator, so no
-- comment in this file may contain that punctuation character.
CREATE TABLE IF NOT EXISTS ui_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    occurred_at TIMESTAMPTZ,
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    user_email TEXT,
    user_role TEXT,
    event_type TEXT NOT NULL,
    category TEXT,
    label TEXT,
    target_id TEXT,
    path TEXT,
    to_path TEXT,
    href TEXT,
    component TEXT,
    session_id TEXT,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    remote_ip TEXT,
    user_agent TEXT
);

CREATE INDEX IF NOT EXISTS idx_ui_events_created_at
    ON ui_events (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ui_events_user_email
    ON ui_events (user_email);

CREATE INDEX IF NOT EXISTS idx_ui_events_event_type
    ON ui_events (event_type);

CREATE INDEX IF NOT EXISTS idx_ui_events_session_id
    ON ui_events (session_id);

-- ---------------------------------------------------------------------------
-- raw_import_files — raw import file provenance
-- ---------------------------------------------------------------------------
-- Raw import file provenance: tracks every source file used to import data for a
-- family or one of its samples. Supports download, file-size display, and
-- SHA-256 integrity verification from the Admin -> Data -> Families page.
CREATE TABLE IF NOT EXISTS raw_import_files (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    family_id UUID NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    sample_id UUID REFERENCES samples(id) ON DELETE CASCADE,
    scope TEXT NOT NULL CHECK (scope IN ('family', 'individual')),
    dataset TEXT NOT NULL DEFAULT '',
    file_name TEXT NOT NULL,
    file_type TEXT NOT NULL DEFAULT '',
    storage_path TEXT NOT NULL,
    managed BOOLEAN NOT NULL DEFAULT FALSE,
    file_size BIGINT,
    sha256 TEXT,
    source TEXT NOT NULL DEFAULT '',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
);

CREATE INDEX IF NOT EXISTS idx_raw_import_files_family
    ON raw_import_files (family_id, scope);

CREATE INDEX IF NOT EXISTS idx_raw_import_files_sample
    ON raw_import_files (sample_id);

-- A given physical file is recorded once per (family, sample, storage path).
-- Re-imports refresh the existing row instead of accumulating duplicates.
CREATE UNIQUE INDEX IF NOT EXISTS uq_raw_import_files_identity
    ON raw_import_files (
        family_id,
        COALESCE(sample_id, '00000000-0000-0000-0000-000000000000'::uuid),
        storage_path
    );

-- ---------------------------------------------------------------------------
-- family_annotation_manifest — per-family upstream annotation provenance
-- ---------------------------------------------------------------------------
-- Phase 0 of clinical-grade traceability (see docs/clinical-traceability.md):
-- record, per family, which upstream annotation modules/versions produced its
-- annotated input (VEP, ClinVar, gnomAD, dbNSFP, SpliceAI, …) so a report can
-- state its provenance and a sign-out can freeze it.
--
-- `modules` is a free-form map of moduleKey -> {version, ...} so new annotation
-- sources can be recorded without a schema change. One row per family (the
-- current manifest); history lives in the audit trail / report snapshots.
CREATE TABLE IF NOT EXISTS family_annotation_manifest (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    family_id   UUID NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    assembly_id UUID REFERENCES assemblies(id) ON DELETE SET NULL,
    modules     JSONB NOT NULL DEFAULT '{}'::jsonb,
    source      TEXT NOT NULL DEFAULT 'manual',   -- 'manifest' | 'vcf_header' | 'manual'
    recorded_by TEXT,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    UNIQUE (family_id)
);

-- Let the platform reference layer record the source release/version it loaded,
-- so the merged manifest can report e.g. the ClinVar/DGV/clinical-CNV release.
ALTER TABLE reference_dataset_imports
    ADD COLUMN IF NOT EXISTS source_version TEXT,
    ADD COLUMN IF NOT EXISTS source_release_date DATE,
    ADD COLUMN IF NOT EXISTS source_url TEXT;

-- ---------------------------------------------------------------------------
-- clinical_audit_events — immutable, hash-chained clinical action log
-- ---------------------------------------------------------------------------
-- Phase 2 of clinical-grade traceability (see docs/clinical-traceability.md):
-- an immutable, append-only record of clinical decisions — who classified, tagged
-- or annotated which variant, when, and what changed (before -> after).
--
-- This is the *clinical action* log, distinct from audit_log_events (the HTTP
-- access log). Events are written in the same transaction as the change they
-- describe, so the trail can never drift from the data.
--
-- row_hash / prev_hash (P1-4): per-family tamper-evidence hash chain. row_hash binds
-- each row's immutable content to the previous row's hash (per family), so
-- deleting/reordering/editing a row becomes detectable. Historical rows keep NULL
-- hashes — the verifier treats the first non-null row_hash as the chain origin.
CREATE TABLE IF NOT EXISTS clinical_audit_events (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    family_id         UUID REFERENCES families(id) ON DELETE SET NULL,
    family_identifier TEXT,            -- denormalised human family id (survives family deletion)
    variant_id        TEXT,            -- null for family-level events
    actor_id          UUID REFERENCES users(id) ON DELETE SET NULL,
    actor             TEXT NOT NULL,   -- denormalised actor identity (survives account removal)
    action            TEXT NOT NULL,   -- 'classification' | 'tags' | 'note' | 'sign_out' | ...
    summary           TEXT,            -- human-readable one-liner
    before            JSONB,
    after             JSONB,
    metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
    row_hash          TEXT,
    prev_hash         TEXT
);

CREATE INDEX IF NOT EXISTS idx_clinical_audit_events_family
    ON clinical_audit_events (family_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_clinical_audit_events_variant
    ON clinical_audit_events (variant_id);

-- The chain is partitioned + ordered on the IMMUTABLE family_identifier (not family_id,
-- which the ON DELETE SET NULL cascade nulls). Index it to keep the per-write chain-head
-- read and the verifier walk cheap.
CREATE INDEX IF NOT EXISTS idx_clinical_audit_events_chain
    ON clinical_audit_events (family_identifier, created_at DESC, id DESC);

-- Append-only at the database level: DELETE is blocked outright; UPDATE is blocked
-- except for the ON DELETE SET NULL cascades that null actor_id / family_id when an
-- account or family is removed (the denormalised actor / family_identifier preserve
-- the record). Compare the rest of the row column-agnostically so new columns stay
-- protected automatically.
CREATE OR REPLACE FUNCTION clinical_audit_events_block_mutation() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'clinical_audit_events is append-only; DELETE is not permitted';
    END IF;
    IF (NEW.actor_id IS NOT NULL AND NEW.actor_id IS DISTINCT FROM OLD.actor_id)
        OR (NEW.family_id IS NOT NULL AND NEW.family_id IS DISTINCT FROM OLD.family_id)
        OR (to_jsonb(NEW) - 'actor_id' - 'family_id')
             IS DISTINCT FROM (to_jsonb(OLD) - 'actor_id' - 'family_id') THEN
        RAISE EXCEPTION 'clinical_audit_events is append-only; UPDATE is not permitted';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS clinical_audit_events_immutable ON clinical_audit_events;
CREATE TRIGGER clinical_audit_events_immutable
    BEFORE UPDATE OR DELETE ON clinical_audit_events
    FOR EACH ROW EXECUTE FUNCTION clinical_audit_events_block_mutation();

-- ---------------------------------------------------------------------------
-- report_signouts — immutable, hash-chained case sign-out
-- ---------------------------------------------------------------------------
-- Phase 3 of clinical-grade traceability (see docs/clinical-traceability.md):
-- case sign-out. Freeze the reported result to exactly what produced it — the
-- annotation/reference versions, the reported variant list, each classification and
-- its evidence snapshot, and the evidence-drift state at the moment of sign-out — as
-- an immutable, versioned, content-hashed record.
--
-- Each sign-out is a new version (amendments append; nothing is ever overwritten).
-- content_hash is the SHA-256 of the canonical snapshot, so tampering is detectable.
-- row_hash / prev_hash (P1-4) chain content_hash + the immutable identity across a
-- family's sign-out versions; pre-chain rows keep NULL hashes.
CREATE TABLE IF NOT EXISTS report_signouts (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    family_id         UUID REFERENCES families(id) ON DELETE SET NULL,
    family_identifier TEXT,
    version           INTEGER NOT NULL,
    signed_out_by     TEXT NOT NULL,   -- denormalised actor identity (survives account removal)
    signed_out_by_id  UUID REFERENCES users(id) ON DELETE SET NULL,
    signed_out_at     TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    content_hash      TEXT NOT NULL,
    snapshot          JSONB NOT NULL,
    row_hash          TEXT,
    prev_hash         TEXT,
    UNIQUE (family_id, version)
);

CREATE INDEX IF NOT EXISTS idx_report_signouts_family
    ON report_signouts (family_id, version DESC);

-- The chain is partitioned on the IMMUTABLE family_identifier (not family_id, which the
-- ON DELETE SET NULL cascade nulls) so a deleted family's signed history stays
-- verifiable. Index it for the per-sign-out chain-head read and the verifier walk.
CREATE INDEX IF NOT EXISTS idx_report_signouts_chain
    ON report_signouts (family_identifier, version DESC);

-- Append-only at the database level (a signed-out report must never change). DELETE
-- is blocked; UPDATE is blocked except the ON DELETE SET NULL cascades that null
-- signed_out_by_id / family_id when an account or family is removed.
CREATE OR REPLACE FUNCTION report_signouts_block_mutation() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'report_signouts is append-only; DELETE is not permitted';
    END IF;
    IF (NEW.signed_out_by_id IS NOT NULL AND NEW.signed_out_by_id IS DISTINCT FROM OLD.signed_out_by_id)
        OR (NEW.family_id IS NOT NULL AND NEW.family_id IS DISTINCT FROM OLD.family_id)
        OR (to_jsonb(NEW) - 'signed_out_by_id' - 'family_id')
             IS DISTINCT FROM (to_jsonb(OLD) - 'signed_out_by_id' - 'family_id') THEN
        RAISE EXCEPTION 'report_signouts is append-only; UPDATE is not permitted';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS report_signouts_immutable ON report_signouts;
CREATE TRIGGER report_signouts_immutable
    BEFORE UPDATE OR DELETE ON report_signouts
    FOR EACH ROW EXECUTE FUNCTION report_signouts_block_mutation();

-- ---------------------------------------------------------------------------
-- integrity_anchors — external SIGNED CHAIN-HEAD ANCHOR
-- ---------------------------------------------------------------------------
-- P1-4 follow-up: external SIGNED CHAIN-HEAD ANCHOR.
--
-- The per-family hash chains (clinical_audit_events/report_signouts) detect tampering
-- by anyone who cannot recompute them, but an OWNER who DISABLEs the trigger can
-- re-chain an interior edit (recompute row_hash/prev_hash for it and every successor)
-- into a self-consistent chain, and can truncate a chain to zero rows — both pass
-- verify_*_chain. This table periodically records every chain's HEAD (per family, per
-- table) and SIGNS the snapshot with an Ed25519 key the database role does not hold, so
-- re-chaining or truncation SINCE a retained signed anchor becomes detectable by a
-- verifier that does not trust the DB.
--
-- One row = one whole-system anchor (all families, both tables, captured atomically).
-- Append-only. The chain of anchors (prev_anchor_hash/anchor_hash) makes
-- deletion/replacement of an INTERIOR anchor detectable; deletion of the TAIL anchors is
-- only caught by an out-of-band retained copy (the deferred export seam). See
-- services/integrity_anchor_service.py for the honest trust boundary.
CREATE TABLE IF NOT EXISTS integrity_anchors (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    anchor_seq       BIGINT NOT NULL UNIQUE,             -- total order over anchors (clock-independent)
    created_at       TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    prev_anchor_hash TEXT,                               -- anchor_hash of anchor_seq-1 (NULL = genesis)
    anchor_root      TEXT NOT NULL,                      -- sha256 over the canonical sorted heads
    anchor_hash      TEXT NOT NULL,                      -- chain_row_hash(prev_anchor_hash, signed_core)
    heads            JSONB NOT NULL,                     -- sorted [{family_identifier, table, height, head_row_hash}]
    chain_count      INTEGER NOT NULL,                   -- len(heads)
    key_id           TEXT NOT NULL,                      -- signing key id (or 'unsigned')
    algo             TEXT NOT NULL DEFAULT 'ed25519',    -- signature algorithm ('ed25519' | 'unsigned')
    public_key       TEXT,                               -- base64 raw Ed25519 public key (auditor reference)
    signature        TEXT                                -- base64 Ed25519 signature over canonical_json(signed_core)
);

CREATE INDEX IF NOT EXISTS idx_integrity_anchors_seq ON integrity_anchors (anchor_seq DESC);

-- Append-only: a signed anchor must never change. No FK carve-out (no nullable FKs), so
-- ANY update or delete is rejected.
CREATE OR REPLACE FUNCTION integrity_anchors_block_mutation() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'integrity_anchors is append-only; DELETE is not permitted';
    END IF;
    RAISE EXCEPTION 'integrity_anchors is append-only; UPDATE is not permitted';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS integrity_anchors_immutable ON integrity_anchors;
CREATE TRIGGER integrity_anchors_immutable
    BEFORE UPDATE OR DELETE ON integrity_anchors
    FOR EACH ROW EXECUTE FUNCTION integrity_anchors_block_mutation();

-- Runtime-role (coga_app) grants/revokes voor de append-only tabellen
-- zijn geconsolideerd in 05_grants.sql (na de brede GRANT ON ALL TABLES).

-- ---------------------------------------------------------------------------
-- qc_threshold_changes — append-only QC cut-off history
-- ---------------------------------------------------------------------------
-- The table lives in 03_assay (next to the thresholds it records); the
-- immutability trigger lives here with the other append-only guards. A cut-off
-- decides whether the device reports a run as inadequate, so its history must not
-- be rewritable — otherwise a past configuration could be made to look like
-- something other than what was actually in force when a case was released.
-- The FK-to-NULL unlink on user deletion is the one permitted change, mirroring
-- the carve-out the clinical-audit guard makes.
CREATE OR REPLACE FUNCTION qc_threshold_changes_block_mutation() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'qc_threshold_changes is append-only; DELETE is not permitted';
    END IF;
    IF (NEW.changed_by IS NOT NULL AND NEW.changed_by IS DISTINCT FROM OLD.changed_by)
        OR (to_jsonb(NEW) - 'changed_by') IS DISTINCT FROM (to_jsonb(OLD) - 'changed_by') THEN
        RAISE EXCEPTION 'qc_threshold_changes is append-only; UPDATE is not permitted';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS qc_threshold_changes_immutable ON qc_threshold_changes;
CREATE TRIGGER qc_threshold_changes_immutable
    BEFORE UPDATE OR DELETE ON qc_threshold_changes
    FOR EACH ROW EXECUTE FUNCTION qc_threshold_changes_block_mutation();
