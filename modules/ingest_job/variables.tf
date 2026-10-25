variable "project_id" {
  type        = string
  description = "Environment project containing the job and scheduler, not the shared repository."
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
  description = "Cloud Run location, matching the data region."
}

variable "scheduler_region" {
  type        = string
  description = "Independent supported Cloud Scheduler location."
  default     = "europe-west1"
}

variable "runtime_sa_email" {
  type        = string
  description = "Existing runtime service account; never the scheduler identity."
}

variable "image_tag" {
  type        = string
  description = "Unique immutable tag already pushed before enabling the job."

  validation {
    condition     = can(regex("^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$", var.image_tag)) && var.image_tag != "latest"
    error_message = "image_tag must be a valid explicit Docker tag, not latest."
  }
}

variable "image_repository" {
  type        = string
  description = "Existing shared Artifact Registry image path from bootstrap, without tag or digest."

  validation {
    condition     = can(regex("^[a-z]+-[a-z]+[0-9]+-docker\\.pkg\\.dev/[a-z0-9-]+/[a-z0-9_-]+/[a-z0-9/_-]+$", var.image_repository))
    error_message = "image_repository must be a regional Artifact Registry image path without tag or digest."
  }
}

variable "image_digest" {
  type        = string
  description = "Optional sha256 digest shared across environments; takes precedence over image_tag."
  default     = null

  validation {
    condition     = var.image_digest == null ? true : can(regex("^sha256:[a-f0-9]{64}$", var.image_digest))
    error_message = "image_digest must be null or sha256: followed by 64 lowercase hexadecimal characters."
  }
}

variable "enable_ingest_job" {
  type        = bool
  description = "Create the job and schedule only after publishing the image to the bootstrap-owned repository."
  default     = false
}

variable "application_env" {
  type        = map(string)
  description = "Non-secret application settings; ALERT_SECRET is a resource name, never a payload. Values enter Terraform state."
}

variable "scheduler_paused" {
  type        = bool
  description = "Pause scheduled launches; root defaults to active in DEV and paused elsewhere."
  default     = true
}

variable "schedule" {
  type        = string
  description = "Europe/Madrid cron schedule, weekly on Monday by default."
  default     = "0 6 * * 1"

  validation {
    condition     = length(split(" ", trimspace(var.schedule))) == 5
    error_message = "schedule must contain five cron fields."
  }
}

variable "task_memory_mib" {
  type        = number
  description = "Memory in MiB for a single 1-vCPU streaming task; validate sizing with DEV metrics."
  default     = 1024

  validation {
    condition     = contains([512, 1024, 2048, 4096], var.task_memory_mib)
    error_message = "task_memory_mib must be 512, 1024, 2048 or 4096 for the 1-vCPU task."
  }
}

variable "task_timeout_seconds" {
  type        = number
  description = "Maximum duration per task attempt; initial estimate, not a transfer benchmark."
  default     = 3600

  validation {
    condition     = var.task_timeout_seconds >= 60 && var.task_timeout_seconds <= 86400 && floor(var.task_timeout_seconds) == var.task_timeout_seconds
    error_message = "task_timeout_seconds must be a whole number between 60 and 86400."
  }
}

variable "task_max_retries" {
  type        = number
  description = "Additional task attempts; default zero avoids automatic full-pipeline repeats."
  default     = 0

  validation {
    condition     = var.task_max_retries >= 0 && var.task_max_retries <= 1 && floor(var.task_max_retries) == var.task_max_retries
    error_message = "task_max_retries must be a whole number from 0 to 1."
  }
}

variable "deletion_protection" {
  type        = bool
  description = "Protect the Cloud Run job from deletion; use true in PROD."
  default     = true
}
