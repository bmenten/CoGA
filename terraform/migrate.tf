# ==========================================
# SCHEMA MIGRATION JOB (db_runtime_role = "coga_app" only)
# ==========================================
#
# With the restricted runtime role the API cannot run DDL, so it no longer applies the
# schema on startup. This Cloud Run job does, as the table owner, from the backend image:
# `python -m app.db_migrate` applies the idempotent schema baselines, seeds the admin user
# and enables coga_app's login from the coga-postgres-app-password secret (as a SCRAM
# verifier, so the plaintext never reaches the database). Terraform runs it whenever the
# backend image changes and the backend service waits for it, so a new revision never
# serves against an older schema. If it fails, the apply stops and the running revision
# keeps serving.
#
# The job runs as its own service account (created in the central infra repo), so the
# API's account never holds the owner's password. See docs/db-runtime-role-runbook.md.

locals {
  # Every app secret, keyed by the setting the backend reads it as (POSTGRES_PASSWORD is the
  # owner's, POSTGRES_APP_PASSWORD coga_app's). Outside development the settings module
  # refuses to start without the app secrets, so the job needs them although it only uses
  # Postgres.
  db_migrate_secret_env = {
    for key in keys(local.secret_ids) :
    (key == "integrity_anchor" ? "INTEGRITY_ANCHOR_SIGNING_KEY" : upper(key)) => key
  }
}

resource "google_secret_manager_secret_iam_member" "db_migrate" {
  for_each  = local.restricted_db_role ? toset(values(local.db_migrate_secret_env)) : toset([])
  secret_id = google_secret_manager_secret.app[each.key].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${local.db_migrate_sa_email}"
}

resource "google_cloud_run_v2_job" "db_migrate" {
  count    = local.restricted_db_role ? 1 : 0
  name     = "${local.name_prefix}-db-migrate"
  location = var.region
  labels   = var.labels

  template {
    task_count = 1

    template {
      service_account = local.db_migrate_sa_email
      # A failed migration is looked at, not retried: the schema files are idempotent,
      # but a repeated failure only hides the first error.
      max_retries = 0
      timeout     = "900s"

      vpc_access {
        connector = google_vpc_access_connector.connector.id
        egress    = "PRIVATE_RANGES_ONLY"
      }

      containers {
        image   = var.backend_image
        command = ["python", "-m", "app.db_migrate"]

        resources {
          limits = {
            cpu    = "1"
            memory = "1Gi"
          }
        }

        env {
          name  = "APP_ENV"
          value = "production"
        }
        # The owner, over the Cloud SQL connector, exactly as the API connects in owner mode.
        env {
          name  = "POSTGRES_DB"
          value = google_sql_database.coga.name
        }
        env {
          name  = "POSTGRES_USER"
          value = google_sql_user.coga.name
        }
        env {
          name  = "POSTGRES_SSLMODE"
          value = "require"
        }
        env {
          name  = "POSTGRES_USE_CLOUD_SQL_CONNECTOR"
          value = "true"
        }
        env {
          name  = "POSTGRES_INSTANCE_CONNECTION_NAME"
          value = google_sql_database_instance.postgres.connection_name
        }
        env {
          name  = "CLOUD_SQL_IP_TYPE"
          value = "PRIVATE"
        }
        # The admin user the migration seeds, as in cloudrun.tf.
        env {
          name  = "ADMIN_USERNAME"
          value = "coga-admin"
        }
        env {
          name  = "ADMIN_EMAIL"
          value = "admin@${var.app_domain}"
        }

        dynamic "env" {
          for_each = local.db_migrate_secret_env
          content {
            name = env.key
            value_source {
              secret_key_ref {
                secret  = google_secret_manager_secret.app[env.value].secret_id
                version = "latest"
              }
            }
          }
        }
      }
    }
  }

  depends_on = [
    google_secret_manager_secret_iam_member.db_migrate,
  ]
}

# Runs the job and waits for it to finish, with gcloud on the machine that runs Terraform
# (the deploy job installs it). A new backend image replaces this resource, so every image
# is migrated before it serves. To re-run it by hand, for example after rotating
# coga_app's password: gcloud run jobs execute coga-db-migrate --region <region> --wait.
resource "terraform_data" "db_migrate" {
  count            = local.restricted_db_role ? 1 : 0
  triggers_replace = [var.backend_image]

  provisioner "local-exec" {
    command = "gcloud run jobs execute \"$JOB\" --region=\"$REGION\" --project=\"$PROJECT\" --wait"
    environment = {
      JOB     = google_cloud_run_v2_job.db_migrate[0].name
      REGION  = google_cloud_run_v2_job.db_migrate[0].location
      PROJECT = var.project_id
    }
  }
}
