output "repository_id" {
  description = "Artifact Registry repository ID."
  value       = google_artifact_registry_repository.ingest.repository_id
}

output "image_uri" {
  description = "Full image URI to build and push before enabling the job."
  value       = local.image_uri
}

output "job_name" {
  description = "Cloud Run job name, or null during repository-only provisioning."
  value       = var.enable_ingest_job ? google_cloud_run_v2_job.ingest[0].name : null
}

output "scheduler_job_name" {
  description = "Scheduler job name, or null while disabled."
  value       = var.enable_ingest_job ? google_cloud_scheduler_job.ingest[0].name : null
}

output "scheduler_sa_email" {
  description = "Scheduler OAuth identity, or null while disabled."
  value       = var.enable_ingest_job ? google_service_account.scheduler[0].email : null
}
