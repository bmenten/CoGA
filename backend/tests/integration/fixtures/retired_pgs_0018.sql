-- Historical schema fixture for testing revision 0019 only; never loaded by the app.
-- Loaded by the baseline and revision 0013. Exploratory records never enter sign-out.
CREATE TABLE IF NOT EXISTS pgs_models (
    pgs_id text PRIMARY KEY CHECK (pgs_id ~ '^PGS[0-9]{6}$'),
    archived boolean NOT NULL DEFAULT false
);
CREATE TABLE IF NOT EXISTS pgs_model_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pgs_id text NOT NULL REFERENCES pgs_models(pgs_id),
    version text NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    study text NOT NULL,
    source_url text NOT NULL,
    genome_build text NOT NULL DEFAULT 'GRCh38' CHECK (genome_build = 'GRCh38'),
    weights_sha256 text NOT NULL,
    resolved_sha256 text NOT NULL,
    reference jsonb NOT NULL,
    weights_text text NOT NULL,
    weights jsonb NOT NULL,
    variant_count integer NOT NULL CHECK (variant_count > 0),
    catalog_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    scoring_status text NOT NULL DEFAULT 'ready' CHECK (scoring_status IN ('ready', 'needs_reference', 'unsupported')),
    scoring_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pgs_id, version),
    UNIQUE (pgs_id, revision)
);
-- Existing immutable versions keep their weights and acquire only defaulted metadata.
ALTER TABLE pgs_model_versions ADD COLUMN IF NOT EXISTS catalog_metadata jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE pgs_model_versions ADD COLUMN IF NOT EXISTS scoring_status text NOT NULL DEFAULT 'ready'
    CHECK (scoring_status IN ('ready', 'needs_reference', 'unsupported'));
ALTER TABLE pgs_model_versions ADD COLUMN IF NOT EXISTS scoring_error text;
ALTER TABLE pgs_model_versions DROP CONSTRAINT IF EXISTS pgs_model_versions_prepared_check,
    ADD CONSTRAINT pgs_model_versions_prepared_check CHECK (
        (scoring_status = 'ready' AND jsonb_array_length(weights) = variant_count)
        OR (scoring_status <> 'ready' AND jsonb_array_length(weights) = 0)
    );
CREATE TABLE IF NOT EXISTS pgs_catalog_imports (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pgs_id text NOT NULL CHECK (pgs_id ~ '^PGS[0-9]{6}$'),
    requested_by uuid NOT NULL,
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'failed', 'interrupted')),
    version_id uuid REFERENCES pgs_model_versions(id),
    error text,
    requested_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz
);
CREATE INDEX IF NOT EXISTS pgs_catalog_imports_queue ON pgs_catalog_imports(status, requested_at);
CREATE TABLE IF NOT EXISTS pgs_sources (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    family_uuid uuid NOT NULL,
    family_identifier text NOT NULL,
    project_id uuid NOT NULL,
    import_job_id uuid,
    package_path text NOT NULL,
    relative_vcf text NOT NULL,
    sha256 text NOT NULL,
    storage_version text NOT NULL,
    storage_fingerprint text NOT NULL,
    samples jsonb NOT NULL,
    absent_record_is_hom_ref boolean NOT NULL,
    file_size bigint NOT NULL,
    file_mtime_ns bigint NOT NULL,
    raw_file_id uuid,
    qualification jsonb NOT NULL DEFAULT '{}'::jsonb,
    imported_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS pgs_sources_family ON pgs_sources(family_uuid, project_id, imported_at DESC);
CREATE TABLE IF NOT EXISTS pgs_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    family_uuid uuid NOT NULL,
    family_identifier text NOT NULL,
    project_id uuid NOT NULL,
    source_id uuid NOT NULL REFERENCES pgs_sources(id),
    version_ids jsonb NOT NULL,
    requested_by uuid NOT NULL,
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'failed', 'interrupted')),
    error text,
    requested_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz
);
CREATE INDEX IF NOT EXISTS pgs_runs_queue ON pgs_runs(status, requested_at);
CREATE TABLE IF NOT EXISTS pgs_results (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL REFERENCES pgs_runs(id),
    source_id uuid NOT NULL REFERENCES pgs_sources(id),
    version_id uuid NOT NULL REFERENCES pgs_model_versions(id),
    sample_id text NOT NULL,
    raw_score double precision,
    status text NOT NULL CONSTRAINT pgs_results_status_check CHECK (status IN ('complete', 'partial', 'incomplete')),
    qc jsonb NOT NULL,
    calculation_version text NOT NULL,
    assumptions jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, version_id, sample_id),
    CONSTRAINT pgs_results_score_status_check CHECK ((status IN ('complete', 'partial')) = (raw_score IS NOT NULL))
);

-- No cascade-mutated columns: these records keep their original identifiers even after
-- a family/project/account is deleted. Access always resolves a *current* family/project.
CREATE OR REPLACE FUNCTION pgs_model_versions_block_mutation() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE'
       AND current_setting('coga.delete_unused_pgs', true) = OLD.pgs_id
       AND NOT public.pgs_model_has_family_use(OLD.pgs_id) THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'PGS model versions are append-only';
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS pgs_model_versions_no_mutation ON pgs_model_versions;
CREATE TRIGGER pgs_model_versions_no_mutation BEFORE UPDATE OR DELETE ON pgs_model_versions
    FOR EACH ROW EXECUTE FUNCTION pgs_model_versions_block_mutation();
