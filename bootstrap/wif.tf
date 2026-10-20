locals {
  ci_projects  = { for env in var.ci_environments : env => local.projects[env] }
  wif_pool     = "projects/${data.google_project.projects["ops"].number}/locations/global/workloadIdentityPools/gitlab"
  wif_provider = "${local.wif_pool}/providers/gitlab-com"
  wif_audience = "https://iam.googleapis.com/${local.wif_provider}"

  ci_plan_roles = toset([
    "roles/iam.securityReviewer",
    "roles/compute.viewer",
    "roles/storage.bucketViewer",
    "roles/bigquery.metadataViewer",
    "roles/run.viewer",
    "roles/cloudscheduler.viewer",
    "roles/secretmanager.viewer",
    "roles/artifactregistry.reader",
  ])
  ci_apply_roles = toset([
    "roles/storage.admin",
    "roles/bigquery.admin",
    "roles/compute.networkAdmin",
    "roles/compute.securityAdmin",
    "roles/compute.instanceAdmin.v1",
    "roles/iam.serviceAccountAdmin",
    "roles/iam.serviceAccountUser",
    "roles/resourcemanager.projectIamAdmin",
    "roles/run.admin",
    "roles/cloudscheduler.admin",
    "roles/secretmanager.admin",
    "roles/artifactregistry.admin",
  ])
  ci_plan_grants = {
    for pair in setproduct(var.ci_environments, local.ci_plan_roles) :
    "${pair[0]}/${pair[1]}" => { env = pair[0], role = pair[1] }
  }
  ci_apply_grants = {
    for pair in setproduct(var.ci_environments, local.ci_apply_roles) :
    "${pair[0]}/${pair[1]}" => { env = pair[0], role = pair[1] }
  }
  human_grants = var.developer_principal == null ? {} : {
    for pair in setproduct(toset(["dev", "test"]), toset([
      "roles/iam.serviceAccountTokenCreator",
      "roles/iap.tunnelResourceAccessor",
    ])) : "${pair[0]}/${pair[1]}" => { env = pair[0], role = pair[1] }
  }
}

resource "google_project_service" "ops_sts" {
  project            = var.ops_project_id
  service            = "sts.googleapis.com"
  disable_on_destroy = false
}

resource "google_iam_workload_identity_pool" "gitlab" {
  project                   = var.ops_project_id
  workload_identity_pool_id = "gitlab"
  display_name              = "GitLab CI"
  description               = "Keyless CI identities for the configured GitLab project."

  depends_on = [google_project_service.apis, google_project_service.ops_sts]
}

resource "google_iam_workload_identity_pool_provider" "gitlab_com" {
  project                            = var.ops_project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.gitlab.workload_identity_pool_id
  workload_identity_pool_provider_id = "gitlab-com"
  display_name                       = "GitLab.com"
  attribute_mapping = {
    "google.subject"          = "assertion.sub"
    "attribute.project_path"  = "assertion.project_path"
    "attribute.ref"           = "assertion.ref"
    "attribute.ref_protected" = "assertion.ref_protected"
  }
  attribute_condition = "assertion.project_path == ${jsonencode(var.gitlab_project_path)}"

  oidc {
    issuer_uri        = "https://gitlab.com"
    allowed_audiences = [local.wif_audience]
  }
}

resource "google_service_account" "ci_plan" {
  for_each = local.ci_projects

  project      = var.ops_project_id
  account_id   = "ci-plan-${each.key}"
  display_name = "${each.key}-ci-plan"
  description  = "GitLab read/refresh and Terraform state identity for ${each.key} plans."

  depends_on = [google_project_service.apis]
}

resource "google_service_account" "ci_apply" {
  for_each = local.ci_projects

  project      = var.ops_project_id
  account_id   = "ci-apply-${each.key}"
  display_name = "${each.key}-ci-apply"
  description  = "GitLab protected-ref Terraform deployment identity for ${each.key}; not used by jobs."

  depends_on = [google_project_service.apis]
}

resource "google_project_iam_member" "ci_plan" {
  for_each = local.ci_plan_grants

  project = local.ci_projects[each.value.env]
  role    = each.value.role
  member  = "serviceAccount:${google_service_account.ci_plan[each.value.env].email}"
}

resource "google_project_iam_member" "ci_apply" {
  for_each = local.ci_apply_grants

  project = local.ci_projects[each.value.env]
  role    = each.value.role
  member  = "serviceAccount:${google_service_account.ci_apply[each.value.env].email}"
}

resource "google_storage_bucket_iam_member" "ci_plan_state" {
  for_each = local.ci_projects

  bucket = google_storage_bucket.terraform_state.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.ci_plan[each.key].email}"
}

resource "google_storage_bucket_iam_member" "ci_apply_state" {
  for_each = local.ci_projects

  bucket = google_storage_bucket.terraform_state.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.ci_apply[each.key].email}"
}

resource "google_service_account_iam_member" "plan_federation" {
  for_each = local.ci_projects

  service_account_id = google_service_account.ci_plan[each.key].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${local.wif_pool}/attribute.project_path/${var.gitlab_project_path}"

  depends_on = [google_iam_workload_identity_pool_provider.gitlab_com]
}

resource "google_service_account_iam_member" "apply_federation" {
  for_each = local.ci_projects

  service_account_id = google_service_account.ci_apply[each.key].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${local.wif_pool}/attribute.ref_protected/true"

  depends_on = [google_iam_workload_identity_pool_provider.gitlab_com]
}

# Human access is deliberately bootstrap-owned and project-wide in DEV/TEST.
# This is broader than the former runtime/VM grants; never add PROD here.
resource "google_project_iam_member" "developer" {
  for_each = local.human_grants

  project = local.projects[each.value.env]
  role    = each.value.role
  member  = var.developer_principal
}
