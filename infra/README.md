# Environment infrastructure

This root composes [raw storage](../modules/storage/README.md),
[BigQuery](../modules/bigquery/README.md), [runtime IAM](../modules/iam/README.md)
and [Telegram secret](../modules/secrets/README.md), plus
[scheduled ingestion](../modules/ingest_job/README.md) and
[private networking](../modules/network/README.md).
The bucket and dataset receive the same `region`. Only DEV environment files are
supplied. With ingestion/demo disabled, this root creates sixteen resources: one bucket, one dataset,
three native tables, one external table, one runtime service account, one secret,
four IAM members, a VPC, subnet and two
firewall rules. Enabling ingestion adds
four resources: Cloud Run Job, scheduler service account, job-scoped invoker member
and Scheduler job.
The optional demo adds one VM. Human access is managed only by the locally
applied [bootstrap](../bootstrap/README.md#gitlab-federation-and-human-access),
not by this root or CI-managed child modules.

## Configuration

- `subnet_cidr` defaults by environment when null: DEV `10.10.0.0/24`,
  TEST `10.20.0.0/24`, PROD `10.30.0.0/24`. DEV tfvars explicitly sets its CIDR.
- `enable_iap_demo_vm` defaults false; `demo_vm_zone` defaults to `${region}-a`.
  DEV explicitly uses `europe-southwest1-a`. The European `e2-micro` is billable;
  enable only for supervised testing and disable/apply immediately afterward.
  Cloud Run is not attached to this VPC. No NAT or connector is added.
- Bootstrap can grant a developer project-level Token Creator and IAP access in
  DEV/TEST only. OS Login/discovery permissions are separate prerequisites.
  See [network runbook](../docs/runbook.md#private-network-and-iap-demo).
- `scheduler_region` defaults to `europe-west1` independently of `region`.
  It is used only by Scheduler; Cloud Run, Artifact Registry and data remain in Madrid.
  See the [placement decision](../docs/architecture.md#scheduler-region-decision).

- [DEV variables](env/dev.tfvars): project, region, bounding box, minimum quarter,
  retention, destroy flags and `image_tag`. `region_bbox`, `min_quarter` and typed
  `app_settings` are passed as non-secret app env vars along with module outputs.
- `enable_ingest_job = false` creates no job or registry in this module.
  Bootstrap owns `ops-ingest` in ops and grants all three Cloud Run service agents
  repository-scoped Reader. Publish the image before enabling the job.
  `image_repository` defaults to the shared ops path; use bootstrap's output if
  its project/region differs. Optional `image_digest` overrides `image_tag` for
  exact artifact promotion; set the same repository and digest in every env.
  `image_tag` remains required from tfvars;
  tags are immutable, so choose a new unique tag for each changed image.
- `scheduler_paused = null` defaults to active in DEV and paused in TEST/PROD;
  DEV tfvars explicitly selects active. It has no effect until the job is enabled.
- Task defaults: 1 vCPU, 1024 MiB, 3600 seconds, zero retries; configurable memory,
  deadline and retry count (0 or 1). The weekly schedule is Monday 06:00
  Europe/Madrid, following daylight saving time. These are initial streaming estimates, not measured
  source-file sizing. See [module limitations](../modules/ingest_job/README.md).
- [DEV backend](env/dev.gcs.tfbackend): the existing ops bucket with prefix
  `infra/dev`. Bootstrap keeps its separate `bootstrap` prefix.
- Separate projects and backend prefixes separate environment resources, costs and
  state paths, but bucket-wide CI Object User access does **not** isolate those
  state paths from one another. [CLI workspaces](https://developer.hashicorp.com/terraform/cli/workspaces)
  do not provide that separation. Use the default workspace, not a workspace
  per environment; use a separate working directory for each environment.
- Standard labels are applied to every supporting resource, including the
  Cloud Run job/executions, demo VM and boot disk. The shared registry has ops labels in bootstrap. Scheduler,
  service accounts, VPC, subnet and firewall resources do not support labels
  in this provider.
- DEV allows bucket/dataset contents to be destroyed and disables Terraform
  table deletion protection. Future PROD variables must reverse those flags.
- No dataset default table expiration is set. Raw objects expire after 30 days
  in DEV; BigQuery native data does not expire automatically.
- Runtime IAM uses bucket-scoped Object User, dataset-scoped Data Editor and
  project-scoped Job User. Human impersonation grants are bootstrap-owned and
  absent in PROD. See [security checks](../docs/security.md).
- The `alert-telegram` secret has user-managed replication in `region` and a
  secret-scoped runtime accessor grant. No versions or values are in Terraform;
  follow [manual provisioning and verification](../docs/security.md).
- `ext_ookla_raw` pins `schemas/ookla_raw.json` and uses typed CUSTOM Hive
  partitions. DEV MR plans use the bootstrap-owned WIF plan identity; no CI apply
  jobs are configured yet.

## Prerequisites and authentication

Commands below are **PowerShell 7 on Windows**, starting at the repository root.
Do not run apply until you have reviewed the plan.

1. Verify bootstrap has created the state bucket and enabled Storage and BigQuery
   APIs in DEV. Check the project IDs and bucket in the DEV configuration.
2. Use an **existing**, authorized Terraform service account. Creating it and
   granting IAM are out of scope here. If none exists, stop at the
   offline checks; do not fall back to service-account keys or an Owner identity.
   Your user needs permission to impersonate it. The service account needs
   object access on the ops state bucket and permissions to manage DEV buckets,
   datasets and tables, plus service-account creation and IAM get/set permissions
   for the runtime grants, plus secret creation and secret IAM management.
   Scheduled ingestion also needs shared-repository read and job/schedule management, job IAM
   get/set and `iam.serviceAccounts.actAs` on runtime/scheduler identities.
   Network deployment needs VPC/subnet/firewall management; the optional demo
   also needs instance/disk creation/deletion. Human grants are not infra resources.
   Scope grants to the intended resources where possible;
   bucket/dataset creation also needs the corresponding project permissions.
3. Set `$sa` to that account's email (do not use a key file), then authenticate:

   ```powershell
   $sa = Read-Host 'Existing Terraform service account email'
   if (-not $sa) { throw 'SA email is empty: tests would run as your own user' }
   gcloud auth login
   gcloud auth application-default login --impersonate-service-account=$sa
   ```

   Terraform uses impersonated ADC for both the provider and GCS backend.
   The `bq` and `gcloud storage` examples below explicitly impersonate the same
   account. Never print or paste access tokens.

The Google constraint follows bootstrap's `~> 8.0`; the lock file reuses its
8.7.0 selection. Check the [Terraform Registry](https://registry.terraform.io/providers/hashicorp/google/latest)
before deliberately upgrading; no current-major claim is made here.

## Offline checks (no GCP calls)

```powershell
terraform -chdir=infra init -backend=false -input=false
terraform fmt -check -recursive infra
terraform fmt -check -recursive modules
terraform -chdir=infra validate
terraform -chdir=infra test
& .\infra\tests\check-secret-plan.ps1
& .\infra\tests\check-ingest-plan.ps1
```

Expected: valid configuration and `53 passed, 0 failed`. Tests mock Google:
they check naming, schemas, labels, partition filters, retention, destroy flags
and input validation. They do not prove deployed resources or API behavior.
Negative tests include reversed/out-of-range/zero-area boxes, malformed quarters,
quarters earlier than 2023 Q3 and zero retention.
Ingestion tests also reject `latest`, invalid task limits, DQ settings and budgets.
The root ingestion plan guard proves actual env wiring, independent locations,
DEV/TEST/PROD pause defaults, API-only secret access and additive IAM.

The [secret plan check](tests/check-secret-plan.ps1) also inspects the JSON plan
from the secrets mock-provider test and rejects any
`google_secret_manager_secret_version` resource, including resources in child
modules. It fails if Terraform fails or does not emit exactly one test plan.
Expected: `PASS: secrets mock plan contains no google_secret_manager_secret_version resources.`
The script does not print plan values.

## DEV plan and apply

```powershell
terraform -chdir=infra init -reconfigure -input=false '-backend-config=env/dev.gcs.tfbackend'
terraform -chdir=infra workspace show
terraform -chdir=infra plan '-var-file=env/dev.tfvars' -out=dev.tfplan
```

Expected workspace: `default`. For a fresh DEV state with
ingestion/demo disabled, expect **16 to add, 0 to
change, 0 to destroy**, all in DEV except state storage in ops. Stop if the plan
touches bootstrap, another environment, creates the disabled demo, or includes
NAT, connectors or other unrequested services.
An existing environment repository is retained and removed from state without
destruction by [registry_migration.tf](registry_migration.tf). Copy/verify its
image before changing the job URI; see the
[shared-registry migration](../docs/runbook.md#shared-ops-registry-migration).
Review it before applying:

```powershell
terraform -chdir=infra apply dev.tfplan
terraform -chdir=infra plan '-var-file=env/dev.tfvars' -detailed-exitcode
$LASTEXITCODE
```

Expected second plan: **No changes**, exit code **0** (2 means changes, 1 means
an error). Terraform outputs the raw bucket, dataset and table IDs. These cloud
checks have not been run by the assistant.

Do not enable the job until its image exists and the secret has a value.
Follow the [scheduled Cloud Run runbook](../docs/runbook.md#scheduled-cloud-run-ingestion)
for build/push, the second reviewed plan, execution and positive/negative checks.
With immutable tags, cleanup targets only untagged leftovers older than seven
days; tagged releases accumulate by design.

## Inspect the deployed resources

```powershell
gcloud storage buckets describe gs://dep-2610-dev-raw --impersonate-service-account=$sa
bq --impersonate_service_account=$sa --format=prettyjson show dep-2610-dev:network_perf
bq --impersonate_service_account=$sa ls dep-2610-dev:network_perf
foreach ($table in 'ookla_tiles', 'load_log', 'mart_quarterly_performance', 'ext_ookla_raw') {
  bq --impersonate_service_account=$sa --format=prettyjson show "dep-2610-dev:network_perf.$table"
}
```

Expected: raw bucket in `EUROPE-SOUTHWEST1`, uniform access, public access
prevention enforced, 30-day delete rule; dataset in the same region without
`defaultTableExpirationMs`; three native tables and `ext_ookla_raw`.
Curated and mart tables have DAY partitioning on `quarter_start` with
`requirePartitionFilter: true`. The load log is unpartitioned.

The external table must show PARQUET, the pinned source schema, the raw source
URI and CUSTOM Hive options with `requirePartitionFilter: true`. To verify in GCP,
use a quarter already uploaded to raw storage:

```powershell
$sql = 'SELECT quadkey FROM `dep-2610-dev.network_perf.ext_ookla_raw` WHERE type = ''mobile'' AND year = 2023 AND quarter = 3 LIMIT 1'
bq --impersonate_service_account=$sa --project_id=dep-2610-dev --location=europe-southwest1 query --use_legacy_sql=false --maximum_bytes_billed=1073741824 $sql
if ($LASTEXITCODE -ne 0) { throw 'Filtered external query failed.' }
$sql = 'SELECT quadkey FROM `dep-2610-dev.network_perf.ext_ookla_raw` LIMIT 1'
bq --impersonate_service_account=$sa --project_id=dep-2610-dev --location=europe-southwest1 query --use_legacy_sql=false --maximum_bytes_billed=1073741824 $sql
if ($LASTEXITCODE -eq 0) { throw 'External query without a partition filter was accepted.' }
```

The negative query must fail for a missing Hive partition filter, not permissions
or a billing cap. Each query is capped at 1 GiB billed; LIMIT does not itself
bound scanned bytes.

Positive partition-filter query (empty result is expected before ingestion):

```powershell
$sql = 'SELECT quadkey FROM `dep-2610-dev.network_perf.ookla_tiles` WHERE quarter_start = DATE ''2023-07-01'' LIMIT 1'
bq --impersonate_service_account=$sa --project_id=dep-2610-dev --location=europe-southwest1 query --use_legacy_sql=false --maximum_bytes_billed=10485760 $sql
if ($LASTEXITCODE -ne 0) { throw 'Partition-filter query failed.' }
```

## Negative cloud checks (must fail)

Reversed bounding box:

```powershell
terraform -chdir=infra plan '-var-file=env/dev.tfvars' '-var=region_bbox={min_lon=10,max_lon=-10,min_lat=35,max_lat=44}'
if ($LASTEXITCODE -eq 0) { throw 'Invalid bounding box was accepted.' }
```

Expected: the `region_bbox must have min < max` validation error, not a credential
or backend error. No changes are applied by this command.

Missing partition filter:

```powershell
$sql = 'SELECT quadkey FROM `dep-2610-dev.network_perf.ookla_tiles` LIMIT 1'
bq --impersonate_service_account=$sa --project_id=dep-2610-dev --location=europe-southwest1 query --use_legacy_sql=false --maximum_bytes_billed=10485760 $sql
if ($LASTEXITCODE -eq 0) { throw 'Query without partition filter was accepted.' }
```

Expected: BigQuery rejects the query because it lacks a filter on `quarter_start`
usable for partition elimination. A 403 or missing-table error is not a passing
test; run the positive query first to rule those out.

## References

- [Hive-partitioned external data](https://cloud.google.com/bigquery/docs/hive-partitioned-queries)
- [Google provider BigQuery table](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/bigquery_table)
- [Source types](../docs/source-schema.md)
