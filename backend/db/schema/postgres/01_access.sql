-- 01_access.sql — Genome foundation + identity/authorization (species, assemblies, chromosomes, users, projects, project_users, auth_login_attempts)

CREATE EXTENSION IF NOT EXISTS pgcrypto;

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

CREATE TABLE IF NOT EXISTS chromosomes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    assembly_id UUID NOT NULL REFERENCES assemblies(id) ON DELETE CASCADE,
    chr TEXT NOT NULL,
    size BIGINT NOT NULL,
    bands JSONB NOT NULL DEFAULT '[]'::jsonb,
    UNIQUE (assembly_id, chr)
);

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username TEXT NOT NULL UNIQUE,
    hashed_password TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'superuser', 'viewer')),
    email TEXT NOT NULL UNIQUE,
    first_name TEXT NOT NULL DEFAULT '',
    last_name TEXT NOT NULL DEFAULT '',
    affiliation TEXT NOT NULL DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    -- The Microsoft Entra ID (Azure AD) object id (`oid`) of an account made at its first
    -- Microsoft sign-in (SEC-4): such an account is found by it, never by its e-mail address,
    -- and has no password (hashed_password holds a marker no password matches). NULL for a
    -- local account: the emergency administrator, and every account without Azure AD.
    azure_object_id UUID UNIQUE
);

-- A database made before Microsoft sign-in gains the column here; a no-op on a database the
-- CREATE TABLE above made (the same column, with the same users_azure_object_id_key).
ALTER TABLE users ADD COLUMN IF NOT EXISTS azure_object_id UUID UNIQUE;

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
