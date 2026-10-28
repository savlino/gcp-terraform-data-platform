output "dataset_id" {
  description = "Dataset ID."
  value       = google_bigquery_dataset.network_perf.dataset_id
}

output "table_ids" {
  description = "Native table IDs keyed by table name."
  value       = { for name, table in google_bigquery_table.native : name => table.table_id }
}

output "native_tables_deletion_protected" {
  description = "True when all native tables have deletion protection enabled."
  value       = alltrue([for table in google_bigquery_table.native : table.deletion_protection])
}

output "dataset_delete_contents_on_destroy" {
  description = "Dataset delete_contents_on_destroy flag."
  value       = google_bigquery_dataset.network_perf.delete_contents_on_destroy
}
