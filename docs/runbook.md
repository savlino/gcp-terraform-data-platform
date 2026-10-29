# Runbook

Operational procedures. Commands are for **PowerShell 7 on Windows**, run from
the repository root. Examples target DEV (`dep-2610-dev`); change `$project`
for other environments.

## GitLab WIF bootstrap and MR plans

This section is deployment guidance, not a record of live federation.
Configuration uses lint/test/plan/policy for DEV/TEST/PROD, plus manual apply
promotion (`apply:dev` -> `apply:test` -> `apply:prod`) on protected `main`.
Read the [role catalogue and shared-state warning](security.md#gitlab-wif-and-ci-role-catalogue)
before authorizing MR code. No keys, secret CI variables or AWS credentials are
needed.

### Apply bootstrap locally and migrate human grants

1. Use an authorized administrator's **user ADC** for bootstrap, not the runtime
   or a CI account. Keep the existing bootstrap GCS backend; the initial
   local-to-GCS migration is not repeated.
2. Set the exact GitLab source project path and optional human principal in your
   local bootstrap inputs. Human grants are project-wide Token Creator and IAP
   on DEV **and TEST**, even while only DEV CI accounts exist. PROD receives
   neither. Keep these same bootstrap inputs on future plans.
3. Apply/recheck bootstrap **before** removing the old infra resource-scoped
   human grants. These independent states cannot use a Terraform `moved` block.
   Retain the existing manual/Owner OS Login authorization or have an
   administrator provision it separately; the old instance OS Login grant will
   be removed, not recreated in bootstrap.

**PowerShell on Windows**, repository root:

```powershell
gcloud auth application-default login
gcloud auth application-default set-quota-project dep-2610-ops
$billing = Read-Host 'Existing billing account ID'
$path = Read-Host 'Exact GitLab namespace/project path'
$developer = Read-Host 'Human user:/group: email principal'
if (-not $billing -or -not $path -or -not $developer) { throw 'Bootstrap inputs must not be empty' }
$inputs = @("-var=billing_account_id=$billing", "-var=gitlab_project_path=$path", "-var=developer_principal=$developer")
terraform -chdir=bootstrap init -input=false
if ($LASTEXITCODE -ne 0) { throw 'Bootstrap backend initialization failed' }
terraform -chdir=bootstrap plan @inputs -out=wif.tfplan
if ($LASTEXITCODE -ne 0) { throw 'Bootstrap plan failed' }
# Review WIF, DEV CI roles, state-bucket grants and exactly four DEV/TEST human grants.
terraform -chdir=bootstrap apply wif.tfplan
if ($LASTEXITCODE -ne 0) { throw 'Bootstrap apply failed' }
terraform -chdir=bootstrap plan @inputs -detailed-exitcode
if ($LASTEXITCODE -ne 0) { throw 'Bootstrap is not yet converged' }
terraform -chdir=bootstrap output project_numbers
terraform -chdir=bootstrap output -raw gitlab_wif_provider
terraform -chdir=bootstrap output -raw gitlab_wif_audience
terraform -chdir=bootstrap output ci_plan_service_accounts
terraform -chdir=bootstrap output ci_apply_service_accounts
```

To omit human grants, omit `developer_principal` instead of using this example's
required `$developer` input. Do not commit personal emails unless intentional.
The plan/apply role grants target only DEV by default; the four human grants
target DEV/TEST projects. Review every incremental change rather than expecting
a fixed total resource count.

Then restore the existing, separate infra deployment ADC (not runtime ADC).
Remove any old `developer_principal` setting from local **infra** tfvars,
`TF_VAR_developer_principal`, saved command lines or wrappers, not bootstrap.
Review a normal DEV infra plan and apply it manually as the existing deployer:

```powershell
$sa = Read-Host 'Existing Terraform deployment service account email'
if (-not $sa) { throw 'SA email is empty: tests would run as your own user' }
gcloud auth application-default login --impersonate-service-account=$sa
terraform -chdir=infra init -reconfigure -backend-config=env/dev.gcs.tfbackend
if ($LASTEXITCODE -ne 0) { throw 'Infra backend initialization failed' }
terraform -chdir=infra plan -var-file=env/dev.tfvars -out=human-migration.tfplan
if ($LASTEXITCODE -ne 0) { throw 'Infra migration plan failed' }
# Expected human removals if present: old runtime Token Creator, demo IAP and demo OS Login.
# VM/network changes are not required for this IAM migration.
terraform -chdir=infra apply human-migration.tfplan
if ($LASTEXITCODE -ne 0) { throw 'Infra human-grant migration failed' }
```

Do not remove state entries manually or disable the VM as an IAM-migration
shortcut. New project grants remain when a demo VM is torn down. Owner/inherited
access can conceal missing explicit grants; it is not proof of least privilege.

### GitLab settings

1. Under **Settings > CI/CD > Variables**, set `GCP_OPS_PROJECT_NUMBER` to the
   numeric `ops` entry in bootstrap's `project_numbers` output. It is non-secret,
   unmasked, and must be available to unprotected MR jobs (not protected-only).
   `GCP_OPS_PROJECT_ID` defaults to `dep-2610-ops` in CI; override it if different.
   No JSON key, ADC file, access token or Telegram credential is a CI variable.
2. Under repository branch rules, protect `main`, disallow direct unreviewed
   pushes and require trusted review. Review protected tags too: apply trust
   accepts **any protected ref** of this project, not just main.
3. Use trusted Linux amd64 Docker runners with outbound HTTPS access to GitLab,
   Google APIs, HashiCorp releases/registry and the pinned container registries.
   The SDK job installs CA certificates, curl and unzip from Debian mirrors,
   then checks the pinned Terraform archive against the official SHA256 sums.
   Check support for `id_tokens` and `artifacts:access: maintainer` in your GitLab
   version. No Docker-in-Docker or privileged runner is required.
4. Restrict project membership and artifact visibility. Plans can contain state
   values; only Maintainers can download the one-day plan artifact. Review
   changes to CI, Terraform and lockfiles before running them. Same-project
   unprotected MR code still gets state **write/delete access across the shared
   bucket**. Never run untrusted fork code with parent-project credentials.
5. Keep the weekly schedule paused during any deployment handover. This pipeline
   neither publishes an image nor applies infrastructure. A future apply step
   must plan for the saved artifact's Terraform version and commit.

### Exact auth flow (Bash in the CI SDK image)

The job requests a GitLab `id_tokens.GITLAB_OIDC_TOKEN` with audience
`https://iam.googleapis.com/projects/${GCP_OPS_PROJECT_NUMBER}/locations/global/workloadIdentityPools/gitlab/providers/gitlab-com`.
The executable core below matches [.gitlab-ci.yml](../.gitlab-ci.yml); directory
permissions, input validation, checksum-verified Terraform installation and
credential cleanup are also in that file:

```bash
export GOOGLE_APPLICATION_CREDENTIALS="$CI_PROJECT_DIR/.ci-auth/wif.json"
umask 077
printf '%s' "$GITLAB_OIDC_TOKEN" > "$CI_PROJECT_DIR/.ci-auth/token"
unset GITLAB_OIDC_TOKEN
WIF_PROVIDER="projects/$GCP_OPS_PROJECT_NUMBER/locations/global/workloadIdentityPools/gitlab/providers/gitlab-com"
gcloud iam workload-identity-pools create-cred-config "$WIF_PROVIDER" \
  --service-account="ci-${CI_SA_KIND}-${CI_ENV}@${GCP_OPS_PROJECT_ID}.iam.gserviceaccount.com" \
  --credential-source-file="$CI_PROJECT_DIR/.ci-auth/token" \
  --output-file="$GOOGLE_APPLICATION_CREDENTIALS"
```

This creates an external-account credential **configuration**, not a private
key. Terraform refreshes it using the job token. Creating the config alone does
not prove exchange/impersonation; `terraform init/plan` must actually work.
Credential files are not cached or published; `after_script` removes them.

### Verify positive and negative behavior

Open a same-project MR from an unprotected branch. Expected automatic jobs:
`fmt`, `validate`, `tflint`, `terraform-test`, then `plan:dev`. The Terraform tests
are provider-mocked and make no GCP calls. The cloud plan refreshes DEV state
with `ci-plan-dev`; review and download its restricted plan artifact. There
must be **no apply job**. Fork MRs run static checks, not cloud plans.
No live pipeline success is claimed until you run this.

Run the optional manual `verify:unprotected-apply-denied` job in that MR.
It first authenticates `ci-plan-dev` and forces an access-token refresh (printed
nowhere), so a broken provider/audience cannot masquerade as the negative result.
It then tries `ci-apply-dev` with the same unprotected token. Expected: an IAM
`iam.serviceAccounts.getAccessToken` denial and a green assertion job printing
`PASS: unprotected-ref token cannot impersonate ci-apply-dev`. Unexpected success
or another failure reason makes the assertion fail; inspect its status because
the optional manual job is non-blocking. JWTs, access tokens and raw auth logs
are never printed or artifacts. This checks the trust policy, not an apply.

Offline negative cases are also in
[bootstrap mock tests](../bootstrap/tests/wif.tftest.hcl): invalid project URL,
unknown/empty CI environments, public human member and workload-as-human
principal must all be rejected. Mock tests cannot prove live IAM denial.

### Promote DEV -> TEST -> PROD

Each environment is `infra/env/<env>.tfvars` plus `<env>.gcs.tfbackend` (state
prefix `infra/<env>`); the Terraform code is identical. On the protected `main`
branch every pipeline runs `plan:<env>` and `policy:<env>` for all three
environments, and exposes manual `apply:dev` -> `apply:test` -> `apply:prod`.
Each apply needs the previous apply, uses the plan file of the same pipeline,
runs as `ci-apply-<env>`, and shares a `resource_group` with its plan job.
Before applying, the job fails if the plan file or its commit marker is
missing, or if the marker differs from `CI_COMMIT_SHA`; Terraform also rejects
a saved plan when the state changed after planning.

Prerequisites (not done by this repository change):

1. Bootstrap: set `ci_environments = ["dev", "test", "prod"]` and apply it with
   the bootstrap deployer. This creates `ci-plan/apply-test` and `-prod`, and
   grants the prod apply identity broad roles on PROD; review the plan first.
2. Copy the image into the ops repository (see the shared-registry runbook) and
   set the same `image_tag` or `image_digest` in all three tfvars.
3. GitLab (Free tier): protect `main` (maintainers push/merge); define the
   non-secret variable `GCP_OPS_PROJECT_NUMBER`. Environments, `resource_group`
   and manual jobs work on Free. Protected environments and deployment
   approvals are **paid** features, so on Free any maintainer can press
   `apply:prod`; the ordering and the manual click are the only gate.

Verify: after each apply, re-run the pipeline; `plan:<env>` must print
`No changes.`, and `terraform state list` must show the same resource addresses
in all three projects (outputs differ only by project/CIDR names). Negative:
start `apply:prod` before `apply:test` succeeded (it stays blocked), or delete
`infra/prod.plan.sha` from a copied artifact and see the guard fail. Neither
has been run live.

## Per-file schema migration

The source changed down/up latency physical types from INT32 to DOUBLE in
2026-Q2 for both network types. See [the footer evidence](source-schema.md).
The shared `ext_ookla_raw` resource is no longer managed. It is **forgotten**
with a `removed { destroy = false }` block in
[registry_migration.tf](../infra/registry_migration.tf), not destroyed: in PROD
the table was applied with `deletion_protection = true`, and Terraform refuses
to destroy a protected table even when the resource is deleted from
configuration in the same apply. The leftover table is deleted by hand after
apply (step 4). `ookla_tiles.avg_lat_ms` changes from INTEGER to FLOAT
(FLOAT64). Terraform may plan a replacement, but BigQuery supports this
specific conversion in place with `ALTER COLUMN SET DATA TYPE`; prefer that
manual migration before generating a fresh Terraform plan. It preserves the
table and its data without disabling Terraform deletion protection. The
down/up fields were already FLOAT. Raw objects are not deleted by this
migration.

1. Pause the environment's Scheduler and stop other writers. Confirm all raw
   quarters required for recovery still exist: DEV/TEST lifecycle deletion may
   already have expired them. Restore missing raw files with sync before
   removing curated data. Do not rely on `load_log` as a raw backup.
2. Review a saved plan per environment, including the correct state backend.
   Expect `ext_ookla_raw` to appear as "will no longer be managed" (forget),
   not as a delete, and, if required by the provider, only the intended
   `ookla_tiles` replacement. No dataset, bucket, secret or mart deletion is
   intended. Do not first disable `deletion_protection` on the external table:
   the `removed` block makes that preparatory apply unnecessary. If the plan
   shows `ookla_tiles` being replaced, use the in-place conversion below first.
   Keep PROD `deletion_protection = true`: the variable also protects the other
   native tables and the Cloud Run Job. Do not retry an old saved plan after
   changing the live schema. A fresh normal plan refreshes state and must no
   longer propose this schema replacement; stop if it still does.
3. P10 blocks only delete/replacement of stateful resources. The forgotten
   external table does not trigger it. If the plan still contains a reviewed
   `ookla_tiles` replacement, use `--allow-destroy` for that plan only, or a
   one-pipeline `ALLOW_DESTROY=true` override for the existing CI checker. The
   override disables **all of P10**, not an address-specific exception; review
   every deletion. Never persist it in CI configuration or project variables.
   P1-P9 and P11 remain active.
4. Apply the reviewed saved plan, then delete the forgotten external table by
   hand in each environment (the `bq rm` commands below). Then deploy a newly
   built image containing the new source contract and SQL. Existing image
   tags/digests do not gain these changes automatically. If
   `SOURCE_SCHEMA_PATH` is explicitly set, change it to `source_contract.json`
   (default `/app/schemas/source_contract.json` in the container). The retired
   `ookla_raw.json` format is no longer used.
5. Run the forced regression checks below for both eras and network types.
   If `ookla_tiles` was replaced, run a **full forced rebuild** over all raw
   quarters, not just the two regression quarters: `load_log` may still
   contain matching SUCCESS rows for data that was removed.
6. Confirm curated and mart results, one successful watermark per rebuilt
   slice, no leftover owned stages, and a clean second Terraform plan.
   Remove the destroy override (if one was used) and resume Scheduler only
   after review. Once all three environments have applied the `removed` block,
   it may be deleted from `registry_migration.tf` in a later change.

**In-place latency conversion (BigQuery SQL workspace, PROD example).**
Use an authorized operator identity; this procedure does not grant new access.
Keep Scheduler paused and ensure no ingestion execution is running. Set the
query processing location to `europe-southwest1` and maximum bytes billed to
`10737418240` for each query job. Inspect the live column first:

```sql
SELECT column_name, data_type, is_nullable
FROM `dep-2610-prod.network_perf.INFORMATION_SCHEMA.COLUMNS`
WHERE table_name = 'ookla_tiles' AND column_name = 'avg_lat_ms';
```

Expect exactly one nullable `INT64` column before conversion. If it is already
`FLOAT64`, skip the ALTER. Stop on any other result. For `INT64`, execute:

```sql
ALTER TABLE `dep-2610-prod.network_perf.ookla_tiles`
ALTER COLUMN avg_lat_ms SET DATA TYPE FLOAT64;
```

Repeat the metadata query and expect `FLOAT64`. This widening conversion uses
approximate floating-point representation; it does not recover fractional
values absent from older integer data. Forced application regression checks
are still required. See the supported conversions in the
[BigQuery DDL reference](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/data-definition-language#alter_column_set_data_type_statement).

Start a new main pipeline without a destroy override and inspect every plan:
`ookla_tiles` must have no delete/create actions and PROD protection must remain
true. A previous failed apply may have committed other changes; review the new
plan rather than assuming it contains the original actions. Promote DEV, TEST,
then PROD using the new pipeline's saved plans. After a successful PROD apply,
check whether `ext_ookla_raw` remains in the live dataset and delete it only
after confirming it is no longer in that environment's Terraform state.
The forget operation may already have completed during the failed apply.

**PowerShell, after the apply of each environment.** The table holds only
metadata; the raw objects in the bucket are not touched. Run it only after the
saved plan was applied, otherwise Terraform will see an unexpected deletion:

```powershell
$project = 'dep-2610-dev'   # repeat for dep-2610-test and dep-2610-prod
bq --project_id=$project --location=europe-southwest1 show "${project}:network_perf.ext_ookla_raw"
if ($LASTEXITCODE -ne 0) { throw 'Expected the forgotten table to still exist before manual deletion.' }
bq --project_id=$project --location=europe-southwest1 rm -f -t "${project}:network_perf.ext_ookla_raw"
if ($LASTEXITCODE -ne 0) { throw 'Table deletion failed.' }
# Negative: the table must be gone.
bq --project_id=$project --location=europe-southwest1 show "${project}:network_perf.ext_ookla_raw"
if ($LASTEXITCODE -eq 0) { throw 'Table still exists.' }
```

`terraform -chdir=infra state list` for the environment must not contain
`ext_ookla_raw`, and a second plan must be empty.

**PowerShell, repository root**, using the deployer credentials described in
[setup](setup.md), with `$envName` set explicitly:

```powershell
$envName = 'dev'
$env:TF_DATA_DIR = Join-Path (Resolve-Path .\infra).Path ".terraform-$envName"
terraform -chdir=infra init -reconfigure -lockfile=readonly "-backend-config=env\$envName.gcs.tfbackend"
if ($LASTEXITCODE -ne 0) { throw 'Backend initialization failed.' }
terraform -chdir=infra state list
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect environment state.' }
terraform -chdir=infra plan "-var-file=env\$envName.tfvars" "-out=$envName.tfplan"
if ($LASTEXITCODE -ne 0) { throw 'Migration plan failed.' }
terraform -chdir=infra show -json "$envName.tfplan" > "infra\$envName.plan.json"
if ($LASTEXITCODE -ne 0) { throw 'Cannot render migration plan.' }

# P10 must reject only if the plan still contains a stateful delete/replacement
# (for example the reviewed ookla_tiles replacement). The forgotten external
# table must not cause it.
.\app\.venv\Scripts\python.exe scripts\check_plan.py "infra\$envName.plan.json" --env $envName
$p10 = $LASTEXITCODE
if ($p10 -eq 0) { Write-Output 'P10 passed: no stateful delete/replacement in this plan.' }
elseif ($p10 -ne 1) { throw 'Policy check failed unexpectedly.' }
# Only after reviewing all deletes, and only if the previous check exited 1:
if ($p10 -eq 1) {
  .\app\.venv\Scripts\python.exe scripts\check_plan.py "infra\$envName.plan.json" --env $envName --allow-destroy
  if ($LASTEXITCODE -ne 0) { throw 'Another policy rule failed; do not apply.' }
}
terraform -chdir=infra apply "$envName.tfplan"
```

For the application checks, use runtime impersonation/configuration from
[raw ingestion](#raw-ingestion) and [build configuration](../app/BUILD.md),
not the deployer identity. Local commands use the updated source checkout;
also verify the deployed image separately. These are billed loads/queries:
raw stages temporarily store the full global file and every query has the
configured `MAXIMUM_BYTES_BILLED`.

```powershell
foreach ($layer in 'mobile', 'fixed') {
  foreach ($quarter in '2026-Q1', '2026-Q2') {
    .\app\.venv\Scripts\python.exe -m ingest build --type $layer --quarter $quarter --force
    if ($LASTEXITCODE -ne 0) { throw "Forced regression failed: $layer $quarter" }
  }
}
# Required after replacement, provided every needed raw file is present:
.\app\.venv\Scripts\python.exe -m ingest build --type all --force
if ($LASTEXITCODE -ne 0) { throw 'Full raw rebuild failed.' }
```

Expected: DQ passes and each forced slice publishes even when ETags match;
all three latency columns are FLOAT64, source row counts come from each
individual loaded file, and both network types remain present for each rebuilt
quarter. A subsequent non-forced build skips those slices (`partitions=0`,
`DQ=NOT_RUN`) but still rebuilds the mart. The offline negative string-latency
footer tests must raise a column/found/allowed-types error before transfer.
These live checks have not been performed by the assistant.

## Private network and IAP demo

All commands in this section are **PowerShell on Windows**, repository root.
They are manual instructions, not a record of deployment or SSH verification.
Use an authorized **separate Terraform deployer** via impersonated ADC for
provider/backend, as described in [setup](setup.md); do not use the ingestion
runtime or create a service-account key. Confirm Compute Engine/IAP APIs are
enabled by the existing bootstrap and check the
[network module](../modules/network/README.md) before applying.

### Remove only the auto-created default network

Without an organization policy, GCP may create a `default` network in each
environment project. This repository neither imports nor deletes that network.
Run the following read-only inventory for **each** of `dep-2610-dev`,
`dep-2610-test`, `dep-2610-prod`; never use the ops project in this procedure.

```powershell
$project = 'dep-2610-dev' # Repeat deliberately for TEST and PROD.
$admin = Read-Host 'Authorized network administration service account email'
if (-not $admin) { throw 'Network administrator SA email is empty' }
if ($project -notin @('dep-2610-dev', 'dep-2610-test', 'dep-2610-prod')) {
  throw 'Expected an environment project, not ops'
}
gcloud compute networks list --project=$project --impersonate-service-account=$admin --filter='name=default' --format=json
if ($LASTEXITCODE -ne 0) { throw 'Default network inventory failed' }
$instances = gcloud compute instances list --project=$project --impersonate-service-account=$admin --format=json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Instance inventory failed' }
$users = @($instances | Where-Object {
  @($_.networkInterfaces | Where-Object { $_.network -match '/networks/default$' }).Count -gt 0
})
if ($users.Count -gt 0) {
  $users | Select-Object name, zone
  throw 'Default network is used by VMs; stop and review migration separately'
}
$rules = @(gcloud compute firewall-rules list --project=$project --impersonate-service-account=$admin --format=json |
  ConvertFrom-Json | ForEach-Object { $_ } | Where-Object { $_.network -match '/networks/default$' })
if ($LASTEXITCODE -ne 0) { throw 'Firewall inventory failed' }
$subnets = @(gcloud compute networks subnets list --project=$project --impersonate-service-account=$admin --format=json |
  ConvertFrom-Json | ForEach-Object { $_ } | Where-Object { $_.network -match '/networks/default$' })
if ($LASTEXITCODE -ne 0) { throw 'Subnet inventory failed' }
$rules | Select-Object name, network
$subnets | Select-Object name, region, ipCidrRange
gcloud compute routers list --project=$project --impersonate-service-account=$admin --format=json
if ($LASTEXITCODE -ne 0) { throw 'Router inventory failed' }
```

Check the console for all network dependencies as well (load balancers,
forwarding rules, VPN/peerings, reserved addresses). If anything depends on the
default network, stop; do not delete/migrate unrelated resources. An absent
network is already clean and needs no deletion. After reviewing the exact
default-network rules/subnets above, the following is destructive:

```powershell
$confirmation = Read-Host "Type $project to delete only its reviewed default network, firewall rules and subnets"
if ($confirmation -cne $project) { throw 'Deletion not confirmed' }
foreach ($rule in $rules) {
  gcloud compute firewall-rules delete $rule.name --project=$project --impersonate-service-account=$admin --quiet
  if ($LASTEXITCODE -ne 0) { throw 'Default firewall rule deletion failed; stop' }
}
foreach ($subnet in $subnets) {
  $subnetRegion = ($subnet.region -split '/')[-1]
  gcloud compute networks subnets delete $subnet.name --region=$subnetRegion --project=$project --impersonate-service-account=$admin --quiet
  if ($LASTEXITCODE -ne 0) { throw 'Default subnet deletion failed; stop' }
}
gcloud compute networks delete default --project=$project --impersonate-service-account=$admin --quiet
if ($LASTEXITCODE -ne 0) { throw 'Default network deletion failed; inspect remaining dependencies' }
gcloud compute networks list --project=$project --impersonate-service-account=$admin --filter='name=default' --format='value(name)'
if ($LASTEXITCODE -ne 0) { throw 'Default network absence check failed' }
```

Expected: final listing is empty. Do not delete the new `dev-private` (or
TEST/PROD equivalent) VPC or any other network. Do not run the deletion block
with stale inventory or after switching `$project`; rerun the inventory and
review for each environment. No deletion was executed by the assistant.

### Deploy the network with no VM

DEV tfvars keeps `enable_iap_demo_vm = false`. Its CIDR is `10.10.0.0/24`;
root defaults for TEST/PROD are `10.20.0.0/24` and `10.30.0.0/24`.
Cloud Run has no VPC attachment before or after this apply.

```powershell
$project = 'dep-2610-dev' # Do not reuse the last project from default-network cleanup.
terraform -chdir=infra init -reconfigure -input=false '-backend-config=env/dev.gcs.tfbackend'
terraform -chdir=infra plan '-var-file=env/dev.tfvars' -out=network-dev.tfplan
if ($LASTEXITCODE -ne 0) { throw 'Network plan failed' }
# Review before applying: four network additions, no VM/IAP IAM, if earlier steps are current.
terraform -chdir=infra apply network-dev.tfplan
if ($LASTEXITCODE -ne 0) { throw 'Network apply failed' }
gcloud compute networks describe dev-private --project=$project --impersonate-service-account=$admin --format=json
if ($LASTEXITCODE -ne 0) { throw 'Custom VPC inspection failed' }
gcloud compute networks subnets describe dev-private --region=europe-southwest1 --project=$project --impersonate-service-account=$admin --format=json
if ($LASTEXITCODE -ne 0) { throw 'Subnet inspection failed' }
```

Expected: `autoCreateSubnetworks=false`, `routingConfig.routingMode=REGIONAL`,
correct CIDR/region and `privateIpGoogleAccess=true`. If an earlier network apply
created `dev-deny-ingress`, the plan replaces it with `dev-deny-all-ingress`;
during the swap the implied 65535 deny still blocks ingress. Review other
changes separately; do not bundle replacements, IAM drift or unrelated updates
into this step.

### Enable briefly and verify IAP SSH

This DEV VM is **billable in Europe**, not free tier. Reserve enough time for
both the test and teardown. Set `enable_iap_demo_vm = true` temporarily in DEV
tfvars. First provision `developer_principal` in the locally applied bootstrap
(for example `user:you@example.com`); it grants project-level Token Creator and
IAP in DEV/TEST, not PROD, independently of this VM flag. Do not pass this input
to infra or commit a personal identity without intending to publish it.

Review/apply another plan as the deployer. Expect one VM and no human IAM
additions. The VM is `dev-iap-demo`, `e2-micro`, `europe-southwest1-a`,
Debian 12, 10 GiB disk.

For SSH, use **the developer's gcloud user login**, not deployer impersonation.
The developer needs bootstrap IAP access plus existing OS Login authorization
and Compute discovery permissions (`compute.projects.get` and instance lookup).
This repository no longer creates an explicit OS Login grant; an administrator
must verify/provision that separately for a non-Owner operator. Non-organization users normally do not
need OS Login External User; organization-owned projects may require that
separate organization-level prerequisite. SSH keys are login keys, never
service-account keys; gcloud may generate/use a local SSH key.

```powershell
$project = 'dep-2610-dev'
$vm = 'dev-iap-demo'
$zone = 'europe-southwest1-a'
gcloud auth list --filter=status:ACTIVE --format='value(account)'
gcloud config get-value auth/impersonate_service_account
# Confirm this is the intended developer; also check no CLOUDSDK_AUTH_* override.
gcloud compute ssh $vm --project=$project --zone=$zone --tunnel-through-iap --ssh-key-expire-after=10m --command='whoami'
if ($LASTEXITCODE -ne 0) { throw 'Positive IAP SSH check failed' }
$vmInfo = gcloud compute instances describe $vm --project=$project --zone=$zone --format=json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Demo VM description failed' }
if (@($vmInfo.networkInterfaces | Where-Object {
  $_.accessConfigs -or $_.ipv6AccessConfigs
}).Count -gt 0) { throw 'Unexpected external IP access configuration' }
$vmInfo | Select-Object name, machineType, tags, shieldedInstanceConfig, metadata, serviceAccounts
$diskName = (($vmInfo.disks | Where-Object { $_.boot }).source -split '/')[-1]
```

Expected: SSH prints a Linux username, no access configs, no attached service
account, `iap-ssh` tag, OS Login enabled and all three Shielded settings true.
Use a non-Owner developer to isolate IAM effectiveness; inherited Owner access
can authorize a tunnel regardless of this explicit member.

### Negative firewall test and immediate teardown

Close existing SSH sessions. Disable only the IAP allow rule temporarily using
the administrator identity; a new SSH tunnel must then fail. Restore the rule
in `finally`, even when the negative test fails:

```powershell
try {
  gcloud compute firewall-rules update dev-allow-iap-ssh --project=$project --impersonate-service-account=$admin --disabled
  if ($LASTEXITCODE -ne 0) { throw 'Cannot disable IAP rule; negative test not started' }
  # Allow firewall propagation before opening a NEW connection.
  Start-Sleep -Seconds 30
  gcloud compute ssh $vm --project=$project --zone=$zone --tunnel-through-iap --ssh-flag='-oConnectTimeout=10' --command='whoami'
  if ($LASTEXITCODE -eq 0) { throw 'Unexpected SSH success with IAP allow disabled' }
} finally {
  gcloud compute firewall-rules update dev-allow-iap-ssh --project=$project --impersonate-service-account=$admin --no-disabled
  if ($LASTEXITCODE -ne 0) { throw 'Firewall restore failed; restore explicitly before proceeding' }
}
```

Expected: the negative connection fails because IAP cannot reach backend port
22 (for example backend connection failure), not because of missing user
credentials, expired keys or OS Login denial. Repeat positive SSH after
restoration to distinguish a firewall denial from unrelated auth failures.
The explicit deny and implied ingress deny remain active throughout.

**Immediately set `enable_iap_demo_vm = false` in DEV tfvars**, then plan and
apply as the deployer, retaining the intended developer setting:

```powershell
terraform -chdir=infra plan '-var-file=env/dev.tfvars' -out=network-dev.tfplan
if ($LASTEXITCODE -ne 0) { throw 'Demo teardown plan failed' }
# Review: destroy demo VM and its two instance IAM members; boot disk auto-deletes.
terraform -chdir=infra apply network-dev.tfplan
if ($LASTEXITCODE -ne 0) { throw 'Demo teardown failed; billable resources may remain' }
gcloud compute instances list --project=$project --impersonate-service-account=$admin --filter="name=$vm" --format='value(name)'
if ($LASTEXITCODE -ne 0) { throw 'VM absence check failed' }
gcloud compute disks list --project=$project --impersonate-service-account=$admin --filter="name=$diskName" --format='value(name)'
if ($LASTEXITCODE -ne 0) { throw 'Boot disk absence check failed' }
terraform -chdir=infra plan '-var-file=env/dev.tfvars' -detailed-exitcode
if ($LASTEXITCODE -ne 0) { throw 'Post-teardown plan is not clean' }
```

Expected: VM/disk listings are empty, no demo IAM remains, the four network
resources remain, and the final plan has no changes. Stopping the VM is not
equivalent to destroying it: persistent disk can still incur charges. If
anything fails, treat teardown as outstanding; there is no automatic TTL.

## Scheduled Cloud Run ingestion

These are **manual PowerShell on Windows** procedures, not evidence of a live
deployment. See [module inputs and limits](../modules/ingest_job/README.md).
Use `enable_ingest_job = false` for initial image-first provisioning: bootstrap
creates the shared ops repository; infra creates no job or schedule until enabled.
For an existing job, follow the migration below before changing its image.
Do not use routine `-target` applies or a placeholder public image.

### Shared ops registry migration

This is **not an in-place project move**: repositories/images cannot be relocated
by changing a Terraform project argument. Bootstrap creates `ops-ingest` in ops.
[registry_migration.tf](../infra/registry_migration.tf) removes the old
`module.ingest_job.google_artifact_registry_repository.ingest` from environment
state with `destroy = false`, retaining its images and rollback path. It does
not copy them. Retained old repositories still incur storage charges.

1. Pause scheduled launches and serialize deployments. Use the authorized local
   bootstrap administrator's ADC and your existing billing/GitLab/human inputs.
   Run `terraform -chdir=bootstrap init`, review/save the bootstrap plan, then
   apply it. Expect the shared ops repository, three Cloud Run service identities,
   repository-level Reader grants for all three agents, and Reader for configured
   CI apply SAs. Environment registry Admin/Reader project grants on CI identities
   are removed. Normal Cloud Run service-agent project roles must remain intact.
2. Authorize a publisher separately with Reader on the old repository and Writer
   on the new one. For a manually impersonated deployer, separately grant Reader
   on the new repository too; bootstrap configures that automatically only for
   the CI apply identities. Do not grant registry access to the runtime SA.
3. Copy the exact existing release, not a rebuild. The following **PowerShell**
   uses Buildx's single-source carbon-copy mode and rejects a changed digest.
   Ensure your installed Buildx supports `--prefer-index=false`.

```powershell
$source = Read-Host 'Old complete image URI, including its immutable tag or digest'
$tag = Read-Host 'Release tag to preserve in ops (not latest)'
$publisher = Read-Host 'Authorized publisher service account email'
if (-not $source -or -not $tag -or -not $publisher) { throw 'Copy inputs must not be empty' }
$repository = terraform -chdir=bootstrap output -raw ingest_image_repository
if ($LASTEXITCODE -ne 0 -or -not $repository) { throw 'Shared repository output is missing' }
$destination = "${repository}:$tag"
$previousImpersonation = $env:CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT
try {
  $env:CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT = $publisher
  $registries = (@(($source -split '/')[0], ($repository -split '/')[0]) | Select-Object -Unique) -join ','
  gcloud auth configure-docker $registries --quiet
  if ($LASTEXITCODE -ne 0) { throw 'Docker auth configuration failed' }
  $sourceManifest = docker buildx imagetools inspect $source --format '{{json .Manifest}}'
  if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect source manifest' }
  $digest = ($sourceManifest | ConvertFrom-Json).digest
  if ($digest -notmatch '^sha256:[a-f0-9]{64}$') { throw 'Source digest is invalid' }
  # Resolve the source to its immutable digest before copying.
  $sourceRepository = ($source -split '@')[0] -replace ':[^/]+$', ''
  docker buildx imagetools create --prefer-index=false --tag $destination "${sourceRepository}@$digest"
  if ($LASTEXITCODE -ne 0) { throw 'Image copy failed; do not change job image' }
  $destinationManifest = docker buildx imagetools inspect $destination --format '{{json .Manifest}}'
  if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect destination manifest' }
  if (($destinationManifest | ConvertFrom-Json).digest -ne $digest) {
    throw 'Digest changed during copy; do not deploy this artifact'
  }
  Write-Output "Verified shared image: ${repository}@$digest"
} finally {
  if ($null -eq $previousImpersonation) {
    Remove-Item Env:\CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT -ErrorAction SilentlyContinue
  } else {
    $env:CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT = $previousImpersonation
  }
}
```

4. Set `image_repository` to bootstrap's output and `image_digest` to the verified
   digest in each environment's tfvars (keep the release `image_tag` for publishing
   and traceability; digest takes precedence). Restore authorized infra-deployer
   impersonated ADC, review a normal full environment plan, and apply manually.
   The old repository must say it is no longer managed but **not destroyed**.
   No stateful deletion override is needed for this migration. Do not delete old
   images until rollout and rollback verification succeeds.
5. **Positive verification:** inspect bootstrap's `cloud_run_service_agents`
   output and repository IAM. Expected agents are the environment project numbers,
   not the ops number. Use `gcloud artifacts repositories get-iam-policy ops-ingest
   --project=dep-2610-ops --location=europe-southwest1` (PowerShell, one command).
   Each must have Reader only at this repository. Describe each deployed job:
   `gcloud run jobs describe <env>-ingest --project=<environment-project>
   --region=europe-southwest1 --format=yaml`; its image must be the same
   `.../dep-2610-ops/ops-ingest/ingest@sha256:...`. Execute a supervised DEV run
   using the existing runbook and confirm no image-pull permission failure.
6. **Negative verification:** as an authorized administrator, impersonate a
   dedicated probe identity with Reader only on `ops-ingest` and no inherited
   Writer/Admin. Attempting `docker push` to a fresh synthetic probe tag must
   fail with permission denied. An Owner's access or merely creating a
   credential configuration is not a valid negative test. Do not remove a live
   service agent's Reader grant to test this.

Local mock tests verify configured scopes/URIs/digest validation, not live
cross-project pulls. Bootstrap's `prevent_destroy` protects the shared repository
from accidental Terraform deletion; it is not an IAM deletion deny.

### 1. Prerequisites and bootstrap-owned repository

Infra, ingest and transform must already be deployed and verified, including
raw Parquet aligned with the pinned external-table schema, native tables, runtime grants and
an enabled `alert-telegram` version. Confirm the existing bootstrap API
configuration has actually enabled `run.googleapis.com`,
`artifactregistry.googleapis.com`, `cloudscheduler.googleapis.com`,
`secretmanager.googleapis.com` and `logging.googleapis.com`. Cloud Run and
Scheduler service agents must retain their normal Google-managed service-agent
roles. Do not grant those roles to workload accounts.

Use the authorized Terraform deployer via impersonated ADC from
[setup](setup.md). It additionally needs shared-repository read and job/schedule management,
job IAM get/set, service-account creation and `iam.serviceAccounts.actAs` on
runtime and scheduler accounts. The environment Cloud Run service agent pulls
from ops using bootstrap's repository-level Reader; the runtime does not need it.
An authorized publisher needs repository-scoped Artifact Registry Writer and
your user must be allowed to impersonate it. This step does not create or grant
publisher/deployer identities.

Apply bootstrap first using its existing inputs to create the shared repository
and all three service-agent Reader grants. Choose a unique `image_tag` in DEV
tfvars; keep `image_digest = null` until a real published digest is available.
Keep `enable_ingest_job = false` for first provisioning.

```powershell
terraform -chdir=infra init -reconfigure -input=false '-backend-config=env/dev.gcs.tfbackend'
terraform -chdir=infra plan '-var-file=env/dev.tfvars' -out=dev.tfplan
if ($LASTEXITCODE -ne 0) { throw 'Infrastructure plan failed' }
# Review the plan before continuing: no job/scheduler yet.
terraform -chdir=infra apply dev.tfplan
if ($LASTEXITCODE -ne 0) { throw 'Infrastructure apply failed' }
```

No environment repository is created. Stop on unexpected deletions or environment
changes. Full fresh infrastructure needs the earlier external-table bootstrap
sequence; this is not a workaround for missing raw data.

### 2. Build and push the tfvars-selected tag

The build context is the repository root. Use Docker Desktop's Linux engine.
The environment override below makes the gcloud Docker credential helper use
the publisher identity, rather than silently pushing as your user. It restores
the prior override afterward and never creates a service-account key.

```powershell
$repository = terraform -chdir=bootstrap output -raw ingest_image_repository
if ($LASTEXITCODE -ne 0 -or -not $repository) { throw 'Image repository output is missing' }
$tag = Read-Host 'Unique release image_tag from tfvars'
if (-not $tag -or $tag -eq 'latest') { throw 'Explicit release tag is required' }
$image = "${repository}:$tag"
$registry = ($image -split '/')[0]
$publisher = Read-Host 'Authorized image publisher service account email'
if (-not $publisher) { throw 'Publisher SA email is empty' }
$previousImpersonation = $env:CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT
try {
  $env:CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT = $publisher
  gcloud auth configure-docker $registry --quiet
  if ($LASTEXITCODE -ne 0) { throw 'Docker credential helper configuration failed' }
  docker build -f app\Dockerfile -t $image .
  if ($LASTEXITCODE -ne 0) { throw 'Image build failed' }
  docker run --rm --network none $image run --help
  if ($LASTEXITCODE -ne 0) { throw 'Offline image smoke test failed' }
  docker push $image
  if ($LASTEXITCODE -ne 0) { throw 'Image push failed; do not enable the job' }
} finally {
  if ($null -eq $previousImpersonation) {
    Remove-Item Env:\CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT -ErrorAction SilentlyContinue
  } else {
    $env:CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT = $previousImpersonation
  }
}
```

Expected: push succeeds and reports a digest. Tags are immutable; use a new tag
for each changed image. Do not overwrite an existing release tag. The cleanup
policy deletes only untagged versions older than seven days. **Tagged releases
accumulate** because immutable tagged artifacts are intentionally retained.
Review registry storage before repeated releases.

### 3. Enable the job; verify manually before activating the schedule

Set `enable_ingest_job = true` in DEV tfvars. Temporarily set
`scheduler_paused = true` for the first trial. Review `min_quarter`, bbox, query
budget and DQ thresholds before launching: default `run` processes **both**
network types and all quarters from `MIN_QUARTER`, not just the Iberian subset
of source bytes. Source objects are global and can be large.

```powershell
terraform -chdir=infra plan '-var-file=env/dev.tfvars' -out=dev.tfplan
if ($LASTEXITCODE -ne 0) { throw 'Job plan failed' }
# Review: four additions (job, scheduler SA, job IAM member, paused schedule).
terraform -chdir=infra apply dev.tfplan
if ($LASTEXITCODE -ne 0) { throw 'Job apply failed' }

$project = 'dep-2610-dev'
$region = 'europe-southwest1'
$schedulerRegion = 'europe-west1'
$job = terraform -chdir=infra output -raw ingest_job_name
if ($LASTEXITCODE -ne 0 -or -not $job) { throw 'Job output is missing' }
$schedule = terraform -chdir=infra output -raw scheduler_job_name
if ($LASTEXITCODE -ne 0 -or -not $schedule) { throw 'Schedule output is missing' }
$operator = Read-Host 'Authorized execution operator service account email'
if (-not $operator) { throw 'Operator SA email is empty' }
gcloud run jobs describe $job --project=$project --region=$region --impersonate-service-account=$operator --format=json
if ($LASTEXITCODE -ne 0) { throw 'Job inspection failed' }
gcloud scheduler jobs describe $schedule --project=$project --location=$schedulerRegion --impersonate-service-account=$operator --format=json
if ($LASTEXITCODE -ne 0) { throw 'Scheduler inspection failed' }
```

Verify image tag, runtime identity, `args: [run]`, all non-secret env settings,
Madrid job / Belgium schedule, OAuth (not OIDC), and a resource-name-only
`ALERT_SECRET`. There must be no Cloud Run secret `valueSource`.
The operator needs job get/run and run-with-overrides for the scoped command,
plus Scheduler inspection and logging access for the following checks.
No extra operator grants are made by this Terraform configuration.

Start with one already verified quarter/type (change the example deliberately).
The `--args` flag replaces the container arguments for **this execution only**:

```powershell
gcloud run jobs execute $job --project=$project --region=$region --impersonate-service-account=$operator '--args=run,--type,mobile,--quarter,2023-Q3' --wait
if ($LASTEXITCODE -ne 0) { throw 'Scoped Cloud Run execution failed' }
```

After checking cost, duration, memory and DQ, the full configured command is:

```powershell
gcloud run jobs execute $job --project=$project --region=$region --impersonate-service-account=$operator --wait
if ($LASTEXITCODE -ne 0) { throw 'Cloud Run execution failed' }
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=$job" --project=$project --impersonate-service-account=$operator --limit=50 --format=json
if ($LASTEXITCODE -ne 0) { throw 'Job log read failed' }
```

Expected: execution succeeds; structured JSON logs show sync, quality,
publication and mart success without secret values or Telegram Bot API URLs.
Check the Telegram chat separately: notification failure is a warning, not a
failed data pipeline. Confirm `load_log` and a populated mart with the runtime
identity (DEV user needs its existing non-PROD Token Creator grant):

```powershell
$runtime = terraform -chdir=infra output -raw runtime_service_account_email
if ($LASTEXITCODE -ne 0 -or -not $runtime) { throw 'Runtime SA output is empty' }
$sql = 'SELECT network_type, quarter_start, status, loaded_row_count, loaded_at FROM `dep-2610-dev.network_perf.load_log` WHERE quarter_start = DATE ''2023-07-01'' AND network_type = ''mobile'' ORDER BY loaded_at DESC'
bq --impersonate_service_account=$runtime --project_id=$project --location=$region query --use_legacy_sql=false --maximum_bytes_billed=10737418240 $sql
if ($LASTEXITCODE -ne 0) { throw 'Load-log verification failed' }
$sql = 'SELECT COUNT(*) AS mart_rows, SUM(total_tests) AS tests FROM `dep-2610-dev.network_perf.mart_quarterly_performance` WHERE quarter_start = DATE ''2023-07-01'' AND network_type = ''mobile'''
bq --impersonate_service_account=$runtime --project_id=$project --location=$region query --use_legacy_sql=false --maximum_bytes_billed=10737418240 $sql
if ($LASTEXITCODE -ne 0) { throw 'Mart verification failed' }
```

Expected: a latest SUCCESS row with positive `loaded_row_count`, positive
`mart_rows` / tests, and a Telegram summary. On an unchanged rerun the load-log
timestamp need not change; mart rebuilds still run. See the independent
calculation and network-type preservation checks later in this runbook.

### 4. Negative invocation test and schedule activation

The runtime must not be able to launch its own job. First obtain a runtime
token successfully, then call `:run` directly so a failed description read
cannot masquerade as the required invocation denial. Do not print the token,
enable tracing or save it. This is a DEV-only test: if IAM is unexpectedly
broad, the request can actually launch the full job; check for overlap first.

```powershell
$token = gcloud auth print-access-token --impersonate-service-account=$runtime
if ($LASTEXITCODE -ne 0 -or -not $token) { throw 'Runtime impersonation failed; not an invocation test' }
$status = $null
try {
  try {
    $null = Invoke-RestMethod -Method Post -Uri "https://run.googleapis.com/v2/projects/$project/locations/$region/jobs/${job}:run" -Headers @{ Authorization = "Bearer $token" } -ContentType 'application/json' -Body '{}'
    $status = 200
  } catch {
    if ($null -eq $_.Exception.Response) { throw 'No HTTP response; invocation denial is unverified' }
    $status = [int]$_.Exception.Response.StatusCode
  }
} finally {
  $token = $null
}
if ($status -ne 403) { throw "Expected jobs:run HTTP 403; got $status. Inspect inherited IAM and any unexpected execution." }
```

Only the scheduler gets an explicit job invoker grant here; inherited admin
roles can still allow other identities. A 404, failed impersonation or disabled
API is not a passing denial test.

Ensure no execution is active, then test the actual Scheduler identity path
while paused (`jobs run` dispatches on demand). The operator additionally needs
`cloudscheduler.jobs.run`:

```powershell
gcloud scheduler jobs run $schedule --project=$project --location=$schedulerRegion --impersonate-service-account=$operator
if ($LASTEXITCODE -ne 0) { throw 'Scheduler dispatch failed' }
gcloud run jobs executions list --job=$job --project=$project --region=$region --impersonate-service-account=$operator --limit=5
if ($LASTEXITCODE -ne 0) { throw 'Execution listing failed' }
```

Inspect that execution to completion and repeat the log/data/Telegram checks.
Scheduler success only means the asynchronous launch request was accepted.
Single task/parallelism one does **not** prevent overlapping executions, and
Scheduler is not exactly-once. Do not launch a manual run during a scheduled one.
No distributed lock is implemented.

After verification, change DEV `scheduler_paused` to false and review/apply
another plan. The schedule is Monday 06:00 Europe/Madrid (following daylight
saving time); TEST/PROD default to paused when
the override is null. The initial task has 1 vCPU / 1 GiB / 3600 seconds and zero
task retries (at most one may be configured); Scheduler retries are disabled.
The app has bounded transfer
retries. No measured source-file size is recorded, so these are streaming-based
estimates, not a guaranteed backfill capacity. Record DEV peak memory and
duration before increasing limits.

### Updating or stopping

For an update, build/push a new unique tag to the shared ops repository once.
Verify its digest and set that same `image_digest` in every environment before
reviewing/applying each job change. If using tag mode, clear `image_digest`
explicitly; otherwise changing `image_tag` does not change the pinned job image.
Do not apply a job pointing to an unpublished image. Keep releases serialized;
immutable tags retain rollback artifacts.

To stop future launches, set `scheduler_paused = true` and review/apply; this
does not cancel active executions. Setting `enable_ingest_job = false` removes
the job, schedule, scheduler identity and grant (subject to deletion protection),
and does not affect the bootstrap-owned repository. PROD protection requires an explicit reviewed change
before deleting the job. Repository cleanup is asynchronous and tagged images
continue to consume storage.

## Telegram alert secret

The `alert-telegram` secret holds one JSON object:

```json
{"bot_token":"<BotFather token>","chat_id":"<numeric chat id>"}
```

Terraform creates only the empty container and the runtime read grant; values
are added manually as secret versions. See [security.md](security.md#alert-telegram-secret)
for the threat model.

Safety rules for every block below:

- Start a dedicated session with `pwsh -NoProfile`, run the block, then `exit`.
  Caught errors stay in `$Error`, and the Telegram request URL contains the token.
- Do not enable transcripts or HTTP debug tracing.
- Never pass the token as a CLI argument. `Read-Host` input is not written to PSReadLine history.
- The temporary file is restricted to your Windows user before plaintext is written
  and removed in `finally`. Deletion is not secure erasure.

### Prerequisites

- `terraform -chdir=infra apply` has created the secret, and the infra backend is initialised.
- Your gcloud account, or an authorised provisioning service account, has
  `secretmanager.versions.add` on the secret. The runtime SA intentionally lacks it.
- For the test step: your account has the non-PROD Token Creator grant on the
  DEV/TEST project (bootstrap `developer_principal`). The deployer ADC can run `terraform state pull`.
- You have sent at least one message to the bot from the target chat, so `getUpdates` can show its chat ID.

### Add a secret version

```powershell
& {
  $project = 'dep-2610-dev'
  $secret = 'alert-telegram'
  $provisioner = Read-Host 'Provisioning SA email (Enter = your own gcloud account)'
  $secureToken = Read-Host 'Bot token from BotFather' -AsSecureString
  $file = [IO.Path]::GetTempFileName()
  $pointer = [IntPtr]::Zero
  $token = $null
  $json = $null
  try {
    $acl = Get-Acl -LiteralPath $file
    $acl.SetAccessRuleProtection($true, $false)
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $acl.SetAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid, 'FullControl', 'Allow'))
    Set-Acl -LiteralPath $file -AclObject $acl -ErrorAction Stop

    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
    $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    if ($token -notmatch '^\d+:[A-Za-z0-9_-]+$') { throw 'Input does not look like a Telegram bot token.' }

    try {
      $updates = Invoke-RestMethod -Uri "https://api.telegram.org/bot$token/getUpdates" -ErrorAction Stop
    } catch {
      throw "getUpdates failed: HTTP $([int]$_.Exception.Response.StatusCode). Details suppressed (URL contains the token)."
    }
    $chats = @($updates.result | ForEach-Object { $_.message.chat } | Where-Object { $_ } |
      Sort-Object id -Unique)
    if ($chats.Count -gt 0) {
      $chats | Format-Table id, type, title, username | Out-Host
    } else {
      Write-Warning 'No recent updates. Send the bot a message and rerun, or enter a known chat ID.'
    }
    $chatId = Read-Host 'Chat ID to alert'
    if ($chatId -notmatch '^-?\d+$') { throw 'Chat ID must be an integer (groups are negative).' }

    $json = @{ bot_token = $token; chat_id = $chatId } | ConvertTo-Json -Compress
    [IO.File]::WriteAllText($file, $json, [Text.UTF8Encoding]::new($false))
    $gcloudArgs = @('secrets', 'versions', 'add', $secret, "--project=$project", "--data-file=$file")
    if ($provisioner) { $gcloudArgs += "--impersonate-service-account=$provisioner" }
    gcloud @gcloudArgs
    if ($LASTEXITCODE -ne 0) { throw 'Adding the secret version failed.' }
  } finally {
    if ($pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    $token = $null
    $json = $null
    $secureToken.Dispose()
    Remove-Item -LiteralPath $file -ErrorAction SilentlyContinue
  }
}
```

Expected: `Created version [N] of the secret [alert-telegram].` Note `N` for rotation.

### Test a secret version

This reads the secret **as the runtime SA**, so it proves the secret-scoped IAM
grant works. It also checks that the token is not in Terraform state, then sends
a test message.

```powershell
& {
  $project = 'dep-2610-dev'
  $secret = 'alert-telegram'
  $version = Read-Host 'Version to test (Enter = latest)'
  if (-not $version) { $version = 'latest' }
  $sa = terraform -chdir=infra output -raw runtime_service_account_email
  if ($LASTEXITCODE -ne 0) { throw 'Cannot read runtime SA output.' }
  if (-not $sa) { throw 'SA email is empty: tests would run as your own user' }
  $raw = $null
  $cfg = $null
  $state = $null
  try {
    $raw = gcloud secrets versions access $version --secret=$secret --project=$project --impersonate-service-account=$sa
    if ($LASTEXITCODE -ne 0) { throw "Runtime SA cannot read version '$version'." }
    try { $cfg = ($raw -join "`n") | ConvertFrom-Json -ErrorAction Stop }
    catch { throw 'Secret payload is not valid JSON. Content suppressed.' }
    if (-not $cfg.bot_token -or -not $cfg.chat_id) { throw 'Secret JSON must contain bot_token and chat_id.' }

    $state = terraform -chdir=infra state pull
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect state using the Terraform deployer ADC.' }
    if (($state -join "`n").Contains($cfg.bot_token)) {
      throw 'Bot token found in Terraform state. Revoke it in BotFather now and investigate.'
    }
    $state = $null

    $body = @{ chat_id = $cfg.chat_id; text = 'gcp-terraform-data-platform: secret test from DEV' } |
      ConvertTo-Json -Compress
    try {
      $resp = Invoke-RestMethod -Method Post -Uri "https://api.telegram.org/bot$($cfg.bot_token)/sendMessage" `
        -ContentType 'application/json' -Body $body -ErrorAction Stop
    } catch {
      throw "sendMessage failed: HTTP $([int]$_.Exception.Response.StatusCode). Details suppressed (URL contains the token)."
    }
    if (-not $resp.ok) { throw 'Telegram returned ok=false.' }
    "OK: message $($resp.result.message_id) sent. Confirm it arrived in the chat."
  } finally {
    $raw = $null
    $cfg = $null
    $state = $null
  }
}
```

Expected: `OK: message <id> sent ...`, and the message appears in the chat.
HTTP 401 means a revoked or wrong token. HTTP 400 usually means a wrong `chat_id`,
or the bot has not been started in that chat.

Negative checks:

- Without a version: the access step fails with `NOT_FOUND`.
- `$sa = ''; if (-not $sa) { throw 'SA email is empty: tests would run as your own user' }`
  must throw before any impersonated call.

## Rotate the telegram token

BotFather's revoke action invalidates the old token and issues a new one, so
the two tokens do not coexist. Alerts fail from the revoke until the new version
is added (step 3). This is based on BotFather behaviour, not an official Telegram
SLA. Check BotFather's reply when you rotate.

1. Record the current version number `N`:

   ```powershell
   gcloud secrets versions list alert-telegram --project dep-2610-dev
   ```

2. Create the new token in Telegram: BotFather → `/mybots` → your bot →
   *API Token* → *Revoke current token* (or `/revoke`). Copy the new token
   directly into the next step only.
3. Add a new version with [Add a secret version](#add-a-secret-version). Use the
   same chat ID. Note the new version `M` from `Created version [M]`.
4. Verify:
   - Run [Test a secret version](#test-a-secret-version) with `latest`. The message must arrive.
   - Confirm that `latest` points to `M`:

     ```powershell
     gcloud secrets versions describe latest --secret=alert-telegram --project dep-2610-dev --format='value(name)'
     ```

     Expected: `.../secrets/alert-telegram/versions/M`.
   - Negative test: run the test block with version `N`. It must fail with
     `sendMessage failed: HTTP 401`, which proves the old token is revoked.
5. Disable the old version:

   ```powershell
   gcloud secrets versions disable <N> --secret=alert-telegram --project dep-2610-dev
   ```

   Negative test: reading the old version must now fail, with an error mentioning the `DISABLED` state:

   ```powershell
   gcloud secrets versions access <N> --secret=alert-telegram --project dep-2610-dev
   ```

6. Delete the old token. BotFather already invalidated it in step 2. Remove any
   remaining copies (notes, password manager entries, clipboard history).
   Optional and irreversible: once you are sure, run
   `gcloud secrets versions destroy <N> --secret=alert-telegram --project dep-2610-dev`.

Cloud Run must read `latest`. If a job pins a version number, redeploy
it with `M` before disabling `N`.

## Raw ingestion

The app compares S3 ETags with **GCS custom metadata**, not `load_log`.
No BigQuery queries, region filtering or transformations are performed yet.
Public source files are global and potentially large; review the printed byte
counts and costs before copying even a single quarter. Source ETags are opaque
identifiers, not MD5 checksums.

### Local configuration and preview

**PowerShell 7 on Windows**, repository root, after installing
[the app](../app/README.md#install-and-local-checks). Do not use keys.
The DEV raw bucket and runtime IAM must already be applied, including the
developer's Token Creator grant. Use user ADC as the source for explicit
application impersonation; Terraform deployment ADC may need to be restored
before subsequent Terraform operations.

```powershell
gcloud auth application-default login
$sa = terraform -chdir=infra output -raw runtime_service_account_email
if ($LASTEXITCODE -ne 0) { throw 'Cannot read runtime SA output.' }
if (-not $sa) { throw 'SA email is empty: tests would run as your own user' }
$env:IMPERSONATE_SERVICE_ACCOUNT = $sa
$env:PROJECT_ID = 'dep-2610-dev'
$env:RAW_BUCKET = 'dep-2610-dev-raw'
$env:SOURCE_BUCKET = 'ookla-open-data'
$env:SOURCE_REGION = 'us-west-2'
Remove-Item Env:\S3_REGION -ErrorAction SilentlyContinue
$env:MIN_QUARTER = '2023-Q3'
& app\.venv\Scripts\python.exe -m ingest sync --type mobile --dry-run
if ($LASTEXITCODE -ne 0) { throw 'Preview failed.' }
```

Expected: JSON plan logs with source/destination paths, sizes and reasons,
without writes. Select an available quarter at or after `MIN_QUARTER` to keep
the test scope fixed:

```powershell
$quarter = '2023-Q3'
& app\.venv\Scripts\python.exe -m ingest sync --type mobile --quarter $quarter --dry-run
if ($LASTEXITCODE -ne 0) { throw 'Narrowed preview failed.' }
```

The exact-quarter filter excludes other quarters even when they are pending.
`--limit N` limits transfers per run; up-to-date files do not count.

### First copy, repeat and metadata

```powershell
& app\.venv\Scripts\python.exe -m ingest sync --type mobile --quarter $quarter
if ($LASTEXITCODE -ne 0) { throw 'First sync failed.' }
& app\.venv\Scripts\python.exe -m ingest sync --type mobile --quarter $quarter
if ($LASTEXITCODE -ne 0) { throw 'Repeat sync failed.' }
```

Expected in a previously empty single-object scope: first run transfers one,
second reports `0 pending` and `transferred=0`. If already synced, both transfer
zero. Logs must show a successful footer check path before the upload; schema
errors fail before GCS writes. The app never prints source row data.

Copy the exact destination URI from the plan log:

```powershell
$object = Read-Host 'Exact gs://dep-2610-dev-raw/ookla/... Parquet object URI'
if (-not $object.StartsWith('gs://dep-2610-dev-raw/ookla/')) {
  throw 'Use only the DEV Ookla object from this test.'
}
gcloud storage objects describe $object --format='json(size,metadata,generation,metageneration)' --impersonate-service-account=$sa
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect object metadata.' }
```

Expected custom metadata: `source_etag` equals the source listing ETag without
HTTP quotes; `source_size` equals object size and source Content-Length.

Check the strict no-overwrite signal for this exact object:

```powershell
$before = gcloud storage objects describe $object --format='value(generation)' --impersonate-service-account=$sa
if ($LASTEXITCODE -ne 0 -or -not $before) { throw 'Cannot read generation before sync.' }
& app\.venv\Scripts\python.exe -m ingest sync --type mobile --quarter $quarter
if ($LASTEXITCODE -ne 0) { throw 'Idempotence sync failed.' }
$after = gcloud storage objects describe $object --format='value(generation)' --impersonate-service-account=$sa
if ($LASTEXITCODE -ne 0 -or -not $after) { throw 'Cannot read generation after sync.' }
if ($before -ne $after) { throw 'Object generation changed: content was overwritten.' }
```

Expected: `transferred=0` and identical content generations. Metadata-only edits
change metageneration rather than generation. Keep `$object` and `$quarter`
aligned throughout these checks.

### Force a changed-ETag decision (DEV only)

This intentionally changes only the test object's metadata, not real source
data or BigQuery. Object User includes the necessary update permission.

```powershell
gcloud storage objects update $object --update-custom-metadata=source_etag=synthetic-etag-mismatch --impersonate-service-account=$sa
if ($LASTEXITCODE -ne 0) { throw 'Metadata update failed.' }
& app\.venv\Scripts\python.exe -m ingest sync --type mobile --quarter $quarter --dry-run
if ($LASTEXITCODE -ne 0) { throw 'Changed-ETag preview failed.' }
& app\.venv\Scripts\python.exe -m ingest sync --type mobile --quarter $quarter
if ($LASTEXITCODE -ne 0) { throw 'Changed-ETag resync failed.' }
```

Expected: exactly that object becomes pending with `etag_changed`; resync
overwrites it and restores the actual source ETag metadata. The next run
transfers nothing in the same single-object scope. Do **not** edit `load_log`:
it has no role in decisions.

### Negative and offline checks

```powershell
& app\.venv\Scripts\python.exe -m pytest app\tests\unit -q
& app\.venv\Scripts\python.exe -m ruff check app
& app\.venv\Scripts\python.exe -m ruff format --check app
& app\.venv\Scripts\python.exe -m ingest sync --type mobile --limit 0
if ($LASTEXITCODE -eq 0) { throw 'Invalid zero limit was accepted.' }
```

Expected: unit tests green, Ruff clean, and zero limit rejected with non-zero
exit code before any network calls. Unit tests also verify that a synthetic
Parquet file missing `devices` fails before transfer, interrupted transfers
abort, and unchanged/changed ETag decisions are idempotent.

Live transfer, Cloud Run execution and regional source coverage have not been
verified by the assistant. Only local mocked tests were run.

## Curated build

Commands are **PowerShell 7 on Windows**, repository root. Reuse the raw-ingestion
environment and impersonation setup above. Raw GCS objects must have sync
metadata, Terraform tables (including the external table) must exist, and the
Telegram JSON version must be provisioned for delivery checks. No new roles are
needed beyond the configured runtime grants.

Install the updated declared dependencies, then configure the build:

```powershell
& app\.venv\Scripts\python.exe -m pip install -e 'app[dev]'
$env:REGION = 'europe-southwest1'
$env:REGION_BBOX = '{"min_lon":-9.6,"max_lon":4.4,"min_lat":35.8,"max_lat":43.9}'
$env:MAXIMUM_BYTES_BILLED = '10737418240'
$env:MINIMUM_ROW_PERCENT = '50'
$env:LATENCY_CEILING_MS = '10000'
$env:MART_ZOOM = '10'
& app\.venv\Scripts\python.exe -m ingest build --type mobile --quarter 2023-Q3 --dry-run
if ($LASTEXITCODE -ne 0) { throw 'Build work preview failed.' }
```

The preview performs a capped watermark query and metadata reads, not mutations
or notifications. Confirm the selected quarter exists in raw and the dataset
and bbox match Terraform. Then run:

```powershell
& app\.venv\Scripts\python.exe -m ingest build --type mobile --quarter 2023-Q3
if ($LASTEXITCODE -ne 0) { throw 'Build failed; inspect DQ and transaction logs.' }
```

Expected: stage/DQ/publication success, mart rebuild, a successful load-log row,
and a Telegram summary. A notification warning does not mean data processing
failed; verify channel arrival separately. There must be no token or Bot API URL
in logs. A second unchanged run selects no partitions but rebuilds the mart.
Operational `loaded_at` values may change; counts and metrics must not.

### Exact rerun and network-type preservation

Compare native row counts around a forced mobile-only rebuild. The fixed count
must not change because both types share the same date partition.

```powershell
$sa = $env:IMPERSONATE_SERVICE_ACCOUNT
if (-not $sa) { throw 'SA email is empty: tests would run as your own user' }
$counts = 'SELECT network_type, COUNT(*) AS rows FROM `dep-2610-dev.network_perf.ookla_tiles` WHERE quarter_start = DATE ''2023-07-01'' GROUP BY network_type ORDER BY network_type'
$before = bq --impersonate_service_account=$sa --project_id=dep-2610-dev --location=europe-southwest1 --format=json query --use_legacy_sql=false --maximum_bytes_billed=10737418240 $counts
if ($LASTEXITCODE -ne 0) { throw 'Count baseline failed.' }
& app\.venv\Scripts\python.exe -m ingest build --type mobile --quarter 2023-Q3 --force
if ($LASTEXITCODE -ne 0) { throw 'Forced rebuild failed.' }
$after = bq --impersonate_service_account=$sa --project_id=dep-2610-dev --location=europe-southwest1 --format=json query --use_legacy_sql=false --maximum_bytes_billed=10737418240 $counts
if ($LASTEXITCODE -ne 0) { throw 'Count comparison failed.' }
if (($before -join "`n") -ne ($after -join "`n")) { throw 'Counts changed on identical source/configuration.' }
```

Keep SQL, thresholds, bbox and source constant during this comparison. No
cross-quarter all-run transaction exists: earlier successful slices remain
committed if a later slice fails. Run sync/build serially per environment.

### Independent mart calculation

Choose an existing ten-digit mart prefix for that quarter/type. The handwritten
[verification SQL](../app/src/ingest/sql/verify_mart.sql) computes the expected
download weighted average, tile count and tests directly from all curated tiles,
regardless of latency outliers. It does not call the mart rebuild query.

```powershell
$prefix = Read-Host 'Existing 10-digit quadkey prefix for mobile 2023 Q3'
if ($prefix -notmatch '^[0-3]{10}$') { throw 'Expected a zoom-10 quadkey prefix.' }
$sql = (Get-Content app\src\ingest\sql\verify_mart.sql -Raw).Replace('{dataset}', 'dep-2610-dev.network_perf')
bq --impersonate_service_account=$sa --project_id=dep-2610-dev --location=europe-southwest1 query --use_legacy_sql=false --maximum_bytes_billed=10737418240 '--parameter=quarter_start:DATE:2023-07-01' '--parameter=network_type:STRING:mobile' "--parameter=prefix:STRING:$prefix" $sql
if ($LASTEXITCODE -ne 0) { throw 'Independent mart calculation failed.' }
```

Expected: exactly one row, `download_matches=true`, equal expected/mart tile and
test counts. No rows means the chosen prefix is absent, not a passing check.
Local numerical tests also cover sparse-latency denominators and NULL QoQ across
a missing quarter; BigQuery query execution still needs this DEV verification.

### Negative quality and configuration checks

```powershell
& app\.venv\Scripts\python.exe -m pytest app\tests\unit\test_build_sql.py app\tests\unit\test_quality.py -q
if ($LASTEXITCODE -ne 0) { throw 'Synthetic quality/preservation tests failed.' }
& app\.venv\Scripts\python.exe -m ingest build --quarter 2023-Q2 --dry-run
if ($LASTEXITCODE -eq 0) { throw 'Unsupported quarter was accepted.' }
```

The synthetic test runs the aggregate quality SQL with SQLite function
adaptations: `devices > tests` fails, curated rows/load log remain unchanged,
then a valid transaction changes mobile without changing fixed. This is local
evidence, not a claim of cloud transaction verification. The invalid quarter
must fail before cloud access. Never inject bad data into PROD.

If a stage remains after process termination, stop other writers and inspect it
with the deployment identity; do not blindly delete another run's stage. Its
one-day expiration is the fallback. If a mart rebuild fails after publication,
rerun build: even with no selected ETags it attempts the full mart transaction.
