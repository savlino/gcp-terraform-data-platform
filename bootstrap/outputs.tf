output "ingest_repository_id" {
  description = "Shared ops Artifact Registry repository ID."
  value       = google_artifact_registry_repository.ingest.repository_id
}

output "ingest_image_repository" {
  description = "Shared image path without tag or digest; pass this to every environment."
  value       = "${var.artifact_registry_region}-docker.pkg.dev/${var.ops_project_id}/${google_artifact_registry_repository.ingest.repository_id}/ingest"
}

output "cloud_run_service_agents" {
  description = "Environment Cloud Run service agents with Reader on the shared repository."
  value       = { for env, identity in google_project_service_identity.cloud_run : env => identity.email }
}

output "gitlab_wif_provider" {
  description = "Full provider name for gcloud create-cred-config (without https://)."
  value       = local.wif_provider
}

output "gitlab_wif_audience" {
  description = "Exact GitLab ID-token audience, also allowlisted by the provider."
  value       = local.wif_audience
}

output "ci_plan_service_accounts" {
  description = "Ops-project plan SA emails keyed by environment."
  value       = { for env, account in google_service_account.ci_plan : env => account.email }
}

output "ci_apply_service_accounts" {
  description = "Ops-project protected-ref apply SA emails keyed by environment."
  value       = { for env, account in google_service_account.ci_apply : env => account.email }
}

output "state_bucket_name" {
  description = "Name of the Terraform state bucket in the ops project."
  value       = google_storage_bucket.terraform_state.name
}

output "project_numbers" {
  description = "Project numbers keyed by environment name."
  value = {
    for name, project in data.google_project.projects :
    name => project.number
  }
}
