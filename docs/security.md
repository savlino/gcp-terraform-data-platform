# Runtime identity and least privilege

This document describes Terraform configuration, not verified live permissions.
The runtime account is separate from the existing Terraform deployment identity.
There are no service-account keys. All grants use additive `*_iam_member`
resources; inherited organization/folder/project policies still affect effective
permissions and must be checked separately.

## Configured grants

| Identity | Role | Scope | Why |
| --- | --- | --- | --- |
| `ingest-runtime@<project_id>.iam.gserviceaccount.com` | `roles/storage.objectUser` | Environment raw bucket only | Create, read, list and replace raw objects. Replacement after an ETag change requires `storage.objects.delete` as well as create. Reads also support external-table queries. |
| Same runtime | `roles/bigquery.dataEditor` | Environment `network_perf` dataset only | Populate native tables, maintain the load log and later create the external table. Does not grant project-level dataset creation. |
| Same runtime | `roles/bigquery.jobUser` | Environment project | Submit query jobs (`bigquery.jobs.create`); jobs belong to projects, so this cannot be a dataset grant. Data access remains separately scoped. |
| Explicit `developer_principal` | `roles/iam.serviceAccountTokenCreator` | That runtime service account only, non-PROD | Local keyless impersonation; absent by default (`null`) and always absent in PROD. |

No basic roles are granted. `objectCreator` plus `objectViewer` is intentionally
not used because it cannot overwrite an existing object. Object User is broader
than create/read, including delete/update; it is restricted to the raw bucket.
The runtime cannot administer the bucket or deploy Terraform with these grants.
Token Creator allows assuming all runtime permissions: grant it only to trusted
developers. This conditional resource is not an organization-wide impersonation
deny policy; it cannot prevent impersonation granted elsewhere.

Dataset IAM members must not be mixed with dataset `access` blocks or
`google_bigquery_dataset_access` resources. They also remove authorized-view
permissions; no authorized views are currently configured.

## Deploy the grants (PowerShell 7 on Windows)

Start at the repository root, using the existing Terraform deployment identity
and impersonated ADC described in [the infra guide](../infra/README.md).
It additionally needs service-account creation and IAM get/set permissions on
the target project, raw bucket, dataset and runtime service account. An
authorized administrator must grant those separately if absent. This step does
not create the deployer or grant it additional permissions.

```powershell
$developer = Read-Host 'Your IAM principal, e.g. user:you@example.com'
terraform -chdir=infra init -reconfigure '-backend-config=env/dev.gcs.tfbackend'
terraform -chdir=infra plan '-var-file=env/dev.tfvars' "-var=developer_principal=$developer" -out=dev.tfplan
```

If action is already applied without other drift, expect **5 additions**:
one service account and four IAM members. With the default null principal,
expect **4 additions**, with no developer impersonation grant. A fresh deployment
including storage/BigQuery has 10 or 9 additions respectively. Review any
unrelated changes before applying.

```powershell
terraform -chdir=infra apply dev.tfplan
terraform -chdir=infra plan '-var-file=env/dev.tfvars' "-var=developer_principal=$developer" -detailed-exitcode
```

Expected second plan: no changes, exit code 0. Supply the same principal on
future plans or explicitly set it in local configuration: reverting to the
default null removes that grant. Do not commit personal identity values.

## Positive permission checks (PowerShell 7 on Windows)

Use CLI user login for the developer granted Token Creator above. Terraform's
ADC is separate; do not replace the deployer's ADC with the runtime identity.
Allow IAM propagation before interpreting a new grant as broken.

