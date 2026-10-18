# Environment infrastructure

This root composes [raw storage](../modules/storage/README.md),
[BigQuery](../modules/bigquery/README.md), [runtime IAM](../modules/iam/README.md)
and [webhook secret](../modules/secrets/README.md).
The bucket and dataset receive the same `region`. Only DEV environment files are
supplied. It creates eleven resources by default: one bucket, one dataset, three
empty native tables, one runtime service account, one secret and four IAM members.
An explicit non-PROD `developer_principal` adds one service-account IAM member.

## Configuration

- [DEV variables](env/dev.tfvars): project, region, bounding box, minimum quarter,
  retention and destroy flags. `region_bbox` and `min_quarter` are validated
  configuration for the future app; they do not filter or load anything yet.
- [DEV backend](env/dev.gcs.tfbackend): the existing ops bucket with prefix
  `infra/dev`. Bootstrap keeps its separate `bootstrap` prefix.
- Separate projects and backend prefixes isolate environment IAM, costs and
  state. [CLI workspaces](https://developer.hashicorp.com/terraform/cli/workspaces)
  do not provide that separation. Use the default workspace, not a workspace
  per environment; use a separate working directory for each environment.
- Standard labels are applied to the bucket, dataset, all tables and secret.
- DEV allows bucket/dataset contents to be destroyed and disables Terraform
  table deletion protection. Future PROD variables must reverse those flags.
- No dataset default table expiration is set. Raw objects expire after 30 days
  in DEV; BigQuery native data does not expire automatically.
- Runtime IAM uses bucket-scoped Object User, dataset-scoped Data Editor and
  project-scoped Job User. Developer impersonation defaults to disabled and is
  never granted in PROD. See [security checks](../docs/security.md).
- The `alert-telegram` secret has user-managed replication in `region` and a
  secret-scoped runtime accessor grant. No versions or values are in Terraform;
  follow [manual provisioning and verification](../docs/security.md).
- `ext_ookla_raw` is deferred until following steps. No network, app, scheduler
  or CI resources are part of current implementation.

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
```

Expected: valid configuration and `21 passed, 0 failed`. Tests mock Google:
they check naming, schemas, labels, partition filters, retention, destroy flags
and input validation. They do not prove deployed resources or API behavior.
Negative tests include reversed/out-of-range/zero-area boxes, malformed quarters,
quarters earlier than 2023 Q3 and zero retention.

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

Expected workspace: `default`. For a fresh DEV state with the default null
developer principal, expect **11 to add, 0 to
change, 0 to destroy**, all in DEV except state storage in ops. Stop if the plan
touches bootstrap, another environment, or includes network resources.
If previous plans were already applied without drift, expect only two secret-related
additions. Preserve any configured developer principal on subsequent plans;
see [security instructions](../docs/security.md).
Review it before applying:

```powershell
terraform -chdir=infra apply dev.tfplan
terraform -chdir=infra plan '-var-file=env/dev.tfvars' -detailed-exitcode
$LASTEXITCODE
```

Expected second plan: **No changes**, exit code **0** (2 means changes, 1 means
an error). Terraform outputs the raw bucket, dataset and table IDs. These cloud
checks have not been run by the assistant.

## Inspect the deployed resources

```powershell
gcloud storage buckets describe gs://dep-2610-dev-raw --impersonate-service-account=$sa
bq --impersonate_service_account=$sa --format=prettyjson show dep-2610-dev:network_perf
bq --impersonate_service_account=$sa ls dep-2610-dev:network_perf
foreach ($table in 'ookla_tiles', 'load_log', 'mart_quarterly_performance') {
  bq --impersonate_service_account=$sa --format=prettyjson show "dep-2610-dev:network_perf.$table"
}
```

Expected: raw bucket in `EUROPE-SOUTHWEST1`, uniform access, public access
prevention enforced, 30-day delete rule; dataset in the same region without
`defaultTableExpirationMs`; exactly the three native tables, initially empty.
Curated and mart tables have DAY partitioning on `quarter_start` with
`requirePartitionFilter: true`. The load log is unpartitioned.

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
