locals {
  labels = {
    project    = "gcp-tf-portfolio"
    env        = var.env
    managed_by = "terraform"
  }
  tables = {
    ookla_tiles                = { partitioned = true, clustering = ["network_type", "quadkey"] }
    load_log                   = { partitioned = false, clustering = ["network_type", "quarter_start"] }
    mart_quarterly_performance = { partitioned = true, clustering = ["network_type", "quadkey_prefix"] }
  }
}

resource "google_bigquery_dataset" "network_perf" {
  project                    = var.project_id
  dataset_id                 = "network_perf"
  friendly_name              = "${var.env}-network-performance"
  location                   = var.location
  delete_contents_on_destroy = var.delete_contents_on_destroy
  labels                     = local.labels
}

resource "google_bigquery_table" "native" {
  for_each = local.tables

  project                  = var.project_id
  dataset_id               = google_bigquery_dataset.network_perf.dataset_id
  table_id                 = each.key
  schema                   = file("${path.module}/../../schemas/${each.key}.json")
  deletion_protection      = var.deletion_protection
  require_partition_filter = each.value.partitioned
  clustering               = each.value.clustering
  labels                   = local.labels

  dynamic "time_partitioning" {
    for_each = each.value.partitioned ? [true] : []
    content {
      type  = "DAY"
      field = "quarter_start"
    }
  }
}

resource "google_bigquery_table" "ext_ookla_raw" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.network_perf.dataset_id
  table_id   = "ext_ookla_raw"
  schema     = file("${path.module}/../../schemas/ookla_raw.json")
  # BigQuery appends the Hive partition keys (type, year, quarter) to the stored
  # schema; without this every plan sees them as dropped columns and forces replacement.
  ignore_auto_generated_schema = true
  deletion_protection          = var.deletion_protection
  labels                       = local.labels

  external_data_configuration {
    autodetect    = false
    source_format = "PARQUET"
    source_uris   = ["gs://${var.raw_bucket_name}/ookla/*"]

    hive_partitioning_options {
      mode                     = "CUSTOM"
      source_uri_prefix        = "gs://${var.raw_bucket_name}/ookla/{type:STRING}/{year:INTEGER}/{quarter:INTEGER}"
      require_partition_filter = true
    }
  }
}
