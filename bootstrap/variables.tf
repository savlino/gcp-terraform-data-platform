variable "gitlab_project_path" {
  type        = string
  description = "Exact case-sensitive GitLab.com namespace/project path authorized to federate."

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)+$", var.gitlab_project_path))
    error_message = "gitlab_project_path must be a namespace/project path without URL, trailing slash or quotes."
  }
}

variable "ci_environments" {
  type        = set(string)
  description = "Environment CI accounts/grants to provision; start with DEV until other environment files exist."
  default     = ["dev"]

  validation {
    condition     = length(var.ci_environments) > 0 && alltrue([for env in var.ci_environments : contains(["dev", "test", "prod"], env)])
    error_message = "ci_environments must be a non-empty subset of dev, test and prod."
  }
}

variable "developer_principal" {
  type        = string
  description = "Optional human user:/group: email principal granted project-level Token Creator and IAP tunnel access in DEV and TEST only."
  default     = null

  validation {
    condition     = var.developer_principal == null ? true : can(regex("^(user|group):[^@\\s:]+@[^@\\s:]+\\.[^@\\s:]+$", var.developer_principal))
    error_message = "developer_principal must be null or a user:/group: email principal, not a workload identity."
  }
}

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
