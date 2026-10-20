# One-time local setup

Complete this checklist before building or deploying the platform. Commands in this document are for **PowerShell 7 on Windows**, unless stated otherwise. Do not create or download service account keys.

## 1. Check local tools

- [ ] Check Terraform (the project requires Terraform 1.11 or newer), Google Cloud CLI, Git, and Python 3.12:

  ```powershell
  terraform -v
  gcloud --version
  git --version
  python --version
  ```

- [ ] If you later add the Google provider, check the Terraform Registry for the current provider major version before pinning it; this setup does not select a provider version.
- [ ] Optional: install `pre-commit` to use the configured hooks. Ruff is optional; if installed, run its hook manually as shown under [Local checks](#local-checks).

## 2. Sign in with your user account

- [ ] Sign in to the Google Cloud CLI and set up Application Default Credentials (ADC):

  ```powershell
  gcloud auth login
  gcloud auth application-default login
  gcloud config set account you@gmail.com
  ```

- [ ] Before running impersonated checks, set the existing service account email
  and stop if it is empty. Keep this guard in the same script block as the checks
  so a failure prevents subsequent commands from running as your own user:

  ```powershell
  $sa = Read-Host 'Existing service account email for impersonated checks'
  if (-not $sa) { throw 'SA email is empty: tests would run as your own user' }
  ```

## 3. Create the projects and link billing

- [ ] List the billing accounts available to your account and copy the account ID:

  ```powershell
  gcloud billing accounts list
  ```

- [ ] In the Google Cloud Console, confirm that you intend to use the selected billing account. The following commands create billable project containers and associate them with that account.
- [ ] Create each project ID and link it to billing. Project IDs are globally unique; if an ID is unavailable, stop and decide on a replacement before proceeding. If a project already exists, verify that it is yours before linking it.
- [ ] Run each create/link command and confirm it succeeds before proceeding to the next one:

  ```powershell
  $prefix  = <prefix>  # replace with your prefix as a string
  $billingAccount = (gcloud billing accounts list --filter="open=true" --format="value(name)")

  foreach ($env in "ops","dev","test","prod") {
    gcloud projects create "$prefix-$env" --name="$prefix-$env"
    gcloud billing projects link "$prefix-$env" --billing-account=$billing
  }
  gcloud config set project "$prefix-ops"

  gcloud services enable serviceusage.googleapis.com cloudresourcemanager.googleapis.com `
      cloudbilling.googleapis.com billingbudgets.googleapis.com iam.googleapis.com `
      --project "$prefix-ops" # allows basic APIs for Terraform
  ```

- [ ] Check that all four projects are visible to the signed-in account:

  ```powershell
  gcloud projects list --format="table(projectId,name)"
  ```

  Expected: rows for `<prefix>-ops`, `<prefix>-dev`, `<prefix>-test`, and `<prefix>-prod`. Visibility depends on your IAM permissions.

- [ ] Set the ADC quota project after `<prefix>-ops` exists. Your account must have permission to use this project:

  ```powershell
  gcloud auth application-default set-quota-project "$prefix-ops"
  ```

## 4. Check trial credit

- [ ] Open [Google Cloud Billing](https://console.cloud.google.com/billing), select the billing account, and check its overview/credits area for any remaining free-trial credit and its expiration. Record the result; do not assume credit is available.

## 5. Check the Ookla Open Data source

The dataset is a quarterly public Parquet release under the `parquet/performance/` prefix. The official documentation describes global zoom-16 tiles and says `tile_x`/`tile_y` are present from Q3 2023. Bucket listings establish that objects exist; they do **not** establish that a particular region has tiles.

- [ ] Review the [official dataset documentation](https://github.com/teamookla/ookla-open-data) and [AWS Open Data Registry entry](https://registry.opendata.aws/speedtest-global-performance/) for the current release, schema, license, and any region-availability notice. Do not assume an April 2026 region change without confirming it in an official source.
- [ ] List available years and quarters over HTTPS without AWS credentials. This PowerShell example lists year prefixes for the mobile layer:

  ```powershell
  $prefix = 'parquet/performance/type=mobile/'
  $encodedPrefix = [uri]::EscapeDataString($prefix)
  $url = "https://ookla-open-data.s3.us-west-2.amazonaws.com/?list-type=2&delimiter=%2F&prefix=$encodedPrefix"
  [xml]$listing = (Invoke-WebRequest -Uri $url -ErrorAction Stop).Content
  $listing.ListBucketResult.CommonPrefixes.Prefix
  ```

- [ ] Choose the latest year shown and list its quarter prefixes (replace `2026` with the latest year from the previous output):

  ```powershell
  $prefix = 'parquet/performance/type=mobile/year=2026/'
  $encodedPrefix = [uri]::EscapeDataString($prefix)
  $url = "https://ookla-open-data.s3.us-west-2.amazonaws.com/?list-type=2&delimiter=%2F&prefix=$encodedPrefix"
  [xml]$listing = (Invoke-WebRequest -Uri $url -ErrorAction Stop).Content
  $listing.ListBucketResult.CommonPrefixes.Prefix
  ```

- [ ] Repeat the year and quarter listing with `type=fixed`. The latest quarter can differ by layer; use the actual prefixes returned rather than assuming they match.
- [ ] List the objects in one latest quarter and select an exact Parquet object key. Replace the year and quarter below with values returned by the listing:

  ```powershell
  $prefix = 'parquet/performance/type=mobile/year=2026/quarter=1/'
  $encodedPrefix = [uri]::EscapeDataString($prefix)
  $url = "https://ookla-open-data.s3.us-west-2.amazonaws.com/?list-type=2&prefix=$encodedPrefix"
  [xml]$listing = (Invoke-WebRequest -Uri $url -ErrorAction Stop).Content
  $listing.ListBucketResult.Contents | Select-Object Key, Size, ETag
  ```

- [ ] Check the selected object's size with an HTTPS `HEAD` request. This requests metadata only and does not download the Parquet file:

  ```powershell
  $object = $listing.ListBucketResult.Contents | Select-Object -First 1
  if (-not $object) { throw 'No objects were returned for the selected quarter.' }
  $key = [string]$object.Key
  $encodedKey = (($key -split '/') | ForEach-Object { [uri]::EscapeDataString($_) }) -join '/'
  $head = Invoke-WebRequest -Method Head -Uri "https://ookla-open-data.s3.us-west-2.amazonaws.com/$encodedKey" -ErrorAction Stop
  $head.StatusCode
  $head.Headers['Content-Length']
  $head.Headers['ETag']
  ```

- [ ] If the AWS CLI is installed, you can also list the layer's year prefixes without signing requests:

  ```powershell
  aws s3 ls s3://ookla-open-data/parquet/performance/type=mobile/ --no-sign-request
  aws s3 ls s3://ookla-open-data/parquet/performance/type=fixed/ --no-sign-request
  ```

- [ ] Confirm that the latest quarter's actual Parquet data has tile geometry intersecting the intended Iberian bounding box. Use a Parquet-capable query/viewer and inspect the `tile` WKT geometry (EPSG:4326) against the exact bounding box you plan to configure. A successful object listing or `HEAD` request is **not** sufficient proof of regional coverage. Avoid downloading a large file until you have reviewed its reported size.
- [ ] Record the checked release, object key, `HEAD` size/ETag, and how regional coverage was verified. Leave this item unchecked if you have not inspected the data.

## Local checks

- [ ] Install the hooks after installing `pre-commit`:

  ```powershell
  pre-commit install
  pre-commit run --all-files
  ```

- [ ] Optional Ruff check, after installing Ruff locally:

  ```powershell
  pre-commit run --hook-stage manual ruff --all-files
  ```

Terraform validation requires initialized Terraform configuration. If the validation hook reports that a configuration is not initialized, run `terraform init` for that root configuration and retry. Terraform's dependency lock file (`.terraform.lock.hcl`) is intentionally not ignored.

## Final verification

- [ ] `terraform version`, `gcloud version`, `docker --version`, and `python --version` complete successfully.
- [ ] `gcloud projects list` shows the four expected project IDs.
- [ ] ADC can return an access token:

  ```powershell
  gcloud auth application-default print-access-token
  ```

  Expected: a non-empty access token. Do not paste or commit the token.

- [ ] Negative check: verify the CLI rejects an invalid project ID lookup (this must fail):

  ```powershell
  gcloud projects describe <prefix>-does-not-exist
  ```

  Expected: a not-found error. Do not treat a successful response as a passing negative check.

## References

- [Terraform installation](https://developer.hashicorp.com/terraform/install)
- [Set up ADC for a local development environment](https://cloud.google.com/docs/authentication/set-up-adc-local-dev-environment)
- [Ookla Open Data documentation](https://github.com/teamookla/ookla-open-data)
- [AWS Open Data Registry: Speedtest Global Performance](https://registry.opendata.aws/speedtest-global-performance/)
