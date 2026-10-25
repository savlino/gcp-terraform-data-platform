mock_provider "google" {
  mock_resource "google_service_account" {
    override_during = plan
    defaults = {
      email = "ingest-scheduler@dep-2610-dev.iam.gserviceaccount.com"
    }
  }
}

variables {
  project_id       = "dep-2610-dev"
  env              = "dev"
  region           = "europe-southwest1"
  scheduler_region = "europe-west1"
  runtime_sa_email = "ingest-runtime@dep-2610-dev.iam.gserviceaccount.com"
  image_tag        = "test-v1"
  image_repository = "europe-southwest1-docker.pkg.dev/dep-2610-ops/ops-ingest/ingest"
  application_env = {
    PROJECT_ID   = "dep-2610-dev"
    ALERT_SECRET = "projects/dep-2610-dev/secrets/alert-telegram"
  }
}

run "disabled_creates_no_job_resources" {
  command = plan
  module {
    source = "../modules/ingest_job"
  }

  assert {
    condition = (
      output.image_uri == "europe-southwest1-docker.pkg.dev/dep-2610-ops/ops-ingest/ingest:test-v1"
    )
    error_message = "Use the existing ops repository, never derive a repository from the environment project."
  }

  assert {
    condition = (
      length(google_cloud_run_v2_job.ingest) == 0 &&
      length(google_cloud_scheduler_job.ingest) == 0 &&
      length(google_service_account.scheduler) == 0 &&
      length(google_cloud_run_v2_job_iam_member.scheduler_invoker) == 0 &&
      output.job_name == null
    )
    error_message = "Disabled ingestion must not create an unusable job or schedule."
  }
}

run "enabled_job_and_oauth_schedule" {
  command = plan
  module {
    source = "../modules/ingest_job"
  }
  variables {
    enable_ingest_job   = true
    scheduler_paused    = false
    deletion_protection = false
  }

  assert {
    condition = (
      google_cloud_run_v2_job.ingest[0].location == var.region &&
      google_cloud_run_v2_job.ingest[0].template[0].task_count == 1 &&
      google_cloud_run_v2_job.ingest[0].template[0].parallelism == 1 &&
      google_cloud_run_v2_job.ingest[0].template[0].template[0].service_account == var.runtime_sa_email &&
      google_cloud_run_v2_job.ingest[0].template[0].template[0].timeout == "3600s" &&
      google_cloud_run_v2_job.ingest[0].template[0].template[0].max_retries == 0 &&
      google_cloud_run_v2_job.ingest[0].template[0].template[0].containers[0].args == tolist(["run"]) &&
      google_cloud_run_v2_job.ingest[0].template[0].template[0].containers[0].image == output.image_uri &&
      google_cloud_run_v2_job.ingest[0].template[0].template[0].containers[0].resources[0].limits == tomap({ cpu = "1", memory = "1024Mi" }) &&
      !google_cloud_run_v2_job.ingest[0].deletion_protection
    )
    error_message = "Run one bounded task as the runtime SA using the configured image and run entrypoint."
  }

  assert {
    condition = (
      tomap({ for item in google_cloud_run_v2_job.ingest[0].template[0].template[0].containers[0].env : item.name => item.value }) == var.application_env &&
      alltrue([for item in google_cloud_run_v2_job.ingest[0].template[0].template[0].containers[0].env : length(item.value_source) == 0]) &&
      google_cloud_run_v2_job.ingest[0].labels == tomap({ project = "gcp-tf-portfolio", env = "dev", managed_by = "terraform" }) &&
      google_cloud_run_v2_job.ingest[0].template[0].labels == google_cloud_run_v2_job.ingest[0].labels
    )
    error_message = "Pass non-secret env settings without Cloud Run secret injection and label the job/executions."
  }

  assert {
    condition = (
      google_service_account.scheduler[0].account_id == "ingest-scheduler" &&
      google_cloud_run_v2_job_iam_member.scheduler_invoker[0].role == "roles/run.invoker" &&
      google_cloud_run_v2_job_iam_member.scheduler_invoker[0].name == google_cloud_run_v2_job.ingest[0].name &&
      google_cloud_run_v2_job_iam_member.scheduler_invoker[0].location == var.region &&
      google_cloud_run_v2_job_iam_member.scheduler_invoker[0].member == "serviceAccount:${google_service_account.scheduler[0].email}" &&
      google_cloud_run_v2_job_iam_member.scheduler_invoker[0].member != "serviceAccount:${var.runtime_sa_email}"
    )
    error_message = "Only the distinct scheduler identity receives the job-scoped additive invoker grant."
  }

  assert {
    condition = (
      google_cloud_scheduler_job.ingest[0].region == var.scheduler_region &&
      !google_cloud_scheduler_job.ingest[0].paused &&
      google_cloud_scheduler_job.ingest[0].schedule == "0 6 * * 1" &&
      google_cloud_scheduler_job.ingest[0].time_zone == "Europe/Madrid" &&
      google_cloud_scheduler_job.ingest[0].retry_config[0].retry_count == 0 &&
      google_cloud_scheduler_job.ingest[0].retry_config[0].max_retry_duration == "0s" &&
      google_cloud_scheduler_job.ingest[0].http_target[0].uri == "https://run.googleapis.com/v2/projects/dep-2610-dev/locations/europe-southwest1/jobs/dev-ingest:run" &&
      google_cloud_scheduler_job.ingest[0].http_target[0].http_method == "POST" &&
      google_cloud_scheduler_job.ingest[0].http_target[0].body == base64encode("{}") &&
      google_cloud_scheduler_job.ingest[0].http_target[0].oauth_token[0].service_account_email == google_service_account.scheduler[0].email &&
      google_cloud_scheduler_job.ingest[0].http_target[0].oauth_token[0].scope == "https://www.googleapis.com/auth/cloud-platform" &&
      length(google_cloud_scheduler_job.ingest[0].http_target[0].oidc_token) == 0
    )
    error_message = "Belgium Scheduler must POST to the Madrid job API with OAuth, not OIDC, without automatic retries."
  }
}

