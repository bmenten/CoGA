-- Gene-panel versioning. A panel now carries a current `version`; every version
-- (including the first) is archived as an immutable snapshot in
-- `gene_panel_versions`, so updating a panel never overwrites or deletes the prior
-- content — the old version stays, timestamped, and can be retrieved.
--
-- The live panel (gene_panel_genes / gene_panel_regions) always equals the latest
-- version's snapshot. This is the foundation for the generated "Mendeliome" panel,
-- which is re-versioned whenever the Monarch gene–disease release changes.
ALTER TABLE gene_panels
    ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1;

CREATE TABLE IF NOT EXISTS gene_panel_versions (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    panel_id         UUID NOT NULL REFERENCES gene_panels(id) ON DELETE CASCADE,
    version          INTEGER NOT NULL,
    name             TEXT NOT NULL,
    description      TEXT,
    source           TEXT,
    external_version TEXT,            -- e.g. the Monarch release for a Mendeliome version
    gene_count       INTEGER NOT NULL,
    genes            JSONB NOT NULL DEFAULT '[]'::jsonb,
    regions          JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_metadata  JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by       UUID REFERENCES users(id) ON DELETE SET NULL,
    created_by_email TEXT,            -- denormalised (survives account removal)
    created_at       TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    UNIQUE (panel_id, version)
);

CREATE INDEX IF NOT EXISTS idx_gene_panel_versions_panel
    ON gene_panel_versions (panel_id, version DESC);
