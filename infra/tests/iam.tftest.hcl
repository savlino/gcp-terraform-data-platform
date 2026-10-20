mock_provider "google" {
  mock_resource "google_service_account" {
    override_during = plan
    defaults = {
      email = "ingest-runtime@dep-2610-dev.iam.gserviceaccount.com"
    }
  }
}

variables {
  project_id      = "dep-2610-dev"
  env             = "dev"
  raw_bucket_name = "dep-2610-dev-raw"
  dataset_id      = "network_perf"
}

run "runtime_scoped_grants" {
  command = plan
  module {
    source = "../modules/iam"
  }

  assert {
    condition = (
      google_service_account.ingest_runtime.account_id == "ingest-runtime" &&
      google_service_account.ingest_runtime.display_name == "dev-ingest-runtime" &&
      output.service_account_email == google_service_account.ingest_runtime.email &&
      google_storage_bucket_iam_member.raw_objects.bucket == var.raw_bucket_name &&
      google_storage_bucket_iam_member.raw_objects.role == "roles/storage.objectUser" &&
      google_bigquery_dataset_iam_member.data_editor.project == var.project_id &&
      google_bigquery_dataset_iam_member.data_editor.dataset_id == var.dataset_id &&
      google_bigquery_dataset_iam_member.data_editor.role == "roles/bigquery.dataEditor" &&
      google_project_iam_member.query_jobs.project == var.project_id &&
      google_project_iam_member.query_jobs.role == "roles/bigquery.jobUser"
    )
    error_message = "Runtime gets scoped object/data access and project-level query creation, not deployment roles."
  }

  assert {
    condition = alltrue([
      for grant in [google_storage_bucket_iam_member.raw_objects, google_bigquery_dataset_iam_member.data_editor, google_project_iam_member.query_jobs] :
      grant.member == "serviceAccount:${google_service_account.ingest_runtime.email}" &&
      !contains(["roles/owner", "roles/editor", "roles/viewer"], grant.role)
    ])
    error_message = "Every workload grant must target the runtime with a non-basic role."
  }
}

run "prod_runtime_identity" {
  command = plan
  module {
    source = "../modules/iam"
  }
  variables {
    project_id = "dep-2610-prod"
    env        = "prod"
  }

  assert {
    condition = (
      google_service_account.ingest_runtime.project == "dep-2610-prod" &&
      google_service_account.ingest_runtime.display_name == "prod-ingest-runtime" &&
      google_project_iam_member.query_jobs.role == "roles/bigquery.jobUser"
    )
    error_message = "PROD must retain its dedicated workload identity and the same limited runtime role."
  }
}
