output "raw_bucket_name" {
  description = "Raw source bucket name."
  value       = module.storage.raw_bucket_name
}

output "dataset_id" {
  description = "BigQuery dataset ID."
  value       = module.bigquery.dataset_id
}

output "table_ids" {
  description = "Native BigQuery table IDs keyed by purpose."
  value       = module.bigquery.table_ids
}

output "runtime_service_account_email" {
  description = "Ingestion runtime identity; not the Terraform deployment identity."
  value       = module.iam.service_account_email
}

output "alert_secret_id" {
  description = "Alert notification secret ID (Telegram bot token and chat ID as JSON); versions are added manually, not through Terraform."
  value       = module.secrets.secret_id
}

output "alert_secret_name" {
  description = "Full resource name of the alert notification secret, used by the ingestion job."
  value       = module.secrets.secret_name
}

output "ingest_repository_id" {
  description = "Application Artifact Registry repository ID."
  value       = module.ingest_job.repository_id
}

output "ingest_image_uri" {
  description = "Image URI using the environment's configured image_tag."
  value       = module.ingest_job.image_uri
}

output "ingest_job_name" {
  description = "Cloud Run job name; null until enabled."
  value       = module.ingest_job.job_name
}

output "scheduler_job_name" {
  description = "Weekly schedule name; null until enabled."
  value       = module.ingest_job.scheduler_job_name
}

output "scheduler_service_account_email" {
  description = "Scheduler OAuth identity; null until enabled."
  value       = module.ingest_job.scheduler_sa_email
}
