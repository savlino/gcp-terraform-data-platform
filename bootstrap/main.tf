locals {
  projects = {
    ops  = var.ops_project_id
    dev  = var.dev_project_id
    test = var.test_project_id
    prod = var.prod_project_id
  }

  shared_apis = toset([
    "cloudresourcemanager.googleapis.com",
    "serviceusage.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
  ])

  environment_apis = toset([
    "compute.googleapis.com",
    "storage.googleapis.com",
    "bigquery.googleapis.com",
    "run.googleapis.com",
    "cloudscheduler.googleapis.com",
    "iap.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "logging.googleapis.com",
  ])

  project_apis = {
    for project_key, project_id in local.projects :
    project_id => (
      project_key == "ops"
      ? setunion(local.shared_apis, toset(["artifactregistry.googleapis.com"]))
      : setunion(local.shared_apis, local.environment_apis)
    )
  }

  api_services = {
    for service in flatten([
      for project_id, apis in local.project_apis : [
        for api in apis : {
          key        = "${project_id}/${api}"
          project_id = project_id
          api        = api
        }
      ]
    ]) : service.key => service
  }
}

data "google_project" "projects" {
  for_each   = local.projects
  project_id = each.value
}

# Common management APIs support project lookup, API activation, IAM, and
# keyless service-account impersonation. Environment APIs cover the platform's
# planned network, storage, BigQuery, Cloud Run, scheduler, secrets, and logs.
resource "google_project_service" "apis" {
  for_each = local.api_services

  project            = each.value.project_id
  service            = each.value.api
  disable_on_destroy = false
}

resource "google_project_service" "ops_storage" {
  project            = var.ops_project_id
  service            = "storage.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "ops_billing_budgets" {
  project            = var.ops_project_id
  service            = "billingbudgets.googleapis.com"
  disable_on_destroy = false
}

resource "google_storage_bucket" "terraform_state" {
  project                     = var.ops_project_id
  name                        = var.state_bucket_name
  location                    = var.state_bucket_location
  force_destroy               = var.state_bucket_force_destroy
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }

  lifecycle {
    prevent_destroy = true
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }

    condition {
      num_newer_versions = 5
    }
  }

  labels = {
    project    = "gcp-tf-portfolio"
    env        = "ops"
    managed_by = "terraform"
  }

  depends_on = [google_project_service.ops_storage]
}

resource "google_billing_budget" "portfolio" {
  billing_account = var.billing_account_id
  display_name    = "gcp-terraform-data-platform monthly budget"

  budget_filter {
    projects = [
      for project in data.google_project.projects :
      "projects/${project.number}"
    ]
    calendar_period = "MONTH"
  }

  amount {
    specified_amount {
      currency_code = var.budget_currency_code
      units         = tostring(var.budget_amount)
    }
  }

  threshold_rules {
    threshold_percent = 0.5
    spend_basis       = "CURRENT_SPEND"
  }

  threshold_rules {
    threshold_percent = 0.9
    spend_basis       = "CURRENT_SPEND"
  }

  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "CURRENT_SPEND"
  }

  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "FORECASTED_SPEND"
  }

  depends_on = [google_project_service.ops_billing_budgets]
}
