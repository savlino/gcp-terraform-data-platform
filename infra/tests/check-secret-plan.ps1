$ErrorActionPreference = 'Stop'
$infraDirectory = Split-Path -Parent $PSScriptRoot
$testFile = Join-Path 'tests' 'secrets.tftest.hcl'
$events = @(
    & terraform "-chdir=$infraDirectory" test "-filter=$testFile" -json -verbose |
        ForEach-Object { $_ | ConvertFrom-Json }
)
if ($LASTEXITCODE -ne 0) {
    throw 'Terraform secrets test failed. Run the test without -json to inspect its diagnostics.'
}

$plans = @($events | Where-Object { $_.type -eq 'test_plan' })
if ($plans.Count -eq 0) {
    throw 'No test_plan events found: check -verbose and the JSON output format.'
}

$changes = @($plans | ForEach-Object { $_.test_plan.resource_changes })
if (-not ($changes | Where-Object { $_.type -eq 'google_secret_manager_secret' })) {
    throw 'Positive control failed: google_secret_manager_secret not found, so the check would pass vacuously.'
}

$versions = @($changes | Where-Object { $_.type -eq 'google_secret_manager_secret_version' })
if ($versions.Count -gt 0) {
    $addresses = ($versions | ForEach-Object { $_.address }) -join ', '
    throw "Secret versions must not be managed by Terraform: $addresses"
}

Write-Output 'PASS: secrets mock plan contains no google_secret_manager_secret_version resources.'
