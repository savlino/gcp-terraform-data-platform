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
