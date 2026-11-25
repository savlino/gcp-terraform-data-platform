# Testing

What each test level proves, where it runs, how to run it locally, which
mutations have shown that a test can fail, and where coverage stops. A green
mocked or local test does not show that a live GCP service works; only the
DEV-backed levels touch GCP.

CI stages: `lint`, `test`, `plan`, `policy`, `apply`, `verify`, `promote`
([.gitlab-ci.yml](../.gitlab-ci.yml)). `apply:dev` runs in `apply`;
`apply:test` and `apply:prod` run in `promote` because `apply:test` needs
`verify:dev`, and GitLab `needs` may only point to the same or an earlier stage.

## 1. Test matrix

| Level | Tool | What it proves | Where it runs (job, stage) | Needs GCP |
| --- | --- | --- | --- | --- |
| Formatting and lint | `terraform fmt -check`, `terraform validate` (`bootstrap`, `infra`), `tflint`, Ruff (`app/src`, `app/tests`, `scripts`) | Terraform is formatted and valid without a backend; TFLint rules from `.tflint.hcl`; Python lint | `fmt`, `validate`, `tflint` (`lint`); Ruff in `python:lint-test` (`test`); MR and `main` | No |
| Terraform module tests | `terraform test`, `command = plan`, mocked `google`/`google-beta` providers: `bootstrap/tests/{registry,wif}.tftest.hcl`, `infra/tests/{bigquery,dev,iam,ingest_job,ingest_root,network,secrets,storage}.tftest.hcl` | Resource wiring, IAM roles and members of named resources, protection flags, variable validation (`expect_failures`): CI identities for exactly `dev`/`test`/`prod`, `ci-sqltest-dev` with only `bigquery.jobUser` on DEV and no registry access, no human access to PROD, `sql_tests` only in DEV, digest pinning overrides the tag | `terraform-test` (`test`), MR and `main` | No |
| Terraform plan guards (local) | `infra/tests/check-secret-plan.ps1`, `infra/tests/check-ingest-plan.ps1` | Parse the JSON events of the mocked secrets and ingest tests: a secret container but no secret version; one job and one schedule, pause defaults per environment, region independence, no VPC attachment or secret injection, application settings, scoped IAM only | Local only | No |
| Plan policy | `scripts/check_plan.py` on the plan JSON of `plan:<env>` | Rules P0-P13 on the whole environment plan (no secret versions or SA keys, no authoritative IAM or basic roles, no public principals, bucket, firewall and VM rules, labels, no data-resource deletes without an allow entry, PROD rules, project of every change equals the environment, no protection relaxed without an allow entry); stale or duplicate allow-destroy entries fail | `policy:<env>` (`policy`), needs `plan:<env>`; MR and `main`. Locally inside `scripts/tf.ps1 -Env <env> -Action plan` | No for the check; the plan itself is refreshed against GCP |
| Plan policy unit tests | `pytest scripts/tests/test_check_plan.py` | Each rule passes and fails on synthetic plans, including stale entries and P12 derivation, convention and exemption paths | `python:lint-test` (`test`) | No |
| Bootstrap destroy guard | `scripts/check_bootstrap_plan.py`, run by `scripts/tf.ps1 -Root bootstrap -Action plan`; tests in `scripts/tests/test_check_bootstrap_plan.py` | Any delete or replacement in the bootstrap plan fails unless listed in `bootstrap/allow-destroy`; stale entries fail. Destroy-only: no other bootstrap rule | Guard: local only (bootstrap is never applied from CI). Unit tests: `python:lint-test` (`test`) | Guard: yes (local plan); unit tests: no |
| Image tag check | `scripts/check_image_tag.py git`; tests in `scripts/tests/test_check_image_tag.py` | `image_tag` and `image_digest` are identical in the three tfvars; `image_tag` resolves to the last commit that changed `IMAGE_INPUTS` (full-SHA comparison); shallow clones and unknown tags fail; `IMAGE_INPUTS` covers every `COPY` source of `app/Dockerfile` | `check:image-tag` (`lint`, `GIT_DEPTH: "0"`); unit tests in `python:lint-test`; MR and `main` | No |
| Pre-apply image check | `image:check:<env>`: `gcloud artifacts docker images describe` as `ci-apply-<env>` | `<image_repository>:<image_tag>` exists in the shared ops repository; a pinned `image_digest` is that tag's digest. `apply:<env>` needs it | `image:check:dev`, `:test`, `:prod` (`apply`), `main` only | Yes, ops Artifact Registry (read) |
| Python unit tests | `pytest app/tests/unit` | Work selection, layout, source footer contract, schema checks, DQ decisions, build orchestration, alerts and secret-safe errors; SQL rendering and mart logic on SQLite | `python:lint-test` (`test`), MR and `main` | No |
| SQL tests on synthetic data | `pytest -m sql app/tests/sql` | Production `stage`, `quality`, `mart` and `publish` SQL in BigQuery on synthetic Parquet of both source eras (latency INT32 and DOUBLE) with NULL latency inputs; expected values computed by hand. Details below | `test:sql` (`test`) as `ci-sqltest-dev`, MR and `main`; the job fails if any test is skipped | Yes, DEV dataset `sql_tests` |
| Post-deploy regression | `verify:dev`: forced `build --type mobile --force` on `dev-ingest` for `VERIFY_QUARTER_INT32` (2025-Q4) and `VERIFY_QUARTER_DOUBLE` (2026-Q2) | The deployed image reads both source eras: each execution succeeds, `load_log` has a mobile `SUCCESS` row for the quarter from the last 30 minutes, `ookla_tiles` has mobile rows for it | `verify:dev` (`verify`), `main` only, after `apply:dev`; `apply:test` needs it | Yes, DEV |

