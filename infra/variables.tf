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
  description = "Single GCP region shared by the raw bucket and BigQuery dataset."
  default     = "europe-southwest1"

  validation {
    condition     = can(regex("^[a-z]+-[a-z]+[0-9]+$", var.region))
    error_message = "region must be a regional location, for example europe-southwest1."
  }
}

variable "region_bbox" {
  type = object({
    min_lon = number
    max_lon = number
    min_lat = number
    max_lat = number
  })
  description = "WGS84 bounding box for later ingestion; defaults to the Iberian Peninsula."
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
  description = "Earliest source quarter for later ingestion, in YYYY-QN format."
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
  description = "Prevent Terraform from deleting BigQuery tables; keep true in prod."
  default     = true
}

variable "delete_contents_on_destroy" {
  type        = bool
  description = "Allow deletion of dataset contents on destroy; keep false in prod."
  default     = false
}
