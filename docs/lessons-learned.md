# Lessons learned

Notes collected while building this project, in the order a reader would hit them.
Each entry: what happened, how it was fixed, and the takeaway. Links point to the documentation
the fix is based on. Items in the last section were not encountered yet; they come from the
data provider's documentation and shaped the design.

## Local environment (Windows, PowerShell)

### Limited sync runs do not define a fixed idempotence sample
- **What happened:** repeating `sync --limit 1` with a backlog can select the next
  pending object, making a successful skip look like a repeated transfer.
- **Fix:** use an exact scope such as `sync --type mobile --quarter 2023-Q3`.
  `--limit N` limits transfers per run; up-to-date files do not count.
- **Takeaway:** verify that the same object's GCS `generation` is unchanged across
  the repeat run. A content overwrite creates a new generation; metadata-only
  updates change `metageneration`, not the content generation. A transfer count
  alone is not strict evidence that a particular object was not overwritten.
- **Docs:** https://cloud.google.com/storage/docs/metadata#generation-number

### "Python was not found" printed by gcloud
- **What happened:** every `gcloud` call printed a Microsoft Store message. Windows ships stub
  `python.exe` / `python3.exe` aliases in `%LOCALAPPDATA%\Microsoft\WindowsApps`; a regular Python
  install creates `python.exe` but not `python3.exe`, so `python3` still hits the stub.
- **Fix:** disable the aliases ("Manage app execution aliases" in Windows search) or delete the stub
  files; optionally pin gcloud's interpreter with the `CLOUDSDK_PYTHON` user environment variable.
- **Takeaway:** check `where.exe python3`, not only `python --version`.

### PowerShell splits arguments that contain a dot
- **What happened:** `terraform plan -out=bootstrap.tfplan` failed with
  "Too many command line arguments"; PowerShell passed `-out=bootstrap` and `.tfplan` separately.
  Without the extension the plan was saved as `bootstrap`, which `*.tfplan` in `.gitignore` did not cover.
- **Fix:** quote such arguments: `"-out=dev.tfplan"`, `"-var-file=env/dev.tfvars"`,
  `"-backend-config=env/dev.gcs.tfbackend"`.
- **Takeaway:** a workaround for one error can silently create a file that escapes `.gitignore`.

### PowerShell does not stop when a native command fails
- **What happened:** a loop of `gcloud projects create` / `billing projects link` would continue
  after a failure.
- **Fix:** check `$LASTEXITCODE` after each external command and `throw` on non-zero.

### Do not work in an elevated shell in `C:\Windows\System32`
- `terraform init` writes `.terraform/` into the current directory. Use a normal shell inside the repo.

## gcloud and billing

### `--filter="open=true"` warned that the key was not present in any resource
- **What happened:** the filter syntax was correct (it is the documented example); the warning meant
  the list was empty for the active account.
- **Fix:** check `gcloud config list` / `gcloud auth list`, run the list without a filter, verify the
  billing account in the console. Extract only the ID with `--format="value(name.basename())"`.
- **Docs:** https://cloud.google.com/sdk/gcloud/reference/billing/accounts/list

### Project limits and free trial (to verify per account)
- New billing accounts have a small, not directly visible limit of linked projects
  (reported as five by third parties); projects pending deletion may still count.
- The free trial ends when credits run out or after 90 days, whichever comes first.
- **Docs:** https://docs.cloud.google.com/free

### Project IDs are not secrets, billing account IDs stay out of files
- A project ID becomes part of many resource names, so it cannot be hidden; protection comes from IAM.
  It must not contain personal or employer data and cannot be changed later.
- The billing account ID is passed via `TF_VAR_*` or `Read-Host`, never committed. Values passed with
  `-var=` end up in shell history.
- **Docs:** https://docs.cloud.google.com/compute/docs/projects

## Terraform state and plans

### Bootstrap chicken-and-egg
- The GCS backend bucket must exist before it can be used, so `bootstrap/` starts with local state,
  creates the bucket, then moves its own state there with `terraform init -migrate-state`.
- The state bucket has `prevent_destroy`; destroying bootstrap later requires migrating its state
  back to local first.
- **Docs:** https://developer.hashicorp.com/terraform/language/backend/gcs

### Stale state lock after an interrupted run
- **What happened:** `Error acquiring the state lock` (HTTP 412) from a previous `plan` that was
  interrupted and left `default.tflock` in the bucket.
- **Fix:** make sure no Terraform process is running (`Get-Process terraform`), compare the lock's
  creation time with your own runs, then `terraform force-unlock <LOCK_ID>`.
- **Takeaway:** safe only while nobody else uses the state; with CI this needs care.

### "Saved plan is stale" on a bad connection
- **What happened:** an earlier apply was interrupted mid-way; part of the resources reached the state,
  so the saved plan no longer matched it.
- **Fix:** look for `errored.tfstate` (written when Terraform cannot save state to the backend) and
  `terraform state push` it; re-plan and apply immediately. If a resource exists in GCP but not in
  state (409 "already exists"), bring it back with `terraform import`, do not delete it.
  `-parallelism=2` reduces concurrent requests on a weak connection.
- **Takeaway:** state can diverge from reality; `state push` and `import` are the recovery tools.

### Plan files are binary
- Read them with `terraform show dev.tfplan` (or `-json`). They may contain variable values, so they
  are deleted after apply and ignored by git.

