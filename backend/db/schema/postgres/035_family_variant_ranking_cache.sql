-- Cache of a family's phenotype-prioritised small-variant ranking. The prioritised
-- default view (Phenotype-priority preset + Mendeliome panel) is expensive to compute
-- (~10 s, dominated by per-gene phenotype scoring), so the ranked order is cached and
-- reused until an input changes.
--
-- Freshness is guarded by `inputs_hash` — a digest of the query filters plus the
-- mutable inputs that change the ranking (the affected individuals' HPO terms, the
-- pedigree/affected status, the gene-panel version, the Monarch release, and a scoring
-- algorithm version). When any of those changes the hash changes, so a stale ranking is
-- never served. The cache holds the compact ranking (variant id + score breakdown); the
-- page's variant records and review state are re-fetched fresh on read.
CREATE TABLE IF NOT EXISTS family_variant_ranking_cache (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    family_id          UUID NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    inputs_hash        TEXT NOT NULL,
    total              INTEGER NOT NULL,
    total_is_estimated BOOLEAN NOT NULL DEFAULT FALSE,
    ranking_truncated  BOOLEAN NOT NULL DEFAULT FALSE,
    ranking            JSONB NOT NULL,            -- ordered [{variant_id, priority:{...}}]
    provenance         JSONB NOT NULL DEFAULT '{}'::jsonb,
    computed_at        TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    UNIQUE (family_id, inputs_hash)
);

CREATE INDEX IF NOT EXISTS idx_family_variant_ranking_cache_recent
    ON family_variant_ranking_cache (family_id, computed_at DESC);
