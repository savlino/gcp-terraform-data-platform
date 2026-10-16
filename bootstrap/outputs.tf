output "state_bucket_name" {
  description = "Name of the Terraform state bucket in the ops project."
  value       = google_storage_bucket.terraform_state.name
}

output "project_numbers" {
  description = "Project numbers keyed by environment name."
  value = {
    for name, project in data.google_project.projects :
    name => project.number
  }
}
