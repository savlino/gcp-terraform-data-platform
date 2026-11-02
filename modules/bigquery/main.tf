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

resource "google_bigquery_dataset" "sql_tests" {
  for_each = var.enable_sql_test_dataset ? { sql_tests = true } : {}

  project                     = var.project_id
  dataset_id                  = "sql_tests"
  friendly_name               = "${var.env}-sql-tests"
  location                    = var.location
  default_table_expiration_ms = 3600000
  delete_contents_on_destroy  = true
  labels                      = local.labels
}

# The dataset and its only grant live together: bootstrap runs before infra and
# cannot grant on a dataset that does not exist yet.
resource "google_bigquery_dataset_iam_member" "sql_tests_data_editor" {
  for_each = var.sql_test_principal == null ? {} : google_bigquery_dataset.sql_tests

  project    = var.project_id
  dataset_id = each.value.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${var.sql_test_principal}"
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
