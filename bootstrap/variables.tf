variable "ops_project_id" {
  type        = string
  description = "Project ID used for bootstrap resources and Terraform state."
  default     = "dep-2610-ops"
}

variable "dev_project_id" {
  type        = string
  description = "Project ID for the development environment."
  default     = "dep-2610-dev"
}

variable "test_project_id" {
  type        = string
  description = "Project ID for the test environment."
  default     = "dep-2610-test"
}

variable "prod_project_id" {
  type        = string
  description = "Project ID for the production environment."
  default     = "dep-2610-prod"
}

variable "billing_account_id" {
  type        = string
  description = "Existing billing account ID to use for the monthly budget."
}

variable "state_bucket_name" {
  type        = string
  description = "Globally unique name for the Terraform state bucket."
  default     = "dep-2610-ops-tf-state"

  validation {
    condition     = length(var.state_bucket_name) >= 3 && length(var.state_bucket_name) <= 63
    error_message = "The state bucket name must be between 3 and 63 characters."
  }
}

variable "state_bucket_location" {
  type        = string
  description = "Cloud Storage location for the Terraform state bucket."
  default     = "EU"
}

variable "state_bucket_force_destroy" {
  type        = bool
  description = "Whether Terraform may delete the state bucket when it contains objects."
  default     = false
}

variable "budget_amount" {
  type        = number
  description = "Positive whole-unit monthly budget amount in budget_currency_code."
  default     = 20

  validation {
    condition     = var.budget_amount > 0 && floor(var.budget_amount) == var.budget_amount
    error_message = "The budget amount must be a positive whole number."
  }
}

variable "budget_currency_code" {
  type        = string
  description = "ISO 4217 currency code; must match the billing account currency."
  default     = "EUR"

  validation {
    condition     = can(regex("^[A-Z]{3}$", var.budget_currency_code))
    error_message = "The budget currency code must be three uppercase letters."
  }
}
