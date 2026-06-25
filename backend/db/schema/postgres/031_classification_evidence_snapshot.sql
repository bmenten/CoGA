-- Phase 1 of clinical-grade traceability (see docs/clinical-traceability.md):
-- freeze the evidence a classification was based on, so drift can be detected when
-- the underlying annotation changes (a new ClinVar/gnomAD/annotation release).
--
-- The snapshot holds the annotation-set identity (the cheap drift key) plus the
-- most decision-relevant values at classification time.
ALTER TABLE small_variant_reviews
    ADD COLUMN IF NOT EXISTS acmg_evidence_snapshot JSONB;
