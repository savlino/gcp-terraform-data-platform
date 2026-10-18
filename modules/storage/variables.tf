variable "project_id" {
  type        = string
  description = "Project in which to create the raw bucket."
}

variable "env" {
  type        = string
  description = "Environment label and retention policy selector."

  validation {
    condition     = contains(["dev", "test", "prod"], var.env)
    error_message = "env must be dev, test or prod."
  }
}

variable "location" {
  type        = string
  description = "Raw bucket location; must match the BigQuery dataset."
}

variable "force_destroy" {
  type        = bool
  description = "Whether the raw bucket may be destroyed when non-empty."
  default     = false
}

variable "raw_retention_days" {
  type        = number
  description = "Delete raw objects after this many days in non-prod only."
  default     = 30

  validation {
    condition     = var.raw_retention_days > 0 && floor(var.raw_retention_days) == var.raw_retention_days
    error_message = "raw_retention_days must be a positive whole number."
  }
}
