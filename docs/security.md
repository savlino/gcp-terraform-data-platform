# Identities, federation and least privilege

This document describes Terraform configuration, not verified live permissions.
The runtime account is separate from the existing Terraform deployment identity.
There are no service-account keys. All grants use additive `*_iam_member`
resources; inherited organization/folder/project policies still affect effective
permissions and must be checked separately.

## Plan policy checks

[scripts/check_plan.py](../scripts/check_plan.py) checks the JSON representation
of a **whole environment** plan, not a targeted plan. It uses Python 3.12 and the
standard library only, accepts UTF-8 with or without a PowerShell BOM, and
examines only `resource_changes` entries with `mode == "managed"`.

| ID | Check | Applies to |
| --- | --- | --- |
| P0 | At least one managed resource change and one `google_bigquery_dataset`; otherwise "check would pass vacuously". | Whole plan, including no-op managed entries |
| P1 | No `google_secret_manager_secret_version`; secret values must never enter state. | All managed changes, including deletes |
| P2 | No `google_service_account_key`. | All managed changes, including deletes |
| P3 | No resource type ending in `_iam_binding` or `_iam_policy`. | All managed changes, including deletes |
| P4 | No `roles/owner`, `roles/editor` or `roles/viewer` in `role`. | Non-null `after` |
| P5 | No `allUsers` or `allAuthenticatedUsers` in `member` or any `members` item. | Non-null `after` |
| P6 | `public_access_prevention == "enforced"` and `uniform_bucket_level_access == true`. | Buckets with non-null `after` |
| P7 | No `0.0.0.0/0` in `source_ranges` for a non-empty ingress `allow`; omitted direction means INGRESS. Deny-only rules are allowed. | Firewalls with non-null `after` |
| P8 | Every `network_interface` has empty `access_config`. | Instances with non-null `after` |
| P9 | If `labels` is present, require `project`, `env`, `managed_by` keys and `labels.env == --env`; ignore `effective_labels` and `terraform_labels`. | Non-null `after` containing labels |
| P10 | No delete/replace of bucket, dataset, table, secret container or Artifact Registry repository without intentional override. | Actions containing `delete`, including delete/create replacements |
| P11 | Tables require `deletion_protection == true`; buckets require `force_destroy == false`; no `user:` member or members item. | Non-null `after`, PROD only |

P4-P9 and P11 ignore deletes (`after == null`); P10 inspects delete actions.
Unknown required fields in `after_unknown`, including nested values, and missing
computed fields emit `SKIP [Pn] address: ... unknown until apply`. A known
violation is still reported when another value is unknown. Missing mandatory
label keys are violations, not computed attributes. A skip is not proof of
compliance; review it before apply and verify effective permissions separately.

**PowerShell on Windows**, from the repository root, after generating a complete
saved DEV plan:

```powershell
terraform -chdir=infra show -json dev.tfplan > plan.json
if ($LASTEXITCODE -ne 0) { throw 'Cannot export plan JSON' }
python .\scripts\check_plan.py .\plan.json --env dev --junit policy-dev.xml
```

Exit codes: `0` no violations (possibly skips), `1` policy violations, `2`
input/usage/report-write errors. Output lists findings and a checked/violated/
skipped summary. For deliberate destructive work only, `--allow-destroy` or
`ALLOW_DESTROY=true` disables **P10 only**. Do not enable this globally in CI.
P1/P2/P3 continue rejecting forbidden resources even during destruction.

JUnit contains one testcase for each P0-P11. Failures name every violating
address. A rule with skips and no failures is skipped; when both occur, failure
takes precedence and skipped details remain in `system-out`.
`policy:dev` consumes the full DEV plan artifact, passes `--env dev`, and publishes
the restricted one-day JUnit report even on policy failure. Its rules match the
plan job so fork MRs cannot depend on a missing cloud plan. `python:lint-test`
runs the app unit suite, synthetic policy tests and Ruff in Python 3.12.
No real plan or credential is a test fixture; JSON plans can contain sensitive
state and must not be committed. Local synthetic checks do not verify a live MR.

