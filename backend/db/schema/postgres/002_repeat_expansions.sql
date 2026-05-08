CREATE TABLE IF NOT EXISTS repeat_expansions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sample_id UUID NOT NULL REFERENCES samples(id) ON DELETE CASCADE,
    family_id UUID NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    assembly_id UUID REFERENCES assemblies(id) ON DELETE SET NULL,
    source TEXT NOT NULL DEFAULT 'trgt',
    locus_id TEXT NOT NULL,
    gene TEXT NOT NULL,
    display_name TEXT NOT NULL,
    disease TEXT NOT NULL,
    inheritance TEXT,
    chr TEXT NOT NULL,
    start BIGINT NOT NULL,
    "end" BIGINT NOT NULL,
    motif TEXT,
    motifs JSONB NOT NULL DEFAULT '[]'::jsonb,
    motif_index INTEGER NOT NULL DEFAULT 0,
    genotype TEXT NOT NULL DEFAULT './.',
    allele_count INTEGER NOT NULL DEFAULT 0,
    alleles JSONB NOT NULL DEFAULT '[]'::jsonb,
    warning_min INTEGER,
    pathogenic_min INTEGER,
    status TEXT NOT NULL DEFAULT 'unknown' CHECK (status IN ('normal', 'intermediate', 'pathogenic', 'unknown')),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
);

CREATE INDEX IF NOT EXISTS idx_repeat_expansions_family_region ON repeat_expansions (family_id, chr, start);
CREATE INDEX IF NOT EXISTS idx_repeat_expansions_sample ON repeat_expansions (sample_id, source);
CREATE INDEX IF NOT EXISTS idx_repeat_expansions_locus ON repeat_expansions (locus_id, gene);
