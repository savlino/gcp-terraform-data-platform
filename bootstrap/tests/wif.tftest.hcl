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

  # Keep deployment TF_VAR_* values out of the access-control scenarios.
  developer_principal       = null
  human_access_environments = ["dev", "test"]
  enable_sql_test_access    = false
}

run "all_environment_federation" {
  command = plan

  assert {
    condition = (
      toset(keys(local.ci_environments)) == toset(["dev", "test", "prod"]) &&
      !contains(keys(local.ci_environments), "ops")
    )
    error_message = "CI environments must be exactly dev, test and prod, never ops."
  }

  assert {
    condition = (
      alltrue([
        for instances in [
          google_storage_bucket_iam_member.ci_plan_state,
          google_storage_bucket_iam_member.ci_apply_state,
          google_service_account_iam_member.plan_federation,
          google_service_account_iam_member.apply_federation
        ] : toset(keys(instances)) == toset(["dev", "test", "prod"])
      ]) &&
      toset(keys(google_project_iam_member.ci_plan)) == toset([
        for pair in setproduct(["dev", "test", "prod"], local.ci_plan_roles) :
        "${pair[0]}/${pair[1]}"
      ]) &&
      toset(keys(google_project_iam_member.ci_apply)) == toset([
        for pair in setproduct(["dev", "test", "prod"], local.ci_apply_roles) :
        "${pair[0]}/${pair[1]}"
      ]) &&
      alltrue([
        for env in ["dev", "test", "prod"] :
        google_service_account.ci_plan[env].account_id == "ci-plan-${env}" &&
        google_service_account.ci_apply[env].account_id == "ci-apply-${env}"
      ])
    )
    error_message = "CI identities and bindings must preserve environment and <env>/<role> state keys and account names."
  }

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
      toset(keys(google_service_account.ci_plan)) == toset(["dev", "test", "prod"]) &&
      toset(keys(google_service_account.ci_apply)) == toset(["dev", "test", "prod"]) &&
      length(google_service_account.ci_sqltest_dev) == 0 &&
      length(google_project_iam_member.ci_sqltest_dev_job_user) == 0 &&
      output.sqltest_sa_email == null &&
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
    error_message = "CI accounts must cover all environments; apply trust requires a protected ref and explicit null disables human grants."
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
      length(google_project_iam_member.ci_plan) == 21 &&
      length(google_project_iam_member.ci_apply) == 33 &&
      toset([for grant in google_project_iam_member.ci_apply : grant.role]) == toset([
        "roles/storage.admin", "roles/bigquery.admin", "roles/compute.networkAdmin",
        "roles/compute.securityAdmin", "roles/compute.instanceAdmin.v1",
        "roles/iam.serviceAccountAdmin", "roles/iam.serviceAccountUser",
        "roles/resourcemanager.projectIamAdmin", "roles/run.admin",
        "roles/cloudscheduler.admin", "roles/secretmanager.admin",
      ]) &&
      alltrue([
        for key, grant in google_project_iam_member.ci_plan :
        grant.project == local.ci_environments[split("/", key)[0]] &&
        grant.member == "serviceAccount:${google_service_account.ci_plan[split("/", key)[0]].email}" &&
        contains([
          "roles/iam.securityReviewer", "roles/compute.viewer", "roles/storage.bucketViewer",
          "roles/bigquery.metadataViewer", "roles/run.viewer", "roles/cloudscheduler.viewer",
          "roles/secretmanager.viewer",
        ], grant.role)
      ]) &&
      alltrue([
        for key, grant in google_project_iam_member.ci_apply :
        grant.project == local.ci_environments[split("/", key)[0]] &&
        grant.member == "serviceAccount:${google_service_account.ci_apply[split("/", key)[0]].email}" &&
        !contains(["roles/owner", "roles/editor", "roles/viewer", "roles/iam.serviceAccountTokenCreator"], grant.role)
      ])
    )
    error_message = "Plan has only the documented metadata roles per environment; apply uses predefined non-basic deployment roles."
  }
}

