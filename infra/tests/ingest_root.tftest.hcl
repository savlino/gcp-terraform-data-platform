mock_provider "google" {
  mock_resource "google_service_account" {
    override_during = plan
    defaults = {
      email = "ingest-scheduler@dep-2610-dev.iam.gserviceaccount.com"
    }
  }
}

override_resource {
  target          = module.iam.google_service_account.ingest_runtime
  override_during = plan
  values = {
    email = "ingest-runtime@dep-2610-dev.iam.gserviceaccount.com"
  }
}

override_resource {
  target          = module.secrets.google_secret_manager_secret.alert_webhook
  override_during = plan
  values = {
    id = "projects/dep-2610-dev/secrets/alert-telegram"
  }
}

variables {
  project_id          = "dep-2610-dev"
  env                 = "dev"
  deletion_protection = false
  image_tag           = "root-test-v1"
  enable_ingest_job   = true
  region_bbox         = { min_lon = -10, max_lon = 5, min_lat = 35, max_lat = 44 }
  min_quarter         = "2024-Q1"
  app_settings = {
    source_bucket        = "ookla-mirror"
    source_region        = "us-east-1"
    maximum_bytes_billed = 123456789
    mart_zoom            = 9
    minimum_row_percent  = 65
    latency_ceiling_ms   = 9000
    source_schema_path   = "/app/schemas/ookla_raw.json"
    native_schema_path   = "/app/schemas/ookla_tiles.json"
  }
}

run "dev_wiring" {
  command = plan

  assert {
    condition = (
      output.ingest_image_uri == "europe-southwest1-docker.pkg.dev/dep-2610-ops/ops-ingest/ingest:root-test-v1" &&
      output.ingest_job_name == "dev-ingest" &&
      output.scheduler_job_name == "dev-ingest-weekly" &&
      output.runtime_service_account_email != output.scheduler_service_account_email
    )
    error_message = "The root must expose the tagged image and distinct runtime/scheduler identities."
  }
}

run "test_paused_by_default" {
  command = plan
  variables {
    env = "test"
  }
  assert {
    condition = (
      output.ingest_image_uri == "europe-southwest1-docker.pkg.dev/dep-2610-ops/ops-ingest/ingest:root-test-v1" &&
      output.scheduler_paused
    )
    error_message = "TEST must use the same ops image as DEV and keep the schedule paused by default."
  }
}

run "prod_paused_by_default" {
  command = plan
  variables {
    env                 = "prod"
    force_destroy       = false
    raw_retention_days  = null
    deletion_protection = true
  }
  assert {
    condition = (
      output.ingest_image_uri == "europe-southwest1-docker.pkg.dev/dep-2610-ops/ops-ingest/ingest:root-test-v1" &&
      output.scheduler_paused &&
      output.ingest_job_deletion_protection &&
      output.native_tables_deletion_protected &&
      output.external_table_deletion_protected &&
      !output.dataset_delete_contents_on_destroy &&
      output.raw_bucket_lifecycle_rule_count == 0 &&
      !output.raw_bucket_force_destroy
    )
    error_message = "PROD must keep the schedule paused, propagate deletion protection to job/BigQuery, and keep bucket protection without a lifecycle rule."
  }
}

run "explicit_dev_pause" {
  command = plan
  variables {
    scheduler_paused = true
  }
}

run "dev_shared_digest" {
  command = plan
  variables {
    image_digest = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  }
  assert {
    condition     = output.ingest_image_uri == "${var.image_repository}@${var.image_digest}"
    error_message = "DEV must use the pinned shared digest."
  }
}

run "test_shared_digest" {
  command = plan
  variables {
    env          = "test"
    image_digest = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  }
  assert {
    condition     = output.ingest_image_uri == run.dev_shared_digest.ingest_image_uri
    error_message = "TEST must use the exact same image URI and digest as DEV."
  }
}

run "prod_shared_digest" {
  command = plan
  variables {
    env                 = "prod"
    deletion_protection = true
    image_digest        = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  }
  assert {
    condition     = output.ingest_image_uri == run.dev_shared_digest.ingest_image_uri
    error_message = "PROD must use the exact same image URI and digest as DEV."
  }
}

run "invalid_quality_threshold" {
  command = plan
  variables {
    app_settings = { minimum_row_percent = 101 }
  }
  expect_failures = [var.app_settings]
}

run "invalid_query_budget" {
  command = plan
  variables {
    app_settings = { maximum_bytes_billed = 0 }
  }
  expect_failures = [var.app_settings]
}

run "invalid_schema_directory" {
  command = plan
  variables {
    app_settings = { schemas_dir = "" }
  }
  expect_failures = [var.app_settings]
}

run "invalid_source_bucket" {
  command = plan
  variables {
    app_settings = { source_bucket = "evil.example/x" }
  }
  expect_failures = [var.app_settings]
}

run "invalid_source_region" {
  command = plan
  variables {
    app_settings = { source_region = "" }
  }
  expect_failures = [var.app_settings]
}
