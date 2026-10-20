# ==========================================
# SCHEMA MIGRATION JOB (db_runtime_role = "coga_app" only)
# ==========================================
#
# With the restricted runtime role the API cannot run DDL, so it no longer applies the
# schema on startup. This Cloud Run job does, as the table owner, from the backend image:
# `python -m app.db_migrate` applies the idempotent schema baselines, seeds the admin user
# and enables coga_app's login from the coga-postgres-app-password secret (as a SCRAM
# verifier, so the plaintext never reaches the database). Terraform runs it whenever the
# backend image changes, right after an on-demand backup of the database (below), and the
# backend service waits for it, so a new revision never serves against an older schema; the
# API checks at its start that the database is at its own revision. If the backup or the job
# fails, the apply stops and the running revision keeps serving.
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

  # The google provider protects a Cloud Run job from deletion by default (6.0+); the rollback
  # to db_runtime_role = "owner" removes this one (docs/db-runtime-role-runbook.md).
  deletion_protection = false

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

        # APP_ENV, CORS_ORIGINS and CORS_ORIGIN_REGEX, as the API gets them (cloudrun.tf). The
        # job loads the backend's settings, which in production refuse the default cross-origin
        # values; given APP_ENV alone, it stopped at import.
        dynamic "env" {
          for_each = local.backend_production_env
          content {
            name  = env.key
            value = env.value
          }
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

  # First the backup (below). In this resource rather than one of its own, so that the run
  # after a failed backup or migration, which replaces this resource, takes a new one too.
  provisioner "local-exec" {
    command     = local.pre_migration_backup.command
    environment = local.pre_migration_backup.environment
  }

  provisioner "local-exec" {
    command = "gcloud run jobs execute \"$JOB\" --region=\"$REGION\" --project=\"$PROJECT\" --wait"
    environment = {
      JOB     = google_cloud_run_v2_job.db_migrate[0].name
      REGION  = google_cloud_run_v2_job.db_migrate[0].location
      PROJECT = var.project_id
    }
  }
}

# ==========================================
# BACKUP BEFORE EACH SCHEMA MIGRATION
# ==========================================
#
# Every new backend image migrates the Postgres schema: the db-migrate job above with the
# restricted role, the API's first start in owner mode. Before either, the apply takes an
# on-demand Cloud SQL backup and waits until it is done: the restore point if that migration
# has to be undone (docs/deployment-gcp.md, 12.3). It does so for every new image, because the
# machine that applies has no path to the database (private address only) to read the revision
# it is at, and until the release candidate a migration can change the schema without a new
# revision. A backup that fails stops the apply before the migration runs, and the running
# revision keeps serving. On-demand backups stay until someone deletes them (12.3).
#
# The script runs gcloud with the credentials of whoever applies; the deploy pipeline's account
# needs the backup permissions (main-repo-reference/coga-prerequisites.tf.example).

locals {
  pre_migration_backup = {
    command = "bash ${path.module}/scripts/backup-postgres-before-migration.sh"
    environment = {
      INSTANCE = google_sql_database_instance.postgres.name
      PROJECT  = var.project_id
      IMAGE    = var.backend_image
    }
  }
}

# Owner mode: the API migrates at its first start, so the backup comes before the backend rolls
# out (cloudrun.tf waits for it). With the restricted role, terraform_data.db_migrate takes it
# right before the job. Not when the API runs as the owner without migrating at start
# (run_db_schema_migrations_on_startup = false): whoever migrates then takes the backup first.
resource "terraform_data" "pre_migration_backup" {
  count            = local.app_runs_migrations ? 1 : 0
  triggers_replace = [var.backend_image]

  provisioner "local-exec" {
    command     = local.pre_migration_backup.command
    environment = local.pre_migration_backup.environment
  }

  # Cloud SQL runs one operation on an instance at a time and refuses a second one, so the
  # backup waits for the database and the owner's login this apply may create or change. The
  # db-migrate resource waits for them through its job.
  depends_on = [
    google_sql_database.coga,
    google_sql_user.coga,
  ]
}
