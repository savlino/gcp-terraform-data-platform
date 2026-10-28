env                = "test"
project_id         = "dep-2610-test"
region             = "europe-southwest1"
scheduler_region   = "europe-west1"
subnet_cidr        = "10.20.0.0/24"
demo_vm_zone       = "europe-southwest1-a"
enable_iap_demo_vm = false
image_tag          = "fee8cd8"
image_digest         = "sha256:e055b4a96daac07f82a8af5e4a5b5edb0592fe7d655f06b574064c3f17db040f"

# The image must exist in the shared ops repository before the job is enabled.
enable_ingest_job = true
scheduler_paused  = true

app_settings = {
  maximum_bytes_billed = 10737418240
  mart_zoom            = 10
  minimum_row_percent  = 80
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
