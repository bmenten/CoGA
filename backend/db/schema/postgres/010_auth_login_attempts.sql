CREATE TABLE IF NOT EXISTS auth_login_attempts (
    scope_type TEXT NOT NULL,
    scope_value TEXT NOT NULL,
    failure_count INTEGER NOT NULL DEFAULT 0,
    last_failure_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    locked_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    PRIMARY KEY (scope_type, scope_value)
);

CREATE INDEX IF NOT EXISTS idx_auth_login_attempts_locked_until
    ON auth_login_attempts (locked_until DESC);
