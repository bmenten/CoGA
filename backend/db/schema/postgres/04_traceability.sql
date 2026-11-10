-- 04_traceability.sql — Import-provenance + append-only, hash-chained clinical audit + integrity

-- ---------------------------------------------------------------------------
-- alembic_version — the schema-migration ledger
-- ---------------------------------------------------------------------------
-- One row: the Alembic revision (backend/db/migrations/versions) the database is at. Only
-- the migration step writes it, as the owner (backend/app/core/postgres_migrations.py);
-- 05_grants.sql leaves coga_app read access only. Alembic would create the same table; it is
-- created here so that 05_grants.sql finds it. The ledger is empty only in an empty database,
-- which revision 0001 makes from these baselines.
CREATE TABLE IF NOT EXISTS alembic_version (
    version_num VARCHAR(32) NOT NULL,
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- ---------------------------------------------------------------------------
-- schema_baselines — the baseline files the database was made from
-- ---------------------------------------------------------------------------
-- One row: the SHA-256 of the five baseline files (baseline_digest in
-- backend/app/core/postgres_migrations.py), which revision 0001 writes when it makes the
-- database. Every start compares it with its own baselines and refuses a database made from
-- other ones: until the release candidate a schema change is an edit of a baseline, and a
-- database is made again from empty rather than migrated (docs/database.md, "Schema
-- Migrations"). Only the migration step writes it, as the owner; 05_grants.sql leaves coga_app
-- read access only.
CREATE TABLE IF NOT EXISTS schema_baselines (
    singleton boolean DEFAULT true NOT NULL,
    digest text NOT NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT schema_baselines_pkey PRIMARY KEY (singleton),
    CONSTRAINT schema_baselines_singleton_check CHECK (singleton),
    CONSTRAINT schema_baselines_digest_check CHECK ((digest ~ '^[0-9a-f]{64}$'::text))
);

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
    error TEXT,
    -- The event's own id, which the backend gives it when it makes the event, before the event
    -- is queued. Every write of the event carries it, and the writer's INSERT does nothing for
    -- an id the table holds (ON CONFLICT (event_id) DO NOTHING): a batch written again after a
    -- commit whose outcome was unknown stores each event once.
    event_id UUID NOT NULL UNIQUE
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
    user_agent TEXT,
    -- The event's own id, as in audit_log_events: a batch written again after a commit whose
    -- outcome was unknown stores each event once.
    event_id UUID NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_ui_events_created_at
    ON ui_events (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ui_events_user_email
    ON ui_events (user_email);

CREATE INDEX IF NOT EXISTS idx_ui_events_event_type
    ON ui_events (event_type);

CREATE INDEX IF NOT EXISTS idx_ui_events_session_id
    ON ui_events (session_id);

-- Append-only like audit_log_events, which it complements: the application only
-- ever INSERTs here, and no job deletes or rewrites a UI event. DELETE is blocked
-- outright, UPDATE too except the ON DELETE SET NULL unlink of user_id when an account is
-- removed (user_email and user_role keep the actor). It is not hash-chained: its rows are
-- what the browser reports of itself, not the device's own record of a clinical action,
-- and the HTTP audit log beside it is not chained either.
CREATE OR REPLACE FUNCTION ui_events_block_mutation() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'ui_events is append-only; DELETE is not permitted';
    END IF;
    IF NEW.user_id IS NOT NULL
        OR (to_jsonb(NEW) - 'user_id') IS DISTINCT FROM (to_jsonb(OLD) - 'user_id') THEN
        RAISE EXCEPTION 'ui_events is append-only; UPDATE is not permitted';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS ui_events_immutable ON ui_events;
CREATE TRIGGER ui_events_immutable
    BEFORE UPDATE OR DELETE ON ui_events
    FOR EACH ROW EXECUTE FUNCTION ui_events_block_mutation();

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
-- row_hash / prev_hash: per-family tamper-evidence hash chain. row_hash binds
-- each row's immutable content to the previous row's hash (per family), so
-- deleting/reordering/editing a row becomes detectable. prev_hash is NULL in the
-- first event of a chain.
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
    row_hash          TEXT NOT NULL,
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
-- row_hash / prev_hash chain content_hash + the immutable identity across a
-- family's sign-out versions; prev_hash is NULL in a family's first version.
CREATE TABLE IF NOT EXISTS report_signouts (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    family_id         UUID REFERENCES families(id) ON DELETE SET NULL,
    family_identifier TEXT NOT NULL,
    version           INTEGER NOT NULL,
    signed_out_by     TEXT NOT NULL,   -- denormalised actor identity (survives account removal)
    signed_out_by_id  UUID REFERENCES users(id) ON DELETE SET NULL,
    signed_out_at     TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    content_hash      TEXT NOT NULL,
    snapshot          JSONB NOT NULL,
    row_hash          TEXT NOT NULL,
    prev_hash         TEXT,
    UNIQUE (family_id, version)
);

CREATE INDEX IF NOT EXISTS idx_report_signouts_family
    ON report_signouts (family_id, version DESC);

-- The chain is partitioned on the IMMUTABLE family_identifier (not family_id, which the
-- ON DELETE SET NULL cascade nulls) so a deleted family's signed history stays
-- verifiable, and it is ordered by version. A family deleted and made again under its ID
-- signs on the same chain, its versions numbered on from the deleted family's, so a
-- version names one signed record of the family ID, and so does its report code. This
-- unique index holds that: UNIQUE (family_id, version) above cannot, as the family's
-- deletion nulls family_id. It also serves the chain-head read and the verifier walk.
CREATE UNIQUE INDEX IF NOT EXISTS uq_report_signouts_family_identifier_version
    ON report_signouts (family_identifier, version);

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
-- The per-family hash chains (clinical_audit_events/report_signouts/
-- family_structure_versions) detect tampering
-- by anyone who cannot recompute them, but an OWNER who DISABLEs the trigger can
-- re-chain an interior edit (recompute row_hash/prev_hash for it and every successor)
-- into a self-consistent chain, and can truncate a chain to zero rows — both pass
-- verify_*_chain. This table records every chain's HEAD (per family, per table) each time
-- an administrator takes an anchor (POST /api/admin/integrity/anchor, which nothing
-- schedules yet, OPS-7) and SIGNS the snapshot with an Ed25519 key the database role does
-- not hold, so re-chaining or truncation SINCE a retained signed anchor becomes detectable
-- by a verifier that does not trust the DB.
--
-- One row = one whole-system anchor (all families, every chained table, captured atomically).
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

-- The runtime role's (coga_app) grants and REVOKEs for the append-only tables are in
-- 05_grants.sql, after its broad GRANT ON ALL TABLES.

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

-- ---------------------------------------------------------------------------
-- import_delivery_revisions — append-only corrections of a delivered package
-- ---------------------------------------------------------------------------
-- The table lives in 03_assay (next to the deliveries it corrects). A revision is the record of
-- what a person changed in a pipeline's package before its import, and why: the import reads the
-- package it ends with, and the family's record names it. It must not be rewritable. DELETE is
-- blocked; UPDATE is blocked but for the ON DELETE SET NULL that nulls created_by when an account
-- is removed (created_by_name keeps who it was).
CREATE OR REPLACE FUNCTION import_delivery_revisions_block_mutation() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'import_delivery_revisions is append-only; DELETE is not permitted';
    END IF;
    IF (NEW.created_by IS NOT NULL AND NEW.created_by IS DISTINCT FROM OLD.created_by)
        OR (to_jsonb(NEW) - 'created_by') IS DISTINCT FROM (to_jsonb(OLD) - 'created_by') THEN
        RAISE EXCEPTION 'import_delivery_revisions is append-only; UPDATE is not permitted';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS import_delivery_revisions_immutable ON import_delivery_revisions;
CREATE TRIGGER import_delivery_revisions_immutable
    BEFORE UPDATE OR DELETE ON import_delivery_revisions
    FOR EACH ROW EXECUTE FUNCTION import_delivery_revisions_block_mutation();

-- ---------------------------------------------------------------------------
-- family_structure_versions — append-only, hash-chained pedigree history
-- ---------------------------------------------------------------------------
-- The table lives in 03_assay (next to the pedigree it records). A structure version is
-- the record of a family's pedigree at a change (members, their sex, role and status, and
-- the relationships), which the segregation, the haplotypes and the ranking rest on, so it
-- must not be rewritable. DELETE is blocked. UPDATE is blocked except the
-- ON DELETE SET NULL cascades that null created_by or family_id when an account or the
-- family is removed (family_identifier keeps the family, and the chain is partitioned on
-- it).
CREATE OR REPLACE FUNCTION family_structure_versions_block_mutation() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'family_structure_versions is append-only; DELETE is not permitted';
    END IF;
    IF (NEW.created_by IS NOT NULL AND NEW.created_by IS DISTINCT FROM OLD.created_by)
        OR (NEW.family_id IS NOT NULL AND NEW.family_id IS DISTINCT FROM OLD.family_id)
        OR (to_jsonb(NEW) - 'created_by' - 'family_id')
             IS DISTINCT FROM (to_jsonb(OLD) - 'created_by' - 'family_id') THEN
        RAISE EXCEPTION 'family_structure_versions is append-only; UPDATE is not permitted';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS family_structure_versions_immutable ON family_structure_versions;
CREATE TRIGGER family_structure_versions_immutable
    BEFORE UPDATE OR DELETE ON family_structure_versions
    FOR EACH ROW EXECUTE FUNCTION family_structure_versions_block_mutation();

-- The exploratory ancestry PCA (outside the intended purpose; docs/ancestry-pca.md): its
-- public reference snapshots, jobs and results. A projection reads the family's stored
-- callsets. Nothing here enters a sign-out. The references and results are append-only.
CREATE TABLE IF NOT EXISTS ancestry_reference_panels (
    sha256 text PRIMARY KEY CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    snapshot jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS ancestry_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    family_uuid uuid NOT NULL,
    family_identifier text NOT NULL,
    project_id uuid NOT NULL,
    reference_sha256 text NOT NULL REFERENCES ancestry_reference_panels(sha256),
    -- A user's request, or the worker's own once the family's genotypes changed (no user).
    requested_by uuid,
    trigger text NOT NULL DEFAULT 'user' CONSTRAINT ancestry_runs_trigger_check CHECK (trigger IN ('user','automatic')),
    CONSTRAINT ancestry_runs_requester_check CHECK (trigger = 'automatic' OR requested_by IS NOT NULL),
    -- The family's small-variant data version the run was queued for, then the one it read.
    storage_version text,
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','completed','failed','interrupted')),
    error text,
    requested_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz
);
CREATE INDEX IF NOT EXISTS ancestry_runs_queue ON ancestry_runs(status, requested_at);
CREATE INDEX IF NOT EXISTS ancestry_runs_family ON ancestry_runs(family_uuid, project_id, requested_at DESC);
CREATE TABLE IF NOT EXISTS ancestry_results (
    run_id uuid PRIMARY KEY REFERENCES ancestry_runs(id),
    -- The family's small-variant data version the projection read.
    storage_version text,
    reference_sha256 text NOT NULL REFERENCES ancestry_reference_panels(sha256),
    samples jsonb NOT NULL,
    calculation_version text NOT NULL,
    assumptions jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE OR REPLACE FUNCTION ancestry_reference_panels_block_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'ancestry_reference_panels is append-only; % is not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS ancestry_reference_panels_no_mutation ON ancestry_reference_panels;
CREATE TRIGGER ancestry_reference_panels_no_mutation BEFORE UPDATE OR DELETE ON ancestry_reference_panels
    FOR EACH ROW EXECUTE FUNCTION ancestry_reference_panels_block_mutation();
CREATE OR REPLACE FUNCTION ancestry_results_block_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'ancestry_results is append-only; % is not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS ancestry_results_no_mutation ON ancestry_results;
CREATE TRIGGER ancestry_results_no_mutation BEFORE UPDATE OR DELETE ON ancestry_results
    FOR EACH ROW EXECUTE FUNCTION ancestry_results_block_mutation();
-- A user's choice, per individual, of how the ancestry PCA reads it (ancestry_choices.py):
-- CoGA's own reading (auto), one callset, possibly with the regions it covers, or left out.
-- The latest choice of an individual holds; append-only, so each calculation's is kept.
CREATE TABLE IF NOT EXISTS ancestry_sample_choices (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    family_uuid uuid NOT NULL,
    project_id uuid NOT NULL,
    sample_id text NOT NULL,
    mode text NOT NULL CONSTRAINT ancestry_sample_choices_mode_check CHECK (mode IN ('auto','callset','exclude')),
    -- A stored callset, or a VCF file the family's package declared for ancestry (file:<its id>).
    callset text CONSTRAINT ancestry_sample_choices_callset_check
        CHECK (callset IN ('clair3','nipt','glimpse2') OR callset ~ '^file:[0-9a-f-]{36}$'),
    -- The regions the chosen callset covers, as the reference markers inside them; never a file.
    regions jsonb,
    chosen_by uuid NOT NULL,
    chosen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT ancestry_sample_choices_callset_mode_check CHECK ((mode = 'callset') = (callset IS NOT NULL)),
    CONSTRAINT ancestry_sample_choices_regions_check CHECK (regions IS NULL OR mode = 'callset')
);
CREATE INDEX IF NOT EXISTS ancestry_sample_choices_family
    ON ancestry_sample_choices(family_uuid, project_id, sample_id, chosen_at DESC);
CREATE OR REPLACE FUNCTION ancestry_sample_choices_block_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'ancestry_sample_choices is append-only; % is not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS ancestry_sample_choices_no_mutation ON ancestry_sample_choices;
CREATE TRIGGER ancestry_sample_choices_no_mutation BEFORE UPDATE OR DELETE ON ancestry_sample_choices
    FOR EACH ROW EXECUTE FUNCTION ancestry_sample_choices_block_mutation();
-- VCF files a family package declared for the ancestry PCA without importing them (a gVCF),
-- and each scan of one: its checksum and its calls at the reference markers, which every
-- calculation reads instead of the file (ancestry_files.py). Both append-only.
CREATE TABLE IF NOT EXISTS ancestry_files (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    family_uuid uuid NOT NULL,
    family_identifier text NOT NULL,
    project_id uuid NOT NULL,
    import_job_id uuid,
    package_path text NOT NULL,
    relative_path text NOT NULL,
    label text NOT NULL,
    declared_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS ancestry_files_family ON ancestry_files(family_uuid, project_id, relative_path, declared_at DESC);
CREATE TABLE IF NOT EXISTS ancestry_file_scans (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id uuid NOT NULL REFERENCES ancestry_files(id),
    status text NOT NULL CONSTRAINT ancestry_file_scans_status_check CHECK (status IN ('scanned','failed')),
    -- The file as it was read, and the reference whose markers were kept.
    sha256 text,
    file_size bigint,
    reference_sha256 text NOT NULL,
    columns jsonb NOT NULL DEFAULT '[]'::jsonb,
    rows jsonb NOT NULL DEFAULT '[]'::jsonb,
    error text,
    scanned_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT ancestry_file_scans_checksum_check CHECK ((status = 'scanned') = (sha256 IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS ancestry_file_scans_file ON ancestry_file_scans(file_id, scanned_at DESC);
CREATE OR REPLACE FUNCTION ancestry_files_block_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only; % is not permitted', TG_TABLE_NAME, TG_OP;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS ancestry_files_no_mutation ON ancestry_files;
CREATE TRIGGER ancestry_files_no_mutation BEFORE UPDATE OR DELETE ON ancestry_files
    FOR EACH ROW EXECUTE FUNCTION ancestry_files_block_mutation();
DROP TRIGGER IF EXISTS ancestry_file_scans_no_mutation ON ancestry_file_scans;
CREATE TRIGGER ancestry_file_scans_no_mutation BEFORE UPDATE OR DELETE ON ancestry_file_scans
    FOR EACH ROW EXECUTE FUNCTION ancestry_files_block_mutation();
