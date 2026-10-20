output "network_name" {
  description = "Custom VPC name."
  value       = google_compute_network.private.name
}

output "subnet_name" {
  description = "Private regional subnet name."
  value       = google_compute_subnetwork.private.name
}

output "subnet_cidr" {
  description = "Effective private subnet CIDR."
  value       = google_compute_subnetwork.private.ip_cidr_range
}

output "iap_demo_vm_name" {
  description = "Demo VM name, or null while disabled."
  value       = var.enable_iap_demo_vm ? google_compute_instance.iap_demo[0].name : null
}

output "iap_demo_vm_zone" {
  description = "Demo VM zone, or null while disabled."
  value       = var.enable_iap_demo_vm ? google_compute_instance.iap_demo[0].zone : null
}
