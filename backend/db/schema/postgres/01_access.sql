-- 01_access.sql — Genome foundation + identity/authorization (species, assemblies, chromosomes, users, projects, project_users with each member's access level, auth_login_attempts)

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

-- An account's global role, lowest first, each with every right of the ones before it
-- (services/access_control.py): a viewer reads the records of their projects; an analyst also
-- analyses them; a supervisor also signs an analysis out; a superuser also has every project
-- and the configuration the analyses read; an admin, the whole administration.
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username TEXT NOT NULL UNIQUE,
    hashed_password TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('viewer', 'analyst', 'supervisor', 'superuser', 'admin')),
    email TEXT NOT NULL UNIQUE,
    first_name TEXT NOT NULL DEFAULT '',
    last_name TEXT NOT NULL DEFAULT '',
    affiliation TEXT NOT NULL DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    -- The Microsoft Entra ID (Azure AD) object id (`oid`) of an account made at its first
    -- Microsoft sign-in: such an account is found by it, never by its e-mail address,
    -- and has no password (hashed_password holds a marker no password matches). NULL for a
    -- local account: the emergency administrator, and every account without Azure AD.
    azure_object_id UUID UNIQUE,
    -- When the account was first active: when it was made, for an account made active, or when
    -- an administrator first activated it, for one that waited for approval (a sign-up, a
    -- first CAS sign-in), which leaves it NULL until then. Only an account that was never
    -- active can be deleted; any other is archived.
    activated_at TIMESTAMPTZ DEFAULT timezone('utc', now()),
    -- When an administrator archived the account: it leaves the Users list's default view,
    -- signs in to nothing, and keeps its row, so every record that names it still does, and a
    -- later sign-in of the same person makes no new request. NULL when it is not archived.
    archived_at TIMESTAMPTZ,
    -- When the account last signed out (POST /api/auth/logout): every token issued to it
    -- before then, CoGA's own and Microsoft's, is refused (dependencies.get_current_user), so a
    -- copied token stops working at the logout, not at its expiry. NULL until the first logout.
    sessions_ended_at TIMESTAMPTZ,
    CONSTRAINT users_archived_inactive_check CHECK (archived_at IS NULL OR NOT is_active)
);

CREATE TABLE IF NOT EXISTS projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    species_id UUID NOT NULL REFERENCES species(id) ON DELETE RESTRICT,
    assembly_id UUID NOT NULL REFERENCES assemblies(id) ON DELETE RESTRICT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
);

-- A project's members and what each may do in it: an analyst reads and changes the
-- project's records, a reader only reads them. The level only takes rights away: a member whose
-- role (users.role) is viewer changes nothing. A superuser or an admin has every right in every
-- project, member or not.
CREATE TABLE IF NOT EXISTS project_users (
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    access_level TEXT NOT NULL DEFAULT 'analyst' CHECK (access_level IN ('analyst', 'reader')),
    PRIMARY KEY (project_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_project_users_user_id ON project_users (user_id);

CREATE TABLE IF NOT EXISTS auth_login_attempts (
    scope_type TEXT NOT NULL,
    scope_value TEXT NOT NULL,
    failure_count INTEGER NOT NULL DEFAULT 0,
    last_failure_at TIMESTAMPTZ,
    locked_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    PRIMARY KEY (scope_type, scope_value)
);

CREATE INDEX IF NOT EXISTS idx_auth_login_attempts_locked_until
    ON auth_login_attempts (locked_until DESC);
