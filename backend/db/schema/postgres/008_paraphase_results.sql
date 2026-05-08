CREATE TABLE IF NOT EXISTS sample_paraphase_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sample_id UUID NOT NULL REFERENCES samples(id) ON DELETE CASCADE,
    family_id UUID NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    assembly_id UUID REFERENCES assemblies(id) ON DELETE SET NULL,
    gene_symbol TEXT NOT NULL,
    total_cn INTEGER,
    gene_cn INTEGER,
    highest_total_cn INTEGER,
    sample_sex TEXT,
    phase_region TEXT,
    region_depth JSONB NOT NULL DEFAULT '{}'::jsonb,
    genome_depth DOUBLE PRECISION,
    payload JSONB NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    UNIQUE(sample_id, gene_symbol)
);

CREATE INDEX IF NOT EXISTS idx_sample_paraphase_results_sample_gene
    ON sample_paraphase_results (sample_id, gene_symbol);

CREATE INDEX IF NOT EXISTS idx_sample_paraphase_results_family_gene
    ON sample_paraphase_results (family_id, gene_symbol);
