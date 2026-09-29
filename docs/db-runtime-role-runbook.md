# Runbook — flip the application to the restricted DB role (`coga_app`)

Schema `05_grants.sql` creates a **restricted runtime role
`coga_app`** and revokes `UPDATE`/`DELETE`/`TRUNCATE` on the append-only tables
(`audit_log_events`, `clinical_audit_events`, `report_signouts`). It ships in
**fallback mode**: the role exists (`NOLOGIN`) but the application still connects as the
table **owner**, so nothing changes at runtime yet.

This runbook performs the **coordinated DSN flip** that makes the application connect as
`coga_app`. That is what actually closes P1-4's owner-bypass gap: as a non-owner the
runtime role cannot `ALTER TABLE … DISABLE TRIGGER`, cannot `SET session_replication_role`
(superuser-only), and cannot `UPDATE`/`DELETE` the append-only tables — so it can neither
edit/remove an audit row or signed report nor re-chain an interior edit.

The **application side of the split is already implemented** — the flip is a configuration
change, no code change:

- Setting `POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP=false` makes the app **skip** the
  owner-only schema DDL + admin seed on startup, so it can boot as `coga_app` without
  crash-looping on DDL it is not allowed to run. Left at its default (`true`) the app
  self-migrates as the owner — the current single-DSN deployment, unchanged.
- The owner-privileged migration is exposed as a standalone entrypoint,
  `python -m backend.app.db_migrate` (`python -m app.db_migrate` inside the backend image).
  It applies the schema and seeds the admin user, and with `POSTGRES_APP_PASSWORD` set it
  also enables `coga_app`'s login (step 2). Both the startup path and this entrypoint call
  the same `init_postgres_schema` / `init_postgres_admin_user`, so there is one source of
  truth for the schema apply.
- On Google Cloud the whole switch is one Terraform variable, `db_runtime_role` (see
  "Google Cloud" below).

> **Regulatory note (IVDR / TF-09b REQ-TRACE-008):** until this flip is done, the running
> application credential *is* the owner and the owner-bypass tampering remains undetectable
> by the hash chain. The flip + the deferred external chain-head anchor together close that
> gap. Treat this as a change-controlled deployment (CMGG SOP H11.1-OP5).

## Invariant: migrations stay as owner, the app runs as `coga_app`

| Connection | Role | Why |
| --- | --- | --- |
| Schema migrations / `python -m backend.app.db_migrate` | **owner** (current `DATABASE_URL` role) | needs `CREATE`/`ALTER TABLE`, trigger management, `GRANT` |
| Application runtime | **`coga_app`** | least privilege; cannot bypass the append-only controls |

Do **not** make migrations run as `coga_app` — it deliberately cannot create or alter
tables or manage triggers.

## Steps

1. **Pick a secret.** Generate a strong password for `coga_app` and store it in the secret
   manager the deployment already uses (do **not** commit it). 
2. **Enable login** (run once, as a role with the rights to do so — a superuser, or a role
   with `CREATEROLE` and `ADMIN OPTION` on `coga_app`). Either give the migration step
   (step 5) the password as `POSTGRES_APP_PASSWORD`, which does this itself and hands
   Postgres only a SCRAM-SHA-256 verifier (as `psql`'s `\password` does, so the plaintext is
   in no statement or log), or run it by hand:
   ```sql
   ALTER ROLE coga_app WITH LOGIN PASSWORD '<from-secret-manager>';
   ```
   (Leaving it `NOLOGIN` until now means no passwordless login ever existed.)
3. **Split the DSNs and disable startup migrations.**
   - Keep the existing owner DSN as a **migration-only** secret (used by the migration/CI
     step that runs `python -m backend.app.db_migrate`).
   - Point the **application** connection at `coga_app` (`POSTGRES_USER=coga_app` +
     the secret password, or a `coga_app` `DATABASE_URL`).
   - Set `POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP=false` on the app (Terraform:
     `run_db_schema_migrations_on_startup = false`) so it no longer attempts owner-only DDL
     at boot.
4. **Connection pooler (PgBouncer / RDS Proxy), if any.** Configure the app's pool to
   authenticate as `coga_app`. Transaction-pooling mode is fine — the app connects
   *directly* as `coga_app` (no `SET ROLE`), so nothing leaks across pooled sessions.
5. **Deploy.** Run the migration step first, **as the owner**:
   `python -m backend.app.db_migrate` (applies the schema + seeds the admin user). Then start
   the app (as `coga_app`, with startup migrations disabled).
