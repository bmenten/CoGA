CREATE TABLE IF NOT EXISTS family_import_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    submitted_path TEXT NOT NULL,
    family_id TEXT,
    project_id UUID REFERENCES projects(id) ON DELETE SET NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'validating', 'running', 'completed', 'failed')),
    dry_run BOOLEAN NOT NULL DEFAULT FALSE,
    worker_id TEXT,
    requested_by TEXT NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    started_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    validation_errors JSONB NOT NULL DEFAULT '[]'::jsonb,
    validation_warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    logs JSONB NOT NULL DEFAULT '[]'::jsonb,
    dataset_summaries JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_family_import_jobs_status
    ON family_import_jobs (status, requested_at);

CREATE INDEX IF NOT EXISTS idx_family_import_jobs_requested
    ON family_import_jobs (requested_at);

CREATE INDEX IF NOT EXISTS idx_family_import_jobs_family
    ON family_import_jobs (family_id);
