mock_provider "google-beta" {}

mock_provider "google" {
  mock_data "google_project" {
    override_during = plan
    defaults = {
      number = "123456789012"
    }
  }
  mock_resource "google_service_account" {
    override_during = plan
    defaults = {
      name  = "projects/dep-2610-ops/serviceAccounts/ci-mock@dep-2610-ops.iam.gserviceaccount.com"
      email = "ci-mock@dep-2610-ops.iam.gserviceaccount.com"
    }
  }
}

override_resource {
  target          = google_service_account.ci_plan["dev"]
  override_during = plan
  values = {
    name  = "projects/dep-2610-ops/serviceAccounts/ci-plan-dev@dep-2610-ops.iam.gserviceaccount.com"
    email = "ci-plan-dev@dep-2610-ops.iam.gserviceaccount.com"
  }
}

override_resource {
  target          = google_service_account.ci_apply["dev"]
  override_during = plan
  values = {
    name  = "projects/dep-2610-ops/serviceAccounts/ci-apply-dev@dep-2610-ops.iam.gserviceaccount.com"
    email = "ci-apply-dev@dep-2610-ops.iam.gserviceaccount.com"
  }
}

override_resource {
  target          = google_service_account.ci_sqltest_dev["dev"]
  override_during = plan
  values = {
    name  = "projects/dep-2610-ops/serviceAccounts/ci-sqltest-dev@dep-2610-ops.iam.gserviceaccount.com"
    email = "ci-sqltest-dev@dep-2610-ops.iam.gserviceaccount.com"
  }
}

variables {
  billing_account_id  = "000000-000000-000000"
  gitlab_project_path = "savlino/gcp-terraform-data-platform"
}

