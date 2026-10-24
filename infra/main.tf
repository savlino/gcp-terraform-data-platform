module "network" {
  source = "../modules/network"

  project_id         = var.project_id
  env                = var.env
  region             = var.region
  subnet_cidr        = var.subnet_cidr != null ? var.subnet_cidr : { dev = "10.10.0.0/24", test = "10.20.0.0/24", prod = "10.30.0.0/24" }[var.env]
  demo_vm_zone       = var.demo_vm_zone != null ? var.demo_vm_zone : "${var.region}-a"
  enable_iap_demo_vm = var.enable_iap_demo_vm
}

module "storage" {
  source = "../modules/storage"

  project_id         = var.project_id
  env                = var.env
  location           = var.region
  force_destroy      = var.force_destroy
  raw_retention_days = var.raw_retention_days
}

module "bigquery" {
  source = "../modules/bigquery"

  project_id                 = var.project_id
  env                        = var.env
  location                   = var.region
  raw_bucket_name            = module.storage.raw_bucket_name
  deletion_protection        = var.deletion_protection
  delete_contents_on_destroy = var.delete_contents_on_destroy
}

module "iam" {
  source = "../modules/iam"

  project_id      = var.project_id
  env             = var.env
  raw_bucket_name = module.storage.raw_bucket_name
  dataset_id      = module.bigquery.dataset_id
}

module "secrets" {
  source = "../modules/secrets"

  project_id       = var.project_id
  env              = var.env
  region           = var.region
  runtime_sa_email = module.iam.service_account_email
}

module "ingest_job" {
  source = "../modules/ingest_job"

  project_id           = var.project_id
  env                  = var.env
  region               = var.region
  scheduler_region     = var.scheduler_region
  runtime_sa_email     = module.iam.service_account_email
  image_tag            = var.image_tag
  enable_ingest_job    = var.enable_ingest_job
  scheduler_paused     = var.scheduler_paused != null ? var.scheduler_paused : var.env != "dev"
  schedule             = var.ingest_schedule
  task_memory_mib      = var.task_memory_mib
  task_timeout_seconds = var.task_timeout_seconds
  task_max_retries     = var.task_max_retries
  deletion_protection  = var.deletion_protection

  application_env = merge({
    PROJECT_ID           = var.project_id
    RAW_BUCKET           = module.storage.raw_bucket_name
    BIGQUERY_DATASET     = module.bigquery.dataset_id
    REGION               = var.region
    REGION_BBOX          = jsonencode(var.region_bbox)
    MIN_QUARTER          = var.min_quarter
    SOURCE_BUCKET        = var.app_settings.source_bucket
    SOURCE_REGION        = var.app_settings.source_region
    SCHEMAS_DIR          = var.app_settings.schemas_dir
    MAXIMUM_BYTES_BILLED = tostring(var.app_settings.maximum_bytes_billed)
    MART_ZOOM            = tostring(var.app_settings.mart_zoom)
    MINIMUM_ROW_PERCENT  = tostring(var.app_settings.minimum_row_percent)
    LATENCY_CEILING_MS   = tostring(var.app_settings.latency_ceiling_ms)
    ALERT_SECRET         = module.secrets.secret_name
    }, {
    for key, value in {
      SOURCE_SCHEMA_PATH = var.app_settings.source_schema_path
      NATIVE_SCHEMA_PATH = var.app_settings.native_schema_path
    } : key => value if value != null
  })

  depends_on = [module.iam, module.bigquery, module.secrets]
}
