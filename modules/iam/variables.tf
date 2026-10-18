variable "project_id" {
  type        = string
  description = "Environment project in which to create the runtime service account."
}

variable "env" {
  type        = string
  description = "Environment; developer impersonation is never granted in prod."

  validation {
    condition     = contains(["dev", "test", "prod"], var.env)
    error_message = "env must be dev, test or prod."
  }
}

variable "raw_bucket_name" {
  type        = string
  description = "Existing raw bucket name, without gs://, for object-level access."
}

variable "dataset_id" {
  type        = string
  description = "Existing environment dataset ID, without a project prefix."
}

variable "developer_principal" {
  type        = string
  description = "Optional user:, group: or serviceAccount: email principal allowed to impersonate runtime outside prod."
  default     = null

  validation {
    condition = var.developer_principal == null ? true : can(regex(
      "^(user|group|serviceAccount):[^@\\s:]+@[^@\\s:]+\\.[^@\\s:]+$",
      var.developer_principal
    ))
    error_message = "developer_principal must be null or a user:, group: or serviceAccount: email principal."
  }
}