run "dev_first_federation" {
  command = plan

  assert {
    condition = (
      google_iam_workload_identity_pool.gitlab.project == var.ops_project_id &&
      google_iam_workload_identity_pool.gitlab.workload_identity_pool_id == "gitlab" &&
      google_iam_workload_identity_pool_provider.gitlab_com.workload_identity_pool_provider_id == "gitlab-com" &&
      one(google_iam_workload_identity_pool_provider.gitlab_com.oidc).issuer_uri == "https://gitlab.com" &&
      one(google_iam_workload_identity_pool_provider.gitlab_com.oidc).allowed_audiences == tolist([
        "https://iam.googleapis.com/projects/123456789012/locations/global/workloadIdentityPools/gitlab/providers/gitlab-com"
      ]) &&
      google_iam_workload_identity_pool_provider.gitlab_com.attribute_condition == "assertion.project_path == \"savlino/gcp-terraform-data-platform\"" &&
      google_iam_workload_identity_pool_provider.gitlab_com.attribute_mapping == tomap({
        "google.subject"          = "assertion.sub"
        "attribute.project_path"  = "assertion.project_path"
        "attribute.ref"           = "assertion.ref"
        "attribute.ref_protected" = "assertion.ref_protected"
      })
    )
    error_message = "GitLab issuer, exact audience, project trust restriction and claim mappings must match."
  }

  assert {
    condition = (
      toset(keys(google_service_account.ci_plan)) == toset(["dev"]) &&
      toset(keys(google_service_account.ci_apply)) == toset(["dev"]) &&
      length(google_service_account.ci_sqltest_dev) == 0 &&
      length(google_project_iam_member.ci_sqltest_dev_job_user) == 0 &&
      google_service_account.ci_plan["dev"].project == var.ops_project_id &&
      google_service_account.ci_plan["dev"].account_id == "ci-plan-dev" &&
      google_service_account.ci_apply["dev"].account_id == "ci-apply-dev" &&
      google_service_account.ci_plan["dev"].email != google_service_account.ci_apply["dev"].email &&
      google_service_account_iam_member.plan_federation["dev"].service_account_id == google_service_account.ci_plan["dev"].name &&
      google_service_account_iam_member.apply_federation["dev"].service_account_id == google_service_account.ci_apply["dev"].name &&
      google_service_account_iam_member.plan_federation["dev"].role == "roles/iam.workloadIdentityUser" &&
      google_service_account_iam_member.plan_federation["dev"].member == "principalSet://iam.googleapis.com/projects/123456789012/locations/global/workloadIdentityPools/gitlab/attribute.project_path/savlino/gcp-terraform-data-platform" &&
      google_service_account_iam_member.apply_federation["dev"].role == "roles/iam.workloadIdentityUser" &&
      google_service_account_iam_member.apply_federation["dev"].member == "principalSet://iam.googleapis.com/projects/123456789012/locations/global/workloadIdentityPools/gitlab/attribute.ref_protected/true" &&
      length(google_project_iam_member.developer) == 0
    )
    error_message = "Default DEV accounts must be separate; apply trust requires a protected ref and humans default off."
  }

  assert {
    condition = (
      google_storage_bucket_iam_member.ci_plan_state["dev"].bucket == google_storage_bucket.terraform_state.name &&
      google_storage_bucket_iam_member.ci_plan_state["dev"].role == "roles/storage.objectUser" &&
      google_storage_bucket_iam_member.ci_plan_state["dev"].member == "serviceAccount:${google_service_account.ci_plan["dev"].email}" &&
      google_storage_bucket_iam_member.ci_apply_state["dev"].role == "roles/storage.objectUser" &&
      google_storage_bucket_iam_member.ci_apply_state["dev"].member == "serviceAccount:${google_service_account.ci_apply["dev"].email}" &&
      google_project_service.ops_sts.service == "sts.googleapis.com"
    )
    error_message = "Both CI accounts require state/lock object access; federation requires STS activation."
  }

  assert {
    condition = (
      length(google_project_iam_member.ci_plan) == 7 &&
      length(google_project_iam_member.ci_apply) == 11 &&
      toset([for grant in google_project_iam_member.ci_apply : grant.role]) == toset([
        "roles/storage.admin", "roles/bigquery.admin", "roles/compute.networkAdmin",
        "roles/compute.securityAdmin", "roles/compute.instanceAdmin.v1",
        "roles/iam.serviceAccountAdmin", "roles/iam.serviceAccountUser",
        "roles/resourcemanager.projectIamAdmin", "roles/run.admin",
        "roles/cloudscheduler.admin", "roles/secretmanager.admin",
      ]) &&
      alltrue([
        for grant in google_project_iam_member.ci_plan :
        grant.project == var.dev_project_id &&
        grant.member == "serviceAccount:${google_service_account.ci_plan["dev"].email}" &&
        contains([
          "roles/iam.securityReviewer", "roles/compute.viewer", "roles/storage.bucketViewer",
          "roles/bigquery.metadataViewer", "roles/run.viewer", "roles/cloudscheduler.viewer",
          "roles/secretmanager.viewer",
        ], grant.role)
      ]) &&
      alltrue([
        for grant in google_project_iam_member.ci_apply :
        grant.project == var.dev_project_id &&
        grant.member == "serviceAccount:${google_service_account.ci_apply["dev"].email}" &&
        !contains(["roles/owner", "roles/editor", "roles/viewer", "roles/iam.serviceAccountTokenCreator"], grant.role)
      ])
    )
    error_message = "Plan has only the documented metadata roles on DEV; apply uses predefined non-basic deployment roles."
  }
}

