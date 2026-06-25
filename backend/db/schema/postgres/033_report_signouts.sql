-- Phase 3 of clinical-grade traceability (see docs/clinical-traceability.md):
-- case sign-out. Freeze the reported result to exactly what produced it — the
-- annotation/reference versions, the reported variant list, each classification and
-- its evidence snapshot, and the evidence-drift state at the moment of sign-out — as
-- an immutable, versioned, content-hashed record.
--
-- Each sign-out is a new version (amendments append; nothing is ever overwritten).
-- content_hash is the SHA-256 of the canonical snapshot, so tampering is detectable.
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
    UNIQUE (family_id, version)
);

CREATE INDEX IF NOT EXISTS idx_report_signouts_family
    ON report_signouts (family_id, version DESC);

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