### One lock file per root module
- `bootstrap/` and `infra/` each have their own `.terraform.lock.hcl`. `terraform init` on Windows
  records Windows hashes only; CI runs on Linux:
  `terraform providers lock -platform=windows_amd64 -platform=linux_amd64`.

## Terraform configuration

### Environments as separate projects, not CLI workspaces
- CLI workspaces in one working directory share the same backend, so they cannot isolate credentials
  and access per environment. Each environment here is its own project with its own state prefix.
- **Docs:** https://developer.hashicorp.com/terraform/cli/workspaces

### Billing Budgets API with user credentials
- The provider needs `billing_project` and `user_project_override = true`, otherwise budget calls
  fail with user ADC.
- **Docs:** https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/billing_budget

### Dataset default table expiration would delete Terraform-managed tables
- `default_table_expiration_ms` applies to tables Terraform creates; BigQuery would delete
  `ookla_tiles` and `load_log` after N days and the next plan would recreate them. Not used;
  cost control in DEV is done with bucket lifecycle rules.

### Hive-partitioned external table postponed until data exists
- Creating it over an empty prefix was not confirmed in the docs, and the provider had a historical
  issue with explicit schema + Hive partitioning. CUSTOM mode encodes key types in the URI prefix;
  partition keys must not overlap with columns inside the files.
- **Docs:** https://docs.cloud.google.com/bigquery/docs/hive-partitioned-queries

### Secrets never in state
- `secret_data` of `google_secret_manager_secret_version` is hidden in plans but stored in state.
  Values are added with gcloud, or via the write-only `secret_data_wo` (Terraform 1.11+).
- **Docs:** https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/secret_manager_secret_version

## Google provider specifics

### Labels are non-authoritative (provider 5.0+)
- **What happened:** a label added with gcloud (`probe=1`) did not show up as drift; `terraform plan`
  reported "No changes".
- **Why:** `labels` manages only the keys defined in the configuration; `effective_labels` lists all
  labels on the resource, including ones set by other clients. Changing the value of a key that *is* in
  the configuration (e.g. `env`) is detected as drift.
- **Fix:** removed the label with `gcloud storage buckets update --remove-labels=probe`.
- **Takeaway:** a clean plan does not mean the resource has nothing extra on it; audits need
  `effective_labels` or a check of the real resource.
- **Docs:** https://googlecloudplatform.github.io/magic-modules/best-practices/labels-and-annotations/

## IAM

### IAM changes are eventually consistent
- A new grant typically takes about 2 minutes, sometimes 7 minutes or longer. A 403 right after apply
  is retried before changing code.
- **Docs:** https://docs.cloud.google.com/iam/docs/access-change-propagation

### Additive IAM resources only
- `*_iam_member` resources add one grant; `*_iam_binding` / `*_iam_policy` are authoritative and can
  remove grants managed elsewhere (including the owner's).

### A negative test that passed for the wrong reason
- **What happened:** "SA cannot update bucket settings" succeeded. The `$sa` variable was empty in a new
  shell, so `--impersonate-service-account=` was ignored and the command ran as the Owner. The missing
  impersonation warning in the output was the clue.
- **Fix:** guard before negative tests:
  `if (-not $sa) { throw 'SA email is empty: tests would run as your own user' }`
- **Takeaway:** first prove that a negative test exercises the identity it claims to test.

### Impersonation instead of keys
- No service account keys exist. Local checks use `--impersonate-service-account` with gcloud and,
  for BigQuery, a short-lived token from `gcloud auth print-access-token --impersonate-service-account`
  sent to the REST API.

## Testing Terraform

- `terraform test` creates real infrastructure by default; tests here use `command = plan` and a mocked
  provider, so they need no credentials and cost nothing.
- Mocked computed attributes get random values unless set in `defaults`; assertions must only compare
  attributes that are either set by the configuration or defined in the mock.
- Variable validation is tested with `expect_failures` (e.g. `allUsers`, a principal without a type
  prefix) and with positive cases for each accepted principal type.
- Mutation check: temporarily changing `objectUser` to `objectCreator` made the test fail, which proves
  the assertion works.
- Limitation: a test asserts on named resources only and cannot enumerate every IAM resource in a plan;
  a plan-JSON scan in CI is planned.
- **Docs:** https://developer.hashicorp.com/terraform/language/tests

## Repository and mirroring

- GitLab is the source of truth (CI runs there); GitHub is a push mirror. Commits go only to GitLab,
  otherwise the mirror diverges.
- A GitHub deploy key with write access (one repository, no expiry) is used instead of a personal access
  token, which expires and would stop the mirror silently.
- The commit email must be linked to the GitHub account for commits to count on the profile.
- **Docs:** https://docs.gitlab.com/user/project/repository/mirror/push/

## Data source notes (from documentation, shaped the design)

- Speedtest by Ookla Open Data is quarterly Parquet in a public S3 bucket, license CC BY-NC-SA 4.0.
- Schema changed over time: loaded-latency columns from Q4 2022, `tile_x` / `tile_y` from Q3 2023,
  so the project starts at 2023 Q3 and declares schemas explicitly.
- Past quarters can be re-aggregated (privacy requests), so loads compare the source ETag, not just
  the quarter.
- Supported regions changed in April 2026; availability of the target region is checked before loading.
- Extreme latency values exist in the source; they are flagged and excluded from the mart, not deleted.
- **Docs:** https://github.com/teamookla/ookla-open-data