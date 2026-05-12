CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username TEXT NOT NULL UNIQUE,
    hashed_password TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'viewer')),
    email TEXT NOT NULL UNIQUE,
    first_name TEXT NOT NULL DEFAULT '',
    last_name TEXT NOT NULL DEFAULT '',
    affiliation TEXT NOT NULL DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
);

CREATE TABLE IF NOT EXISTS species (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    common_name TEXT NOT NULL,
    tax_id INTEGER NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
);

CREATE TABLE IF NOT EXISTS assemblies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    species_id UUID NOT NULL REFERENCES species(id) ON DELETE CASCADE,
    assembly_name TEXT NOT NULL,
    version TEXT NOT NULL,
    release_date DATE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    UNIQUE (species_id, assembly_name, version)
);

CREATE INDEX IF NOT EXISTS idx_assemblies_name ON assemblies (assembly_name);

CREATE TABLE IF NOT EXISTS projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    species_id UUID NOT NULL REFERENCES species(id) ON DELETE RESTRICT,
    assembly_id UUID NOT NULL REFERENCES assemblies(id) ON DELETE RESTRICT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
);

CREATE TABLE IF NOT EXISTS project_users (
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    PRIMARY KEY (project_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_project_users_user_id ON project_users (user_id);

CREATE TABLE IF NOT EXISTS families (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    family_id TEXT NOT NULL UNIQUE,
    pedigree TEXT,
    roi_query TEXT,
    roi_label TEXT,
    roi_source TEXT CHECK (roi_source IN ('gene', 'region')),
    roi_assembly_id UUID REFERENCES assemblies(id) ON DELETE SET NULL,
    roi_chr TEXT,
    roi_start BIGINT,
    roi_end BIGINT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
);

CREATE TABLE IF NOT EXISTS family_projects (
    family_id UUID NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    PRIMARY KEY (family_id, project_id)
);

CREATE INDEX IF NOT EXISTS idx_family_projects_project_id ON family_projects (project_id);

CREATE TABLE IF NOT EXISTS samples (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sample_id TEXT NOT NULL UNIQUE,
    family_id UUID NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    sex TEXT NOT NULL CHECK (sex IN ('male', 'female', 'und')),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
);

CREATE INDEX IF NOT EXISTS idx_samples_family_id ON samples (family_id);

CREATE TABLE IF NOT EXISTS sample_projects (
    sample_id UUID NOT NULL REFERENCES samples(id) ON DELETE CASCADE,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    PRIMARY KEY (sample_id, project_id)
);

CREATE INDEX IF NOT EXISTS idx_sample_projects_project_id ON sample_projects (project_id);

CREATE TABLE IF NOT EXISTS family_members (
    family_id UUID NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    sample_id UUID NOT NULL REFERENCES samples(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('proband', 'father', 'mother', 'sibling')),
    affected BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (family_id, sample_id)
);

CREATE INDEX IF NOT EXISTS idx_family_members_sample_id ON family_members (sample_id);

CREATE TABLE IF NOT EXISTS chromosomes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    assembly_id UUID NOT NULL REFERENCES assemblies(id) ON DELETE CASCADE,
    chr TEXT NOT NULL,
    size BIGINT NOT NULL,
    bands JSONB NOT NULL DEFAULT '[]'::jsonb,
    UNIQUE (assembly_id, chr)
);

CREATE TABLE IF NOT EXISTS genes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    assembly_id UUID NOT NULL REFERENCES assemblies(id) ON DELETE CASCADE,
    gene_id TEXT NOT NULL,
    hgnc_symbol TEXT NOT NULL,
    chr TEXT NOT NULL,
    start BIGINT NOT NULL,
    "end" BIGINT NOT NULL,
    exons JSONB NOT NULL DEFAULT '[]'::jsonb,
    strand INTEGER NOT NULL,
    biotype TEXT NOT NULL,
    description TEXT NOT NULL,
    source TEXT NOT NULL,
    extra JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_genes_assembly_region ON genes (assembly_id, chr, start, "end");
CREATE INDEX IF NOT EXISTS idx_genes_symbol ON genes (hgnc_symbol);

CREATE TABLE IF NOT EXISTS blacklist (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    assembly_id UUID NOT NULL REFERENCES assemblies(id) ON DELETE CASCADE,
    chr TEXT NOT NULL,
    start BIGINT NOT NULL,
    "end" BIGINT NOT NULL,
    label TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_blacklist_assembly_region ON blacklist (assembly_id, chr, start);

CREATE TABLE IF NOT EXISTS clinical_cnvs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    assembly_id UUID NOT NULL REFERENCES assemblies(id) ON DELETE CASCADE,
    chr TEXT NOT NULL,
    start BIGINT NOT NULL,
    "end" BIGINT NOT NULL,
    type TEXT,
    label TEXT NOT NULL,
    details_html TEXT
);

CREATE INDEX IF NOT EXISTS idx_clinical_cnvs_assembly_region ON clinical_cnvs (assembly_id, chr, start);

CREATE TABLE IF NOT EXISTS repeat_loci (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    locus_id TEXT NOT NULL UNIQUE,
    gene TEXT NOT NULL,
    display_name TEXT NOT NULL,
    disease TEXT NOT NULL,
    inheritance TEXT,
    motif TEXT,
    motif_index INTEGER NOT NULL DEFAULT 0,
    warning_min INTEGER,
    pathogenic_min INTEGER,
    x_linked BOOLEAN NOT NULL DEFAULT FALSE,
    aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
    notes TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
);

CREATE INDEX IF NOT EXISTS idx_repeat_loci_gene ON repeat_loci (gene);

CREATE TABLE IF NOT EXISTS gene_info (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    assembly_id UUID NOT NULL REFERENCES assemblies(id) ON DELETE CASCADE,
    hgnc_symbol TEXT NOT NULL,
    gene_id TEXT,
    display_name TEXT,
    summary TEXT,
    aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
    previous_symbols JSONB NOT NULL DEFAULT '[]'::jsonb,
    ensembl_gene_id TEXT,
    ncbi_gene_id TEXT,
    hgnc_id TEXT,
    omim_gene_id TEXT,
    gene_type TEXT,
    location TEXT,
    homologs JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_status JSONB NOT NULL DEFAULT '{}'::jsonb,
    extra JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    UNIQUE (assembly_id, hgnc_symbol)
);

CREATE INDEX IF NOT EXISTS idx_gene_info_updated_at ON gene_info (updated_at);

CREATE TABLE IF NOT EXISTS gene_info_refresh_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scope TEXT NOT NULL CHECK (scope IN ('symbol', 'all_human')),
    symbol TEXT,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    active_slot TEXT UNIQUE,
    worker_id TEXT,
    requested_by TEXT NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    started_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    total_symbols INTEGER NOT NULL DEFAULT 0,
    completed_symbols INTEGER NOT NULL DEFAULT 0,
    updated_records INTEGER NOT NULL DEFAULT 0,
    human_assemblies INTEGER NOT NULL DEFAULT 0,
    current_symbol TEXT,
    error TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_gene_info_refresh_jobs_status ON gene_info_refresh_jobs (status, requested_at);
CREATE INDEX IF NOT EXISTS idx_gene_info_refresh_jobs_requested ON gene_info_refresh_jobs (requested_at);

CREATE TABLE IF NOT EXISTS small_variant_reviews (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    family_id UUID NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    variant_key BIGINT,
    variant_id TEXT,
    classification TEXT,
    tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    tag_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    note TEXT,
    compound_het_group_id TEXT,
    compound_het_partner_variant_keys JSONB NOT NULL DEFAULT '[]'::jsonb,
    compound_het_partner_variant_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    compound_het_gene TEXT,
    compound_het_gene_id TEXT,
    compound_het_classification TEXT,
    compound_het_tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    compound_het_tag_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    compound_het_note TEXT,
    compound_het_phase_status TEXT,
    compound_het_updated_by TEXT,
    compound_het_updated_at TIMESTAMPTZ,
    updated_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
);

CREATE INDEX IF NOT EXISTS idx_small_variant_reviews_family ON small_variant_reviews (family_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_small_variant_reviews_family_variant_key
    ON small_variant_reviews (family_id, variant_key) WHERE variant_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_small_variant_reviews_family_variant_id
    ON small_variant_reviews (family_id, variant_id) WHERE variant_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS small_variant_filter_presets (
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

CREATE UNIQUE INDEX IF NOT EXISTS idx_small_variant_filter_presets_unique
    ON small_variant_filter_presets (COALESCE(family_id, '00000000-0000-0000-0000-000000000000'::uuid), scope, owner, name);

CREATE TABLE IF NOT EXISTS small_variant_tag_definitions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    family_id UUID REFERENCES families(id) ON DELETE CASCADE,
    key TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    description TEXT,
    scope TEXT NOT NULL DEFAULT 'global' CHECK (scope IN ('global', 'project')),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    "group" TEXT NOT NULL DEFAULT 'custom' CHECK ("group" IN ('collaboration', 'classification', 'custom')),
    color TEXT NOT NULL DEFAULT '#5b6b79',
    sort_order INTEGER NOT NULL DEFAULT 500,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    CHECK (
        (scope = 'global' AND project_id IS NULL)
        OR (scope = 'project' AND project_id IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS small_variant_tag_definition_project_links (
    tag_id UUID NOT NULL REFERENCES small_variant_tag_definitions(id) ON DELETE CASCADE,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    PRIMARY KEY (tag_id, project_id)
);

CREATE TABLE IF NOT EXISTS gene_panels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_by UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
);

CREATE TABLE IF NOT EXISTS gene_panel_genes (
    panel_id UUID NOT NULL REFERENCES gene_panels(id) ON DELETE CASCADE,
    gene_symbol TEXT NOT NULL,
    PRIMARY KEY (panel_id, gene_symbol)
);

CREATE TABLE IF NOT EXISTS gene_panel_regions (
    panel_id UUID NOT NULL REFERENCES gene_panels(id) ON DELETE CASCADE,
    gene TEXT NOT NULL,
    chr TEXT NOT NULL,
    start BIGINT NOT NULL,
    "end" BIGINT NOT NULL,
    PRIMARY KEY (panel_id, gene, chr, start, "end")
);
