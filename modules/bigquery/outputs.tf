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