run "dev_sql_test_identity_is_job_user_only" {
  command = plan
  variables {
    enable_sql_test_access = true
  }

  # Expected values are literals derived from the mocks above (ops project number
  # 123456789012, the ci_sqltest_dev override) and the test variables, never from
  # locals or variable defaults of the code under test.

  assert {
    condition = (
      length(google_service_account.ci_sqltest_dev) == 1 &&
      toset(keys(google_service_account.ci_sqltest_dev)) == toset(["dev"]) &&
      google_service_account.ci_sqltest_dev["dev"].project == "dep-2610-ops" &&
      google_service_account.ci_sqltest_dev["dev"].account_id == "ci-sqltest-dev" &&
      strcontains(google_service_account.ci_sqltest_dev["dev"].display_name, "runs SQL tests in DEV") &&
      strcontains(google_service_account.ci_sqltest_dev["dev"].description, "runs SQL integration tests in DEV") &&
      output.sqltest_sa_email == "ci-sqltest-dev@dep-2610-ops.iam.gserviceaccount.com"
    )
    error_message = "Expected exactly one ci_sqltest_dev SA keyed \"dev\" in dep-2610-ops with account_id ci-sqltest-dev, display_name containing \"runs SQL tests in DEV\", description containing \"runs SQL integration tests in DEV\", and output sqltest_sa_email = ci-sqltest-dev@dep-2610-ops.iam.gserviceaccount.com."
  }

  assert {
    condition = (
      length(google_service_account_iam_member.sqltest_federation) == 1 &&
      toset(keys(google_service_account_iam_member.sqltest_federation)) == toset(["dev"]) &&
      google_service_account_iam_member.sqltest_federation["dev"].service_account_id == "projects/dep-2610-ops/serviceAccounts/ci-sqltest-dev@dep-2610-ops.iam.gserviceaccount.com" &&
      google_service_account_iam_member.sqltest_federation["dev"].role == "roles/iam.workloadIdentityUser" &&
      google_service_account_iam_member.sqltest_federation["dev"].member == "principalSet://iam.googleapis.com/projects/123456789012/locations/global/workloadIdentityPools/gitlab/attribute.project_path/savlino/gcp-terraform-data-platform"
    )
    error_message = "Expected exactly one sqltest_federation binding keyed \"dev\" on the ci-sqltest-dev SA: role roles/iam.workloadIdentityUser, member principalSet on pool projects/123456789012/.../gitlab with attribute.project_path/savlino/gcp-terraform-data-platform."
  }

  assert {
    condition = (
      length(google_project_iam_member.ci_sqltest_dev_job_user) == 1 &&
      toset(keys(google_project_iam_member.ci_sqltest_dev_job_user)) == toset(["dev"]) &&
      google_project_iam_member.ci_sqltest_dev_job_user["dev"].project == "dep-2610-dev" &&
      google_project_iam_member.ci_sqltest_dev_job_user["dev"].role == "roles/bigquery.jobUser" &&
      google_project_iam_member.ci_sqltest_dev_job_user["dev"].member == "serviceAccount:ci-sqltest-dev@dep-2610-ops.iam.gserviceaccount.com"
    )
    error_message = "Expected exactly one ci_sqltest_dev_job_user grant keyed \"dev\": roles/bigquery.jobUser on project dep-2610-dev for serviceAccount:ci-sqltest-dev@dep-2610-ops.iam.gserviceaccount.com."
  }

  assert {
    condition = (
      length([
        for grant in concat(
          values(google_project_iam_member.ci_plan),
          values(google_project_iam_member.ci_apply),
          values(google_project_iam_member.developer),
          values(google_project_iam_member.ci_sqltest_dev_job_user)
        ) : grant if grant.member == "serviceAccount:ci-sqltest-dev@dep-2610-ops.iam.gserviceaccount.com"
      ]) == 1 &&
      toset([
        for grant in concat(
          values(google_project_iam_member.ci_plan),
          values(google_project_iam_member.ci_apply),
          values(google_project_iam_member.developer),
          values(google_project_iam_member.ci_sqltest_dev_job_user)
        ) : "${grant.project}|${grant.role}" if grant.member == "serviceAccount:ci-sqltest-dev@dep-2610-ops.iam.gserviceaccount.com"
      ]) == toset(["dep-2610-dev|roles/bigquery.jobUser"])
    )
    error_message = "Across google_project_iam_member ci_plan, ci_apply, developer and ci_sqltest_dev_job_user, the ci-sqltest-dev SA must have exactly one grant: roles/bigquery.jobUser on dep-2610-dev."
  }

  assert {
    condition = alltrue([
      for grant in concat(
        values(google_storage_bucket_iam_member.ci_plan_state),
        values(google_storage_bucket_iam_member.ci_apply_state)
      ) : grant.member != "serviceAccount:ci-sqltest-dev@dep-2610-ops.iam.gserviceaccount.com"
    ])
    error_message = "No google_storage_bucket_iam_member ci_plan_state or ci_apply_state grant may name the ci-sqltest-dev SA."
  }

  assert {
    condition = alltrue([
      for grant in values(google_artifact_registry_repository_iam_member.ci_apply_reader) :
      grant.member != "serviceAccount:ci-sqltest-dev@dep-2610-ops.iam.gserviceaccount.com"
    ])
    error_message = "No google_artifact_registry_repository_iam_member.ci_apply_reader grant may name the ci-sqltest-dev SA."
  }
}

run "human_grants_dev_test_only" {
  command = plan
  variables {
    developer_principal = "user:developer@example.com"
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
        for env, project in local.ci_environments :
        google_service_account.ci_plan[env].account_id == "ci-plan-${env}" &&
        google_service_account.ci_apply[env].account_id == "ci-apply-${env}" &&
        google_project_iam_member.ci_plan["${env}/roles/compute.viewer"].project == project &&
        google_project_iam_member.ci_apply["${env}/roles/compute.networkAdmin"].project == project
      ])
    )
    error_message = "Human grants are exactly DEV/TEST project grants even when PROD CI exists."
  }
}

run "human_access_can_be_limited_to_test" {
  command = plan
  variables {
    developer_principal       = "user:developer@example.com"
    human_access_environments = ["test"]
  }
  assert {
    condition = (
      length(google_project_iam_member.developer) == 2 &&
      google_project_iam_member.developer["test/roles/iap.tunnelResourceAccessor"].project == var.test_project_id &&
      toset(keys(google_service_account.ci_plan)) == toset(["dev", "test", "prod"])
    )
    error_message = "Selecting human TEST access must not change CI identities."
  }
}

run "reject_project_url" {
  command = plan
  variables {
    gitlab_project_path = "https://gitlab.com/savlino/gcp-terraform-data-platform"
  }
  expect_failures = [var.gitlab_project_path]
}

run "reject_group_human" {
  command = plan
  variables {
    developer_principal = "group:developers@example.com"
  }
  expect_failures = [var.developer_principal]
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

run "reject_bare_email_as_human" {
  command = plan
  variables {
    developer_principal = "developer@example.com"
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
