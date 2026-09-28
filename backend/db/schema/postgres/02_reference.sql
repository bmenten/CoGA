-- 02_reference.sql — Referentie-/annotatiedata (genoom-annotatie, panels, HPO, Monarch, CNV-KB)
-- Idempotent domein-baseline. Consolideert de originele migraties 001/005/012/013/015/018/019/020/021/022/026/027/034/042.
-- FK-doelen (assemblies, users) leven in 01_core.sql (achterwaartse referenties).

-- ---------------------------------------------------------------------------
-- genes
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS genes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    assembly_id uuid NOT NULL,
    gene_id text NOT NULL,
    hgnc_symbol text NOT NULL,
    chr text NOT NULL,
    start bigint NOT NULL,
    "end" bigint NOT NULL,
    exons jsonb DEFAULT '[]'::jsonb NOT NULL,
    strand integer NOT NULL,
    biotype text NOT NULL,
    description text NOT NULL,
    source text NOT NULL,
    extra jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT genes_pkey PRIMARY KEY (id),
    CONSTRAINT genes_assembly_id_fkey FOREIGN KEY (assembly_id) REFERENCES assemblies(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_genes_assembly_region ON genes USING btree (assembly_id, chr, start, "end");
CREATE INDEX IF NOT EXISTS idx_genes_assembly_symbol_prefix ON genes USING btree (assembly_id, upper(hgnc_symbol) text_pattern_ops);
CREATE INDEX IF NOT EXISTS idx_genes_lower_gene_id ON genes USING btree (lower(gene_id));
CREATE INDEX IF NOT EXISTS idx_genes_lower_symbol ON genes USING btree (lower(hgnc_symbol));
CREATE INDEX IF NOT EXISTS idx_genes_lower_transcript_id ON genes USING btree (lower(COALESCE((extra ->> 'transcript_id'::text), ''::text)));
CREATE INDEX IF NOT EXISTS idx_genes_symbol ON genes USING btree (hgnc_symbol);
CREATE INDEX IF NOT EXISTS idx_genes_upper_gene_id ON genes USING btree (upper(gene_id));
CREATE INDEX IF NOT EXISTS idx_genes_upper_symbol ON genes USING btree (upper(hgnc_symbol));

-- ---------------------------------------------------------------------------
-- gene_info
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gene_info (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    assembly_id uuid NOT NULL,
    hgnc_symbol text NOT NULL,
    gene_id text,
    display_name text,
    summary text,
    aliases jsonb DEFAULT '[]'::jsonb NOT NULL,
    previous_symbols jsonb DEFAULT '[]'::jsonb NOT NULL,
    ensembl_gene_id text,
    ncbi_gene_id text,
    hgnc_id text,
    omim_gene_id text,
    gene_type text,
    location text,
    homologs jsonb DEFAULT '[]'::jsonb NOT NULL,
    source_status jsonb DEFAULT '{}'::jsonb NOT NULL,
    extra jsonb DEFAULT '{}'::jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT gene_info_pkey PRIMARY KEY (id),
    CONSTRAINT gene_info_assembly_id_hgnc_symbol_key UNIQUE (assembly_id, hgnc_symbol),
    CONSTRAINT gene_info_assembly_id_fkey FOREIGN KEY (assembly_id) REFERENCES assemblies(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_gene_info_gene_id ON gene_info USING btree (gene_id);
CREATE INDEX IF NOT EXISTS idx_gene_info_updated_at ON gene_info USING btree (updated_at);
CREATE INDEX IF NOT EXISTS idx_gene_info_upper_symbol ON gene_info USING btree (upper(hgnc_symbol));

-- ---------------------------------------------------------------------------
-- gene_info_refresh_jobs
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gene_info_refresh_jobs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    scope text NOT NULL,
    symbol text,
    status text NOT NULL,
    active_slot text,
    worker_id text,
    requested_by text NOT NULL,
    requested_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    started_at timestamp with time zone,
    heartbeat_at timestamp with time zone,
    completed_at timestamp with time zone,
    total_symbols integer DEFAULT 0 NOT NULL,
    completed_symbols integer DEFAULT 0 NOT NULL,
    updated_records integer DEFAULT 0 NOT NULL,
    human_assemblies integer DEFAULT 0 NOT NULL,
    current_symbol text,
    error text,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT gene_info_refresh_jobs_pkey PRIMARY KEY (id),
    CONSTRAINT gene_info_refresh_jobs_active_slot_key UNIQUE (active_slot),
    CONSTRAINT gene_info_refresh_jobs_scope_check CHECK ((scope = ANY (ARRAY['symbol'::text, 'all_human'::text]))),
    CONSTRAINT gene_info_refresh_jobs_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'running'::text, 'completed'::text, 'failed'::text])))
);

