mock_provider "google" {
  mock_resource "google_service_account" {
    override_during = plan
    defaults = {
      name  = "projects/dep-2610-ops/serviceAccounts/ci-mock@dep-2610-ops.iam.gserviceaccount.com"
      email = "ci-mock@dep-2610-ops.iam.gserviceaccount.com"
    }
  }
  mock_resource "google_artifact_registry_repository" {
    override_during = plan
    defaults = {
      name = "projects/dep-2610-ops/locations/europe-southwest1/repositories/ops-ingest"
    }
  }
  mock_data "google_project" {
    override_during = plan
    defaults = {
      number = "123456789012"
    }
  }
}

mock_provider "google-beta" {}

override_resource {
  target          = google_project_service_identity.cloud_run["dev"]
  override_during = plan
  values = {
    email = "service-111111111111@serverless-robot-prod.iam.gserviceaccount.com"
  }
}

override_resource {
  target          = google_project_service_identity.cloud_run["test"]
  override_during = plan
  values = {
    email = "service-222222222222@serverless-robot-prod.iam.gserviceaccount.com"
  }
}

override_resource {
  target          = google_project_service_identity.cloud_run["prod"]
  override_during = plan
  values = {
    email = "service-333333333333@serverless-robot-prod.iam.gserviceaccount.com"
  }
}

variables {
  billing_account_id  = "000000-000000-000000"
  gitlab_project_path = "savlino/gcp-terraform-data-platform"
}

run "shared_ops_registry" {
  command = plan

  assert {
    condition = (
      google_artifact_registry_repository.ingest.project == var.ops_project_id &&
      google_artifact_registry_repository.ingest.location == var.artifact_registry_region &&
      google_artifact_registry_repository.ingest.repository_id == "ops-ingest" &&
      google_artifact_registry_repository.ingest.format == "DOCKER" &&
      google_artifact_registry_repository.ingest.labels == tomap({
        project = "gcp-tf-portfolio", env = "ops", managed_by = "terraform"
      }) &&
      google_artifact_registry_repository.ingest.docker_config[0].immutable_tags &&
      google_artifact_registry_repository.ingest.cleanup_policy_dry_run == var.artifact_cleanup_dry_run &&
      length(google_artifact_registry_repository.ingest.cleanup_policies) == 1 &&
      alltrue([for policy in google_artifact_registry_repository.ingest.cleanup_policies :
        policy.action == "DELETE" &&
        policy.condition[0].tag_state == "UNTAGGED" &&
        policy.condition[0].older_than == "604800s"
      ]) &&
      output.ingest_image_repository == "europe-southwest1-docker.pkg.dev/dep-2610-ops/ops-ingest/ingest" &&
      google_project_service.apis["dep-2610-ops/artifactregistry.googleapis.com"].project == var.ops_project_id
    )
    error_message = "Bootstrap must own one labelled immutable shared ops repository with the existing cleanup policy."
  }

  assert {
    condition = (
      toset(keys(google_project_service_identity.cloud_run)) == toset(["dev", "test", "prod"]) &&
      toset(keys(google_artifact_registry_repository_iam_member.cloud_run_reader)) == toset(["dev", "test", "prod"]) &&
      alltrue([for env, identity in google_project_service_identity.cloud_run :
        identity.project == local.projects[env] &&
        identity.service == "run.googleapis.com" &&
        google_artifact_registry_repository_iam_member.cloud_run_reader[env].project == var.ops_project_id &&
        google_artifact_registry_repository_iam_member.cloud_run_reader[env].repository == google_artifact_registry_repository.ingest.name &&
        google_artifact_registry_repository_iam_member.cloud_run_reader[env].role == "roles/artifactregistry.reader" &&
        google_artifact_registry_repository_iam_member.cloud_run_reader[env].member == "serviceAccount:${identity.email}"
      ]) &&
      length(toset(values(output.cloud_run_service_agents))) == 3 &&
      output.cloud_run_service_agents["prod"] == "service-333333333333@serverless-robot-prod.iam.gserviceaccount.com"
    )
    error_message = "All three distinct Cloud Run agents, including PROD with DEV-only CI, need repository-scoped Reader."
  }

  assert {
    condition = (
      toset(keys(google_artifact_registry_repository_iam_member.ci_apply_reader)) == toset(["dev"]) &&
      google_artifact_registry_repository_iam_member.ci_apply_reader["dev"].role == "roles/artifactregistry.reader" &&
      google_artifact_registry_repository_iam_member.ci_apply_reader["dev"].member == "serviceAccount:${google_service_account.ci_apply["dev"].email}" &&
      alltrue([for grant in google_project_iam_member.ci_apply : grant.role != "roles/artifactregistry.admin"])
    )
    error_message = "Apply identities may read the shared image but must not administer environment registries."
  }
}

run "configurable_registry_location" {
  command = plan
  variables {
    artifact_registry_region = "europe-west1"
  }
  assert {
    condition     = output.ingest_image_repository == "europe-west1-docker.pkg.dev/dep-2610-ops/ops-ingest/ingest"
    error_message = "Registry region must come from a bootstrap variable."
  }
}

run "reject_invalid_registry_region" {
  command = plan
  variables {
    artifact_registry_region = "not-a-region"
  }
  expect_failures = [var.artifact_registry_region]
}
