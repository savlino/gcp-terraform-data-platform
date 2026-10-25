output "dataset_id" {
  description = "Dataset ID."
  value       = google_bigquery_dataset.network_perf.dataset_id
}

output "table_ids" {
  description = "Native and external table IDs keyed by table name."
  value = merge(
    { for name, table in google_bigquery_table.native : name => table.table_id },
    { ext_ookla_raw = google_bigquery_table.ext_ookla_raw.table_id }
  )
}

output "native_tables_deletion_protected" {
  description = "True when all native tables have deletion protection enabled."
  value       = alltrue([for table in google_bigquery_table.native : table.deletion_protection])
}

output "external_table_deletion_protected" {
  description = "Deletion protection state of the external table."
  value       = google_bigquery_table.ext_ookla_raw.deletion_protection
}

output "dataset_delete_contents_on_destroy" {
  description = "Dataset delete_contents_on_destroy flag."
  value       = google_bigquery_dataset.network_perf.delete_contents_on_destroy
}
