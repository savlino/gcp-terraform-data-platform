output "raw_bucket_name" {
  description = "Raw bucket name."
  value       = google_storage_bucket.raw.name
}

output "raw_bucket_force_destroy" {
  description = "Whether force_destroy is enabled on the raw bucket."
  value       = google_storage_bucket.raw.force_destroy
}

output "raw_bucket_lifecycle_rule_count" {
  description = "Number of lifecycle rules configured on the raw bucket."
  value       = length(google_storage_bucket.raw.lifecycle_rule)
}