## Configured grants

| Identity | Role | Scope | Why |
| --- | --- | --- | --- |
| `ingest-runtime@<project_id>.iam.gserviceaccount.com` | `roles/storage.objectUser` | Environment raw bucket only | Create, read, list and replace raw objects. Replacement after an ETag change requires `storage.objects.delete` as well as create. Reads also support external-table queries. |
| Same runtime | `roles/bigquery.dataEditor` | Environment `network_perf` dataset only | Populate native tables, maintain the load log and later create the external table. Does not grant project-level dataset creation. |
| Same runtime | `roles/bigquery.jobUser` | Environment project | Submit query jobs (`bigquery.jobs.create`); jobs belong to projects, so this cannot be a dataset grant. Data access remains separately scoped. |
| Same runtime | `roles/secretmanager.secretAccessor` | Environment `alert-telegram` secret only | Read the Telegram alert config (bot token, chat ID); no version creation or project-wide secret access. |
| Bootstrap `developer_principal` | `roles/iam.serviceAccountTokenCreator` | DEV and TEST projects only | Optional human local impersonation; broad across all SAs in those projects, not ops CI accounts. Absent by default and never granted in PROD. |
| `ingest-scheduler@<project_id>.iam.gserviceaccount.com` | `roles/run.invoker` | Environment Cloud Run job only, when enabled | OAuth-authenticated Scheduler launch; no data, secret or runtime impersonation grants. |
| Same bootstrap human | `roles/iap.tunnelResourceAccessor` | DEV and TEST projects only, independent of VM flag | Deliberate tunnel authorization, even if inherited Owner access already exists. No explicit OS Login grant is managed by this repo. |

## Private network and IAP-only SSH

The [network module](../modules/network/README.md) creates a custom-mode VPC and
one private IPv4 subnet with Private Google Access. Default CIDRs are DEV
`10.10.0.0/24`, TEST `10.20.0.0/24` and PROD `10.30.0.0/24`; a variable permits
an explicit override. A priority-65534 rule (`<env>-deny-all-ingress`)
explicitly denies all IPv4 ingress. GCP already has an implied deny at 65535,
but that rule is invisible in code and cannot be logged; the explicit copy has
firewall logging enabled (without metadata), so denied attempts are auditable.
The only allow is TCP 22 from Google's fixed IAP proxy range
`35.235.240.0/20`, priority 1000, targeting `iap-ssh` tagged instances. There is
no default internal-access allow. An allow at lower numeric priority wins over
the deny; upstream hierarchical firewall policies still matter.

The optional demo VM has no external IP, eliminating a directly routable
internet SSH endpoint. IAP provides identity-authorized encrypted forwarding
instead of maintaining a public-IP bastion, its patching and an always-on
compute bill. The network tag controls firewall targeting, not IAM. Bootstrap's
optional human IAP grant is project-wide in DEV/TEST and remains when the VM is
off. OS Login and Compute discovery permissions are existing/manual operator
prerequisites, not grants made by CI-managed infra. No human grant is configured
in PROD. This is not an organization-wide deny policy and cannot remove inherited
Owner permissions. Test with a non-Owner account to isolate explicit grants.

`enable_iap_demo_vm = false` by default. When enabled, the `e2-micro` in
`europe-southwest1-a` uses Shielded Secure Boot/vTPM/integrity monitoring,
OS Login and a 10 GiB auto-deleting disk. It has no attached service account,
no runtime data permissions, no metadata SSH keys and no serial-port access.
The disk and VM carry standard labels; network, subnet and firewall resources
have no labels field in the pinned provider.

European `e2-micro` use is not covered by the Compute Engine free-tier VM
allowance. Enable only for supervised verification, then disable and apply
immediately; stopping alone leaves disk charges. There is no automatic expiry.
Private Google Access is not general internet access; no NAT or connector is
added. Cloud Run is **not attached** to this VPC, so this demonstration is not
a claim of private Cloud Run egress or isolation.

