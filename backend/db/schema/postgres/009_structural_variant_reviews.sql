CREATE TABLE IF NOT EXISTS structural_variant_reviews (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    family_id UUID NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    variant_key BIGINT,
    variant_id TEXT,
    classification TEXT,
    tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    tag_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    note TEXT,
    updated_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
);

CREATE INDEX IF NOT EXISTS idx_structural_variant_reviews_family ON structural_variant_reviews (family_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_structural_variant_reviews_family_variant_key
    ON structural_variant_reviews (family_id, variant_key) WHERE variant_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_structural_variant_reviews_family_variant_id
    ON structural_variant_reviews (family_id, variant_id) WHERE variant_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS structural_variant_filter_presets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    family_id UUID REFERENCES families(id) ON DELETE CASCADE,
    scope TEXT NOT NULL CHECK (scope IN ('family', 'global')),
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    filters JSONB NOT NULL DEFAULT '{}'::jsonb,
    sample_filters JSONB NOT NULL DEFAULT '{}'::jsonb,
    sample_templates JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_structural_variant_filter_presets_unique
    ON structural_variant_filter_presets (COALESCE(family_id, '00000000-0000-0000-0000-000000000000'::uuid), scope, owner, name);
