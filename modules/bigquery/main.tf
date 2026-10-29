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
