-- Make the prioritised-ranking cache panel-agnostic so a narrower gene panel can be
-- served from a broader cached ranking (the per-variant scores are panel-independent —
-- the panel only restricts which variants are in scope).
--
-- `base_hash` digests every ranking input EXCEPT the gene panel, so all panels over the
-- same family/phenotype/filters share a base_hash. `panel_id` records which panel a row
-- was computed for, so coverage (requested genes ⊆ cached panel's genes) can be checked.
-- A request whose panel is covered by a complete (non-truncated) cached superset is
-- served by re-validating membership against ClickHouse — no re-scoring.
ALTER TABLE family_variant_ranking_cache
    ADD COLUMN IF NOT EXISTS base_hash TEXT,
    ADD COLUMN IF NOT EXISTS panel_id TEXT;

CREATE INDEX IF NOT EXISTS idx_family_variant_ranking_cache_base
    ON family_variant_ranking_cache (family_id, base_hash);
