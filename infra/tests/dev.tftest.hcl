mock_provider "google" {}

variables {
  env                        = "dev"
  project_id                 = "dep-2610-dev"
  region                     = "europe-southwest1"
  image_tag                  = "test-v1"
  force_destroy              = true
  deletion_protection        = false
  delete_contents_on_destroy = true
}

run "dev_plan" {
  command = plan

  assert {
    condition     = output.network_name == "dev-private" && output.subnet_cidr == "10.10.0.0/24" && output.iap_demo_vm_name == null
    error_message = "DEV must default to its private subnet without a billable demo."
  }

  assert {
    condition     = output.raw_bucket_name == "dep-2610-dev-raw" && output.dataset_id == "network_perf"
    error_message = "DEV resource names must be stable."
  }

  assert {
    condition     = toset(keys(output.table_ids)) == toset(["ookla_tiles", "load_log", "mart_quarterly_performance", "ext_ookla_raw"])
    error_message = "The dataset must expose three native tables and the raw external table."
  }
}

run "test_subnet_default" {
  command = plan
  variables {
    env = "test"
  }
  assert {
    condition     = output.subnet_cidr == "10.20.0.0/24" && output.iap_demo_vm_name == null
    error_message = "TEST must default to its own CIDR with no VM."
  }
}

run "prod_subnet_default" {
  command = plan
  variables {
    env = "prod"
  }
  assert {
    condition     = output.subnet_cidr == "10.30.0.0/24" && output.iap_demo_vm_name == null
    error_message = "PROD must default to its own CIDR with no VM."
  }
}

run "subnet_override" {
  command = plan
  variables {
    subnet_cidr = "10.99.0.0/24"
  }
  assert {
    condition     = output.subnet_cidr == "10.99.0.0/24"
    error_message = "An explicit CIDR must override the environment default."
  }
}

run "reversed_longitude" {
  command = plan
  variables {
    region_bbox = { min_lon = 10, max_lon = -10, min_lat = 35, max_lat = 44 }
  }
  expect_failures = [var.region_bbox]
}

run "reversed_latitude" {
  command = plan
  variables {
    region_bbox = { min_lon = -10, max_lon = 10, min_lat = 44, max_lat = 35 }
  }
  expect_failures = [var.region_bbox]
}

run "out_of_range_coordinates" {
  command = plan
  variables {
    region_bbox = { min_lon = -181, max_lon = 10, min_lat = 35, max_lat = 91 }
  }
  expect_failures = [var.region_bbox]
}

run "zero_area" {
  command = plan
  variables {
    region_bbox = { min_lon = -10, max_lon = -10, min_lat = 35, max_lat = 44 }
  }
  expect_failures = [var.region_bbox]
}

run "quarter_before_tile_coordinates" {
  command = plan
  variables {
    min_quarter = "2023-Q2"
  }
  expect_failures = [var.min_quarter]
}

run "malformed_quarter" {
  command = plan
  variables {
    min_quarter = "2024-Q5"
  }
  expect_failures = [var.min_quarter]
}

run "non_numeric_quarter" {
  command = plan
  variables {
    min_quarter = "invalid"
  }
  expect_failures = [var.min_quarter]
}

run "invalid_retention" {
  command = plan
  variables {
    raw_retention_days = 0
  }
  expect_failures = [var.raw_retention_days]
}

run "invalid_scheduler_region" {
  command = plan
  variables {
    scheduler_region = "EU"
  }
  expect_failures = [var.scheduler_region]
}
