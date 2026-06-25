-- Phase 0 of SNV + SV compound heterozygosity (see docs/snv-sv-compound-het.md):
-- a per-family index of which genes are hit by a structural variant, so the small-variant
-- workspace can flag "this gene is also hit by an SV" (the cross-type second-hit / compound
-- -het mechanism). Built lazily from the SV calls and rebuilt when SVs are re-imported.
CREATE TABLE IF NOT EXISTS family_sv_gene_index (
    family_id   UUID NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    gene_symbol TEXT NOT NULL,            -- upper-cased for case-insensitive lookup
    sv_count    INTEGER NOT NULL,
    svs         JSONB NOT NULL,           -- [{sv_id, sv_type, chr, start, end, gt:{sample:gt}}]
    PRIMARY KEY (family_id, gene_symbol)
);

-- Marks that a family's index has been built (distinguishes "built, no SV-gene hits" from
-- "not built yet"), so the lazy builder runs at most once until SVs change.
CREATE TABLE IF NOT EXISTS family_sv_gene_index_status (
    family_id   UUID PRIMARY KEY REFERENCES families(id) ON DELETE CASCADE,
    sv_total    INTEGER NOT NULL DEFAULT 0,
    gene_count  INTEGER NOT NULL DEFAULT 0,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
);
