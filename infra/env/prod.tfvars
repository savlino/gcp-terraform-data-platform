env                = "prod"
project_id         = "dep-2610-prod"
region             = "europe-southwest1"
scheduler_region   = "europe-west1"
subnet_cidr        = "10.30.0.0/24"
demo_vm_zone       = "europe-southwest1-a"
enable_iap_demo_vm = false
image_tag          = "83ac391"
image_repository_url = "europe-southwest1-docker.pkg.dev/dep-2610-ops/ops-ingest"
image_digest         = "sha256:bcc8d2f28bc71f813cf5cff93dc5318621664052a354e64a478f872a6fbc6902"

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

raw_retention_days         = null
force_destroy              = false
deletion_protection        = true
delete_contents_on_destroy = false
