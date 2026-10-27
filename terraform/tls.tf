# ==========================================
# CLICKHOUSE TLS (TF-13 S-2)
# ==========================================
#
# Encrypts the backend↔ClickHouse link. A private CA + server cert are generated
# locally; ClickHouse serves HTTPS on 8443 and the backend verifies the cert
# against the CA. The cert pins both a DNS SAN (used for verification when the
# backend connects by IP) and the VM's static internal IP.
#
# The CA cert is public and wired straight into the backend env. The server key +
# cert are stored in Secret Manager and fetched by the VM at boot (so the key is
# never in instance metadata).
#
# Neither private key enters the Terraform state or plan. Both are ephemeral: every plan and
# apply generates them afresh and passes them only to write-only arguments, which a resource
# reads when it is created and never stores. So only the run that issues the certificates
# uses its keys: the server key goes to Secret Manager with them, and the CA key is kept
# nowhere, so it can sign nothing after that run. The CA and the server cert are therefore
# always issued together, in one run:
#  - raising local.clickhouse_tls_key_version replaces both keys and both certs;
#  - a new certificate request (the VM's address changed) re-issues the CA too
#    (replace_triggered_by below) and stores the new server key with it;
#  - near expiry the CA, issued first, renews first and re-issues the server cert with it,
#    for the same server key (so both certs keep the same validity and early renewal).
# Any other change that would re-issue the server cert alone fails the apply, because that
# run's CA key does not match the CA cert: raise the key version in the same change.
#
# A new CA reaches the backend in the same apply (its next revision), but the VM loads a new
# key and cert only at boot or at its daily refresh, so ClickHouse is unreachable from the
# backend until then: restart the VM right after such an apply (docs/deployment-gcp.md
# §12.4).

locals {
  clickhouse_tls_dns = "coga-clickhouse"

  # Raise by one to replace the CA, the server key and both certificates on the next apply.
  # The keys are never stored, so this number is the only sign that they changed.
  clickhouse_tls_key_version = 1
}

ephemeral "tls_private_key" "ca" {
  algorithm = "RSA"
  rsa_bits  = 4096
}

ephemeral "tls_private_key" "clickhouse" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "tls_self_signed_cert" "ca" {
  private_key_pem_wo         = ephemeral.tls_private_key.ca.private_key_pem
  private_key_pem_wo_version = local.clickhouse_tls_key_version
  is_ca_certificate          = true
  validity_period_hours      = 87600 # 10 years
  early_renewal_hours        = 720

  subject {
    common_name  = "CoGA ClickHouse CA"
    organization = "CMGG"
  }

  allowed_uses = ["cert_signing", "crl_signing", "digital_signature"]

  lifecycle {
    # The server cert can only be signed in the run that creates the CA (above).
    replace_triggered_by = [tls_cert_request.clickhouse]
  }
}

resource "tls_cert_request" "clickhouse" {
  private_key_pem_wo         = ephemeral.tls_private_key.clickhouse.private_key_pem
  private_key_pem_wo_version = local.clickhouse_tls_key_version

  subject {
    common_name  = local.clickhouse_tls_dns
    organization = "CMGG"
  }

  dns_names    = [local.clickhouse_tls_dns]
  ip_addresses = [google_compute_address.clickhouse.address]
}

resource "tls_locally_signed_cert" "clickhouse" {
  cert_request_pem              = tls_cert_request.clickhouse.cert_request_pem
  ca_private_key_pem_wo         = ephemeral.tls_private_key.ca.private_key_pem
  ca_private_key_pem_wo_version = local.clickhouse_tls_key_version
  ca_cert_pem                   = tls_self_signed_cert.ca.cert_pem

  validity_period_hours = 87600 # 10 years
  early_renewal_hours   = 720

  allowed_uses = ["server_auth", "digital_signature", "key_encipherment"]
}

# --- Server key + cert in Secret Manager (fetched by the VM at boot) --------
#
# The VM reads the latest version of each secret (scripts/), so a new version is created
# before the old one is destroyed: the other way round, a VM that booted in between found no
# version and did not start ClickHouse (SEC-19). During such an apply it may still read a new
# key with the old certificate, or the reverse; the restart after it loads the new pair
# (docs/deployment-gcp.md §12.4). Terraform extends create_before_destroy to everything the
# versions depend on: a later change that replaces the secrets, the VM's address, its subnet
# or its network under the same name then fails on that name, rather than destroying first.

resource "google_secret_manager_secret" "clickhouse_tls" {
  for_each  = toset(["key", "cert"])
  secret_id = "${local.name_prefix}-clickhouse-tls-${each.key}"
  labels    = var.labels

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }
}

# Write-only: Secret Manager holds the key, the state does not.
resource "google_secret_manager_secret_version" "clickhouse_tls_key" {
  secret                 = google_secret_manager_secret.clickhouse_tls["key"].id
  secret_data_wo         = ephemeral.tls_private_key.clickhouse.private_key_pem
  secret_data_wo_version = tostring(local.clickhouse_tls_key_version)

  lifecycle {
    create_before_destroy = true
    # A new certificate request was made with this run's server key: store that key.
    replace_triggered_by = [tls_cert_request.clickhouse]
  }
}

resource "google_secret_manager_secret_version" "clickhouse_tls_cert" {
  secret      = google_secret_manager_secret.clickhouse_tls["cert"].id
  secret_data = tls_locally_signed_cert.clickhouse.cert_pem

  lifecycle {
    create_before_destroy = true
  }
}

resource "google_secret_manager_secret_iam_member" "clickhouse_vm_tls" {
  for_each  = google_secret_manager_secret.clickhouse_tls
  secret_id = each.value.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${local.clickhouse_vm_sa_email}"
}
