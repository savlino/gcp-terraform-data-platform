<#
.SYNOPSIS
Guarded local Terraform plan/apply for one environment (PowerShell 5.1 and 7).

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

.EXAMPLE
.\scripts\tf.ps1 -Env dev -Action plan
.\scripts\tf.ps1 -Env dev -Action apply
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('dev', 'test', 'prod')]
    [string]$Env,

    [Parameter(Mandatory = $true)]
    [ValidateSet('plan', 'apply')]
    [string]$Action
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$MaxPlanAgeMinutes = 30
$repoRoot = Split-Path -Parent $PSScriptRoot
$infraDir = Join-Path $repoRoot 'infra'
$projectId = "dep-2610-$Env"
$planName = "$Env.tfplan"
$planPath = Join-Path $infraDir $planName
$jsonPath = Join-Path $infraDir "$Env.plan.json"
$markerPath = Join-Path $infraDir "$Env.tfplan.meta.json"
$allowDestroyPath = Join-Path $infraDir "$Env.allow-destroy"
$backendPath = Join-Path $infraDir "env\$Env.gcs.tfbackend"
$varFilePath = Join-Path $infraDir "env\$Env.tfvars"

Write-Host "=== Terraform environment: $Env | project: $projectId | action: $Action ==="

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

function Assert-CheckedPlan {
    if (-not (Test-Path -LiteralPath $planPath)) { throw "$planPath not found; run -Action plan first." }
    if (-not (Test-Path -LiteralPath $markerPath)) {
        throw "No policy-checked marker for $planPath; the plan was not created (or did not pass) through this script."
    }
    $marker = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
    if ($marker.env -ne $Env -or $marker.project_id -ne $projectId) {
        throw "Plan was created for '$($marker.env)' / '$($marker.project_id)', not '$Env' / '$projectId'."
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
    # Separate data dir per environment: a backend initialised for one
    # environment can never be reused silently by another one.
    $env:TF_DATA_DIR = Join-Path $infraDir ".terraform-$Env"
    try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }

    Invoke-Native 'terraform init' {
        terraform "-chdir=$infraDir" init -input=false -reconfigure "-backend-config=env/$Env.gcs.tfbackend"
    }

    if ($Action -eq 'plan') {
        Remove-Item -LiteralPath $planPath, $jsonPath, $markerPath -Force -ErrorAction SilentlyContinue

        Invoke-Native 'terraform plan' {
            terraform "-chdir=$infraDir" plan -input=false -lock-timeout=120s "-var-file=env/$Env.tfvars" "-out=$planName"
        }

        $json = terraform "-chdir=$infraDir" show -json $planName
        if ($LASTEXITCODE -ne 0) { throw "terraform show failed with exit code $LASTEXITCODE." }
        [System.IO.File]::WriteAllText($jsonPath, ($json -join "`n"), (New-Object System.Text.UTF8Encoding($false)))

        $policyArgs = @(
            (Join-Path $repoRoot 'scripts\check_plan.py'), $jsonPath,
            '--env', $Env, '--project-id', $projectId
        )
        $allowDestroy = Test-Path -LiteralPath $allowDestroyPath
        if ($allowDestroy) {
            $listed = @(Get-Content -LiteralPath $allowDestroyPath | ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith('#') })
            Write-Warning "$allowDestroyPath exempts $($listed.Count) address(es) in $Env from P10/P13:"
            $listed | ForEach-Object { Write-Warning "  $_" }
            $policyArgs += @('--allow-destroy-file', $allowDestroyPath)
        }
        $python = Get-PythonExe
        Invoke-Native 'Plan policy check' { & $python @policyArgs }

        $marker = [ordered]@{
            env           = $Env
            project_id    = $projectId
            plan_sha256   = (Get-FileHash -LiteralPath $planPath -Algorithm SHA256).Hash
            created_utc   = [DateTime]::UtcNow.ToString('o')
            allow_destroy = $allowDestroy
        }
        [System.IO.File]::WriteAllText($markerPath, ($marker | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
        Write-Host "Plan for $Env ($projectId) passed the policy check. Review it, then run: .\scripts\tf.ps1 -Env $Env -Action apply (within $MaxPlanAgeMinutes minutes)."
    }
    else {
        Invoke-Native 'terraform apply' {
            terraform "-chdir=$infraDir" apply -input=false -lock-timeout=120s $planName
        }
        # A saved plan is single-use.
        Remove-Item -LiteralPath $planPath, $markerPath -Force -ErrorAction SilentlyContinue
        Write-Host "Applied $Env ($projectId)."
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
