locals {
  labels = {
    project    = "gcp-tf-portfolio"
    env        = var.env
    managed_by = "terraform"
  }
  image_uri = var.image_digest == null ? "${var.image_repository}:${var.image_tag}" : "${var.image_repository}@${var.image_digest}"
}

resource "google_cloud_run_v2_job" "ingest" {
  count = var.enable_ingest_job ? 1 : 0

  project             = var.project_id
  name                = "${var.env}-ingest"
  location            = var.region
  labels              = local.labels
  deletion_protection = var.deletion_protection

  template {
    labels      = local.labels
    task_count  = 1
    parallelism = 1

    template {
      service_account = var.runtime_sa_email
      timeout         = "${var.task_timeout_seconds}s"
      max_retries     = var.task_max_retries

      containers {
        image = local.image_uri
        args  = ["run"]

        resources {
          limits = {
            cpu    = "1"
            memory = "${var.task_memory_mib}Mi"
          }
        }

        dynamic "env" {
          for_each = var.application_env
          content {
            name  = env.key
            value = env.value
          }
        }
      }
    }
  }
}

resource "google_service_account" "scheduler" {
  count = var.enable_ingest_job ? 1 : 0

  project      = var.project_id
  account_id   = "ingest-scheduler"
  display_name = "${var.env}-ingest-scheduler"
  description  = "OAuth identity used only to launch the ${var.env} ingestion job."
}

resource "google_cloud_run_v2_job_iam_member" "scheduler_invoker" {
  count = var.enable_ingest_job ? 1 : 0

  project  = var.project_id
  location = google_cloud_run_v2_job.ingest[0].location
  name     = google_cloud_run_v2_job.ingest[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler[0].email}"
}

resource "google_cloud_scheduler_job" "ingest" {
  count = var.enable_ingest_job ? 1 : 0

  project          = var.project_id
  region           = var.scheduler_region
  name             = "${var.env}-ingest-weekly"
  description      = "Launch the ${var.env} Ookla ingestion job weekly."
  schedule         = var.schedule
  time_zone        = "Europe/Madrid"
  paused           = var.scheduler_paused
  attempt_deadline = "180s"

  retry_config {
    retry_count        = 0
    max_retry_duration = "0s"
  }

  http_target {
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.ingest[0].name}:run"
    http_method = "POST"
    body        = base64encode("{}")
    headers     = { "Content-Type" = "application/json" }

    oauth_token {
      service_account_email = google_service_account.scheduler[0].email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  depends_on = [google_cloud_run_v2_job_iam_member.scheduler_invoker]
}
