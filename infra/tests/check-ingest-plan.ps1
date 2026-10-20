$ErrorActionPreference = 'Stop'
$infraDirectory = Split-Path -Parent $PSScriptRoot
$testFile = Join-Path 'tests' 'ingest_root.tftest.hcl'
$events = @(
    & terraform "-chdir=$infraDirectory" test "-filter=$testFile" -json -verbose |
        ForEach-Object { $_ | ConvertFrom-Json }
)
if ($LASTEXITCODE -ne 0) {
    throw 'Ingest root mock tests failed. Run Terraform test without -json for diagnostics.'
}

$expectedPause = @{
    dev_wiring             = $false
    test_paused_by_default = $true
    prod_paused_by_default = $true
    explicit_dev_pause     = $true
}
$plans = @($events | Where-Object {
    $_.type -eq 'test_plan' -and $expectedPause.ContainsKey($_.'@testrun')
})
if ($plans.Count -ne $expectedPause.Count) {
    throw 'Expected exactly four successful root mock plans; refusing a vacuous pass.'
}

foreach ($plan in $plans) {
    $changes = @($plan.test_plan.resource_changes)
    $jobs = @($changes | Where-Object { $_.type -eq 'google_cloud_run_v2_job' })
    $schedules = @($changes | Where-Object { $_.type -eq 'google_cloud_scheduler_job' })
    if ($jobs.Count -ne 1 -or $schedules.Count -ne 1) {
        throw 'Expected exactly one job and one schedule.'
    }
    $job = $jobs[0].change.after
    $schedule = $schedules[0].change.after
    if ($schedule.paused -ne $expectedPause[$plan.'@testrun']) {
        throw "Wrong scheduler pause default in $($plan.'@testrun')."
    }
    if ($job.location -ne 'europe-southwest1' -or $schedule.region -ne 'europe-west1') {
        throw 'Scheduler and data/job regions must remain independent.'
    }
    $expectedProtection = $plan.'@testrun' -eq 'prod_paused_by_default'
    $tables = @($changes | Where-Object { $_.type -eq 'google_bigquery_table' })
    if ($tables.Count -eq 0 -or $job.deletion_protection -ne $expectedProtection -or
        @($tables | Where-Object { $_.change.after.deletion_protection -ne $expectedProtection }).Count -ne 0) {
        throw 'The job and BigQuery tables must share the root deletion protection flag.'
    }
    $container = $job.template[0].template[0].containers[0]
    if (@($job.template[0].template[0].vpc_access).Count -ne 0) {
        throw 'Cloud Run must remain unattached to the demo VPC.'
    }
    $envMap = @{}
    foreach ($item in $container.env) {
        if (@($item.value_source).Count -ne 0) {
            throw 'Cloud Run secret injection is prohibited.'
        }
        $envMap[$item.name] = $item.value
    }
    $bucket = @($changes | Where-Object { $_.type -eq 'google_storage_bucket' })[0].change.after
    $dataset = @($changes | Where-Object { $_.type -eq 'google_bigquery_dataset' })[0].change.after
    $secret = @($changes | Where-Object { $_.type -eq 'google_secret_manager_secret' })[0].change.after
    $expectedEnv = @{
        PROJECT_ID           = 'dep-2610-dev'
        RAW_BUCKET           = $bucket.name
        BIGQUERY_DATASET     = $dataset.dataset_id
        ALERT_SECRET         = $secret.id
        REGION               = 'europe-southwest1'
        SOURCE_BUCKET        = 'ookla-mirror'
        SOURCE_REGION        = 'us-east-1'
        MIN_QUARTER          = '2024-Q1'
        MAXIMUM_BYTES_BILLED = '123456789'
        MART_ZOOM            = '9'
        MINIMUM_ROW_PERCENT  = '65'
        LATENCY_CEILING_MS   = '9000'
        SCHEMAS_DIR          = '/app/schemas'
        SOURCE_SCHEMA_PATH   = '/app/schemas/ookla_raw.json'
        NATIVE_SCHEMA_PATH   = '/app/schemas/ookla_tiles.json'
    }
    if ($envMap.Count -ne $expectedEnv.Count + 1) {
        throw 'Unexpected/missing application environment settings.'
    }
    foreach ($key in $expectedEnv.Keys) {
        if (-not $envMap.ContainsKey($key) -or $envMap[$key] -cne $expectedEnv[$key]) {
            throw "Incorrect application setting: $key"
        }
    }
    $bbox = $envMap.REGION_BBOX | ConvertFrom-Json
    if ($bbox.min_lon -ne -10 -or $bbox.max_lon -ne 5 -or $bbox.min_lat -ne 35 -or $bbox.max_lat -ne 44) {
        throw 'The configured bounding box must reach the application unchanged.'
    }
    foreach ($change in $changes) {
        if ($change.type -match '_iam_(policy|binding)$' -or $change.type -eq 'google_secret_manager_secret_version') {
            throw "Prohibited resource: $($change.type)"
        }
        if ($change.type -match '_iam_member$') {
            $role = $change.change.after.role
            if ($role -in @('roles/owner', 'roles/editor', 'roles/viewer')) {
                throw 'Basic roles must never be granted.'
            }
            if ($change.type -eq 'google_project_iam_member' -and $role -ne 'roles/bigquery.jobUser') {
                throw 'Only bigquery.jobUser may be project-scoped.'
            }
        }
    }
    $invokers = @($changes | Where-Object { $_.type -eq 'google_cloud_run_v2_job_iam_member' })
    if ($invokers.Count -ne 1 -or $invokers[0].change.after.role -ne 'roles/run.invoker' -or
        $invokers[0].change.after.member -ne 'serviceAccount:ingest-scheduler@dep-2610-dev.iam.gserviceaccount.com') {
        throw 'Only the scheduler identity may receive the job-scoped invoker grant.'
    }
}
Write-Output 'PASS: four root mock plans preserve env wiring, pause defaults, regions, deletion protection, additive IAM and API-only secret access.'
