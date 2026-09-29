# ==========================================
# OBJECT STORAGE (PHI family data + reference data)
# ==========================================
#
# NOTE: the backend's object-storage layer is currently S3/boto3-only
# (backend/app/core/object_storage.py). A native GCS backend is a separate code
# change (tracked as a follow-up). These buckets are provisioned now so the
# infrastructure, IAM, and audit posture are ready:
#  - phi: raw family data (CRAM/BAM + family packages) — read-only to the app.
#  - refdata: reference data (dbNSFP, HPO, clinical CNVs) mounted into the backend.

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

  # IGV runs in the browser and reads aligned reads straight from this bucket, through
  # the short-lived signed URLs the CRAM endpoint hands out, with range requests. A
  # browser lets the page read those responses only when the bucket's CORS policy names
  # the page's origin, so it names the app's origin alone, for reads alone.
  cors {
    origin          = ["https://${var.app_domain}"]
    method          = ["GET", "HEAD"]
    response_header = ["Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Range"]
    max_age_seconds = 3600
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

# PHI bucket: the app only READS family bytes (presigned/streamed). Viewer only.
resource "google_storage_bucket_iam_member" "backend_phi_reader" {
  bucket = google_storage_bucket.phi.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${local.backend_sa_email}"
}

# Reference data is mounted read-write so first-run bootstrap can populate it.
resource "google_storage_bucket_iam_member" "backend_refdata_user" {
  bucket = google_storage_bucket.refdata.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${local.backend_sa_email}"
}

# NOTE (S-4: byte-level PHI download audit): presigned/direct object reads bypass the
# app, so they are captured via project-wide GCS Data Access audit logs
# (storage.googleapis.com DATA_READ/DATA_WRITE). That is a project-level IAM audit
# config and now lives in the central infra repo (terraform/main-repo-reference/), not
# here, so this pipeline needs no project-IAM-admin rights.
