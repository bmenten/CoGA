terraform {
  required_version = ">= 1.6.0"

  required_providers {
    google = {
      source = "hashicorp/google"
      # 5.30+ for Cloud Run v2 GCS volume mounts and Cloud SQL `ssl_mode`.
      version = ">= 5.30.0, < 6.0.0"
    }
    # Generates the private CA + ClickHouse server cert locally (no external calls).
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }

  # Remote state in GCS. Bucket + prefix are supplied at `terraform init` time via
  # -backend-config (see .github/workflows/build.yml and docs/deployment-gcp.md §7), so the
  # same config serves every environment. The state bucket holds DB/user passwords in
  # plaintext — it MUST be a private, versioned, CMEK-encrypted bucket with tight IAM.
  backend "gcs" {}
}
