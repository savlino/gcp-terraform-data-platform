mock_provider "google" {}

variables {
  project_id       = "dep-2610-dev"
  env              = "dev"
  region           = "europe-southwest1"
  runtime_sa_email = "ingest-runtime@dep-2610-dev.iam.gserviceaccount.com"
}

run "regional_container_and_accessor" {
  command = plan
  module {
    source = "../modules/secrets"
  }

  assert {
    condition = (
      google_secret_manager_secret.alert_webhook.project == var.project_id &&
      output.secret_id == "alert-telegram" &&
      one(one(one(google_secret_manager_secret.alert_webhook.replication).user_managed).replicas).location == var.region
    )
    error_message = "Create the named secret with user-managed replication in the supplied region."
  }

  assert {
    condition = google_secret_manager_secret.alert_webhook.labels == tomap({
      project = "gcp-tf-portfolio", env = "dev", managed_by = "terraform"
    })
    error_message = "Secret must carry the required labels."
  }

  assert {
    condition = (
      google_secret_manager_secret_iam_member.runtime_accessor.project == var.project_id &&
      google_secret_manager_secret_iam_member.runtime_accessor.secret_id == output.secret_id &&
      google_secret_manager_secret_iam_member.runtime_accessor.role == "roles/secretmanager.secretAccessor" &&
      google_secret_manager_secret_iam_member.runtime_accessor.member == "serviceAccount:${var.runtime_sa_email}"
    )
    error_message = "Accessor must target only this secret and the supplied runtime identity."
  }
}
