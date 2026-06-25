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
