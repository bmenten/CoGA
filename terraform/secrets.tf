# ==========================================
# SECRET MANAGER
# ==========================================
#
# Terraform creates the secret *containers* only. The secret *values* (versions)
# are added out-of-band so plaintext never flows through Terraform variables for the
# app secrets:
#
#   printf '%s' "$SECRET_KEY"   | gcloud secrets versions add coga-secret-key            --data-file=- --project <p>
#   printf '%s' "$ANCHOR_KEY"   | gcloud secrets versions add coga-integrity-anchor-key  --data-file=- --project <p>
#   printf '%s' "$ADMIN_PW"     | gcloud secrets versions add coga-admin-password        --data-file=- --project <p>
#   printf '%s' "$PG_PW"        | gcloud secrets versions add coga-postgres-password      --data-file=- --project <p>
#   printf '%s' "$CH_PW"        | gcloud secrets versions add coga-clickhouse-password    --data-file=- --project <p>
#   printf '%s' "$APP_PG_PW"    | gcloud secrets versions add coga-postgres-app-password  --data-file=- --project <p>
#
# Versions MUST exist before `terraform apply` (the Cloud SQL user and the Cloud Run
# revisions resolve `latest` at apply time). See terraform/README.md "Bootstrap". The
# last one, coga_app's password (printable ASCII), is needed only before switching to
# db_runtime_role = "coga_app": docs/db-runtime-role-runbook.md, "Google Cloud".

resource "google_secret_manager_secret" "app" {
  for_each  = local.secret_ids
  secret_id = each.value
  labels    = var.labels

  # Pin replication to the deployment region for data residency.
  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }
}

# The Cloud SQL user password is read from Secret Manager so there is a single
# source of truth shared with the backend's POSTGRES_PASSWORD env. (This value does
# enter Terraform state for the SQL user — keep the state bucket private + CMEK.)
data "google_secret_manager_secret_version" "postgres_password" {
  secret = google_secret_manager_secret.app["postgres_password"].id
}

# --- Access grants ----------------------------------------------------------
#
# RESOURCE-level grants on the secrets this config creates, so they stay here (they
# cannot escalate to project-level roles). The SAs are created in the central infra
# repo; we grant them by email.

# Backend reads every app secret it injects via secret_key_ref, and one Postgres password:
# the owner's, or with db_runtime_role = "coga_app" only coga_app's. The API then cannot
# fetch the owner's password with its own credentials either.
resource "google_secret_manager_secret_iam_member" "backend" {
  for_each  = setsubtract(keys(local.secret_ids), [local.restricted_db_role ? "postgres_password" : "postgres_app_password"])
  secret_id = google_secret_manager_secret.app[each.key].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${local.backend_sa_email}"
}

# The ClickHouse VM reads only its own password at boot.
resource "google_secret_manager_secret_iam_member" "clickhouse_vm" {
  secret_id = google_secret_manager_secret.app["clickhouse_password"].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${local.clickhouse_vm_sa_email}"
}
