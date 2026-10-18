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