The GCP-created `default` network is outside Terraform management and must be
removed manually after checking dependencies. Without an organization policy
this cannot prevent future default-network creation in other projects. See
[manual cleanup, SSH checks and teardown](runbook.md#private-network-and-iap-demo).

The runtime receives no invocation grant. Only the scheduler gets an explicit
invoker grant in this configuration; administrators or inherited policies may
still allow other callers. A live 403 check must target `jobs:run` itself, not
just a failed job-description read. See the
[scheduled ingestion checks](runbook.md#scheduled-cloud-run-ingestion).
The app receives `ALERT_SECRET` as a non-secret resource name and fetches the
latest payload through the Secret Manager API; Cloud Run secret injection is
not used. Normal Cloud Run/Scheduler service agents and publisher/deployer
permissions are prerequisites, not new project-level workload grants.

## GitLab WIF and CI role catalogue

[Bootstrap federation](../bootstrap/wif.tf) is applied locally by an authorized
administrator, never by the infrastructure CI pipeline. Pool `gitlab` and
provider `gitlab-com` live in ops. The issuer is `https://gitlab.com`; the sole
allowed ID-token audience is
`https://iam.googleapis.com/projects/<ops-number>/locations/global/workloadIdentityPools/gitlab/providers/gitlab-com`.
Mappings are `google.subject = assertion.sub`, plus `attribute.project_path`,
`attribute.ref` and `attribute.ref_protected` from their matching claims.
The provider condition compares `assertion.project_path` to the configured
`gitlab_project_path`. GitLab MR tokens identify the source project, so fork
tokens do not satisfy this trust. Cloud plan jobs also exclude fork MRs.

`ci-plan-<env>` and `ci-apply-<env>` live in ops; `ci_environments = ["dev"]`
is the default. Each SA receives additive `roles/iam.workloadIdentityUser`:

- Plan: `principalSet://iam.googleapis.com/projects/<ops-number>/locations/global/workloadIdentityPools/gitlab/attribute.project_path/<path>`.
- Apply: `principalSet://iam.googleapis.com/projects/<ops-number>/locations/global/workloadIdentityPools/gitlab/attribute.ref_protected/true`.

The apply selector relies on the provider's project-path condition. Keep this
pool dedicated to this project: another provider in the same pool could widen
trust. A protected ref is not necessarily `main`; review protected branch **and
tag** rules. There are no CI apply jobs yet. These grants do not give the plan
account permission to impersonate the apply account.

All roles below are on the corresponding **environment project**, not ops.
This deliberate deployment-identity exception avoids basic Owner/Editor/Viewer
roles but is not equivalent to a minimal custom role.

| Plan role | Required refresh surface |
| --- | --- |
| `roles/iam.securityReviewer` | Project, bucket, dataset, SA and Cloud Run IAM policies, including resource-level IAM reads. |
| `roles/compute.viewer` | VPC, subnet, firewalls, image and optional VM/disk reads. |
| `roles/storage.bucketViewer` | Bucket settings, without raw-object content access. |
| `roles/bigquery.metadataViewer` | Dataset/table definitions without table-data reads or query submission. |
| `roles/run.viewer` | Cloud Run job definitions. |
| `roles/cloudscheduler.viewer` | Scheduler job definitions. |
| `roles/secretmanager.viewer` | Secret container metadata, not version payloads. |
| `roles/artifactregistry.reader` | Repository configuration; also permits artifact reads. |

| Apply role | Managed surface / trade-off |
| --- | --- |
| `roles/storage.admin` | Raw bucket settings, lifecycle and bucket IAM; also raw object data. |
| `roles/bigquery.admin` | Dataset/table creation and dataset IAM; also broad data/query access. |
| `roles/compute.networkAdmin` | VPC and subnet lifecycle. |
| `roles/compute.securityAdmin` | Firewall lifecycle. |
| `roles/compute.instanceAdmin.v1` | Optional VM and boot disk lifecycle; broader compute capabilities. |
| `roles/iam.serviceAccountAdmin` | Runtime/scheduler service accounts and their policies; does not supply `actAs`. |
| `roles/iam.serviceAccountUser` | Project-level `actAs` for attaching newly created runtime/scheduler SAs without a first-apply dependency on existing accounts; also permits attaching other environment SAs. |
| `roles/resourcemanager.projectIamAdmin` | Runtime BigQuery Job User grant; permits powerful project-IAM changes/self-escalation. |
| `roles/run.admin` | Job and job-invoker IAM lifecycle. |
| `roles/cloudscheduler.admin` | Schedule lifecycle. |
| `roles/secretmanager.admin` | Container and secret-IAM lifecycle; predefined role also permits secret payload access/deletion even though Terraform manages no values. |
| `roles/artifactregistry.admin` | Repository, immutable-tag setting and cleanup policy lifecycle. |

These are trusted deployment administrators, not strong isolation boundaries.
Resource-scoped `actAs` and a custom secret-management role could narrow access,
but require additional bootstrap choreography/custom-role maintenance. No
IAP-admin role is needed: human grants are bootstrap-owned, not infra resources.
Runtime and scheduler roles remain resource-scoped wherever possible.

Both CI SAs additionally receive bucket-scoped `roles/storage.objectUser` on
the ops state bucket for reads and state/lock writes/deletes. This is **not
read-only planning**: bucket-wide access covers every prefix, including bootstrap
and future PROD state. Backend prefixes do not enforce IAM isolation. A malicious
MR can execute Terraform/provider code with the plan identity and can corrupt or
exfiltrate shared state. Separate state buckets/identities or a trusted-plan gate
are required before treating environments as isolated.

Trust repository members and runners, review CI/Terraform changes, and protect
`main`/release tags from untrusted pushes. Provider cache contains binaries only;
lockfiles/checksums must be reviewed. Plans may embed state values, so artifacts
are restricted to Maintainers and expire after one day. Neither JWTs nor credential
configs, gcloud caches or secret payloads belong in artifacts or caches. CI config
is locally checked; live exchange, refresh-role sufficiency, GitLab artifact
permissions and the unprotected-ref denial remain manual verification.

No basic roles are granted. `objectCreator` plus `objectViewer` is intentionally
not used because it cannot overwrite an existing object. Object User is broader
than create/read, including delete/update; it is restricted to the raw bucket.
The runtime cannot administer the bucket or deploy Terraform with these grants.
Bootstrap's project-level human Token Creator allows assuming **any** SA in
DEV/TEST: grant it only to trusted humans. It does not grant access to ops CI SAs
and is not an organization-wide impersonation deny.

Dataset IAM members must not be mixed with dataset `access` blocks or
`google_bigquery_dataset_access` resources. They also remove authorized-view
permissions; no authorized views are currently configured.

## Deploy the grants (PowerShell 7 on Windows)

Start at the repository root, using the existing Terraform deployment identity
and impersonated ADC described in [the infra guide](../infra/README.md).
Bootstrap provisions the CI identities/roles separately. Human grants must be
applied there first, before removing the old infra grants; see
[migration and GitLab setup](runbook.md#gitlab-wif-bootstrap-and-mr-plans).

```powershell
terraform -chdir=infra init -reconfigure '-backend-config=env/dev.gcs.tfbackend'
terraform -chdir=infra plan '-var-file=env/dev.tfvars' -out=dev.tfplan
```

For a fresh deployment, repository-only configuration creates 17 resources
with no human grants. Enabling scheduled ingestion adds four more. The optional
demo adds one VM and no human IAM.
For an existing deployment, review the incremental plan rather than assuming a
fixed addition count. Follow the [image-first sequence](runbook.md#scheduled-cloud-run-ingestion)
before enabling the job. Review any unrelated changes before applying.

```powershell
terraform -chdir=infra apply dev.tfplan
terraform -chdir=infra plan '-var-file=env/dev.tfvars' -detailed-exitcode
```

Expected second plan: no changes, exit code 0. Keep the human principal in local
bootstrap configuration only; infra no longer accepts that variable.

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
