# Runbook

Operational procedures. Commands are for **PowerShell 7 on Windows**, run from
the repository root. Examples target DEV (`dep-2610-dev`); change `$project`
for other environments.

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
tfvars; set `developer_principal` explicitly in your local configuration (for
example `user:you@example.com`, never a secret). Preserve that value across
plans: it also controls the existing non-PROD runtime impersonation grant.
Do not commit a personal identity without intending to publish it.

Review/apply another plan as the deployer. Expect one VM and two instance IAM
members; if the developer was previously null, the separate runtime
impersonation member is also added. The VM is `dev-iap-demo`, `e2-micro`,
`europe-southwest1-a`, Debian 12, 10 GiB disk. Both demo grants are omitted in
PROD even with a principal set.

For SSH, use **the developer's gcloud user login**, not deployer impersonation.
The developer needs the generated instance IAP/OS Login grants, plus existing
Compute discovery permissions (`compute.projects.get` and instance lookup).
No broad project role is added here. Non-organization users normally do not
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
The [DEV tfvars](../infra/env/dev.tfvars) initially has
`enable_ingest_job = false`: the first apply adds the repository but no job or
schedule. Do not use routine `-target` applies or a placeholder public image.

### 1. Prerequisites and repository-first apply

Infra, ingest and transform must already be deployed and verified, including
raw Parquet aligned with the pinned external-table schema, native tables, runtime grants and
an enabled `alert-telegram` version. Confirm the existing bootstrap API
configuration has actually enabled `run.googleapis.com`,
`artifactregistry.googleapis.com`, `cloudscheduler.googleapis.com`,
`secretmanager.googleapis.com` and `logging.googleapis.com`. Cloud Run and
Scheduler service agents must retain their normal Google-managed service-agent
roles. Do not grant those roles to workload accounts.

Use the authorized Terraform deployer via impersonated ADC from
[setup](setup.md). It additionally needs repository/job/schedule management,
job IAM get/set, service-account creation and `iam.serviceAccounts.actAs` on
runtime and scheduler accounts. The same-project Cloud Run service agent pulls
the image; the runtime does not need Artifact Registry Reader.
An authorized publisher needs repository-scoped Artifact Registry Writer and
your user must be allowed to impersonate it. This step does not create or grant
publisher/deployer identities.

Choose a unique `image_tag` in DEV tfvars.
Keep `enable_ingest_job = false`; preserve any existing developer principal.

```powershell
terraform -chdir=infra init -reconfigure -input=false '-backend-config=env/dev.gcs.tfbackend'
terraform -chdir=infra plan '-var-file=env/dev.tfvars' -out=dev.tfplan
if ($LASTEXITCODE -ne 0) { throw 'Repository plan failed' }
# Review the plan before continuing: no job/scheduler yet.
terraform -chdir=infra apply dev.tfplan
if ($LASTEXITCODE -ne 0) { throw 'Repository apply failed' }
```

If the preceding infrastructure is unchanged, expect just one new repository
`dev-ingest` in `europe-southwest1`. Stop on unexpected deletions or environment
changes. Full fresh infrastructure needs the earlier external-table bootstrap
sequence; this is not a workaround for missing raw data.

### 2. Build and push the tfvars-selected tag

The build context is the repository root. Use Docker Desktop's Linux engine.
The environment override below makes the gcloud Docker credential helper use
the publisher identity, rather than silently pushing as your user. It restores
the prior override afterward and never creates a service-account key.

```powershell
$image = terraform -chdir=infra output -raw ingest_image_uri
if ($LASTEXITCODE -ne 0 -or -not $image) { throw 'Image URI output is missing' }
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

For an update, choose a new unique `image_tag` in tfvars, build/push that exact
URI first, then review/apply the job change. Read `ingest_image_uri` only after
state reflects that tag; for subsequent releases you can form the new URI from
the existing registry/repository plus the tfvars tag without applying an
unpublished image. Do not apply an enabled job pointing to an image not yet
pushed. Keep releases serialized; immutable tags preserve rollbacks.

To stop future launches, set `scheduler_paused = true` and review/apply; this
does not cancel active executions. Setting `enable_ingest_job = false` removes
the job, schedule, scheduler identity and grant (subject to deletion protection),
but keeps the repository. PROD protection requires an explicit reviewed change
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
  runtime SA (`developer_principal`). The deployer ADC can run `terraform state pull`.
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
