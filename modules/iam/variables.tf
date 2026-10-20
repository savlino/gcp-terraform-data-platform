variable "project_id" {
  type        = string
  description = "Environment project in which to create the runtime service account."
}

variable "env" {
  type        = string
  description = "Environment used to name the runtime identity."

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