CREATE INDEX IF NOT EXISTS idx_gene_info_refresh_jobs_requested ON gene_info_refresh_jobs USING btree (requested_at);
CREATE INDEX IF NOT EXISTS idx_gene_info_refresh_jobs_status ON gene_info_refresh_jobs USING btree (status, requested_at);

-- ---------------------------------------------------------------------------
-- blacklist
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS blacklist (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    assembly_id uuid NOT NULL,
    chr text NOT NULL,
    start bigint NOT NULL,
    "end" bigint NOT NULL,
    label text NOT NULL,
    CONSTRAINT blacklist_pkey PRIMARY KEY (id),
    CONSTRAINT blacklist_assembly_id_fkey FOREIGN KEY (assembly_id) REFERENCES assemblies(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_blacklist_assembly_region ON blacklist USING btree (assembly_id, chr, start);

-- ---------------------------------------------------------------------------
-- clinical_cnvs  (verrijkt via 018/020)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS clinical_cnvs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    assembly_id uuid NOT NULL,
    chr text NOT NULL,
    start bigint NOT NULL,
    "end" bigint NOT NULL,
    type text,
    label text NOT NULL,
    details_html text,
    omim_id text,
    decipher_id text,
    description text,
    cytoband text,
    source_id text,
    omim_title text,
    orpha_id text,
    orpha_name text,
    CONSTRAINT clinical_cnvs_pkey PRIMARY KEY (id),
    CONSTRAINT clinical_cnvs_assembly_id_fkey FOREIGN KEY (assembly_id) REFERENCES assemblies(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_clinical_cnvs_assembly_region ON clinical_cnvs USING btree (assembly_id, chr, start);

-- ---------------------------------------------------------------------------
-- clinical_cnv_kb_jobs
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS clinical_cnv_kb_jobs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    assembly_id uuid NOT NULL,
    assembly_name text NOT NULL,
    status text DEFAULT 'queued'::text NOT NULL,
    skip_clinvar boolean DEFAULT false NOT NULL,
    requested_by text,
    requested_at timestamp with time zone DEFAULT now() NOT NULL,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    inserted integer DEFAULT 0 NOT NULL,
    error text,
    log text,
    CONSTRAINT clinical_cnv_kb_jobs_pkey PRIMARY KEY (id),
    CONSTRAINT clinical_cnv_kb_jobs_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'running'::text, 'completed'::text, 'failed'::text]))),
    CONSTRAINT clinical_cnv_kb_jobs_assembly_id_fkey FOREIGN KEY (assembly_id) REFERENCES assemblies(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_clinical_cnv_kb_jobs_active ON clinical_cnv_kb_jobs USING btree (status) WHERE (status = ANY (ARRAY['queued'::text, 'running'::text]));
CREATE INDEX IF NOT EXISTS idx_clinical_cnv_kb_jobs_requested_at ON clinical_cnv_kb_jobs USING btree (requested_at DESC);

-- ---------------------------------------------------------------------------
-- dgv_variants
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dgv_variants (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    assembly_id uuid NOT NULL,
    chr text NOT NULL,
    start bigint NOT NULL,
    "end" bigint NOT NULL,
    accession text,
    variant_type text,
    variant_subtype text,
    variant_class text DEFAULT 'other'::text NOT NULL,
    frequency double precision,
    observed_gains integer,
    observed_losses integer,
    sample_size integer,
    source text,
    CONSTRAINT dgv_variants_pkey PRIMARY KEY (id),
    CONSTRAINT dgv_variants_assembly_id_fkey FOREIGN KEY (assembly_id) REFERENCES assemblies(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_dgv_variants_assembly_region ON dgv_variants USING btree (assembly_id, chr, start);

-- ---------------------------------------------------------------------------
-- segmental_duplications
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS segmental_duplications (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    assembly_id uuid NOT NULL,
    chr text NOT NULL,
    start bigint NOT NULL,
    "end" bigint NOT NULL,
    label text NOT NULL,
    source text,
    CONSTRAINT segmental_duplications_pkey PRIMARY KEY (id),
    CONSTRAINT segmental_duplications_assembly_id_fkey FOREIGN KEY (assembly_id) REFERENCES assemblies(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_segmental_duplications_assembly_region ON segmental_duplications USING btree (assembly_id, chr, start);

-- ---------------------------------------------------------------------------
-- gene_panels  (kolommen gevouwen uit 005/013/034)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gene_panels (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    description text,
    source text DEFAULT 'local'::text NOT NULL,
    external_id text,
    external_version text,
    external_url text,
    source_updated_at timestamp with time zone,
    source_metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_by uuid NOT NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    CONSTRAINT gene_panels_pkey PRIMARY KEY (id),
    CONSTRAINT gene_panels_name_key UNIQUE (name),
    CONSTRAINT gene_panels_created_by_fkey FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_gene_panels_source_external ON gene_panels USING btree (source, external_id) WHERE (external_id IS NOT NULL);

-- ---------------------------------------------------------------------------
-- gene_panel_genes
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gene_panel_genes (
    panel_id uuid NOT NULL,
    gene_symbol text NOT NULL,
    CONSTRAINT gene_panel_genes_pkey PRIMARY KEY (panel_id, gene_symbol),
    CONSTRAINT gene_panel_genes_panel_id_fkey FOREIGN KEY (panel_id) REFERENCES gene_panels(id) ON DELETE CASCADE
);

-- ---------------------------------------------------------------------------
-- gene_panel_regions
-- ---------------------------------------------------------------------------
-- A panel's coordinates, one row per gene (or PanelApp region) per assembly. A family's
-- panel filter reads only its own assembly's rows (#515, TF-06 H12). The mtDNA has the
-- same coordinates in GRCh38 and T2T, so the assembly is part of the key.
CREATE TABLE IF NOT EXISTS gene_panel_regions (
    panel_id uuid NOT NULL,
    assembly_id uuid NOT NULL,
    gene text NOT NULL,
    chr text NOT NULL,
    start bigint NOT NULL,
    "end" bigint NOT NULL,
    CONSTRAINT gene_panel_regions_pkey PRIMARY KEY (panel_id, assembly_id, gene, chr, start, "end"),
    CONSTRAINT gene_panel_regions_panel_id_fkey FOREIGN KEY (panel_id) REFERENCES gene_panels(id) ON DELETE CASCADE,
    CONSTRAINT gene_panel_regions_assembly_id_fkey FOREIGN KEY (assembly_id) REFERENCES assemblies(id) ON DELETE CASCADE
);

-- Upgrade a table created before regions were scoped by assembly (#515). A row copied
-- from the gene reference is kept once for every assembly whose gene record it matches
-- exactly (the mtDNA matches in both GRCh38 and T2T); a row that matches none
-- (PanelApp's own coordinates) cannot be attributed safely and is dropped: the panel's
-- gene list is untouched, the family filter resolves gene loci per assembly at query
-- time, and a re-import restores PanelApp coordinates. A no-op once the column exists.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'gene_panel_regions'
          AND column_name = 'assembly_id'
    ) THEN
        ALTER TABLE gene_panel_regions ADD COLUMN assembly_id uuid;
        ALTER TABLE gene_panel_regions DROP CONSTRAINT gene_panel_regions_pkey;
        INSERT INTO gene_panel_regions (panel_id, assembly_id, gene, chr, start, "end")
        SELECT DISTINCT r.panel_id, g.assembly_id, r.gene, r.chr, r.start, r."end"
        FROM gene_panel_regions r
        JOIN genes g
          ON upper(g.hgnc_symbol) = upper(r.gene)
         AND g.chr = r.chr
         AND g.start = r.start
         AND g."end" = r."end"
        WHERE r.assembly_id IS NULL;
        DELETE FROM gene_panel_regions WHERE assembly_id IS NULL;
        ALTER TABLE gene_panel_regions ALTER COLUMN assembly_id SET NOT NULL;
        ALTER TABLE gene_panel_regions
            ADD CONSTRAINT gene_panel_regions_pkey
            PRIMARY KEY (panel_id, assembly_id, gene, chr, start, "end");
        ALTER TABLE gene_panel_regions
            ADD CONSTRAINT gene_panel_regions_assembly_id_fkey
            FOREIGN KEY (assembly_id) REFERENCES assemblies(id) ON DELETE CASCADE;
    END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- gene_panel_versions  (034)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gene_panel_versions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    panel_id uuid NOT NULL,
    version integer NOT NULL,
    name text NOT NULL,
    description text,
    source text,
    external_version text,
    gene_count integer NOT NULL,
    genes jsonb DEFAULT '[]'::jsonb NOT NULL,
    regions jsonb DEFAULT '[]'::jsonb NOT NULL,
    source_metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_by uuid,
    created_by_email text,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT gene_panel_versions_pkey PRIMARY KEY (id),
    CONSTRAINT gene_panel_versions_panel_id_version_key UNIQUE (panel_id, version),
    CONSTRAINT gene_panel_versions_created_by_fkey FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT gene_panel_versions_panel_id_fkey FOREIGN KEY (panel_id) REFERENCES gene_panels(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_gene_panel_versions_panel ON gene_panel_versions USING btree (panel_id, version DESC);

-- ---------------------------------------------------------------------------
-- hpo_term  (015)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS hpo_term (
    hpo_id text NOT NULL,
    label text NOT NULL,
    definition text,
    is_obsolete boolean DEFAULT false NOT NULL,
    replaced_by text,
    release_version text,
    release_date date,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT hpo_term_pkey PRIMARY KEY (hpo_id),
    CONSTRAINT hpo_term_hpo_id_check CHECK ((hpo_id ~ '^HP:[0-9]{7}$'::text))
);

CREATE INDEX IF NOT EXISTS idx_hpo_term_label_lower ON hpo_term USING btree (lower(label));

-- ---------------------------------------------------------------------------
-- hpo_synonym
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS hpo_synonym (
    hpo_id text NOT NULL,
    synonym text NOT NULL,
    CONSTRAINT hpo_synonym_pkey PRIMARY KEY (hpo_id, synonym),
    CONSTRAINT hpo_synonym_hpo_id_fkey FOREIGN KEY (hpo_id) REFERENCES hpo_term(hpo_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_hpo_synonym_lower ON hpo_synonym USING btree (lower(synonym));

-- ---------------------------------------------------------------------------
-- hpo_edge
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS hpo_edge (
    child_id text NOT NULL,
    parent_id text NOT NULL,
    relation text DEFAULT 'is_a'::text NOT NULL,
    CONSTRAINT hpo_edge_pkey PRIMARY KEY (child_id, parent_id, relation),
    CONSTRAINT hpo_edge_child_id_fkey FOREIGN KEY (child_id) REFERENCES hpo_term(hpo_id) ON DELETE CASCADE,
    CONSTRAINT hpo_edge_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES hpo_term(hpo_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_hpo_edge_parent ON hpo_edge USING btree (parent_id);

-- ---------------------------------------------------------------------------
-- hpo_closure
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS hpo_closure (
    hpo_id text NOT NULL,
    ancestor_id text NOT NULL,
    distance integer NOT NULL,
    CONSTRAINT hpo_closure_pkey PRIMARY KEY (hpo_id, ancestor_id),
    CONSTRAINT hpo_closure_distance_check CHECK ((distance >= 0)),
    CONSTRAINT hpo_closure_ancestor_id_fkey FOREIGN KEY (ancestor_id) REFERENCES hpo_term(hpo_id) ON DELETE CASCADE,
    CONSTRAINT hpo_closure_hpo_id_fkey FOREIGN KEY (hpo_id) REFERENCES hpo_term(hpo_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_hpo_closure_ancestor ON hpo_closure USING btree (ancestor_id);

-- ---------------------------------------------------------------------------
-- monarch_gene_disease  (026)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS monarch_gene_disease (
    hgnc_id text NOT NULL,
    gene_symbol text NOT NULL,
    mondo_id text NOT NULL,
    disease_label text,
    predicate text NOT NULL,
    predicates jsonb DEFAULT '[]'::jsonb NOT NULL,
    sources jsonb DEFAULT '[]'::jsonb NOT NULL,
    causal boolean DEFAULT false NOT NULL,
    release_version text,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT monarch_gene_disease_pkey PRIMARY KEY (hgnc_id, mondo_id)
);

CREATE INDEX IF NOT EXISTS idx_monarch_gene_disease_mondo ON monarch_gene_disease USING btree (mondo_id);
CREATE INDEX IF NOT EXISTS idx_monarch_gene_disease_symbol ON monarch_gene_disease USING btree (upper(gene_symbol));

-- ---------------------------------------------------------------------------
-- monarch_disease_phenotype  (027)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS monarch_disease_phenotype (
    mondo_id text NOT NULL,
    disease_label text,
    hpo_id text NOT NULL,
    phenotype_label text,
    negated boolean DEFAULT false NOT NULL,
    sources jsonb DEFAULT '[]'::jsonb NOT NULL,
    release_version text,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT monarch_disease_phenotype_pkey PRIMARY KEY (mondo_id, hpo_id)
);

CREATE INDEX IF NOT EXISTS idx_monarch_disease_phenotype_hpo ON monarch_disease_phenotype USING btree (hpo_id);

-- ---------------------------------------------------------------------------
-- repeat_loci
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS repeat_loci (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    locus_id text NOT NULL,
    gene text NOT NULL,
    display_name text NOT NULL,
    disease text NOT NULL,
    inheritance text,
    motif text,
    motif_index integer DEFAULT 0 NOT NULL,
    warning_min integer,
    pathogenic_min integer,
    x_linked boolean DEFAULT false NOT NULL,
    aliases jsonb DEFAULT '[]'::jsonb NOT NULL,
    notes text,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT repeat_loci_pkey PRIMARY KEY (id),
    CONSTRAINT repeat_loci_locus_id_key UNIQUE (locus_id)
);

CREATE INDEX IF NOT EXISTS idx_repeat_loci_gene ON repeat_loci USING btree (gene);

-- ---------------------------------------------------------------------------
-- reference_dataset_imports  (021)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reference_dataset_imports (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    assembly_id uuid NOT NULL,
    dataset_type text NOT NULL,
    inserted integer DEFAULT 0 NOT NULL,
    replaced boolean DEFAULT false NOT NULL,
    source text,
    performed_by text,
    performed_at timestamp with time zone DEFAULT now() NOT NULL,
    source_version text,
    source_release_date date,
    source_url text,
    CONSTRAINT reference_dataset_imports_pkey PRIMARY KEY (id),
    CONSTRAINT reference_dataset_imports_assembly_id_fkey FOREIGN KEY (assembly_id) REFERENCES assemblies(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_reference_dataset_imports_latest ON reference_dataset_imports USING btree (assembly_id, dataset_type, performed_at DESC);
