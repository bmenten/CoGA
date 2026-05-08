CREATE TABLE IF NOT EXISTS audit_log_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    user_email TEXT,
    user_role TEXT,
    method TEXT NOT NULL,
    route_path TEXT,
    path TEXT NOT NULL,
    query_string TEXT,
    status_code INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    remote_ip TEXT,
    user_agent TEXT,
    referer TEXT,
    protocol TEXT,
    request_body JSONB,
    request_meta JSONB NOT NULL DEFAULT '{}'::jsonb,
    db_update JSONB,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_log_events_created_at
    ON audit_log_events (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_log_events_user_email
    ON audit_log_events (user_email);

CREATE INDEX IF NOT EXISTS idx_audit_log_events_route_path
    ON audit_log_events (route_path);
