env                     = "dev"
project_id              = "dep-2610-dev"
enable_sql_test_dataset = true
sql_test_principal      = "serviceAccount:ci-sqltest-dev@dep-2610-ops.iam.gserviceaccount.com"
region                  = "europe-southwest1"
scheduler_region        = "europe-west1"
subnet_cidr             = "10.10.0.0/24"
demo_vm_zone            = "europe-southwest1-a"
enable_iap_demo_vm      = false
image_tag               = "986c22e"
image_digest            = "sha256:30ab83ced1d9f73caa279d7ab15806b0b51f47e6fd8150291730431f91721ad8"

# Enable only after the image_tag exists in the shared ops repository (image_repository default).
enable_ingest_job = true
scheduler_paused  = true

# Full DEV DQ baseline: docs/data-quality.md (2026-10-20).
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
