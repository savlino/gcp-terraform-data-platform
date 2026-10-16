provider "google" {
  project = var.ops_project_id

  # Attribute API quota and billing to the ops project for user-ADC calls,
  # including Billing Budgets API requests.
  billing_project       = var.ops_project_id
  user_project_override = true
}
