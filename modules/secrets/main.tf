resource "google_secret_manager_secret" "alert_webhook" {
  project   = var.project_id
  secret_id = "alert-telegram"

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }

  labels = {
    project    = "gcp-tf-portfolio"
    env        = var.env
    managed_by = "terraform"
  }
}

resource "google_secret_manager_secret_iam_member" "runtime_accessor" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.alert_webhook.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${var.runtime_sa_email}"
}
