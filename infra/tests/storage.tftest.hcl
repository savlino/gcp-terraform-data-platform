mock_provider "google" {}

variables {
  env                = "dev"
  project_id         = "dep-2610-dev"
  location           = "europe-southwest1"
  force_destroy      = true
  raw_retention_days = 30
}

run "dev_bucket" {
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
      length(google_storage_bucket.raw.lifecycle_rule) == 1 &&
      one(one(google_storage_bucket.raw.lifecycle_rule).condition).age == 30 &&
      one(one(google_storage_bucket.raw.lifecycle_rule).action).type == "Delete" &&
      one(google_storage_bucket.raw.soft_delete_policy).retention_duration_seconds == 0
    )
    error_message = "DEV raw objects must expire after 30 days without soft-delete retention."
  }

  assert {
    condition = google_storage_bucket.raw.labels == tomap({
      project = "gcp-tf-portfolio", env = "dev", managed_by = "terraform"
    })
    error_message = "Required bucket labels must be present."
  }
}

run "prod_bucket" {
  command = plan
  module {
    source = "../modules/storage"
  }
  variables {
    env           = "prod"
    project_id    = "dep-2610-prod"
    force_destroy = false
  }

  assert {
    condition     = length(google_storage_bucket.raw.lifecycle_rule) == 0 && !google_storage_bucket.raw.force_destroy
    error_message = "PROD must not receive the non-prod expiration rule or force destruction."
  }
}
