output "service_account_email" {
  description = "Runtime service account email for the ingestion job and local impersonation."
  value       = google_service_account.ingest_runtime.email
}
