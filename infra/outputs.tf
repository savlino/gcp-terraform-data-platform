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
