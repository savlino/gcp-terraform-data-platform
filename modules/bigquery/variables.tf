variable "project_id" {
  type        = string
  description = "Project in which to create the BigQuery dataset."
}

variable "env" {
  type        = string
  description = "Environment label."

  validation {
    condition     = contains(["dev", "test", "prod"], var.env)
    error_message = "env must be dev, test or prod."
  }
}

variable "location" {
  type        = string
  description = "Dataset location; must match the raw bucket."
}

variable "deletion_protection" {
  type        = bool
  description = "Prevent Terraform from destroying native tables."
  default     = true
}

variable "delete_contents_on_destroy" {
  type        = bool
  description = "Whether dataset deletion may remove its contents."
  default     = false
}

variable "enable_sql_test_dataset" {
  type        = bool
  description = "Create the disposable dataset reserved for DEV SQL integration tests."
  default     = false
}

variable "sql_test_principal" {
  type        = string
  description = "Service account email granted roles/bigquery.dataEditor on sql_tests only; null for no grant. Ignored when the dataset is disabled."
  default     = null

  validation {
    condition     = var.sql_test_principal == null ? true : can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\\.iam\\.gserviceaccount\\.com$", var.sql_test_principal))
    error_message = "sql_test_principal must be null or a user-managed service account email (name@project.iam.gserviceaccount.com) without a member prefix."
  }
}