```powershell
gcloud auth login
$runtime = terraform -chdir=infra output -raw runtime_service_account_email
if ($LASTEXITCODE -ne 0) { throw 'Cannot read runtime output.' }
$project = 'dep-2610-dev'
$raw = 'dep-2610-dev-raw'
$region = 'europe-southwest1'
$probeName = 'iam-probe-' + [guid]::NewGuid().ToString('N') + '.txt'
$localProbe = Join-Path ([IO.Path]::GetTempPath()) $probeName
$probeUri = "gs://$raw/iam-probes/$probeName"

try {
  Set-Content -LiteralPath $localProbe -Value 'synthetic IAM probe v1' -Encoding ascii
  gcloud storage cp $localProbe $probeUri --impersonate-service-account=$runtime
  if ($LASTEXITCODE -ne 0) { throw 'Object creation failed.' }
  Set-Content -LiteralPath $localProbe -Value 'synthetic IAM probe v2' -Encoding ascii
  gcloud storage cp $localProbe $probeUri --impersonate-service-account=$runtime
  if ($LASTEXITCODE -ne 0) { throw 'Object overwrite failed.' }
  gcloud storage ls "gs://$raw/iam-probes/" --impersonate-service-account=$runtime
  if ($LASTEXITCODE -ne 0) { throw 'Object listing failed.' }
  $content = gcloud storage cat $probeUri --impersonate-service-account=$runtime
  if ($LASTEXITCODE -ne 0 -or "$content".Trim() -ne 'synthetic IAM probe v2') {
    throw 'Object read did not return the replacement content.'
  }
} finally {
  if (Test-Path -LiteralPath $localProbe) { Remove-Item -LiteralPath $localProbe }
}

gcloud storage rm $probeUri --impersonate-service-account=$runtime
if ($LASTEXITCODE -ne 0) { throw "Probe cleanup failed: $probeUri" }

$sql = 'SELECT network_type, quarter_start, source_etag FROM `dep-2610-dev.network_perf.load_log` LIMIT 1'
bq --impersonate_service_account=$runtime --project_id=$project --location=$region query --use_legacy_sql=false --maximum_bytes_billed=10485760 $sql
if ($LASTEXITCODE -ne 0) { throw 'load_log query failed.' }
```

Expected: create and overwrite succeed, listing finds the probe, reading returns
v2, deletion succeeds, and the query succeeds (empty before ingestion). A failed
probe may leave one synthetic remote object; delete only the printed unique probe
URI after diagnosing the failure.

## Negative permission checks (must fail with 403)

1. Use another environment's **existing**, private raw bucket. TEST/PROD buckets
   are not created yet by the supplied DEV configuration. An administrator must
   confirm the chosen bucket exists first; a 404 is not proof of IAM isolation.

   ```powershell
   $otherRaw = Read-Host 'Existing private raw bucket in another environment (name only)'
   if ($otherRaw -eq $raw) { throw 'Choose another environment bucket.' }
   gcloud storage ls "gs://$otherRaw/" --impersonate-service-account=$runtime
   if ($LASTEXITCODE -eq 0) { throw 'Unexpected access to another environment.' }
   ```

   Expected: 403 for `storage.objects.list`, not authentication, service-account
   impersonation, API-disabled or missing-bucket errors. If no other bucket
   exists, record this check as blocked rather than claiming it passes.

2. Attempt dataset creation in DEV:

   ```powershell
   $deniedDataset = 'iam_denied_probe_' + [guid]::NewGuid().ToString('N')
   bq --impersonate_service_account=$runtime --project_id=$project --location=$region mk --dataset "${project}:$deniedDataset"
   if ($LASTEXITCODE -eq 0) {
     throw "Unexpected dataset creation. Inspect inherited IAM, then remove the empty dataset ${project}:$deniedDataset using the Terraform deployer/admin."
   }
   ```

   Expected: 403 for `bigquery.datasets.create`. A success is a security-test
   failure, potentially due to permissions inherited elsewhere; it leaves an
   empty uniquely named dataset for an administrator to remove. No query job
   is submitted by this metadata operation.

## Offline checks

```powershell
terraform -chdir=infra init -backend=false -input=false
terraform -chdir=infra validate
terraform -chdir=infra test
```

Tests mock Google and run plan-only checks; they never create cloud resources.
They cover exact runtime role scopes, null defaults, explicit DEV/TEST
impersonation, suppressed PROD impersonation, absence of basic roles across all
four configured IAM grant types and rejection of `allUsers`.

## References

- [Service account impersonation](https://cloud.google.com/iam/docs/service-account-impersonation)
- [BigQuery access control](https://cloud.google.com/bigquery/docs/access-control)
- [Storage IAM roles](https://cloud.google.com/storage/docs/access-control/iam-roles)
- [Dataset IAM caveats in provider 8.7.0](https://registry.terraform.io/providers/hashicorp/google/8.7.0/docs/resources/bigquery_dataset_iam)
