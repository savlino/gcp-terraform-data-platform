output "network_name" {
  description = "Custom VPC name; Cloud Run is not attached."
  value       = module.network.network_name
}

output "subnet_name" {
  description = "Regional private subnet name."
  value       = module.network.subnet_name
}

output "subnet_cidr" {
  description = "Effective environment subnet CIDR."
  value       = module.network.subnet_cidr
}

output "iap_demo_vm_name" {
  description = "Temporary demo VM name; null by default."
  value       = module.network.iap_demo_vm_name
}

output "iap_demo_vm_zone" {
  description = "Temporary demo VM zone; null by default."
  value       = module.network.iap_demo_vm_zone
}

output "raw_bucket_name" {
  description = "Raw source bucket name."
  value       = module.storage.raw_bucket_name
}

output "raw_bucket_force_destroy" {
  description = "Whether force_destroy is enabled for the raw bucket."
  value       = module.storage.raw_bucket_force_destroy
}

output "raw_bucket_lifecycle_rule_count" {
  description = "Number of raw bucket lifecycle rules."
  value       = module.storage.raw_bucket_lifecycle_rule_count
}

output "dataset_id" {
  description = "BigQuery dataset ID."
  value       = module.bigquery.dataset_id
}

output "table_ids" {
  description = "Native BigQuery table IDs keyed by purpose."
  value       = module.bigquery.table_ids
}

output "native_tables_deletion_protected" {
  description = "True when all native BigQuery tables are deletion-protected."
  value       = module.bigquery.native_tables_deletion_protected
}

output "dataset_delete_contents_on_destroy" {
  description = "Dataset delete_contents_on_destroy flag."
  value       = module.bigquery.dataset_delete_contents_on_destroy
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
  description = "Existing shared Artifact Registry repository ID (managed by bootstrap)."
  value       = split("/", var.image_repository)[2]
}

output "ingest_image_uri" {
  description = "Shared image URI using image_digest when set, otherwise image_tag."
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

output "scheduler_paused" {
  description = "Whether the schedule is paused; null until enabled."
  value       = module.ingest_job.scheduler_paused
}

output "ingest_job_deletion_protection" {
  description = "Whether the Cloud Run job has deletion protection; null until enabled."
  value       = module.ingest_job.job_deletion_protection
}
