# ==========================================
# MANAGED DATABASES (POSTGRES on Cloud SQL, CLICKHOUSE on Compute Engine)
# ==========================================

# ---- Cloud SQL for PostgreSQL ---------------------------------------------

resource "google_sql_database_instance" "postgres" {
  name             = "${local.name_prefix}-postgres"
  database_version = "POSTGRES_16"
  region           = var.region

  # CMEK key (the Cloud SQL service agent is granted encrypt/decrypt on it in the
  # central infra repo, before this config runs).
  encryption_key_name = local.cmek_key

  depends_on = [
    google_service_networking_connection.private_vpc_connection,
  ]

  settings {
    tier              = var.db_tier
    availability_type = var.db_availability_type
    disk_autoresize   = true
    disk_type         = "PD_SSD"

    ip_configuration {
      ipv4_enabled                                  = false
      private_network                               = google_compute_network.vpc_network.id
      ssl_mode                                      = "ENCRYPTED_ONLY"
      enable_private_path_for_google_cloud_services = true
    }

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      start_time                     = "02:00"
      transaction_log_retention_days = 7
      backup_retention_settings {
        retained_backups = var.db_backup_retained_count
        retention_unit   = "COUNT"
      }
    }

    maintenance_window {
      day  = 7 # Sunday
      hour = 3
    }

    user_labels = var.labels
  }

  deletion_protection = true
}

resource "google_sql_database" "coga" {
  name     = "coga"
  instance = google_sql_database_instance.postgres.name
}

resource "google_sql_user" "coga" {
  name     = "coga_admin"
  instance = google_sql_database_instance.postgres.name
  password = data.google_secret_manager_secret_version.postgres_password.secret_data
}

# ---- ClickHouse on Compute Engine (Container-Optimized OS) -----------------
#
# No managed ClickHouse on GCP, so it runs in a container on a COS VM with a
# dedicated, snapshot-backed CMEK data disk. The admin password is fetched from
# Secret Manager at boot (COS has no gcloud → curl + metadata token), so it is
# never stored in instance metadata or Terraform state.

locals {
  # Registry host of the ClickHouse image when it is in Artifact Registry, whose pulls need
  # the VM's credentials; empty for any other registry (the Docker Hub image needs none).
  clickhouse_image_registry = can(regex("^[a-z0-9-]+-docker\\.pkg\\.dev/", var.clickhouse_image)) ? split("/", var.clickhouse_image)[0] : ""
}

resource "google_compute_disk" "clickhouse_data" {
  name = "${local.name_prefix}-clickhouse-data"
  type = "pd-ssd"
  zone = var.zone
  size = var.clickhouse_data_disk_gb

  disk_encryption_key {
    kms_key_self_link = local.cmek_key
  }

  labels = var.labels
}

resource "google_compute_instance" "clickhouse" {
  name         = "${local.name_prefix}-clickhouse-vm"
  machine_type = var.clickhouse_machine_type
  zone         = var.zone
  tags         = ["clickhouse"]
  labels       = var.labels

  # Allow Terraform to stop the instance to apply changes (e.g. machine type).
  allow_stopping_for_update = true

  boot_disk {
    initialize_params {
      image = "cos-cloud/cos-stable"
      size  = 20
      type  = "pd-ssd"
    }

    kms_key_self_link = local.cmek_key
  }

  attached_disk {
    source      = google_compute_disk.clickhouse_data.id
    device_name = "clickhouse-data"
  }

  network_interface {
    network    = google_compute_network.vpc_network.id
    subnetwork = google_compute_subnetwork.subnet.id
    network_ip = google_compute_address.clickhouse.address
    # No external IP. Egress (Docker Hub pull) via Cloud NAT; Google APIs via PGA. With
    # var.clickhouse_restrict_egress only the Google APIs remain reachable (egress.tf).
  }

  # Live-migrate on host maintenance so ClickHouse is not SIGTERM'd mid-merge.
  scheduling {
    on_host_maintenance = "MIGRATE"
    automatic_restart   = true
    preemptible         = false
  }

  service_account {
    email  = local.clickhouse_vm_sa_email
    scopes = ["cloud-platform"]
  }

  metadata = {
    startup-script = templatefile("${path.module}/scripts/clickhouse-startup.sh.tftpl", {
      project_id       = var.project_id
      password_secret  = google_secret_manager_secret.app["clickhouse_password"].secret_id
      tls_key_secret   = google_secret_manager_secret.clickhouse_tls["key"].secret_id
      tls_cert_secret  = google_secret_manager_secret.clickhouse_tls["cert"].secret_id
      https_port       = 8443
      clickhouse_image = var.clickhouse_image
      ch_db            = "coga"
      ch_user          = "clickhouse_admin"
      registry_host    = local.clickhouse_image_registry
    })
    shutdown-script = file("${path.module}/scripts/clickhouse-shutdown.sh")
    # Installed by the startup script and run by a daily systemd timer for cert rotation.
    cert-refresh-script = templatefile("${path.module}/scripts/clickhouse-cert-refresh.sh.tftpl", {
      project_id      = var.project_id
      tls_key_secret  = google_secret_manager_secret.clickhouse_tls["key"].secret_id
      tls_cert_secret = google_secret_manager_secret.clickhouse_tls["cert"].secret_id
    })
  }

  depends_on = [
    google_secret_manager_secret_iam_member.clickhouse_vm,
    google_secret_manager_secret_iam_member.clickhouse_vm_tls,
    google_secret_manager_secret_version.clickhouse_tls_key,
    google_secret_manager_secret_version.clickhouse_tls_cert,
    google_compute_router_nat.nat,
    # With the egress lockdown, the first boot needs the private Google DNS to pull.
    google_compute_firewall.clickhouse_egress_google_apis,
    google_dns_record_set.private_google_a,
    google_dns_record_set.private_google_cname,
  ]

  lifecycle {
    precondition {
      condition     = !var.clickhouse_restrict_egress || local.clickhouse_image_registry != ""
      error_message = "clickhouse_restrict_egress blocks Docker Hub: mirror clickhouse_image into Artifact Registry and point the variable at it (a <region>-docker.pkg.dev host) first."
    }
  }
}

# ---- ClickHouse data-disk backups (daily snapshots) -----------------------

resource "google_compute_resource_policy" "clickhouse_snapshots" {
  name   = "${local.name_prefix}-clickhouse-snapshots"
  region = var.region

  snapshot_schedule_policy {
    schedule {
      daily_schedule {
        days_in_cycle = 1
        start_time    = "01:00"
      }
    }
    retention_policy {
      max_retention_days    = var.clickhouse_snapshot_retention_days
      on_source_disk_delete = "KEEP_AUTO_SNAPSHOTS"
    }
    snapshot_properties {
      storage_locations = [var.region]
      labels            = var.labels
    }
  }
}

resource "google_compute_disk_resource_policy_attachment" "clickhouse_snapshots" {
  name = google_compute_resource_policy.clickhouse_snapshots.name
  disk = google_compute_disk.clickhouse_data.name
  zone = var.zone
}
