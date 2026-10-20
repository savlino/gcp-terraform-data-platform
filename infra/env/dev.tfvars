env              = "dev"
project_id       = "dep-2610-dev"
region           = "europe-southwest1"
scheduler_region = "europe-west1"
image_tag        = "v1"

# Enable only after pushing image_tag to the Terraform-created repository.
enable_ingest_job = false
scheduler_paused  = false

app_settings = {
  maximum_bytes_billed = 10737418240
  mart_zoom            = 10
  minimum_row_percent  = 50
  latency_ceiling_ms   = 10000
}

region_bbox = {
  min_lon = -9.6
  max_lon = 4.4
  min_lat = 35.8
  max_lat = 43.9
}
min_quarter = "2023-Q3"

raw_retention_days         = 30
force_destroy              = true
deletion_protection        = false
delete_contents_on_destroy = true
