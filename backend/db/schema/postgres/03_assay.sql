-- 03_assay.sql — Domain: Families/samples + per-sample assay data + review/curation
-- Consolidated baseline. Folds all later ADD COLUMN / index additions into each
-- table's final form. Idempotent: safe to re-run on every boot.

-- ---------------------------------------------------------------------------
-- family_statuses (admin-managed catalogue; families.status_id references it)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS family_statuses (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    key text NOT NULL,
    label text NOT NULL,
    description text,
    color text DEFAULT '#5b6b79'::text NOT NULL,
    sort_order integer DEFAULT 500 NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT family_statuses_pkey PRIMARY KEY (id),
    CONSTRAINT family_statuses_key_key UNIQUE (key),
    CONSTRAINT family_statuses_created_by_fkey FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
);

INSERT INTO family_statuses (key, label, color, sort_order) VALUES
    ('solved', 'Solved', '#1f9d57', 10),
    ('strong_candidate', 'Strong candidate', '#3fae6b', 20),
    ('reviewed_no_candidate', 'Reviewed, no clear candidate', '#c08a2d', 30),
    ('closed', 'Closed, no longer under analysis', '#6b7280', 40),
    ('analysis_in_progress', 'Analysis in progress', '#2f6fb0', 50),
    ('data_ready', 'Data ready for analysis', '#3b82a6', 60),
    ('waiting_for_data', 'Waiting for data', '#b08415', 70),
    ('loading_failed', 'Loading failed', '#c61f2d', 80),
    ('no_data_expected', 'No data expected', '#9aa3ad', 90)
ON CONFLICT (key) DO NOTHING;

