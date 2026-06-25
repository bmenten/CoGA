-- Phase 2 of clinical-grade traceability (see docs/clinical-traceability.md):
-- an immutable, append-only record of clinical decisions — who classified, tagged
-- or annotated which variant, when, and what changed (before -> after).
--
-- This is the *clinical action* log, distinct from audit_log_events (the HTTP
-- access log). Events are written in the same transaction as the change they
-- describe, so the trail can never drift from the data.
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
    metadata          JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_clinical_audit_events_family
    ON clinical_audit_events (family_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_clinical_audit_events_variant
    ON clinical_audit_events (variant_id);

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
