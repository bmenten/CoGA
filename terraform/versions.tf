terraform {
  # 1.11+ for write-only arguments, which keep the database owner's password and the
  # ClickHouse TLS private keys out of the state and the plan (ephemeral resources: 1.10+).
  # CI pins the version it runs (.github/workflows/build.yml).
  required_version = ">= 1.11.0"

  required_providers {
    google = {
      source = "hashicorp/google"
      # 8.x for the write-only password of the Cloud SQL user, the write-only data of a Secret
      # Manager secret version (whose version argument 8.0 made a string) and the ephemeral
      # Secret Manager secret version; 8.7.0 is the release this configuration was validated
      # against.
      version = ">= 8.7.0, < 9.0.0"
    }
    # Generates the private CA + ClickHouse server cert locally (no external calls).
    # 4.4+ for the ephemeral private key's write-only use by the certificate resources.
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.4"
    }
  }

  # Remote state in GCS. Bucket + prefix are supplied at `terraform init` time via
  # -backend-config (see .github/workflows/build.yml and docs/deployment-gcp.md §7), so the
  # same config serves every environment. The state holds no password or private key
  # (secrets.tf, tls.tf), but it maps the whole deployment: keep the bucket private,
  # versioned and CMEK-encrypted, with tight IAM.
  backend "gcs" {}
}
