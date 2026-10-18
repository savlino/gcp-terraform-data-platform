# Runbook

Operational procedures. Commands are for **PowerShell 7 on Windows**, run from
the repository root. Examples target DEV (`dep-2610-dev`); change `$project`
for other environments.

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

Cloud Run (step 7) must read `latest`. If a job pins a version number, redeploy
it with `M` before disabling `N`.