6. **Verify** (see below). 
7. **Rollback** (if needed): repoint the application connection back to the owner role and
   set `POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP=true` again, then redeploy. No schema
   change is involved, so rollback is immediate.

## Google Cloud (Terraform)

On Google Cloud the database has only a private address, so the owner-only steps run
inside the VPC as a Cloud Run job, `coga-db-migrate` ([terraform/migrate.tf](../terraform/migrate.tf)),
and the switch is the variable `db_runtime_role`.

**Before** (once):

1. **First deployment in owner mode** (`db_runtime_role = "owner"`, the default). The app
   applies the schema on startup, which creates `coga_app` without a login.
2. **Service account.** The central infra repo creates `coga-db-migrate` with
   `roles/cloudsql.client`, and gives the deploy pipeline's account
   `roles/iam.serviceAccountUser` on it
   ([coga-prerequisites.tf.example](../terraform/main-repo-reference/coga-prerequisites.tf.example)).
   It is separate from the backend's account so the API never holds the owner's password.
3. **Password.** Add a version to the `coga-postgres-app-password` secret: a generated,
   printable-ASCII value, for example `openssl rand -base64 36 | tr -d '\n'`.

**Switch:** set `db_runtime_role = "coga_app"` (in CI: the `gcp-deploy` environment's
`COGA_TFVARS` variable) and deploy, as a change-controlled deployment. That one apply:

- grants `coga-db-migrate` the secrets it reads and creates the job: the backend image
  running `python -m app.db_migrate` as the owner, over the Cloud SQL connector;
- runs the job and waits for it. It applies the schema, seeds the admin user and enables
  `coga_app`'s login from `POSTGRES_APP_PASSWORD` (step 2);
- only then rolls the backend out as `coga_app` with startup migrations off, and removes
  the backend account's access to the owner's password.

Every later deploy of a new backend image runs the job first. If it fails, the apply
stops and the running revision keeps serving.

**Verify:** the checks under "Verification" below. Connecting directly as `coga_app` needs a
client inside the VPC, so the automated proof is the CI smoke test, which takes the same
path. Also confirm that the backend's latest revision has `POSTGRES_USER=coga_app` and that
the backend's service account has no accessor role on `coga-postgres-password`.

**Rotate `coga_app`'s password:** add a new version to `coga-postgres-app-password`, run the
job (`gcloud run jobs execute coga-db-migrate --region <region> --wait`), then roll the
backend so new instances read the new version. Between those two steps, running instances
cannot open new database connections, so do it at a quiet time.

**Rollback:** set `db_runtime_role = "owner"` and deploy. The backend connects as the owner
again and applies the schema on startup, and the job is removed. `coga_app` keeps its
login, unused.

## Verification

- The app boots, reads, and writes normally (sign-out, classification, audit all work),
  and **account/family deletion still works** (the `ON DELETE SET NULL` cascades do not need
  the runtime role to hold `UPDATE` on the append-only tables).
- As `coga_app`, the forbidden operations are refused. The most faithful check is to
  connect **directly as the `coga_app` login** (after step 2) and run them. (`SET ROLE
  coga_app` from another session only works if that session is a superuser or a member of
  `coga_app` — a non-superuser owner cannot `SET ROLE` into it.)
  ```sql
  -- connected as coga_app:
  UPDATE clinical_audit_events SET summary = summary;          -- ERROR: permission denied
  DELETE FROM report_signouts;                                  -- ERROR: permission denied
  ALTER TABLE audit_log_events DISABLE TRIGGER USER;            -- ERROR: must be owner
  ```
- The automated proof is two integration tests in the CI `smoke` job:
  - `backend/tests/integration/test_app_role_privileges.py` asserts the allow/deny outcomes
    above plus the `ON DELETE SET NULL` cascade (via `SET ROLE coga_app`).
  - `backend/tests/integration/test_app_boots_as_restricted_role.py` runs the migration
    out-of-band as the owner, then **boots the whole app as `coga_app`** with startup
    migrations disabled — proving the deployed flip serves without owner-only DDL and that
    the append-only revoke holds on a real `coga_app` login (not just via `SET ROLE`).

## Future schema changes

Any **new append-only table** must repeat the `REVOKE UPDATE, DELETE, TRUNCATE … FROM
coga_app;` from migration 040 — the `ALTER DEFAULT PRIVILEGES` in 040 otherwise grants the
new table full CRUD to `coga_app`.
