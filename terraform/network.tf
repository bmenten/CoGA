# ==========================================
# NETWORKING & PRIVATE CONNECTIONS
# ==========================================
#
# This config creates the VPC, subnet and IP range, so it applies cleanly to a new
# project. If you are adopting existing network resources, `terraform import` them
# (or add import blocks) before the first apply.

resource "google_compute_network" "vpc_network" {
  name                    = "${local.name_prefix}-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "subnet" {
  name          = "${local.name_prefix}-subnet"
  ip_cidr_range = "10.0.0.0/24"
  region        = var.region
  network       = google_compute_network.vpc_network.id

  # Lets the (external-IP-less) ClickHouse VM reach Google APIs (Secret Manager,
  # Artifact Registry, Logging) over private connectivity without Cloud NAT.
  private_ip_google_access = true
}

# Stable internal IP for the ClickHouse VM so the TLS cert can pin it (IP SAN) and
# the backend connects to a fixed address.
resource "google_compute_address" "clickhouse" {
  name         = "${local.name_prefix}-clickhouse-ip"
  region       = var.region
  subnetwork   = google_compute_subnetwork.subnet.id
  address_type = "INTERNAL"
}

# ---- Private Service Access for Cloud SQL private IP -----------------------

resource "google_compute_global_address" "private_ip_alloc" {
  name          = "${local.name_prefix}-private-ip-alloc"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.vpc_network.id
}

resource "google_service_networking_connection" "private_vpc_connection" {
  network                 = google_compute_network.vpc_network.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip_alloc.name]
}

# ---- Serverless VPC connector (Cloud Run -> private VPC) -------------------

resource "google_vpc_access_connector" "connector" {
  name          = "${local.name_prefix}-vpc-connector"
  region        = var.region
  ip_cidr_range = "10.8.0.0/28"
  network       = google_compute_network.vpc_network.name
}

# ---- Cloud NAT (egress for the private ClickHouse VM: Docker Hub pull, OS pkgs) ----

resource "google_compute_router" "router" {
  name    = "${local.name_prefix}-router"
  region  = var.region
  network = google_compute_network.vpc_network.id
}

resource "google_compute_router_nat" "nat" {
  name                               = "${local.name_prefix}-nat"
  router                             = google_compute_router.router.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

# ---- Firewall -------------------------------------------------------------

# Backend (via the serverless connector range) -> ClickHouse HTTPS on the VM.
# Only the TLS port is reachable; the plain HTTP interface (8123) stays VM-local.
resource "google_compute_firewall" "allow_clickhouse" {
  name      = "${local.name_prefix}-allow-clickhouse"
  network   = google_compute_network.vpc_network.name
  direction = "INGRESS"

  allow {
    protocol = "tcp"
    ports    = ["8443"]
  }

  # Connector range (Cloud Run egress) + the subnet itself.
  source_ranges = ["10.8.0.0/28", "10.0.0.0/24"]
  target_tags   = ["clickhouse"]
}

# NOTE: there is intentionally NO SSH ingress to the ClickHouse VM. The VM has no
# public IP and is a Container-Optimized OS box managed entirely through metadata
# startup/shutdown scripts + Secret Manager, so no interactive shell is needed for
# normal operation. If break-glass debugging is ever required, add a temporary
# IAP-scoped rule (source 35.235.240.0/20, tcp/22) and remove it afterwards, or use
# the serial console.
