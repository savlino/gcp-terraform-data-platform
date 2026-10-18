resource "google_service_account" "ingest_runtime" {
  project      = var.project_id
  account_id   = "ingest-runtime"
  display_name = "${var.env}-ingest-runtime"
  description  = "Runtime identity for ${var.env} Ookla ingestion; not a Terraform deployment identity."
}

resource "google_storage_bucket_iam_member" "raw_objects" {
  bucket = var.raw_bucket_name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.ingest_runtime.email}"
}

resource "google_bigquery_dataset_iam_member" "data_editor" {
  project    = var.project_id
  dataset_id = var.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.ingest_runtime.email}"
}

# Query jobs belong to the project, not a dataset; jobs.create cannot be
# granted at dataset scope. Data access remains dataset/bucket scoped.
resource "google_project_iam_member" "query_jobs" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.ingest_runtime.email}"
}

resource "google_service_account_iam_member" "developer_token_creator" {
  count = var.developer_principal != null && var.env != "prod" ? 1 : 0

  service_account_id = google_service_account.ingest_runtime.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = var.developer_principal
}
