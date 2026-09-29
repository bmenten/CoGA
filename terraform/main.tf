provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  name_prefix = "coga"

  # CMEK key applied uniformly to Cloud SQL, Compute disks, and GCS. Encryption is
  # mandatory (organization policy), so the key is always set — there is no on/off
  # toggle and no Google-managed-key fallback.
  cmek_key = var.cmek_key_self_link

  # Runtime service-account emails. The accounts themselves (and all their IAM role
  # grants + the project's API enablement) are provisioned in the CENTRAL infra repo,
  # not here — see terraform/main-repo-reference/. This config only *references* the
  # SAs, so the CoGA deploy pipeline needs no service-account-admin or project-IAM-admin
  # rights and therefore cannot grant itself privileges. Defaults follow the central
  # repo's naming convention; set the vars if it differs.
  backend_sa_email       = var.backend_service_account_email != "" ? var.backend_service_account_email : "${local.name_prefix}-backend-run@${var.project_id}.iam.gserviceaccount.com"
  frontend_sa_email      = var.frontend_service_account_email != "" ? var.frontend_service_account_email : "${local.name_prefix}-frontend-run@${var.project_id}.iam.gserviceaccount.com"
  clickhouse_vm_sa_email = var.clickhouse_vm_service_account_email != "" ? var.clickhouse_vm_service_account_email : "${local.name_prefix}-clickhouse-vm@${var.project_id}.iam.gserviceaccount.com"
  db_migrate_sa_email    = var.db_migrate_service_account_email != "" ? var.db_migrate_service_account_email : "${local.name_prefix}-db-migrate@${var.project_id}.iam.gserviceaccount.com"

  # Secret Manager secret ids (containers created in secrets.tf; versions added
  # out-of-band — see docs/deployment-gcp.md §5.5).
  secret_ids = {
    secret_key          = "${local.name_prefix}-secret-key"
    integrity_anchor    = "${local.name_prefix}-integrity-anchor-key"
    admin_password      = "${local.name_prefix}-admin-password"
    postgres_password   = "${local.name_prefix}-postgres-password"
    clickhouse_password = "${local.name_prefix}-clickhouse-password"
    # The restricted runtime role's password. Created in every mode, so a version can be
    # added before the switch; read only with db_runtime_role = "coga_app".
    postgres_app_password = "${local.name_prefix}-postgres-app-password"
  }

  # Postgres login the API runs as (var.db_runtime_role). With the restricted role, the
  # db-migrate job applies the schema instead (migrate.tf) and the API never receives the
  # owner's password: not in its environment, and not through Secret Manager either.
  restricted_db_role = var.db_runtime_role == "coga_app"
  # The secret_ids entry holding each login's password.
  db_login_secret = {
    owner    = "postgres_password"
    coga_app = "postgres_app_password"
  }
  app_db_user            = local.restricted_db_role ? "coga_app" : google_sql_user.coga.name
  app_db_password_secret = local.db_login_secret[var.db_runtime_role]
  app_runs_migrations    = local.restricted_db_role ? false : var.run_db_schema_migrations_on_startup
}
