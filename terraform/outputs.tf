output "load_balancer_ip" {
  description = "Global IP of the HTTPS load balancer. Create an A record for app_domain pointing here, then the managed cert will provision."
  value       = google_compute_global_address.lb_ip.address
}

output "app_url" {
  description = "Public application URL."
  value       = "https://${var.app_domain}"
}

output "backend_service_url" {
  description = "Backend Cloud Run URL (internal-LB ingress; not directly reachable)."
  value       = google_cloud_run_v2_service.backend.uri
}

output "frontend_service_url" {
  description = "Frontend Cloud Run URL (internal-LB ingress; not directly reachable)."
  value       = google_cloud_run_v2_service.frontend.uri
}

output "postgres_private_ip" {
  description = "Cloud SQL private IP."
  value       = google_sql_database_instance.postgres.private_ip_address
}

output "clickhouse_internal_ip" {
  description = "Internal IP of the ClickHouse VM."
  value       = google_compute_instance.clickhouse.network_interface[0].network_ip
}

output "phi_bucket" {
  description = "GCS bucket for PHI family data (for the future native-GCS storage backend)."
  value       = google_storage_bucket.phi.name
}

output "refdata_bucket" {
  description = "GCS bucket mounted into the backend at /data/ref-data."
  value       = google_storage_bucket.refdata.name
}

output "backend_service_account" {
  description = "Backend runtime service account email (created in the central infra repo; referenced here)."
  value       = local.backend_sa_email
}

output "db_migrate_job" {
  description = "Cloud Run job that applies the Postgres schema as the owner (db_runtime_role = \"coga_app\" only; null otherwise). Terraform runs it on every new backend image; by hand: gcloud run jobs execute <name> --region <region> --wait."
  value       = local.restricted_db_role ? google_cloud_run_v2_job.db_migrate[0].name : null
}
