# Prepare public GRCh38 reference assets as the deploy account, before the API rolls
# out. The runtime account keeps objectViewer and its read-only /data/ref-data mount.
# This uses only Python's standard library and the same authenticated gcloud that
# Terraform already needs for the pre-migration backup. No app/database secrets.
resource "terraform_data" "grch38_reference" {
  triggers_replace = [
    var.backend_image,
    filesha256("${path.module}/../backend/app/reference_fasta.py"),
    google_storage_bucket.refdata.name,
  ]

  provisioner "local-exec" {
    command = "python3 \"${path.module}/../backend/app/reference_fasta.py\" --gcs-bucket=\"$REFDATA_BUCKET\" --project=\"$PROJECT\""
    environment = {
      REFDATA_BUCKET = google_storage_bucket.refdata.name
      PROJECT        = var.project_id
    }
  }
}
