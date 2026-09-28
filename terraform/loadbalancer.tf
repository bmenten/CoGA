# ==========================================
# EXTERNAL HTTPS LOAD BALANCER (global, EXTERNAL_MANAGED)
# ==========================================
#
# Routing:  /api/*  -> backend Cloud Run     (everything under the FastAPI /api prefix)
#           /*      -> frontend Cloud Run    (SPA + static assets)
# TLS terminates here on a Google-managed cert for var.app_domain; the app emits
# HSTS (ENABLE_HSTS=true) because TLS is in front of it.

# ---- Serverless NEGs -------------------------------------------------------

resource "google_compute_region_network_endpoint_group" "backend" {
  name                  = "${local.name_prefix}-backend-neg"
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = google_cloud_run_v2_service.backend.name
  }
}

resource "google_compute_region_network_endpoint_group" "frontend" {
  name                  = "${local.name_prefix}-frontend-neg"
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = google_cloud_run_v2_service.frontend.name
  }
}

# ---- Backend services ------------------------------------------------------

resource "google_compute_backend_service" "backend" {
  name                  = "${local.name_prefix}-backend-svc"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  protocol              = "HTTPS"
  security_policy       = var.enable_cloud_armor ? google_compute_security_policy.lb[0].id : null
  backend {
    group = google_compute_region_network_endpoint_group.backend.id
  }
  # Sample 10% of requests — enough for traffic/latency visibility without the cost
  # and volume of logging every request in production. (The clinical audit trail is
  # separate: it lives in the app/DB, not in these LB access logs.)
  log_config {
    enable      = true
    sample_rate = 0.1
  }
}

resource "google_compute_backend_service" "frontend" {
  name                  = "${local.name_prefix}-frontend-svc"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  protocol              = "HTTPS"
  security_policy       = var.enable_cloud_armor ? google_compute_security_policy.lb[0].id : null
  backend {
    group = google_compute_region_network_endpoint_group.frontend.id
  }
  log_config {
    enable      = true
    sample_rate = 0.1
  }
}

# ---- URL map (path routing) -----------------------------------------------

resource "google_compute_url_map" "https" {
  name            = "${local.name_prefix}-urlmap"
  default_service = google_compute_backend_service.frontend.id

  host_rule {
    hosts        = [var.app_domain]
    path_matcher = "main"
  }

  path_matcher {
    name            = "main"
    default_service = google_compute_backend_service.frontend.id

    path_rule {
      paths   = ["/api", "/api/*"]
      service = google_compute_backend_service.backend.id
    }
  }
}

# ---- Managed TLS cert + HTTPS proxy + forwarding rule ----------------------

resource "google_compute_managed_ssl_certificate" "cert" {
  name = "${local.name_prefix}-cert"
  managed {
    domains = [var.app_domain]
  }
}

# TLS 1.2+ with the MODERN cipher profile; without a policy Google's default applies,
# which still accepts TLS 1.0 (#520).
resource "google_compute_ssl_policy" "https" {
  name            = "${local.name_prefix}-ssl-policy"
  profile         = "MODERN"
  min_tls_version = "TLS_1_2"
}

resource "google_compute_target_https_proxy" "https" {
  name             = "${local.name_prefix}-https-proxy"
  url_map          = google_compute_url_map.https.id
  ssl_certificates = [google_compute_managed_ssl_certificate.cert.id]
  ssl_policy       = google_compute_ssl_policy.https.id
}

resource "google_compute_global_address" "lb_ip" {
  name = "${local.name_prefix}-lb-ip"
}

resource "google_compute_global_forwarding_rule" "https" {
  name                  = "${local.name_prefix}-https-fr"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  target                = google_compute_target_https_proxy.https.id
  port_range            = "443"
  ip_address            = google_compute_global_address.lb_ip.address
}

# ---- HTTP -> HTTPS redirect ------------------------------------------------

resource "google_compute_url_map" "http_redirect" {
  name = "${local.name_prefix}-http-redirect"
  default_url_redirect {
    https_redirect         = true
    redirect_response_code = "MOVED_PERMANENTLY_DEFAULT"
    strip_query            = false
  }
}

resource "google_compute_target_http_proxy" "http" {
  name    = "${local.name_prefix}-http-proxy"
  url_map = google_compute_url_map.http_redirect.id
}

resource "google_compute_global_forwarding_rule" "http" {
  name                  = "${local.name_prefix}-http-fr"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  target                = google_compute_target_http_proxy.http.id
  port_range            = "80"
  ip_address            = google_compute_global_address.lb_ip.address
}
