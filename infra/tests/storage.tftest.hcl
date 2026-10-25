mock_provider "google" {}

variables {
  env                = "dev"
  project_id         = "dep-2610-dev"
  location           = "europe-southwest1"
  force_destroy      = true
  raw_retention_days = null
}

run "lifecycle_disabled_when_null" {
  command = plan
  module {
    source = "../modules/storage"
  }

  assert {
    condition = (
      google_storage_bucket.raw.uniform_bucket_level_access &&
      google_storage_bucket.raw.public_access_prevention == "enforced" &&
      google_storage_bucket.raw.force_destroy &&
      google_storage_bucket.raw.location == var.location
    )
    error_message = "DEV bucket must be private, regional and destroyable."
  }

  assert {
    condition = (
      length(google_storage_bucket.raw.lifecycle_rule) == 0 &&
      one(google_storage_bucket.raw.soft_delete_policy).retention_duration_seconds == 0
    )
    error_message = "Null raw_retention_days must disable lifecycle deletion while keeping soft-delete disabled."
  }

  assert {
    condition = google_storage_bucket.raw.labels == tomap({
      project = "gcp-tf-portfolio", env = "dev", managed_by = "terraform"
    })
    error_message = "Required bucket labels must be present."
  }
}

run "lifecycle_enabled_when_set" {
  command = plan
  module {
    source = "../modules/storage"
  }
  variables {
    raw_retention_days = 30
  }

  assert {
    condition = (
      length(google_storage_bucket.raw.lifecycle_rule) == 1 &&
      one(one(google_storage_bucket.raw.lifecycle_rule).condition).age == 30 &&
      one(one(google_storage_bucket.raw.lifecycle_rule).action).type == "Delete"
    )
    error_message = "A positive raw_retention_days must create one Delete lifecycle rule with the configured age."
  }
}

run "reject_zero_retention" {
  command = plan
  module {
    source = "../modules/storage"
  }
  variables {
    raw_retention_days = 0
  }
  expect_failures = [var.raw_retention_days]
}
