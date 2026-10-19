mock_provider "google" {}

variables {
  env                        = "dev"
  project_id                 = "dep-2610-dev"
  location                   = "europe-southwest1"
  raw_bucket_name            = "dep-2610-dev-raw"
  deletion_protection        = false
  delete_contents_on_destroy = true
}

run "native_tables" {
  command = plan
  module {
    source = "../modules/bigquery"
  }

  assert {
    condition = (
      google_bigquery_table.ext_ookla_raw.table_id == "ext_ookla_raw" &&
      google_bigquery_table.ext_ookla_raw.dataset_id == google_bigquery_dataset.network_perf.dataset_id &&
      !google_bigquery_table.ext_ookla_raw.deletion_protection &&
      google_bigquery_table.ext_ookla_raw.labels == google_bigquery_dataset.network_perf.labels &&
      one(google_bigquery_table.ext_ookla_raw.external_data_configuration).autodetect &&
      one(google_bigquery_table.ext_ookla_raw.external_data_configuration).source_format == "PARQUET" &&
      toset(one(google_bigquery_table.ext_ookla_raw.external_data_configuration).source_uris) == toset(["gs://${var.raw_bucket_name}/ookla/*"]) &&
      one(one(google_bigquery_table.ext_ookla_raw.external_data_configuration).hive_partitioning_options).mode == "CUSTOM" &&
      one(one(google_bigquery_table.ext_ookla_raw.external_data_configuration).hive_partitioning_options).source_uri_prefix == "gs://${var.raw_bucket_name}/ookla/{type:STRING}/{year:INTEGER}/{quarter:INTEGER}" &&
      one(one(google_bigquery_table.ext_ookla_raw.external_data_configuration).hive_partitioning_options).require_partition_filter
    )
    error_message = "External table must use the supplied bucket, PARQUET autodetection and typed CUSTOM Hive partitions with a required filter."
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
      avg_d_kbps      = "INTEGER", avg_u_kbps = "INTEGER", avg_lat_ms = "INTEGER",
      avg_lat_down_ms = "FLOAT", avg_lat_up_ms = "FLOAT", tests = "INTEGER", devices = "INTEGER"
    })
    error_message = "Source column types must match docs/source-schema.md."
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
    raw_bucket_name            = "dep-2610-prod-raw"
    deletion_protection        = true
    delete_contents_on_destroy = false
  }

  assert {
    condition = (
      alltrue([for table in google_bigquery_table.native : table.deletion_protection]) &&
      google_bigquery_table.ext_ookla_raw.deletion_protection &&
      !google_bigquery_dataset.network_perf.delete_contents_on_destroy
    )
    error_message = "Protection flags must propagate to PROD dataset and tables."
  }
}