### SQL tests (`app/tests/sql/`)

- Selected by the `sql` marker and skipped unless `SQL_TEST_DATASET` is set to
  `<project_id>.<dataset_id>` (DEV: `dep-2610-dev.sql_tests`). GCP clients are
  created only in the session fixture, so collection needs no credentials.
  Module names are `test_bq_*.py`; `tests/unit/test_layout.py` fails if two
  test modules share a basename.
- Each test creates its own copies of `ookla_tiles`, `load_log` and
  `mart_quarterly_performance` from `schemas/*.json` with the Terraform
  partitioning, clustering and `require_partition_filter`. Table names carry a
  per-run prefix; the session fixture deletes them in teardown, also after
  failures. The dataset's one-hour table expiration is a safety net for
  cancelled runs. `network_perf` and buckets are never touched.
- Inputs are loaded from memory (`load_table_from_json`, `load_table_from_file`
  with pyarrow Parquet). SQL is the production `app/src/ingest/sql/*.sql`,
  rendered by `ingest.build.sql(..., tables=...)` and run through production
  `query()` with `maximum_bytes_billed` (100 MiB).
- Cases:
  - `stage`: bbox filter, kbps to Mbps, identical target types for both eras,
    NULL latency stays NULL in both eras;
  - `quality`: exact planted ERROR counts, empty region, latency sentinel as
    WARN, `null_down_rows` WARN equal to the real NULL count with no ERROR;
  - `mart`: tests-weighted averages, outlier affects only its own latency
    average, quarter-over-quarter only for the adjacent quarter, NULL latency
    excluded from latency averages but included in speeds, tiles and tests;
  - `publish`: replacing one network type and quarter leaves the other type
    and quarters unchanged.

## 2. Running each level locally

**PowerShell** on Windows, from the repository root, with the app virtual
environment in `app\.venv`. Terraform tests need no credentials (mocked
providers, `init -backend=false`).

