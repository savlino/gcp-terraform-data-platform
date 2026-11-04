<#
.SYNOPSIS
Guarded local Terraform plan/apply for one environment or for bootstrap (PowerShell 5.1 and 7).

.DESCRIPTION
Every run pins the Terraform data directory, backend and var-file to the same
environment. Plan runs scripts/check_plan.py (including rule P12: the plan must
not touch another project) before the plan can be applied. Apply only applies a
plan created by this script for the same environment, unchanged, and not older
than 30 minutes.

Intentional destroys or protection changes: list the reviewed resource
addresses, one per line, in infra/<env>.allow-destroy (git-ignored). Only those
addresses are exempt from P10 (stateful delete/replace) and P13 (relaxing
deletion_protection/force_destroy), for that environment only. Delete the file
after the apply.

-Root bootstrap (no -Env): the default .terraform data directory (TF_DATA_DIR
is unset during the run and restored afterwards), plain init, plan to
bootstrap.tfplan. Inputs come from TF_VAR_* or gitignored personal.auto.tfvars;
developer_principal is required (explicit null disables human access). The plan
fails on any delete/replace unless the address is listed in
bootstrap/allow-destroy (same format and stale-entry rule as infra), and prints
N to add / change / destroy plus every address to be destroyed.

.EXAMPLE
.\scripts\tf.ps1 -Env dev -Action plan
.\scripts\tf.ps1 -Env dev -Action apply
.\scripts\tf.ps1 -Root bootstrap -Action plan
#>
[CmdletBinding()]
param(
    [ValidateSet('dev', 'test', 'prod')]
    [string]$Env,

    [Parameter(Mandatory = $true)]
    [ValidateSet('plan', 'apply')]
    [string]$Action,

    # Last, so existing positional calls (-Env, -Action) bind as before.
    [ValidateSet('infra', 'bootstrap')]
    [string]$Root = 'infra'
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$MaxPlanAgeMinutes = 30
$repoRoot = Split-Path -Parent $PSScriptRoot
$isBootstrap = $Root -eq 'bootstrap'
if ($isBootstrap -and $Env) { throw '-Env does not apply to -Root bootstrap: bootstrap has one state for all projects.' }
if (-not $isBootstrap -and -not $Env) { throw '-Env (dev, test or prod) is required for -Root infra.' }

$rootDir = Join-Path $repoRoot $Root
if ($isBootstrap) {
    $label = 'bootstrap'
    $projectId = 'dep-2610-ops'
    $allowDestroyPath = Join-Path $rootDir 'allow-destroy'
    $backendPath = Join-Path $rootDir 'backend.tf'
}
else {
    $label = $Env
    $projectId = "dep-2610-$Env"
    $allowDestroyPath = Join-Path $rootDir "$Env.allow-destroy"
    $backendPath = Join-Path $rootDir "env\$Env.gcs.tfbackend"
    $varFilePath = Join-Path $rootDir "env\$Env.tfvars"
}
$planName = "$label.tfplan"
$planPath = Join-Path $rootDir $planName
$jsonPath = Join-Path $rootDir "$label.plan.json"
$markerPath = Join-Path $rootDir "$label.tfplan.meta.json"

if ($isBootstrap) {
    Write-Host "=== Terraform root: bootstrap | project: $projectId (state, WIF, CI accounts; grants in dev/test/prod) | action: $Action ==="
}
else {
    Write-Host "=== Terraform environment: $Env | project: $projectId | action: $Action ==="
}

function Invoke-Native {
    param([string]$Description, [scriptblock]$Command)
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE."
    }
}

function Get-PythonExe {
    $venv = Join-Path $repoRoot 'app\.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venv) { return $venv }
    foreach ($name in 'python', 'python3') {
        $found = Get-Command $name -ErrorAction SilentlyContinue
        if ($found) { return $found.Source }
    }
    throw 'Python 3 was not found (expected app\.venv or python on PATH).'
}

function Get-HclString {
    param([string]$Path, [string]$Name)
    $line = Select-String -LiteralPath $Path -Pattern "^\s*$Name\s*=\s*""([^""]*)""" | Select-Object -First 1
    if (-not $line) { throw "$Name is not set in $Path." }
    return $line.Matches[0].Groups[1].Value
}

if ($isBootstrap) {
    if ((Get-HclString $backendPath 'prefix') -ne 'bootstrap') { throw "bootstrap/backend.tf: prefix is not 'bootstrap'." }
}
else {
    foreach ($path in $backendPath, $varFilePath) {
        if (-not (Test-Path -LiteralPath $path)) { throw "Missing $path." }
    }

    # Static consistency of the environment selection, before anything touches state.
    if ((Get-HclString $varFilePath 'env') -ne $Env) { throw "env/$Env.tfvars: env is not '$Env'." }
    if ((Get-HclString $varFilePath 'project_id') -ne $projectId) {
        throw "env/$Env.tfvars: project_id is not '$projectId'."
    }
    if ((Get-HclString $backendPath 'prefix') -ne "infra/$Env") {
        throw "env/$Env.gcs.tfbackend: prefix is not 'infra/$Env'."
    }
}

