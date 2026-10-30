env                = "test"
project_id         = "dep-2610-test"
region             = "europe-southwest1"
scheduler_region   = "europe-west1"
subnet_cidr        = "10.20.0.0/24"
demo_vm_zone       = "europe-southwest1-a"
enable_iap_demo_vm = false
image_tag          = "alerts-20261030-122040"
image_digest       = "sha256:7b8ba04d8cf949274d14af6c6c0140710201181f6c7bbf74b28651a4d9bec2ec"

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
