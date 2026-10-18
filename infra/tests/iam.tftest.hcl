mock_provider "google" {
  mock_resource "google_service_account" {
    override_during = plan
    defaults = {
      name  = "projects/dep-2610-dev/serviceAccounts/ingest-runtime@dep-2610-dev.iam.gserviceaccount.com"
      email = "ingest-runtime@dep-2610-dev.iam.gserviceaccount.com"
    }
  }
}

variables {
  env             = "dev"
  project_id      = "dep-2610-dev"
  raw_bucket_name = "dep-2610-dev-raw"
  dataset_id      = "network_perf"
}

run "default_no_impersonation" {
  command = plan
  module {
    source = "../modules/iam"
  }

  assert {
    condition = (
      google_service_account.ingest_runtime.account_id == "ingest-runtime" &&
      google_service_account.ingest_runtime.project == var.project_id &&
      output.service_account_email == google_service_account.ingest_runtime.email &&
      length(google_service_account_iam_member.developer_token_creator) == 0
    )
    error_message = "Create the runtime identity without a developer grant by default."
  }

  assert {
    condition = (
      google_storage_bucket_iam_member.raw_objects.bucket == var.raw_bucket_name &&
      google_storage_bucket_iam_member.raw_objects.role == "roles/storage.objectUser" &&
      google_storage_bucket_iam_member.raw_objects.member == "serviceAccount:${output.service_account_email}" &&
      google_bigquery_dataset_iam_member.data_editor.project == var.project_id &&
      google_bigquery_dataset_iam_member.data_editor.dataset_id == var.dataset_id &&
      google_bigquery_dataset_iam_member.data_editor.role == "roles/bigquery.dataEditor" &&
      google_bigquery_dataset_iam_member.data_editor.member == "serviceAccount:${output.service_account_email}" &&
      google_project_iam_member.query_jobs.project == var.project_id &&
      google_project_iam_member.query_jobs.role == "roles/bigquery.jobUser" &&
      google_project_iam_member.query_jobs.member == "serviceAccount:${output.service_account_email}"
    )
    error_message = "Runtime grants must use the requested roles, identity and narrow scopes."
  }
}

run "dev_impersonation" {
  command = plan
  module {
    source = "../modules/iam"
  }
  variables {
    developer_principal = "user:developer@example.com"
  }

  assert {
    condition = (
      length(google_service_account_iam_member.developer_token_creator) == 1 &&
      google_service_account_iam_member.developer_token_creator[0].member == var.developer_principal &&
      google_service_account_iam_member.developer_token_creator[0].role == "roles/iam.serviceAccountTokenCreator" &&
      google_service_account_iam_member.developer_token_creator[0].service_account_id == google_service_account.ingest_runtime.name
    )
    error_message = "DEV impersonation must be scoped to this service account."
  }

  assert {
    condition = alltrue([
      for role in concat(
        [
          google_storage_bucket_iam_member.raw_objects.role,
          google_bigquery_dataset_iam_member.data_editor.role,
          google_project_iam_member.query_jobs.role
        ],
        [for grant in google_service_account_iam_member.developer_token_creator : grant.role]
      ) : !contains(["roles/owner", "roles/editor", "roles/viewer"], role)
    ])
    error_message = "Runtime and developer grants must not use basic roles (owner, editor or viewer)."
  }
}

run "test_impersonation" {
  command = plan
  module {
    source = "../modules/iam"
  }
  variables {
    env                 = "test"
    project_id          = "dep-2610-test"
    raw_bucket_name     = "dep-2610-test-raw"
    developer_principal = "group:developers@example.com"
  }

  assert {
    condition     = length(google_service_account_iam_member.developer_token_creator) == 1
    error_message = "An explicit developer principal must enable impersonation in TEST."
  }
}

run "prod_blocks_impersonation" {
  command = plan
  module {
    source = "../modules/iam"
  }
  variables {
    env                 = "prod"
    project_id          = "dep-2610-prod"
    raw_bucket_name     = "dep-2610-prod-raw"
    developer_principal = "user:developer@example.com"
  }

  assert {
    condition     = length(google_service_account_iam_member.developer_token_creator) == 0
    error_message = "PROD must never create the developer token creator grant, even when configured."
  }
}

run "reject_public_principal" {
  command = plan
  module {
    source = "../modules/iam"
  }
  variables {
    developer_principal = "allUsers"
  }
  expect_failures = [var.developer_principal]
}

run "accept_group_principal" {
  command = plan
  module { source = "../modules/iam" }
  variables { developer_principal = "group:developers@example.com" }
  assert {
    condition     = length(google_service_account_iam_member.developer_token_creator) == 1
    error_message = "A group principal must be accepted."
  }
}

run "accept_service_account_principal" {
  command = plan
  module { source = "../modules/iam" }
  variables { developer_principal = "serviceAccount:ci@dep-2610-ops.iam.gserviceaccount.com" }
  assert {
    condition     = length(google_service_account_iam_member.developer_token_creator) == 1
    error_message = "A service account principal must be accepted."
  }
}