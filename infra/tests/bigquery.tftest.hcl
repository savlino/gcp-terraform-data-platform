mock_provider "google" {}

variables {
  env                        = "dev"
  project_id                 = "dep-2610-dev"
  location                   = "europe-southwest1"
  deletion_protection        = false
  delete_contents_on_destroy = true
}

run "native_tables" {
  command = plan
  module {
    source = "../modules/bigquery"
  }

  assert {
    condition     = toset(keys(output.table_ids)) == toset(["ookla_tiles", "load_log", "mart_quarterly_performance"])
    error_message = "Only the three native tables belong in Terraform; raw stages are application-owned."
  }

  assert {
    condition = (
      google_bigquery_dataset.network_perf.location == "europe-southwest1" &&
      google_bigquery_dataset.network_perf.default_table_expiration_ms == null &&
      google_bigquery_dataset.network_perf.delete_contents_on_destroy
    )
    error_message = "Dataset must use the supplied region, no table expiration and the DEV destroy flag."
  }

  assert {
    condition = alltrue([
      for name in ["ookla_tiles", "mart_quarterly_performance"] :
      google_bigquery_table.native[name].require_partition_filter &&
      one(google_bigquery_table.native[name].time_partitioning).field == "quarter_start" &&
      one(google_bigquery_table.native[name].time_partitioning).type == "DAY"
    ])
    error_message = "Curated and mart tables must partition on quarter_start and require a filter."
  }

  assert {
    condition = (
      google_bigquery_table.native["ookla_tiles"].clustering == tolist(["network_type", "quadkey"]) &&
      !google_bigquery_table.native["load_log"].require_partition_filter &&
      length(google_bigquery_table.native["load_log"].time_partitioning) == 0 &&
      alltrue([for table in google_bigquery_table.native : !table.deletion_protection])
    )
    error_message = "Clustering, watermark lookup and DEV deletion flags must match the contract."
  }

  assert {
    condition = alltrue([
      for table in google_bigquery_table.native : table.labels == tomap({
        project = "gcp-tf-portfolio", env = "dev", managed_by = "terraform"
      })
      ]) && google_bigquery_dataset.network_perf.labels == tomap({
      project = "gcp-tf-portfolio", env = "dev", managed_by = "terraform"
    })
    error_message = "Dataset and tables must carry the required labels."
  }

  assert {
    condition = tomap({
      for field in jsondecode(google_bigquery_table.native["ookla_tiles"].schema) :
      field.name => field.type
      if !contains(["network_type", "quarter_start", "loaded_at", "avg_d_mbps", "avg_u_mbps"], field.name)
      }) == tomap({
      quadkey         = "STRING", tile = "STRING", tile_x = "FLOAT", tile_y = "FLOAT",
      avg_d_kbps      = "INTEGER", avg_u_kbps = "INTEGER", avg_lat_ms = "FLOAT",
      avg_lat_down_ms = "FLOAT", avg_lat_up_ms = "FLOAT", tests = "INTEGER", devices = "INTEGER"
    })
    error_message = "Curated types must normalize all latency columns to FLOAT64."
  }
}

run "protected_tables" {
  command = plan
  module {
    source = "../modules/bigquery"
  }
  variables {
    env                        = "prod"
    project_id                 = "dep-2610-prod"
    deletion_protection        = true
    delete_contents_on_destroy = false
  }

  assert {
    condition = (
      alltrue([for table in google_bigquery_table.native : table.deletion_protection]) &&
      !google_bigquery_dataset.network_perf.delete_contents_on_destroy
    )
    error_message = "Protection flags must propagate to PROD dataset and tables."
  }
}