function Assert-CheckedPlan {
    if (-not (Test-Path -LiteralPath $planPath)) { throw "$planPath not found; run -Action plan first." }
    if (-not (Test-Path -LiteralPath $markerPath)) {
        throw "No policy-checked marker for $planPath; the plan was not created (or did not pass) through this script."
    }
    $marker = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
    $markerRoot = if ($marker.PSObject.Properties['root']) { $marker.root } else { 'infra' }
    if ($markerRoot -ne $Root -or $marker.env -ne $label -or $marker.project_id -ne $projectId) {
        throw "Plan was created for '$markerRoot' / '$($marker.env)' / '$($marker.project_id)', not '$Root' / '$label' / '$projectId'."
    }
    if ((Get-FileHash -LiteralPath $planPath -Algorithm SHA256).Hash -ne $marker.plan_sha256) {
        throw "$planName changed after it was checked; run -Action plan again."
    }
    $created = [DateTime]::Parse($marker.created_utc, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::RoundtripKind).ToUniversalTime()
    $age = [DateTime]::UtcNow - $created
    $fileAge = [DateTime]::UtcNow - (Get-Item -LiteralPath $planPath).LastWriteTimeUtc
    if ($age.TotalMinutes -gt $MaxPlanAgeMinutes -or $fileAge.TotalMinutes -gt $MaxPlanAgeMinutes) {
        throw "Plan is older than $MaxPlanAgeMinutes minutes; run -Action plan again."
    }
}

if ($Action -eq 'apply') { Assert-CheckedPlan }

$previousDataDir = $env:TF_DATA_DIR
$previousEncoding = $null
try { $previousEncoding = [Console]::OutputEncoding } catch { $previousEncoding = $null }

try {
    if ($isBootstrap) {
        # Bootstrap uses its default bootstrap\.terraform directory.
        Remove-Item Env:\TF_DATA_DIR -ErrorAction SilentlyContinue
    }
    else {
        # Separate data dir per environment: a backend initialised for one
        # environment can never be reused silently by another one.
        $env:TF_DATA_DIR = Join-Path $rootDir ".terraform-$Env"
    }
    try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }

    if ($isBootstrap) {
        Invoke-Native 'terraform init' { terraform "-chdir=$rootDir" init -input=false }
    }
    else {
        Invoke-Native 'terraform init' {
            terraform "-chdir=$rootDir" init -input=false -reconfigure "-backend-config=env/$Env.gcs.tfbackend"
        }
    }

    if ($Action -eq 'plan') {
        Remove-Item -LiteralPath $planPath, $jsonPath, $markerPath -Force -ErrorAction SilentlyContinue

        if ($isBootstrap) {
            # Missing required inputs must fail rather than prompt or remove grants.
            Invoke-Native 'terraform plan' {
                terraform "-chdir=$rootDir" plan -input=false -lock-timeout=120s "-out=$planName"
            }
        }
        else {
            Invoke-Native 'terraform plan' {
                terraform "-chdir=$rootDir" plan -input=false -lock-timeout=120s "-var-file=env/$Env.tfvars" "-out=$planName"
            }
        }

        $json = terraform "-chdir=$rootDir" show -json $planName
        if ($LASTEXITCODE -ne 0) { throw "terraform show failed with exit code $LASTEXITCODE." }
        [System.IO.File]::WriteAllText($jsonPath, ($json -join "`n"), (New-Object System.Text.UTF8Encoding($false)))

        if ($isBootstrap) {
            $policyArgs = @((Join-Path $repoRoot 'scripts\check_bootstrap_plan.py'), $jsonPath)
            $exemptRules = 'the bootstrap destroy guard'
        }
        else {
            $policyArgs = @(
                (Join-Path $repoRoot 'scripts\check_plan.py'), $jsonPath,
                '--env', $Env, '--project-id', $projectId
            )
            $exemptRules = 'P10/P13'
        }
        $allowDestroy = Test-Path -LiteralPath $allowDestroyPath
        if ($allowDestroy) {
            $listed = @(Get-Content -LiteralPath $allowDestroyPath | ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith('#') })
            Write-Warning "$allowDestroyPath exempts $($listed.Count) address(es) in $label from ${exemptRules}:"
            $listed | ForEach-Object { Write-Warning "  $_" }
            $policyArgs += @('--allow-destroy-file', $allowDestroyPath)
        }
        $python = Get-PythonExe
        Invoke-Native 'Plan policy check' { & $python @policyArgs }

        $marker = [ordered]@{
            root          = $Root
            env           = $label
            project_id    = $projectId
            plan_sha256   = (Get-FileHash -LiteralPath $planPath -Algorithm SHA256).Hash
            created_utc   = [DateTime]::UtcNow.ToString('o')
            allow_destroy = $allowDestroy
        }
        [System.IO.File]::WriteAllText($markerPath, ($marker | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
        $applyCommand = if ($isBootstrap) { '.\scripts\tf.ps1 -Root bootstrap -Action apply' } else { ".\scripts\tf.ps1 -Env $Env -Action apply" }
        Write-Host "Plan for $label ($projectId) passed the policy check. Review it, then run: $applyCommand (within $MaxPlanAgeMinutes minutes)."
    }
    else {
        Invoke-Native 'terraform apply' {
            terraform "-chdir=$rootDir" apply -input=false -lock-timeout=120s $planName
        }
        # A saved plan is single-use.
        Remove-Item -LiteralPath $planPath, $markerPath -Force -ErrorAction SilentlyContinue
        Write-Host "Applied $label ($projectId)."
    }
}
finally {
    if ($null -eq $previousDataDir) {
        Remove-Item Env:\TF_DATA_DIR -ErrorAction SilentlyContinue
    }
    else {
        $env:TF_DATA_DIR = $previousDataDir
    }
    if ($previousEncoding) { try { [Console]::OutputEncoding = $previousEncoding } catch { } }
}
