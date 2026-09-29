# ==========================================
# CLICKHOUSE EGRESS LOCKDOWN (var.clickhouse_restrict_egress, off by default)
# ==========================================
#
# The ClickHouse VM holds every genotype. With the lockdown on it may open connections
# only to Google APIs, through the private.googleapis.com range (Private Google Access);
# every other outbound connection is refused at the VPC firewall. A compromised database
# VM then cannot send data anywhere else, and the image pull no longer depends on Docker
# Hub. Replies to the backend's connections are unaffected (firewall rules are stateful),
# and the metadata server, which also answers DNS, is always reachable.
#
# Before switching it on (docs/deployment-gcp.md, "ClickHouse egress"):
#   - mirror clickhouse_image into Artifact Registry and point the variable at it (a
#     <region>-docker.pkg.dev host); the plan refuses a Docker Hub image;
#   - let the VM's service account read that repository (roles/artifactregistry.reader);
#   - enable the Cloud DNS API (dns.googleapis.com) for the private zones below.
# Container-Optimized OS can then no longer update itself in place: patch it by
# recreating the VM on a current image (the data disk is separate and is kept).

locals {
  # private.googleapis.com: reachable only from inside Google's network.
  private_google_apis_range = "199.36.153.8/30"
  private_google_apis_ips   = ["199.36.153.8", "199.36.153.9", "199.36.153.10", "199.36.153.11"]

  # Private DNS for the Google domains the VM uses: Secret Manager, Logging and the like on
  # googleapis.com, and Artifact Registry on pkg.dev. Each name in the zone resolves to
  # the private range above.
  private_google_zones = {
    googleapis = { dns_name = "googleapis.com.", target = "private.googleapis.com." }
    pkg-dev    = { dns_name = "pkg.dev.", target = "pkg.dev." }
  }
}

resource "google_compute_firewall" "clickhouse_egress_google_apis" {
  count     = var.clickhouse_restrict_egress ? 1 : 0
  name      = "${local.name_prefix}-clickhouse-egress-google-apis"
  network   = google_compute_network.vpc_network.name
  direction = "EGRESS"
  priority  = 1000

  allow {
    protocol = "tcp"
    ports    = ["443"]
  }

  destination_ranges = [local.private_google_apis_range]
  target_tags        = ["clickhouse"]
}

resource "google_compute_firewall" "clickhouse_egress_deny_all" {
  count     = var.clickhouse_restrict_egress ? 1 : 0
  name      = "${local.name_prefix}-clickhouse-egress-deny-all"
  network   = google_compute_network.vpc_network.name
  direction = "EGRESS"
  # Below the allow rule above; above the implied allow-all egress rule (65535).
  priority = 65000

  deny {
    protocol = "all"
  }

  destination_ranges = ["0.0.0.0/0"]
  target_tags        = ["clickhouse"]
}

resource "google_dns_managed_zone" "private_google" {
  for_each = { for key, zone in local.private_google_zones : key => zone if var.clickhouse_restrict_egress }

  name        = "${local.name_prefix}-private-${each.key}"
  dns_name    = each.value.dns_name
  description = "Resolves ${each.value.dns_name} to private.googleapis.com inside the CoGA VPC (egress.tf)."
  visibility  = "private"
  labels      = var.labels

  private_visibility_config {
    networks {
      network_url = google_compute_network.vpc_network.id
    }
  }
}

resource "google_dns_record_set" "private_google_a" {
  for_each     = google_dns_managed_zone.private_google
  managed_zone = each.value.name
  name         = local.private_google_zones[each.key].target
  type         = "A"
  ttl          = 300
  rrdatas      = local.private_google_apis_ips
}

resource "google_dns_record_set" "private_google_cname" {
  for_each     = google_dns_managed_zone.private_google
  managed_zone = each.value.name
  name         = "*.${each.value.dns_name}"
  type         = "CNAME"
  ttl          = 300
  rrdatas      = [local.private_google_zones[each.key].target]
}
