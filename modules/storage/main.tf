resource "google_storage_bucket" "raw" {
  project                     = var.project_id
  name                        = "${var.project_id}-raw"
  location                    = var.location
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = var.force_destroy

  # Raw files are reproducible; avoid the default seven-day soft-delete cost.
  soft_delete_policy {
    retention_duration_seconds = 0
  }

  dynamic "lifecycle_rule" {
    for_each = var.raw_retention_days == null ? [] : [var.raw_retention_days]
    content {
      action {
        type = "Delete"
      }
      condition {
        age = lifecycle_rule.value
      }
    }
  }

  labels = {
    project    = "gcp-tf-portfolio"
    env        = var.env
    managed_by = "terraform"
  }
}