```powershell
$python = '.\app\.venv\Scripts\python.exe'

# Formatting and lint
terraform fmt -check -recursive bootstrap
terraform fmt -check -recursive infra
terraform fmt -check -recursive modules
terraform -chdir=bootstrap init -backend=false -lockfile=readonly
terraform -chdir=bootstrap validate
terraform -chdir=infra init -backend=false -lockfile=readonly
terraform -chdir=infra validate
tflint --recursive --config="$PWD\.tflint.hcl"
Push-Location app; & .\.venv\Scripts\python.exe -m ruff check src tests; Pop-Location
& $python -m ruff check --config app/pyproject.toml scripts

# Terraform module tests and local plan guards
terraform -chdir=bootstrap test
terraform -chdir=infra test
& .\infra\tests\check-secret-plan.ps1
& .\infra\tests\check-ingest-plan.ps1

# Plan policy, bootstrap guard and image tag unit tests (synthetic input)
& $python -m pytest scripts\tests

# Plan policy / bootstrap destroy guard on a real plan (needs ADC)
.\scripts\tf.ps1 -Env dev -Action plan
.\scripts\tf.ps1 -Root bootstrap -Action plan   # requires $env:TF_VAR_developer_principal

# Image tag check (no GCP; needs full git history)
& $python scripts\check_image_tag.py

# Python unit tests
Push-Location app; & .\.venv\Scripts\python.exe -m pytest tests\unit; Pop-Location
```