run "allow_one_retry_and_protect_job" {
  command = plan
  module {
    source = "../modules/ingest_job"
  }
  variables {
    enable_ingest_job = true
    task_max_retries  = 1
  }

  assert {
    condition = (
      google_cloud_run_v2_job.ingest[0].template[0].template[0].max_retries == 1 &&
      google_cloud_run_v2_job.ingest[0].deletion_protection
    )
    error_message = "Allow one retry and preserve the default job deletion protection."
  }
}

run "reject_latest" {
  command = plan
  module {
    source = "../modules/ingest_job"
  }
  variables {
    image_tag = "latest"
  }
  expect_failures = [var.image_tag]
}

run "digest_pinning_overrides_tag" {
  command = plan
  module {
    source = "../modules/ingest_job"
  }
  variables {
    enable_ingest_job = true
    image_digest      = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  }
  assert {
    condition = (
      output.image_uri == "${var.image_repository}@${var.image_digest}" &&
      google_cloud_run_v2_job.ingest[0].template[0].template[0].containers[0].image == output.image_uri
    )
    error_message = "Pin the exact shared digest, not an environment-specific tag."
  }
}

run "reject_invalid_digest" {
  command = plan
  module {
    source = "../modules/ingest_job"
  }
  variables {
    image_digest = "sha256:too-short"
  }
  expect_failures = [var.image_digest]
}

run "reject_tag_in_repository_path" {
  command = plan
  module {
    source = "../modules/ingest_job"
  }
  variables {
    image_repository = "europe-southwest1-docker.pkg.dev/dep-2610-ops/ops-ingest/ingest:latest"
  }
  expect_failures = [var.image_repository]
}

run "reject_invalid_memory" {
  command = plan
  module {
    source = "../modules/ingest_job"
  }
  variables {
    task_memory_mib = 128
  }
  expect_failures = [var.task_memory_mib]
}

run "reject_unbounded_timeout" {
  command = plan
  module {
    source = "../modules/ingest_job"
  }
  variables {
    task_timeout_seconds = 0
  }
  expect_failures = [var.task_timeout_seconds]
}

run "reject_excess_retries" {
  command = plan
  module {
    source = "../modules/ingest_job"
  }
  variables {
    task_max_retries = 2
  }
  expect_failures = [var.task_max_retries]
}
