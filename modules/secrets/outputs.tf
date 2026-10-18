output "secret_id" {
  description = "Short secret ID used to add versions manually."
  value       = google_secret_manager_secret.alert_webhook.secret_id
}

output "secret_name" {
  description = "Fully qualified secret resource name for the future app."
  value       = google_secret_manager_secret.alert_webhook.id
}
