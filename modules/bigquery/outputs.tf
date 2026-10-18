output "dataset_id" {
  description = "Dataset ID."
  value       = google_bigquery_dataset.network_perf.dataset_id
}

output "table_ids" {
  description = "Native table IDs keyed by table name."
  value       = { for name, table in google_bigquery_table.native : name => table.table_id }
}