-- ---------------------------------------------------------------------------
-- families
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS families (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    family_id text NOT NULL,
    pedigree text,
    roi_query text,
    roi_label text,
    roi_source text,
    roi_assembly_id uuid,
    roi_chr text,
    roi_start bigint,
    roi_end bigint,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    status_id uuid,
    assigned_to_id uuid,
    reviewed_by_id uuid,
    CONSTRAINT families_pkey PRIMARY KEY (id),
    CONSTRAINT families_family_id_key UNIQUE (family_id),
    CONSTRAINT families_roi_source_check CHECK ((roi_source = ANY (ARRAY['gene'::text, 'region'::text]))),
    CONSTRAINT families_roi_assembly_id_fkey FOREIGN KEY (roi_assembly_id) REFERENCES assemblies(id) ON DELETE SET NULL,
    CONSTRAINT families_status_id_fkey FOREIGN KEY (status_id) REFERENCES family_statuses(id) ON DELETE SET NULL,
    CONSTRAINT families_assigned_to_id_fkey FOREIGN KEY (assigned_to_id) REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT families_reviewed_by_id_fkey FOREIGN KEY (reviewed_by_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_families_status_id ON families USING btree (status_id);
CREATE INDEX IF NOT EXISTS idx_families_assigned_to_id ON families USING btree (assigned_to_id);
CREATE INDEX IF NOT EXISTS idx_families_reviewed_by_id ON families USING btree (reviewed_by_id);

-- ---------------------------------------------------------------------------
-- samples
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS samples (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sample_id text NOT NULL,
    family_id uuid NOT NULL,
    sex text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT samples_pkey PRIMARY KEY (id),
    CONSTRAINT samples_sample_id_key UNIQUE (sample_id),
    CONSTRAINT samples_sex_check CHECK ((sex = ANY (ARRAY['male'::text, 'female'::text, 'und'::text]))),
    CONSTRAINT samples_family_id_fkey FOREIGN KEY (family_id) REFERENCES families(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_samples_family_id ON samples USING btree (family_id);

-- ---------------------------------------------------------------------------
-- family_members
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS family_members (
    family_id uuid NOT NULL,
    sample_id uuid NOT NULL,
    role text NOT NULL,
    clinical_status text DEFAULT 'unknown'::text NOT NULL,
    carrier_status text DEFAULT 'unknown'::text NOT NULL,
    carrier_type text,
    carrier_evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    affected boolean DEFAULT false NOT NULL,
    active boolean DEFAULT true NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT family_members_pkey PRIMARY KEY (family_id, sample_id),
    CONSTRAINT family_members_carrier_status_check CHECK ((carrier_status = ANY (ARRAY['unknown'::text, 'not_carrier'::text, 'carrier'::text]))),
    CONSTRAINT family_members_carrier_type_check CHECK ((carrier_type = ANY (ARRAY['obligate'::text, 'proven'::text, 'reported'::text, 'inferred'::text]))),
    CONSTRAINT family_members_clinical_status_check CHECK ((clinical_status = ANY (ARRAY['unknown'::text, 'unaffected'::text, 'affected'::text]))),
    CONSTRAINT family_members_role_check CHECK ((role = ANY (ARRAY['proband'::text, 'father'::text, 'mother'::text, 'sibling'::text, 'embryo'::text, 'relative'::text]))),
    CONSTRAINT family_members_family_id_fkey FOREIGN KEY (family_id) REFERENCES families(id) ON DELETE CASCADE,
    CONSTRAINT family_members_sample_id_fkey FOREIGN KEY (sample_id) REFERENCES samples(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_family_members_sample_id ON family_members USING btree (sample_id);
CREATE INDEX IF NOT EXISTS idx_family_members_status ON family_members USING btree (family_id, clinical_status, carrier_status) WHERE active;

-- ---------------------------------------------------------------------------
-- family_projects
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS family_projects (
    family_id uuid NOT NULL,
    project_id uuid NOT NULL,
    CONSTRAINT family_projects_pkey PRIMARY KEY (family_id, project_id),
    CONSTRAINT family_projects_family_id_fkey FOREIGN KEY (family_id) REFERENCES families(id) ON DELETE CASCADE,
    CONSTRAINT family_projects_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_family_projects_project_id ON family_projects USING btree (project_id);

-- ---------------------------------------------------------------------------
-- sample_projects
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sample_projects (
    sample_id uuid NOT NULL,
    project_id uuid NOT NULL,
    CONSTRAINT sample_projects_pkey PRIMARY KEY (sample_id, project_id),
    CONSTRAINT sample_projects_sample_id_fkey FOREIGN KEY (sample_id) REFERENCES samples(id) ON DELETE CASCADE,
    CONSTRAINT sample_projects_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sample_projects_project_id ON sample_projects USING btree (project_id);

-- ---------------------------------------------------------------------------
-- family_relationships (final form; superseded 001 by 014)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS family_relationships (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    family_id uuid NOT NULL,
    relationship_type text NOT NULL,
    sample_id_a uuid NOT NULL,
    sample_id_b uuid NOT NULL,
    role_a text,
    role_b text,
    source text DEFAULT 'manual'::text NOT NULL,
    active boolean DEFAULT true NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT family_relationships_pkey PRIMARY KEY (id),
    CONSTRAINT family_relationships_check CHECK ((sample_id_a <> sample_id_b)),
    CONSTRAINT family_relationships_relationship_type_check CHECK ((relationship_type = ANY (ARRAY['parent_child'::text, 'couple'::text]))),
    CONSTRAINT family_relationships_family_id_fkey FOREIGN KEY (family_id) REFERENCES families(id) ON DELETE CASCADE,
    CONSTRAINT family_relationships_sample_id_a_fkey FOREIGN KEY (sample_id_a) REFERENCES samples(id) ON DELETE CASCADE,
    CONSTRAINT family_relationships_sample_id_b_fkey FOREIGN KEY (sample_id_b) REFERENCES samples(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_family_relationships_family_type ON family_relationships USING btree (family_id, relationship_type) WHERE active;
CREATE INDEX IF NOT EXISTS idx_family_relationships_sample_a ON family_relationships USING btree (sample_id_a) WHERE active;
CREATE INDEX IF NOT EXISTS idx_family_relationships_sample_b ON family_relationships USING btree (sample_id_b) WHERE active;

-- ---------------------------------------------------------------------------
-- family_structure_versions (final form; superseded 001 by 014)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS family_structure_versions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    family_id uuid NOT NULL,
    version integer NOT NULL,
    structure_hash text NOT NULL,
    snapshot jsonb NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT family_structure_versions_pkey PRIMARY KEY (id),
    CONSTRAINT family_structure_versions_family_id_version_key UNIQUE (family_id, version),
    CONSTRAINT family_structure_versions_family_id_fkey FOREIGN KEY (family_id) REFERENCES families(id) ON DELETE CASCADE,
    CONSTRAINT family_structure_versions_created_by_fkey FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_family_structure_versions_family ON family_structure_versions USING btree (family_id, version DESC);

-- ---------------------------------------------------------------------------
-- family_import_jobs
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS family_import_jobs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    submitted_path text NOT NULL,
    family_id text,
    project_id uuid,
    status text NOT NULL,
    dry_run boolean DEFAULT false NOT NULL,
    worker_id text,
    requested_by text NOT NULL,
    requested_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    started_at timestamp with time zone,
    heartbeat_at timestamp with time zone,
    completed_at timestamp with time zone,
    validation_errors jsonb DEFAULT '[]'::jsonb NOT NULL,
    validation_warnings jsonb DEFAULT '[]'::jsonb NOT NULL,
    logs jsonb DEFAULT '[]'::jsonb NOT NULL,
    dataset_summaries jsonb DEFAULT '[]'::jsonb NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    error text,
    CONSTRAINT family_import_jobs_pkey PRIMARY KEY (id),
    CONSTRAINT family_import_jobs_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'validating'::text, 'running'::text, 'completed'::text, 'failed'::text]))),
    CONSTRAINT family_import_jobs_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_family_import_jobs_family ON family_import_jobs USING btree (family_id);
CREATE INDEX IF NOT EXISTS idx_family_import_jobs_requested ON family_import_jobs USING btree (requested_at);
CREATE INDEX IF NOT EXISTS idx_family_import_jobs_status ON family_import_jobs USING btree (status, requested_at);

-- ---------------------------------------------------------------------------
-- individual_hpo
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS individual_hpo (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    family_id uuid NOT NULL,
    sample_id uuid NOT NULL,
    hpo_id text NOT NULL,
    status text NOT NULL,
    onset text,
    evidence text,
    source text DEFAULT 'manual'::text NOT NULL,
    note text,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT individual_hpo_pkey PRIMARY KEY (id),
    CONSTRAINT individual_hpo_family_id_sample_id_hpo_id_status_key UNIQUE (family_id, sample_id, hpo_id, status),
    CONSTRAINT individual_hpo_status_check CHECK ((status = ANY (ARRAY['present'::text, 'absent'::text, 'unknown'::text]))),
    CONSTRAINT individual_hpo_family_id_fkey FOREIGN KEY (family_id) REFERENCES families(id) ON DELETE CASCADE,
    CONSTRAINT individual_hpo_sample_id_fkey FOREIGN KEY (sample_id) REFERENCES samples(id) ON DELETE CASCADE,
    CONSTRAINT individual_hpo_hpo_id_fkey FOREIGN KEY (hpo_id) REFERENCES hpo_term(hpo_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_individual_hpo_family_sample ON individual_hpo USING btree (family_id, sample_id);
CREATE INDEX IF NOT EXISTS idx_individual_hpo_hpo_status ON individual_hpo USING btree (hpo_id, status);

-- ---------------------------------------------------------------------------
-- repeat_expansions
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS repeat_expansions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sample_id uuid NOT NULL,
    family_id uuid NOT NULL,
    assembly_id uuid,
    source text DEFAULT 'trgt'::text NOT NULL,
    locus_id text NOT NULL,
    gene text NOT NULL,
    display_name text NOT NULL,
    disease text NOT NULL,
    inheritance text,
    chr text NOT NULL,
    start bigint NOT NULL,
    "end" bigint NOT NULL,
    motif text,
    motifs jsonb DEFAULT '[]'::jsonb NOT NULL,
    motif_index integer DEFAULT 0 NOT NULL,
    genotype text DEFAULT './.'::text NOT NULL,
    allele_count integer DEFAULT 0 NOT NULL,
    alleles jsonb DEFAULT '[]'::jsonb NOT NULL,
    warning_min integer,
    pathogenic_min integer,
    status text DEFAULT 'unknown'::text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    uploaded_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT repeat_expansions_pkey PRIMARY KEY (id),
    CONSTRAINT repeat_expansions_status_check CHECK ((status = ANY (ARRAY['normal'::text, 'intermediate'::text, 'pathogenic'::text, 'unknown'::text]))),
    CONSTRAINT repeat_expansions_assembly_id_fkey FOREIGN KEY (assembly_id) REFERENCES assemblies(id) ON DELETE SET NULL,
    CONSTRAINT repeat_expansions_family_id_fkey FOREIGN KEY (family_id) REFERENCES families(id) ON DELETE CASCADE,
    CONSTRAINT repeat_expansions_sample_id_fkey FOREIGN KEY (sample_id) REFERENCES samples(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_repeat_expansions_family_region ON repeat_expansions USING btree (family_id, chr, start);
CREATE INDEX IF NOT EXISTS idx_repeat_expansions_locus ON repeat_expansions USING btree (locus_id, gene);
CREATE INDEX IF NOT EXISTS idx_repeat_expansions_sample ON repeat_expansions USING btree (sample_id, source);

-- ---------------------------------------------------------------------------
-- sample_paraphase_results
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sample_paraphase_results (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sample_id uuid NOT NULL,
    family_id uuid NOT NULL,
    assembly_id uuid,
    gene_symbol text NOT NULL,
    total_cn integer,
    gene_cn integer,
    highest_total_cn integer,
    sample_sex text,
    phase_region text,
    region_depth jsonb DEFAULT '{}'::jsonb NOT NULL,
    genome_depth double precision,
    payload jsonb NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    uploaded_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT sample_paraphase_results_pkey PRIMARY KEY (id),
    CONSTRAINT sample_paraphase_results_sample_id_gene_symbol_key UNIQUE (sample_id, gene_symbol),
    CONSTRAINT sample_paraphase_results_assembly_id_fkey FOREIGN KEY (assembly_id) REFERENCES assemblies(id) ON DELETE SET NULL,
    CONSTRAINT sample_paraphase_results_family_id_fkey FOREIGN KEY (family_id) REFERENCES families(id) ON DELETE CASCADE,
    CONSTRAINT sample_paraphase_results_sample_id_fkey FOREIGN KEY (sample_id) REFERENCES samples(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sample_paraphase_results_family_gene ON sample_paraphase_results USING btree (family_id, gene_symbol);
CREATE INDEX IF NOT EXISTS idx_sample_paraphase_results_sample_gene ON sample_paraphase_results USING btree (sample_id, gene_symbol);

-- ---------------------------------------------------------------------------
-- nipt_artifact_variants
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nipt_artifact_variants (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    assembly_id uuid NOT NULL,
    assay_key text DEFAULT 'nipt_cfdna'::text NOT NULL,
    variant_id text NOT NULL,
    recurrence_count integer DEFAULT 0 NOT NULL,
    source text DEFAULT 'curated'::text NOT NULL,
    label text,
    created_by uuid,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT nipt_artifact_variants_pkey PRIMARY KEY (id),
    CONSTRAINT nipt_artifact_variants_assembly_id_assay_key_variant_id_key UNIQUE (assembly_id, assay_key, variant_id),
    CONSTRAINT nipt_artifact_variants_source_check CHECK ((source = ANY (ARRAY['curated'::text, 'auto'::text]))),
    CONSTRAINT nipt_artifact_variants_assembly_id_fkey FOREIGN KEY (assembly_id) REFERENCES assemblies(id) ON DELETE CASCADE,
    CONSTRAINT nipt_artifact_variants_created_by_fkey FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_nipt_artifacts_scope ON nipt_artifact_variants USING btree (assembly_id, assay_key);

-- ---------------------------------------------------------------------------
-- sample_interval_track_sources
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sample_interval_track_sources (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sample_id uuid NOT NULL,
    family_id uuid NOT NULL,
    assembly_id uuid,
    track_type text NOT NULL,
    source text DEFAULT 'web'::text NOT NULL,
    filename text DEFAULT ''::text NOT NULL,
    row_count bigint DEFAULT 0 NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    uploaded_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT sample_interval_track_sources_pkey PRIMARY KEY (id),
    CONSTRAINT sample_interval_track_sources_sample_id_track_type_source_f_key UNIQUE (sample_id, track_type, source, filename),
    CONSTRAINT sample_interval_track_sources_track_type_check CHECK ((track_type = ANY (ARRAY['coverage'::text, 'apcad'::text, 'apcad_pcf'::text, 'segments'::text, 'haplotype'::text]))),
    CONSTRAINT sample_interval_track_sources_assembly_id_fkey FOREIGN KEY (assembly_id) REFERENCES assemblies(id) ON DELETE SET NULL,
    CONSTRAINT sample_interval_track_sources_family_id_fkey FOREIGN KEY (family_id) REFERENCES families(id) ON DELETE CASCADE,
    CONSTRAINT sample_interval_track_sources_sample_id_fkey FOREIGN KEY (sample_id) REFERENCES samples(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sample_interval_track_sources_assembly_type ON sample_interval_track_sources USING btree (assembly_id, track_type);
CREATE INDEX IF NOT EXISTS idx_sample_interval_track_sources_family_type ON sample_interval_track_sources USING btree (family_id, track_type);
CREATE INDEX IF NOT EXISTS idx_sample_interval_track_sources_sample_type ON sample_interval_track_sources USING btree (sample_id, track_type);

-- ---------------------------------------------------------------------------
-- small_variant_reviews (folds ACMG cols from 025 + evidence snapshot from 031)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS small_variant_reviews (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    family_id uuid NOT NULL,
    variant_key bigint,
    variant_id text,
    classification text,
    tags jsonb DEFAULT '[]'::jsonb NOT NULL,
    tag_metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    note text,
    compound_het_group_id text,
    compound_het_partner_variant_keys jsonb DEFAULT '[]'::jsonb NOT NULL,
    compound_het_partner_variant_ids jsonb DEFAULT '[]'::jsonb NOT NULL,
    compound_het_gene text,
    compound_het_gene_id text,
    compound_het_classification text,
    compound_het_tags jsonb DEFAULT '[]'::jsonb NOT NULL,
    compound_het_tag_metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    compound_het_note text,
    compound_het_phase_status text,
    compound_het_updated_by text,
    compound_het_updated_at timestamp with time zone,
    updated_by text NOT NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    acmg jsonb,
    acmg_point_total integer,
    acmg_class text,
    acmg_evidence_snapshot jsonb,
    CONSTRAINT small_variant_reviews_pkey PRIMARY KEY (id),
    CONSTRAINT small_variant_reviews_family_id_fkey FOREIGN KEY (family_id) REFERENCES families(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_small_variant_reviews_family ON small_variant_reviews USING btree (family_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_small_variant_reviews_family_variant_id ON small_variant_reviews USING btree (family_id, variant_id) WHERE (variant_id IS NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS idx_small_variant_reviews_family_variant_key ON small_variant_reviews USING btree (family_id, variant_key) WHERE (variant_key IS NOT NULL);

-- ---------------------------------------------------------------------------
-- small_variant_filter_presets
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS small_variant_filter_presets (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    family_id uuid,
    scope text NOT NULL,
    owner text NOT NULL,
    name text NOT NULL,
    description text,
    filters jsonb DEFAULT '{}'::jsonb NOT NULL,
    sample_filters jsonb DEFAULT '{}'::jsonb NOT NULL,
    sample_templates jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT small_variant_filter_presets_pkey PRIMARY KEY (id),
    CONSTRAINT small_variant_filter_presets_scope_check CHECK ((scope = ANY (ARRAY['family'::text, 'global'::text]))),
    CONSTRAINT small_variant_filter_presets_family_id_fkey FOREIGN KEY (family_id) REFERENCES families(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_small_variant_filter_presets_unique ON small_variant_filter_presets USING btree (COALESCE(family_id, '00000000-0000-0000-0000-000000000000'::uuid), scope, owner, name);

-- ---------------------------------------------------------------------------
-- small_variant_tag_definitions (folds scope/project cols from 006)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS small_variant_tag_definitions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    family_id uuid,
    key text NOT NULL,
    label text NOT NULL,
    description text,
    scope text DEFAULT 'global'::text NOT NULL,
    project_id uuid,
    "group" text DEFAULT 'custom'::text NOT NULL,
    color text DEFAULT '#5b6b79'::text NOT NULL,
    sort_order integer DEFAULT 500 NOT NULL,
    created_by text NOT NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    CONSTRAINT small_variant_tag_definitions_pkey PRIMARY KEY (id),
    CONSTRAINT small_variant_tag_definitions_key_key UNIQUE (key),
    CONSTRAINT small_variant_tag_definitions_check CHECK ((((scope = 'global'::text) AND (project_id IS NULL)) OR ((scope = 'project'::text) AND (project_id IS NOT NULL)))),
    CONSTRAINT small_variant_tag_definitions_group_check CHECK (("group" = ANY (ARRAY['collaboration'::text, 'classification'::text, 'custom'::text]))),
    CONSTRAINT small_variant_tag_definitions_scope_check CHECK ((scope = ANY (ARRAY['global'::text, 'project'::text]))),
    CONSTRAINT small_variant_tag_definitions_scope_project_check CHECK ((((scope = 'global'::text) AND (project_id IS NULL)) OR ((scope = 'project'::text) AND (project_id IS NOT NULL)))),
    CONSTRAINT small_variant_tag_definitions_family_id_fkey FOREIGN KEY (family_id) REFERENCES families(id) ON DELETE CASCADE,
    CONSTRAINT small_variant_tag_definitions_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

-- ---------------------------------------------------------------------------
-- small_variant_tag_definition_project_links
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS small_variant_tag_definition_project_links (
    tag_id uuid NOT NULL,
    project_id uuid NOT NULL,
    CONSTRAINT small_variant_tag_definition_project_links_pkey PRIMARY KEY (tag_id, project_id),
    CONSTRAINT small_variant_tag_definition_project_links_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    CONSTRAINT small_variant_tag_definition_project_links_tag_id_fkey FOREIGN KEY (tag_id) REFERENCES small_variant_tag_definitions(id) ON DELETE CASCADE
);

-- ---------------------------------------------------------------------------
-- structural_variant_reviews (folds CNV ACMG cols from 026)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS structural_variant_reviews (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    family_id uuid NOT NULL,
    variant_key bigint,
    variant_id text,
    classification text,
    tags jsonb DEFAULT '[]'::jsonb NOT NULL,
    tag_metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    note text,
    updated_by text NOT NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    cnv_acmg jsonb,
    cnv_point_total double precision,
    cnv_class text,
    CONSTRAINT structural_variant_reviews_pkey PRIMARY KEY (id),
    CONSTRAINT structural_variant_reviews_family_id_fkey FOREIGN KEY (family_id) REFERENCES families(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_structural_variant_reviews_family ON structural_variant_reviews USING btree (family_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_structural_variant_reviews_family_variant_id ON structural_variant_reviews USING btree (family_id, variant_id) WHERE (variant_id IS NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS idx_structural_variant_reviews_family_variant_key ON structural_variant_reviews USING btree (family_id, variant_key) WHERE (variant_key IS NOT NULL);

-- ---------------------------------------------------------------------------
-- structural_variant_filter_presets
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS structural_variant_filter_presets (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    family_id uuid,
    scope text NOT NULL,
    owner text NOT NULL,
    name text NOT NULL,
    description text,
    filters jsonb DEFAULT '{}'::jsonb NOT NULL,
    sample_filters jsonb DEFAULT '{}'::jsonb NOT NULL,
    sample_templates jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT structural_variant_filter_presets_pkey PRIMARY KEY (id),
    CONSTRAINT structural_variant_filter_presets_scope_check CHECK ((scope = ANY (ARRAY['family'::text, 'global'::text]))),
    CONSTRAINT structural_variant_filter_presets_family_id_fkey FOREIGN KEY (family_id) REFERENCES families(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_structural_variant_filter_presets_unique ON structural_variant_filter_presets USING btree (COALESCE(family_id, '00000000-0000-0000-0000-000000000000'::uuid), scope, owner, name);

-- ---------------------------------------------------------------------------
-- family_sv_gene_index
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS family_sv_gene_index (
    family_id uuid NOT NULL,
    gene_symbol text NOT NULL,
    sv_count integer NOT NULL,
    svs jsonb NOT NULL,
    CONSTRAINT family_sv_gene_index_pkey PRIMARY KEY (family_id, gene_symbol),
    CONSTRAINT family_sv_gene_index_family_id_fkey FOREIGN KEY (family_id) REFERENCES families(id) ON DELETE CASCADE
);

-- ---------------------------------------------------------------------------
-- family_sv_gene_index_status
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS family_sv_gene_index_status (
    family_id uuid NOT NULL,
    sv_total integer DEFAULT 0 NOT NULL,
    gene_count integer DEFAULT 0 NOT NULL,
    computed_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT family_sv_gene_index_status_pkey PRIMARY KEY (family_id),
    CONSTRAINT family_sv_gene_index_status_family_id_fkey FOREIGN KEY (family_id) REFERENCES families(id) ON DELETE CASCADE
);

-- ---------------------------------------------------------------------------
-- family_variant_ranking_cache (folds superset cols from 036)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS family_variant_ranking_cache (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    family_id uuid NOT NULL,
    inputs_hash text NOT NULL,
    total integer NOT NULL,
    total_is_estimated boolean DEFAULT false NOT NULL,
    ranking_truncated boolean DEFAULT false NOT NULL,
    ranking jsonb NOT NULL,
    provenance jsonb DEFAULT '{}'::jsonb NOT NULL,
    computed_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    base_hash text,
    panel_id text,
    CONSTRAINT family_variant_ranking_cache_pkey PRIMARY KEY (id),
    CONSTRAINT family_variant_ranking_cache_family_id_inputs_hash_key UNIQUE (family_id, inputs_hash),
    CONSTRAINT family_variant_ranking_cache_family_id_fkey FOREIGN KEY (family_id) REFERENCES families(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_family_variant_ranking_cache_base ON family_variant_ranking_cache USING btree (family_id, base_hash);
CREATE INDEX IF NOT EXISTS idx_family_variant_ranking_cache_recent ON family_variant_ranking_cache USING btree (family_id, computed_at DESC);

-- ---------------------------------------------------------------------------
-- qc_threshold_profiles / qc_thresholds (admin-managed sequencing-QC cut-offs)
--
-- Thresholds are grouped into named profiles because a single set cannot serve
-- more than one assay: ~18x mean depth is normal for PacBio HiFi WGS and a hard
-- failure on a 100x panel, so one global cut-off would misflag constantly. A
-- family resolves to the profile named in families.metadata->>'qc_profile', and
-- to the profile flagged is_default otherwise.
--
-- The *metric catalogue* (which metrics exist, their units, and whether a low or
-- a high value is the bad one) lives in code — backend/app/services/qc_threshold_service.py
-- QC_METRICS — because it is determined by what the QC parsers emit, not by
-- configuration. Only the cut-off values are admin-settable, which is also what
-- keeps an operator from inverting a comparison direction by accident.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qc_threshold_profiles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    key text NOT NULL,
    label text NOT NULL,
    description text,
    is_default boolean DEFAULT false NOT NULL,
    sort_order integer DEFAULT 500 NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT qc_threshold_profiles_pkey PRIMARY KEY (id),
    CONSTRAINT qc_threshold_profiles_key_key UNIQUE (key),
    CONSTRAINT qc_threshold_profiles_created_by_fkey FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
);

-- At most one default profile, so threshold resolution is never ambiguous.
CREATE UNIQUE INDEX IF NOT EXISTS qc_threshold_profiles_one_default
    ON qc_threshold_profiles ((is_default)) WHERE is_default;

CREATE TABLE IF NOT EXISTS qc_thresholds (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    profile_id uuid NOT NULL,
    metric_key text NOT NULL,
    -- Either bound may be null: a metric can carry only a warning, only an error,
    -- or (both null) be listed without gating anything.
    warn_value double precision,
    error_value double precision,
    -- Written through from the code catalogue at save time; never read for evaluation.
    -- It is here for legibility: a threshold row that outlives the code version that
    -- wrote it must still be interpretable as a cut-off without checking out that
    -- commit, which is what an IVDR audit of a historical configuration needs.
    direction text DEFAULT 'lower_is_worse'::text NOT NULL,
    updated_by uuid,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT qc_thresholds_pkey PRIMARY KEY (id),
    CONSTRAINT qc_thresholds_direction_check
        CHECK ((direction = ANY (ARRAY['lower_is_worse'::text, 'higher_is_worse'::text]))),
    CONSTRAINT qc_thresholds_profile_metric_key UNIQUE (profile_id, metric_key),
    CONSTRAINT qc_thresholds_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES qc_threshold_profiles(id) ON DELETE CASCADE,
    CONSTRAINT qc_thresholds_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_qc_thresholds_profile ON qc_thresholds USING btree (profile_id);

-- Seed profiles only. The cut-off VALUES are deliberately not seeded: they are a
-- clinical decision for the lab, and shipping invented numbers would make an
-- unreviewed cut-off look authoritative. An empty profile gates nothing, so the
-- QC chip stays neutral until a threshold is entered in the admin page.
INSERT INTO qc_threshold_profiles (key, label, description, is_default, sort_order) VALUES
    ('default', 'Default', 'Applies to any family that does not name a profile.', true, 10),
    ('long_read_wgs', 'Long-read WGS', 'PacBio HiFi / ONT whole-genome runs.', false, 20),
    ('short_read_wgs', 'Short-read WGS', 'Illumina whole-genome runs.', false, 30),
    ('short_read_panel', 'Short-read panel', 'Targeted panels, where expected depth is far higher.', false, 40)
ON CONFLICT (key) DO NOTHING;
