resource "google_artifact_registry_repository" "ingest" {
  project       = var.ops_project_id
  location      = var.artifact_registry_region
  repository_id = "ops-ingest"
  description   = "Shared immutable Ookla ingestion releases for DEV, TEST and PROD."
  format        = "DOCKER"
  labels = {
    project    = "gcp-tf-portfolio"
    env        = "ops"
    managed_by = "terraform"
  }

  docker_config {
    immutable_tags = true
  }

  cleanup_policy_dry_run = var.artifact_cleanup_dry_run
  cleanup_policies {
    id     = "delete-untagged"
    action = "DELETE"
    condition {
      tag_state  = "UNTAGGED"
      older_than = "604800s"
    }
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.apis]
}

resource "google_project_service_identity" "cloud_run" {
  provider = google-beta
  for_each = { for env, project in local.projects : env => project if env != "ops" }

  project = each.value
  service = "run.googleapis.com"

  depends_on = [google_project_service.apis]
}

resource "google_artifact_registry_repository_iam_member" "cloud_run_reader" {
  for_each = google_project_service_identity.cloud_run

  project    = var.ops_project_id
  location   = google_artifact_registry_repository.ingest.location
  repository = google_artifact_registry_repository.ingest.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${each.value.email}"
}

# Cross-project deployment also checks the caller's access to the image.
resource "google_artifact_registry_repository_iam_member" "ci_apply_reader" {
  for_each = google_service_account.ci_apply

  project    = var.ops_project_id
  location   = google_artifact_registry_repository.ingest.location
  repository = google_artifact_registry_repository.ingest.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${each.value.email}"
}
