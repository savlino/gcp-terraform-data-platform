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
| Same runtime | `roles/secretmanager.secretAccessor` | Environment `alert-telegram` secret only | Read the Telegram alert config (bot token, chat ID); no version creation or project-wide secret access. |
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

For a fresh deployment, expect 12 additions with the developer grant, or 11
with the default null principal. If previous plans were already applied without
drift, new apply adds only two resources: a secret container and its IAM member.
Review any unrelated changes before applying.

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

## Alert Telegram secret

Ookla Open Data is public: source listing and reads need no credentials.
The only application secret is the Telegram alert configuration in
`alert-telegram`, stored as one JSON object:
`{"bot_token":"<BotFather token>","chat_id":"<numeric chat id>"}`.
The bot token is a credential: anyone holding it can act as the bot. Never commit,
paste into CLI arguments/history, print, log, put in tfvars or ordinary CI
variables, or pass it through Terraform. The chat ID is not a credential, but it is
stored with the token so that the runtime reads one secret.

The default implementation creates no versions. The optional write-only
Terraform version path is not implemented; see [the module trade-off](../modules/secrets/README.md).
There is no secret-reading Terraform data source, so manual payloads never enter
Terraform state. A token-free state does not prove there were no leaks in other
systems, earlier state versions or logs.

The Telegram Bot API puts the token in the request URL
(`https://api.telegram.org/bot<token>/...`). Error messages, HTTP traces and
proxy logs can therefore expose it. The runbook scripts suppress error details
and print only the HTTP status. Future application code must do the same: never
log request URLs or raw exceptions from Telegram calls.

Version uploads need `secretmanager.versions.add`, which the runtime
intentionally lacks. Use your own authorized account or an existing provisioning
identity. This configuration grants neither.

Procedures (PowerShell 7) are in [the runbook](runbook.md#telegram-alert-secret):

- add a version;
- test it as the runtime SA, including a check that the token is not in Terraform state, and a `sendMessage`;
- rotate the token.

Mock tests cannot verify these live properties. Negative local test:
`$sa = ''; if (-not $sa) { throw 'SA email is empty: tests would run as your own user' }`
must throw before any impersonated command. The secrets mock test checks the
exact secret-scoped role and identity, without reading any value.
[check-secret-plan.ps1](../infra/tests/check-secret-plan.ps1) fails if a
secret version ever appears in the plan.

## References

- [Service account impersonation](https://cloud.google.com/iam/docs/service-account-impersonation)
- [BigQuery access control](https://cloud.google.com/bigquery/docs/access-control)
- [Storage IAM roles](https://cloud.google.com/storage/docs/access-control/iam-roles)
- [Dataset IAM caveats in provider 8.7.0](https://registry.terraform.io/providers/hashicorp/google/8.7.0/docs/resources/bigquery_dataset_iam)