Mock providers replace cloud calls, not root input variables: the bootstrap
test files set `developer_principal`, `human_access_environments` and
`enable_sql_test_access` in their `variables` blocks, so deployment `TF_VAR_*`
values cannot change the scenarios. A missing required bootstrap input is
checked with the noninteractive wrapper procedure in the
[bootstrap runbook](runbook.md#bootstrap-only-through-scriptstfps1--root-bootstrap),
not with `expect_failures`.

**Pre-apply image check** (manual equivalent of `image:check:<env>`):

```powershell
$ref = (& $python scripts\check_image_tag.py ref dev) -split ' '
gcloud artifacts docker images describe "$($ref[0]):$($ref[1])" --project dep-2610-ops "--format=value(image_summary.digest)"
# Must print a digest; if tfvars pins one ($ref[2] is not '-'), it must equal $ref[2].
```

**SQL tests** against DEV. ADC impersonates `ci-sqltest-dev`; this needs
`roles/iam.serviceAccountTokenCreator` on that account in `dep-2610-ops`,
which bootstrap does not grant to humans, so it is a manual, temporary step:

```powershell
gcloud auth application-default login --impersonate-service-account=ci-sqltest-dev@dep-2610-ops.iam.gserviceaccount.com
Set-Location app
$env:SQL_TEST_DATASET = "dep-2610-dev.sql_tests"
.\.venv\Scripts\python.exe -m pytest -m sql tests\sql -v
Remove-Item Env:SQL_TEST_DATASET
Set-Location ..
```

Without `SQL_TEST_DATASET`, `pytest tests` from `app` runs the unit tests and
reports the SQL tests as skipped; no credentials are needed for collection.

**Post-deploy regression**: CI only (`verify:dev`). The manual equivalent is
the forced rebuild in the [runbook](runbook.md#post-apply-verification-verifydev).

## 3. Mutation checks

A test is trusted once a deliberate defect makes it fail. Each mutation is a
temporary change that must be reverted. Result and date record only runs that
were actually performed; empty cells mean the check is defined but not
recorded.

| Mutation | Expected failing test | Result | Date |
| --- | --- | --- | --- |
| `roles/storage.objectUser` changed to `roles/storage.objectCreator` in a Terraform IAM grant | The `terraform test` assertion on that role (`infra/tests/iam.tftest.hcl` `runtime_scoped_grants` or `bootstrap/tests/wif.tftest.hcl` `all_environment_federation`; [lessons learned](lessons-learned.md#testing-terraform) does not say which) | Failed as expected | |
| WIF pool path built from the project ID instead of the project number | `bootstrap/tests/wif.tftest.hcl` `dev_sql_test_identity_is_job_user_only` (literal pool path in the federation member) | | |
| `"test"` added to the `ci-sqltest-dev` map | `bootstrap/tests/wif.tftest.hcl` `dev_sql_test_identity_is_job_user_only` (exactly one SA keyed `dev`) | | |
| `ci_apply_reader` granted to `ci-sqltest-dev` | `bootstrap/tests/wif.tftest.hcl` `dev_sql_test_identity_is_job_user_only` (no registry grant names the SA) | | |
| `COALESCE(avg_lat_down_ms, 0)` in `app/src/ingest/sql/stage.sql` | `test_bq_stage.py::test_stage_keeps_null_latency_as_null` (both eras) | | |
| NULL latency counted as 0 in the latency average of `app/src/ingest/sql/mart.sql` | `test_bq_mart.py::test_null_latency_is_excluded_from_latency_averages_only` | | |
| Broken production SQL on an MR branch | `test:sql` fails (see [section 4](#4-negative-ci-evidence)) | | |
| `SQL_TEST_DATASET` unset in `test:sql` | `test:sql` fails on `skipped="0"` instead of passing with skipped tests | | |
| P12 or P13 removed from `check_plan.py`, or stale-entry detection disabled | The matching tests in `scripts/tests/test_check_plan.py` | | |
| tfvars `image_tag = "986c22e"` (state of `main` at `381df6d`) | `check:image-tag`: `image_tag 986c22e is not a commit in this repository` | Failed as expected (real pipeline on `ci/image-tag-guards` before it was merged with `main`) | 2026-11-24 |
| `COPY app/BUILD.md ...` added to `app/Dockerfile` (source outside `IMAGE_INPUTS`) | `test_check_image_tag.py::test_image_inputs_cover_every_dockerfile_copy_source` | Failed as expected (local run) | 2026-11-25 |

## 4. Negative CI evidence

`test:sql` was run on an MR branch with deliberately broken production SQL; the
pipeline failed on `test:sql` and blocked the MR:

- MR view: [docs/screenshots/mr-sql-test-failure.png](screenshots/mr-sql-test-failure.png)
- Job log: [docs/screenshots/job-test-sql-failure.png](screenshots/job-test-sql-failure.png)

Screenshots must not show personal data (emails, project numbers, tokens).

`check:image-tag` failed on a real pipeline on 2026-11-24 when a branch still
carried the unpublished tag `986c22e` (see the mutation table above); no
screenshot is stored.

## 5. Cross-environment data consistency

After all three environments have processed the same quarters, `ookla_tiles`
row counts per network type and quarter must be identical in DEV, TEST and
PROD: the code, image and source files are the same. Needs BigQuery read
access in all three projects (bootstrap grants humans no PROD access). The
query filters on `quarter_start`, which the table requires.

```powershell
$counts = @{}
foreach ($e in 'dev', 'test', 'prod') {
  $p = "dep-2610-$e"
  $out = bq --project_id=$p --location=europe-southwest1 query --use_legacy_sql=false --format=csv --maximum_bytes_billed=104857600 "SELECT network_type, quarter_start, COUNT(*) AS tile_rows FROM ``$p.network_perf.ookla_tiles`` WHERE quarter_start >= '2023-07-01' GROUP BY 1, 2 ORDER BY 1, 2"
  if ($LASTEXITCODE -ne 0) { throw "bq failed for $e" }
  $counts[$e] = $out -join "`n"
}
if ($counts.dev -ne $counts.test -or $counts.dev -ne $counts.prod) {
  $counts.GetEnumerator() | ForEach-Object { "== $($_.Key)"; $_.Value }
  throw 'ookla_tiles counts differ between environments'
}
'PASS: identical ookla_tiles counts'; $counts.dev
```

| Date | Quarters compared | Result |
| --- | --- | --- |
| YYYY-MM-DD | | |

## 6. Known coverage boundaries

- `terraform test` asserts on named resources only; it cannot enumerate every
  resource of a type in a plan. The plan-JSON policy in CI covers that gap for
  environment plans.
- `check_plan.py` is not applied to bootstrap. The bootstrap guard is
  destroy-only (`check_bootstrap_plan.py`) and runs locally through
  `scripts/tf.ps1 -Root bootstrap`; other bootstrap changes rely on review.
- SQL tests run only in DEV (`sql_tests`); TEST and PROD SQL behavior is
  inferred from the identical image and SQL files.
- `verify:dev` checks two quarters of `mobile` only, presence of rows and a
  fresh `SUCCESS` watermark; not `fixed`, other quarters, column types or
  individual DQ rules.
- `image:check:<env>` requires the image in every environment, also where the
  ingest job is disabled.
- `python:lint-test` runs `pytest app/tests/unit`, so CI does not exercise SQL
  test collection without `SQL_TEST_DATASET`.
- No load or performance tests.