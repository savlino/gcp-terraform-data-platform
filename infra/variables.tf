variable "env" {
  type        = string
  description = "Environment name; use a separate project and backend prefix for each."

  validation {
    condition     = contains(["dev", "test", "prod"], var.env)
    error_message = "env must be dev, test or prod."
  }
}

variable "project_id" {
  type        = string
  description = "Existing GCP project ID for this environment."

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "project_id must be a valid 6-30 character GCP project ID."
  }
}

variable "region" {
  type        = string
  description = "GCP region shared by raw storage, BigQuery, Artifact Registry and Cloud Run; Scheduler is separate."
  default     = "europe-southwest1"

  validation {
    condition     = can(regex("^[a-z]+-[a-z]+[0-9]+$", var.region))
    error_message = "region must be a regional location, for example europe-southwest1."
  }
}

variable "scheduler_region" {
  type        = string
  description = "Cloud Scheduler region, independent of the Cloud Run and data region; must support Cloud Scheduler."
  default     = "europe-west1"

  validation {
    condition     = can(regex("^[a-z]+-[a-z]+[0-9]+$", var.scheduler_region))
    error_message = "scheduler_region must be a regional location, for example europe-west1; check Cloud Scheduler availability."
  }
}

variable "region_bbox" {
  type = object({
    min_lon = number
    max_lon = number
    min_lat = number
    max_lat = number
  })
  description = "WGS84 bounding box passed to the build application; defaults to the Iberian Peninsula."
  default = {
    min_lon = -9.6
    max_lon = 4.4
    min_lat = 35.8
    max_lat = 43.9
  }

  validation {
    condition = (
      var.region_bbox.min_lon >= -180 && var.region_bbox.max_lon <= 180 &&
      var.region_bbox.min_lat >= -90 && var.region_bbox.max_lat <= 90 &&
      var.region_bbox.min_lon < var.region_bbox.max_lon &&
      var.region_bbox.min_lat < var.region_bbox.max_lat
    )
    error_message = "region_bbox must have min < max, longitudes within [-180, 180] and latitudes within [-90, 90]."
  }
}

variable "min_quarter" {
  type        = string
  description = "Earliest source quarter passed to the application, in YYYY-QN format."
  default     = "2023-Q3"

  validation {
    condition = can(regex("^[0-9]{4}-Q[1-4]$", var.min_quarter)) && try(
      tonumber(substr(var.min_quarter, 0, 4)) * 4 + tonumber(substr(var.min_quarter, 6, 1)) >= 2023 * 4 + 3,
      false
    )
    error_message = "min_quarter must be YYYY-Q1 through YYYY-Q4, no earlier than 2023-Q3."
  }
}

variable "raw_retention_days" {
  type        = number
  description = "Age in days before raw objects are deleted in non-prod; ignored in prod."
  default     = 30

  validation {
    condition     = var.raw_retention_days > 0 && floor(var.raw_retention_days) == var.raw_retention_days
    error_message = "raw_retention_days must be a positive whole number."
  }
}

variable "force_destroy" {
  type        = bool
  description = "Allow deletion of a non-empty raw bucket; keep false in prod."
  default     = false
}

variable "deletion_protection" {
  type        = bool
  description = "Prevent Terraform from deleting BigQuery tables and the Cloud Run job; keep true in prod."
  default     = true
}

variable "delete_contents_on_destroy" {
  type        = bool
  description = "Allow deletion of dataset contents on destroy; keep false in prod."
  default     = false
}

variable "developer_principal" {
  type        = string
  description = "Optional email IAM principal for runtime impersonation; ignored in prod. Validated by the IAM module."
  default     = null
}

variable "image_tag" {
  type        = string
  description = "Unique immutable application image tag; set in environment tfvars. Validated by ingest_job."
}

variable "enable_ingest_job" {
  type        = bool
  description = "Enable Cloud Run and Scheduler only after pushing the configured image; repository is always created."
  default     = false
}

variable "scheduler_paused" {
  type        = bool
  description = "Override schedule pause state; null means active in DEV and paused in TEST/PROD."
  default     = null
}

variable "ingest_schedule" {
  type        = string
  description = "Ingestion cron in Europe/Madrid; validated by ingest_job."
  default     = "0 6 * * 1"
}

variable "task_memory_mib" {
  type        = number
  description = "Memory for the 1-vCPU Cloud Run task; initial streaming estimate, validated by ingest_job."
  default     = 1024
}

variable "task_timeout_seconds" {
  type        = number
  description = "Task attempt deadline in seconds; validated by ingest_job."
  default     = 3600
}

variable "task_max_retries" {
  type        = number
  description = "Additional Cloud Run task attempts, 0 or 1; zero relies on app-level retries and manual recovery."
  default     = 0
}

variable "app_settings" {
  type = object({
    source_bucket        = optional(string, "ookla-open-data")
    source_region        = optional(string, "us-west-2")
    schemas_dir          = optional(string, "/app/schemas")
    source_schema_path   = optional(string)
    native_schema_path   = optional(string)
    maximum_bytes_billed = optional(number, 10737418240)
    mart_zoom            = optional(number, 10)
    minimum_row_percent  = optional(number, 50)
    latency_ceiling_ms   = optional(number, 10000)
  })
  description = "Non-secret application settings. Identity, bucket, dataset, region, bbox, minimum quarter and alert secret come from root inputs/module outputs."
  default     = {}

  validation {
    condition = (
      var.app_settings.maximum_bytes_billed > 0 &&
      floor(var.app_settings.maximum_bytes_billed) == var.app_settings.maximum_bytes_billed &&
      var.app_settings.mart_zoom >= 1 && var.app_settings.mart_zoom <= 16 &&
      floor(var.app_settings.mart_zoom) == var.app_settings.mart_zoom &&
      var.app_settings.minimum_row_percent >= 0 && var.app_settings.minimum_row_percent <= 100 &&
      var.app_settings.latency_ceiling_ms > 0
    )
    error_message = "App budget must be a positive integer, mart_zoom an integer in 1..16, minimum_row_percent in 0..100 and latency_ceiling_ms positive."
  }

  validation {
    condition = (
      can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.app_settings.source_bucket)) &&
      !strcontains(var.app_settings.source_bucket, "..") &&
      can(regex("^[a-z]{2}(-[a-z]+)+-[0-9]$", var.app_settings.source_region)) &&
      length(trimspace(var.app_settings.schemas_dir)) > 0 &&
      alltrue([for path in [var.app_settings.source_schema_path, var.app_settings.native_schema_path] : path == null ? true : length(trimspace(path)) > 0])
    )
    error_message = "source_bucket must be a valid S3 bucket name, source_region an AWS region (e.g. us-west-2), schemas directory non-empty; schema overrides must be null or non-empty paths."
  }
}
