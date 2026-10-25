variable "project_id" {
  type        = string
  description = "Project in which to create the raw bucket."
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
  description = "Raw bucket location; must match the BigQuery dataset."
}

variable "force_destroy" {
  type        = bool
  description = "Whether the raw bucket may be destroyed when non-empty."
  default     = false
}

variable "raw_retention_days" {
  type        = number
  description = "Optional object age for lifecycle deletion; null disables the rule."
  default     = null

  validation {
    condition     = var.raw_retention_days == null ? true : var.raw_retention_days >= 1 && floor(var.raw_retention_days) == var.raw_retention_days
    error_message = "raw_retention_days must be null or an integer greater than or equal to 1."
  }
}