CREATE OR REPLACE FUNCTION pgs_sources_block_mutation() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'PGS WGS sources are append-only'; END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS pgs_sources_no_mutation ON pgs_sources;
CREATE TRIGGER pgs_sources_no_mutation BEFORE UPDATE OR DELETE ON pgs_sources
    FOR EACH ROW EXECUTE FUNCTION pgs_sources_block_mutation();
CREATE OR REPLACE FUNCTION pgs_results_block_mutation() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'PGS results are append-only'; END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS pgs_results_no_mutation ON pgs_results;
CREATE TRIGGER pgs_results_no_mutation BEFORE UPDATE OR DELETE ON pgs_results
    FOR EACH ROW EXECUTE FUNCTION pgs_results_block_mutation();

-- Family requests pin models before an unused model can be removed. The same
-- parent lock serializes requests, publication and administrative deletion.
CREATE OR REPLACE FUNCTION pgs_runs_pin_models() RETURNS trigger AS $$
DECLARE selected_version text;
BEGIN
    IF jsonb_typeof(NEW.version_ids) <> 'array' THEN
        RAISE EXCEPTION 'PGS model versions must be an array' USING ERRCODE = '23514';
    END IF;
    FOR selected_version IN SELECT jsonb_array_elements_text(NEW.version_ids) ORDER BY 1 LOOP
        PERFORM 1 FROM public.pgs_models m JOIN public.pgs_model_versions v USING (pgs_id)
        WHERE v.id::text = selected_version FOR KEY SHARE OF m;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'A selected PGS model no longer exists; reload before calculating' USING ERRCODE = '23503';
        END IF;
    END LOOP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp;
DROP TRIGGER IF EXISTS pgs_runs_pin_models ON pgs_runs;
CREATE TRIGGER pgs_runs_pin_models BEFORE INSERT ON pgs_runs
    FOR EACH ROW EXECUTE FUNCTION pgs_runs_pin_models();

CREATE OR REPLACE FUNCTION pgs_model_has_family_use(target text) RETURNS boolean AS $$
    SELECT EXISTS (SELECT 1 FROM public.pgs_results r JOIN public.pgs_model_versions v ON v.id = r.version_id WHERE v.pgs_id = target)
        OR EXISTS (SELECT 1 FROM public.pgs_runs r JOIN public.pgs_model_versions v ON r.version_ids ? v.id::text WHERE v.pgs_id = target);
$$ LANGUAGE sql VOLATILE SET search_path = pg_catalog, public, pg_temp;

CREATE OR REPLACE FUNCTION delete_unused_pgs_model(target text) RETURNS text AS $$
BEGIN
    IF target !~ '^PGS[0-9]{6}$' THEN
        RAISE EXCEPTION 'Invalid PGS ID' USING ERRCODE = '22023';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtext('pgs-catalog:' || target));
    PERFORM 1 FROM public.pgs_models WHERE pgs_id = target FOR UPDATE;
    IF NOT FOUND THEN RETURN 'not_found'; END IF;
    IF public.pgs_model_has_family_use(target) THEN RETURN 'in_use'; END IF;
    IF EXISTS (SELECT 1 FROM public.pgs_catalog_imports WHERE pgs_id = target AND status IN ('queued', 'running')) THEN
        RETURN 'import_busy';
    END IF;
    UPDATE public.pgs_catalog_imports SET version_id = NULL
        WHERE version_id IN (SELECT id FROM public.pgs_model_versions WHERE pgs_id = target);
    PERFORM set_config('coga.delete_unused_pgs', target, true);
    DELETE FROM public.pgs_model_versions WHERE pgs_id = target;
    DELETE FROM public.pgs_models WHERE pgs_id = target;
    PERFORM set_config('coga.delete_unused_pgs', '', true);
    RETURN 'deleted';
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp;

CREATE TABLE IF NOT EXISTS pgs_result_dismissals (
    result_id uuid PRIMARY KEY REFERENCES pgs_results(id),
    dismissed_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE OR REPLACE FUNCTION pgs_result_dismissals_guard() RETURNS trigger AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.pgs_results WHERE id = NEW.result_id AND status = 'incomplete') THEN
        RAISE EXCEPTION 'Only incomplete PGS results can be removed from the overview' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp;
DROP TRIGGER IF EXISTS pgs_result_dismissals_guard ON pgs_result_dismissals;
CREATE TRIGGER pgs_result_dismissals_guard BEFORE INSERT ON pgs_result_dismissals
    FOR EACH ROW EXECUTE FUNCTION pgs_result_dismissals_guard();
CREATE OR REPLACE FUNCTION pgs_result_dismissals_block_mutation() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'PGS score removal records are append-only'; END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS pgs_result_dismissals_no_mutation ON pgs_result_dismissals;
CREATE TRIGGER pgs_result_dismissals_no_mutation BEFORE UPDATE OR DELETE ON pgs_result_dismissals
    FOR EACH ROW EXECUTE FUNCTION pgs_result_dismissals_block_mutation();
