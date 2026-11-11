# ==========================================
# OBJECT STORAGE (PHI family data + reference data)
# ==========================================
#
# Both buckets are read-only to the app, and each is mounted into the backend as a folder
# (cloudrun.tf); the app never reads a bucket by its gs:// URI:
#  - phi: the import packages (VCFs, CRAM/BAM, QC), under imports/<project>/<package>,
#    mounted at /data/imports. Package import reads them, and IGV's reads and tracks are
#    served from them, where they lie.
#  - refdata: reference data (dbNSFP, HPO, clinical CNVs), mounted at /data/ref-data.

resource "google_storage_bucket" "phi" {
  name                        = "${var.project_id}-${local.name_prefix}-phi"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  labels                      = var.labels

  versioning {
    enabled = true
  }

  encryption {
    default_kms_key_name = local.cmek_key
  }

  lifecycle_rule {
    condition {
      age        = 30
      with_state = "ARCHIVED"
    }
    action {
      type = "Delete"
    }
  }

  lifecycle_rule {
    condition {
      age = 7
    }
    action {
      type = "AbortIncompleteMultipartUpload"
    }
  }
}

resource "google_storage_bucket" "refdata" {
  name                        = "${var.project_id}-${local.name_prefix}-refdata"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  labels                      = var.labels

  versioning {
    enabled = true
  }

  encryption {
    default_kms_key_name = local.cmek_key
  }
}

# --- App access (least privilege) ------------------------------------------
#
# These are RESOURCE-level grants on the buckets this config creates, so they stay
# here (granting IAM on your own bucket cannot escalate to project-level roles). The
# runtime SA itself is created in the central infra repo; we grant it by email.

# PHI bucket: the backend mounts its imports/ folder read-only (cloudrun.tf) and the packages
# are written into it out of band, so the app only READS family bytes. Viewer only.
resource "google_storage_bucket_iam_member" "backend_phi_reader" {
  bucket = google_storage_bucket.phi.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${local.backend_sa_email}"
}

# Refdata bucket: the backend mounts it read-only (cloudrun.tf) and the files are
# loaded into it out of band, so the app only READS reference data. Viewer only.
resource "google_storage_bucket_iam_member" "backend_refdata_reader" {
  bucket = google_storage_bucket.refdata.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${local.backend_sa_email}"
}

# NOTE (S-4: byte-level PHI download audit): no browser reads the PHI bucket; the backend
# reads it through the mount, as its own service account, and those object reads are captured
# via project-wide GCS Data Access audit logs (storage.googleapis.com DATA_READ/DATA_WRITE).
# That is a project-level IAM audit config and lives in the central infra repo
# (terraform/main-repo-reference/), not here, so this pipeline needs no project-IAM-admin
# rights.