run "dev_sql_test_identity_is_job_user_only" {
  command = plan
  variables {
    enable_sql_test_access = true
  }

  assert {
    condition = (
      google_service_account.ci_sqltest_dev["dev"].project == var.ops_project_id &&
      google_service_account.ci_sqltest_dev["dev"].account_id == "ci-sqltest-dev" &&
      google_service_account_iam_member.sqltest_federation["dev"].service_account_id == google_service_account.ci_sqltest_dev["dev"].name &&
      google_service_account_iam_member.sqltest_federation["dev"].role == "roles/iam.workloadIdentityUser" &&
      google_service_account_iam_member.sqltest_federation["dev"].member == "principalSet://iam.googleapis.com/${local.wif_pool}/attribute.project_path/${var.gitlab_project_path}"
    )
    error_message = "The DEV SQL test account must be in ops and federate only the configured GitLab project path."
  }

  assert {
    condition = (
      google_project_iam_member.ci_sqltest_dev_job_user["dev"].project == var.dev_project_id &&
      google_project_iam_member.ci_sqltest_dev_job_user["dev"].role == "roles/bigquery.jobUser" &&
      google_project_iam_member.ci_sqltest_dev_job_user["dev"].member == "serviceAccount:${google_service_account.ci_sqltest_dev["dev"].email}"
    )
    error_message = "Bootstrap grants the SQL test account only BigQuery job submission in DEV; dataset access belongs to infra."
  }

  assert {
    condition = (
      length([
        for grant in concat(
          values(google_project_iam_member.ci_plan),
          values(google_project_iam_member.ci_apply),
          values(google_project_iam_member.developer),
          values(google_project_iam_member.ci_sqltest_dev_job_user)
        ) : grant.role if grant.member == "serviceAccount:${google_service_account.ci_sqltest_dev["dev"].email}"
      ]) == 1 &&
      toset([
        for grant in concat(
          values(google_project_iam_member.ci_plan),
          values(google_project_iam_member.ci_apply),
          values(google_project_iam_member.developer),
          values(google_project_iam_member.ci_sqltest_dev_job_user)
        ) : grant.role if grant.member == "serviceAccount:${google_service_account.ci_sqltest_dev["dev"].email}"
      ]) == toset(["roles/bigquery.jobUser"]) &&
      alltrue([
        for grant in concat(
          values(google_storage_bucket_iam_member.ci_plan_state),
          values(google_storage_bucket_iam_member.ci_apply_state)
        ) : grant.member != "serviceAccount:${google_service_account.ci_sqltest_dev["dev"].email}"
      ])
    )
    error_message = "The SQL test account must have no other project-level role or bucket/repository grant."
  }
}

run "human_grants_dev_test_only" {
  command = plan
  variables {
    developer_principal = "user:developer@example.com"
    ci_environments     = ["dev", "test", "prod"]
  }

  assert {
    condition = (
      length(google_project_iam_member.developer) == 4 &&
      toset([for grant in google_project_iam_member.developer : grant.project]) == toset([var.dev_project_id, var.test_project_id]) &&
      toset([for grant in google_project_iam_member.developer : grant.role]) == toset([
        "roles/iam.serviceAccountTokenCreator", "roles/iap.tunnelResourceAccessor"
      ]) &&
      alltrue([for grant in google_project_iam_member.developer : grant.member == var.developer_principal]) &&
      toset(keys(google_service_account.ci_plan)) == toset(["dev", "test", "prod"]) &&
      alltrue([
        for env, project in local.ci_projects :
        google_service_account.ci_plan[env].account_id == "ci-plan-${env}" &&
        google_service_account.ci_apply[env].account_id == "ci-apply-${env}" &&
        google_project_iam_member.ci_plan["${env}/roles/compute.viewer"].project == project &&
        google_project_iam_member.ci_apply["${env}/roles/compute.networkAdmin"].project == project
      ])
    )
    error_message = "Human grants are exactly DEV/TEST project grants even when PROD CI exists."
  }
}

run "human_test_grants_independent_of_dev_ci" {
  command = plan
  variables {
    developer_principal = "group:developers@example.com"
  }
  assert {
    condition = (
      length(google_project_iam_member.developer) == 4 &&
      google_project_iam_member.developer["test/roles/iap.tunnelResourceAccessor"].project == var.test_project_id &&
      toset(keys(google_service_account.ci_plan)) == toset(["dev"])
    )
    error_message = "Human TEST access must not depend on creating TEST CI identities."
  }
}

run "reject_project_url" {
  command = plan
  variables {
    gitlab_project_path = "https://gitlab.com/savlino/gcp-terraform-data-platform"
  }
  expect_failures = [var.gitlab_project_path]
}

run "reject_unknown_environment" {
  command = plan
  variables {
    ci_environments = ["staging"]
  }
  expect_failures = [var.ci_environments]
}

run "reject_empty_environments" {
  command = plan
  variables {
    ci_environments = []
  }
  expect_failures = [var.ci_environments]
}

run "reject_public_human" {
  command = plan
  variables {
    developer_principal = "allUsers"
  }
  expect_failures = [var.developer_principal]
}

run "reject_workload_as_human" {
  command = plan
  variables {
    developer_principal = "serviceAccount:workload@example.iam.gserviceaccount.com"
  }
  expect_failures = [var.developer_principal]
}

run "reject_prod_human_environment" {
  command = plan
  variables {
    human_access_environments = ["dev", "prod"]
  }
  expect_failures = [var.human_access_environments]
}
