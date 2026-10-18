variable "project_id" {
  type        = string
  description = "Existing environment project for the webhook secret."
}

variable "env" {
  type        = string
  description = "Environment label."

  validation {
    condition     = contains(["dev", "test", "prod"], var.env)
    error_message = "env must be dev, test or prod."
  }
}

variable "region" {
  type        = string
  description = "Region for user-managed Secret Manager replication."
}

variable "runtime_sa_email" {
  type        = string
  description = "Runtime service account email allowed to read this secret."
}
