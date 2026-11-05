# Testing strategy

This matrix distinguishes checks configured today from proposed DEV-backed
checks. A green local or mocked test does not establish that a live GCP service
works.

| Level | Tool | What it checks | Where it runs | GCP required | Status |
| --- | --- | --- | --- | --- | --- |
| Syntax and style | `terraform fmt`, `terraform validate`, `tflint`, Ruff | Terraform formatting/configuration and Python lint/style | CI: `lint` | No | Configured |
| Terraform modules | `terraform test` with mocked providers | Module wiring, resource properties and variable validation | CI: `test` | No | Configured; mocks do not call GCP |
| Plan policy | `scripts/check_plan.py` and its synthetic tests | P0-P13 against a whole-environment Terraform plan JSON | CI: `policy`; plan JSON is produced by `plan:<env>` | No for the policy check; the input plan is cloud-refreshed in CI | Configured |
| Python logic | `pytest app/tests/unit` | Work selection, source footer contract, DQ decisions, alerts and secret-safe error handling | CI: `python:lint-test` in `test` | No | Configured |
| SQL integration | `pytest -m sql tests/sql` (from `app/`) | Production `stage`, `quality`, `mart` and `publish` SQL on synthetic data in BigQuery | Locally against DEV; CI: `test:sql` in `test` (MR and `main`) as `ci-sqltest-dev` | Yes, DEV (`sql_tests` dataset) | Configured; first CI run and first run against GCP pending. The job fails if any SQL test is skipped |
| Read-path regression | `verify:dev`: forced `build --type mobile --force` on the DEV Cloud Run job for `VERIFY_QUARTER_INT32` (2025-Q4) and `VERIFY_QUARTER_DOUBLE` (2026-Q2) | Each execution succeeds, `load_log` has a `SUCCESS` row for mobile and that quarter from the last 30 minutes, and `ookla_tiles` has mobile rows for it | CI: `verify`, `main` only, after `apply:dev`; `apply:test` needs it | Yes, DEV | Configured; first pipeline run pending. Does not check column types or DQ details; see the [runbook](runbook.md#post-apply-verification-verifydev) |

## Current CI and local commands

The GitLab stages are `lint`, `test`, `plan`, `policy`, `apply`, `verify` and
`promote`. `apply:dev` runs in `apply`; `apply:test` and `apply:prod` run in
`promote` because `apply:test` needs `verify:dev`, and GitLab `needs` may only
point to jobs in the same or an earlier stage.

Commands below are **PowerShell 7 on Windows**, from the repository root.
Terraform tests use mocked providers and require initialization without a
remote backend:

```powershell
terraform fmt -check -recursive bootstrap
terraform fmt -check -recursive infra
terraform fmt -check -recursive modules

terraform -chdir=bootstrap init -backend=false -lockfile=readonly
terraform -chdir=bootstrap validate
terraform -chdir=infra init -backend=false -lockfile=readonly
terraform -chdir=infra validate
terraform -chdir=bootstrap test
terraform -chdir=infra test

Set-Location app
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m pytest tests\unit
Set-Location ..
.\app\.venv\Scripts\python.exe -m ruff check --config app/pyproject.toml scripts
.\app\.venv\Scripts\python.exe -m pytest scripts\tests
```

Mock providers replace cloud calls, not root input variables. The bootstrap
test files explicitly set `developer_principal`,
`human_access_environments` and `enable_sql_test_access` in their file-level
`variables` blocks, so deployment `TF_VAR_*` values for these inputs cannot
change the test scenarios. Individual runs override these fixtures to test
enabled access and invalid inputs. Keep deployment variables intact when
running tests; clearing them can change the next real plan.
CI environments are derived from the project map, with literal assertions for
`dev`, `test`, `prod` and exclusion of `ops`. The explicit-null fixture checks
that no human bindings are created; negative runs reject groups, bare emails,
workload identities and PROD human access. Missing required input is checked
with the noninteractive wrapper procedure in the
[bootstrap runbook](runbook.md#bootstrap-only-through-scriptstfps1--root-bootstrap),
not with `expect_failures`, which handles validation rather than missing inputs.

In CI, Ruff and Python tests run with Python 3.12. Terraform formatting,
validation and mocked tests use the pinned Terraform image; TFLint uses its
pinned image. The Terraform JSON policy checker consumes plans from the
environment plan jobs, so its decision is only as current as the backend and
saved plan it receives. See [CI configuration](../.gitlab-ci.yml).

## DEV-backed checks

### BigQuery SQL tests (`app/tests/sql/`)

- Selected by the `sql` marker and skipped unless `SQL_TEST_DATASET` is set to
  `<project_id>.<dataset_id>` (DEV: `dep-2610-dev.sql_tests`). `testpaths` is
  `tests`, so `pytest`, `pytest tests` and `pytest -m sql` collect them and
  report them as skipped. GCP clients and `SQL_TEST_DATASET` are touched only
  inside the session fixture, never at import, so collection needs no
  credentials. Modules are named `test_bq_*.py`; a unit test
  (`tests/unit/test_layout.py`) fails if two test modules share a basename.
- They never touch `network_perf` or buckets. Each test creates its own copies
  of `ookla_tiles`, `load_log` and `mart_quarterly_performance` from
  `schemas/*.json`, with the Terraform partitioning, clustering and
  `require_partition_filter`.
- Every table name starts with a per-run prefix (`t<UTC timestamp>_<random>_`).
  The session fixture deletes all tables with that prefix in teardown, also
  after failures, and fails if a deletion fails. The dataset's one-hour table
  expiration is only a safety net.
- Inputs are loaded from memory (`load_table_from_json`, and
  `load_table_from_file` for synthetic Parquet built with pyarrow); no GCS.
  Both source eras are covered: latency down/up as INT32 and as DOUBLE. Each
  synthetic source file has two in-region tiles with NULL `avg_lat_down_ms`
  (one also NULL `avg_lat_up_ms`).
- SQL is the production `app/src/ingest/sql/*.sql`, rendered by
  `ingest.build.sql(..., tables=...)` and run through production `query()`, so
  every query job has `maximum_bytes_billed` (100 MiB in the tests; DML
  bills at least 10 MB per referenced table). Load jobs
  are not billed per byte and have no such setting.
- Cases: bbox filter, kbps to Mbps and target types for both eras (`stage`);
  NULL latency stays NULL in both eras (`stage`); exact ERROR counts, empty
  region, latency-sentinel WARN and `null_down_rows` WARN equal to the real
  NULL count with no ERROR (`quality`); tests-weighted averages, outlier
  isolation, NULL latencies excluded from latency numerator and denominator
  but included in speeds, tiles and tests, and quarter-over-quarter (`mart`); replacing one network type leaves the other unchanged (`publish`).
  Expected values are computed by hand in comments.

Run (**PowerShell 7 on Windows**, ADC impersonating `ci-sqltest-dev`). Impersonation
needs `roles/iam.serviceAccountTokenCreator` on that account in `dep-2610-ops`;
bootstrap does not grant it to humans (its human Token Creator grant is on the
environment projects only), so that permission is a manual, temporary step:

```powershell
gcloud auth application-default login --impersonate-service-account=ci-sqltest-dev@dep-2610-ops.iam.gserviceaccount.com
Set-Location app
$env:SQL_TEST_DATASET = "dep-2610-dev.sql_tests"
.\.venv\Scripts\python.exe -m pytest -m sql tests\sql -v
Remove-Item Env:SQL_TEST_DATASET
Set-Location ..
```

CI-equivalent collection check without GCP (**bash**, Linux, from the
repository root, as in `python:lint-test`). Credentials point to a missing
file, so any credential lookup during collection would fail:

```bash
python -m pip install --no-cache-dir -e "./app[dev]"
unset SQL_TEST_DATASET
export GOOGLE_APPLICATION_CREDENTIALS=/nonexistent/credentials.json
pytest -rs app/tests                 # unit tests pass, SQL tests skipped
(cd app && pytest -rs -m sql)        # SQL tests skipped, unit tests deselected
```

The `python:lint-test` job still runs `pytest app/tests/unit` only, so CI does
not exercise this no-GCP collection path; `test:sql` collects `app/tests/sql`
with `SQL_TEST_DATASET` set.

`test:sql` (**bash**, `python:3.12-slim`) authenticates through WIF as
`ci-sqltest-dev` with the `.gcp_auth` template (no gcloud in the image) and runs:

```bash
python -m pip install --no-cache-dir -e "./app[dev]"
SQL_TEST_DATASET=dep-2610-dev.sql_tests pytest -m sql app/tests/sql --junitxml=sql-junit.xml
grep -q 'skipped="0"' sql-junit.xml   # a skipped run fails the job
```

The JUnit report is uploaded even when tests fail. A cancelled job skips the
session teardown; the one-hour table expiration in `sql_tests` removes leftovers.

The local SQLite tests in `tests/unit` remain fast feedback but are not a
substitute for BigQuery syntax or behavior.

## Running each level locally

| Level | Command (**PowerShell 7**, repository root unless noted) |
| --- | --- |
| Syntax and style | `terraform fmt -check -recursive infra`, `terraform -chdir=infra validate`, `.\app\.venv\Scripts\python.exe -m ruff check --config app/pyproject.toml scripts` |
| Terraform modules | `terraform -chdir=infra test`, `terraform -chdir=bootstrap test` |
| Plan policy | `.\app\.venv\Scripts\python.exe -m pytest scripts\tests` (synthetic plans); a real plan goes through `.\scripts\tf.ps1 -Env <env> -Action plan`, which runs `check_plan.py` |
| Python logic | `Set-Location app; .\.venv\Scripts\python.exe -m pytest tests\unit` |
| SQL integration | see [BigQuery SQL tests](#bigquery-sql-tests-apptestssql) (needs DEV and impersonation) |
| Read-path regression | CI only (`verify:dev`); the manual equivalent is the [forced rebuild](runbook.md#per-file-schema-migration) |

## Mutation checks

A test is only trusted once a deliberate defect makes it fail. Each check
below is a temporary change that must be reverted. "Result" records only what
was actually run; an empty result means the check is defined but not yet
performed.

| Test | Mutation | Expected failure | Result |
| --- | --- | --- | --- |
| Bootstrap IAM test | `objectUser` changed to `objectCreator` | The role assertion fails | Run once; see [lessons learned](lessons-learned.md#testing-terraform) |
| Bootstrap WIF test (SQL test SA) | (1) pool path built with the project ID, (2) `"test"` added to the SQL test SA map, (3) `ci_apply_reader` granted to the SQL test SA | Assertions 1, 2 and 3 fail respectively | Not recorded |
| SQL test `stage` (NULL latency) | Wrap the latency column in `COALESCE(x, 0)` in `stage.sql` | NULL-stays-NULL test fails in both eras | Not run against BigQuery |
| Plan policy P12/P13 and stale entries | Remove the rule or the allow-file entry | The matching test in `scripts/tests` fails | Not recorded |

The check IDs B8 and D5 do not appear anywhere in this repository; if they
come from an external checklist, add their results to this table with the
command used and the date.

## Why these tests exist

The [2026-10-29 incident](incidents/2026-10-29-cross-env-plan.md) (a TEST plan
applied to DEV state) is the reason for the wrapper `scripts/tf.ps1` and policy
rules P12 and P13. The 2026-Q2 source type change, hidden by a matching
watermark, is the reason for `verify:dev`. See
[lessons learned](lessons-learned.md#testing-lessons).

`verify:dev` exercises both physical Parquet eras (INT32 and DOUBLE latency)
through the deployed Cloud Run job. It uses `--force`, so a matching ETag cannot
skip the read path, and fails if either quarter fails. A forced build overwrites
the curated DEV mobile partition for each quarter with the same raw input and
rebuilds the mart. Raw files for both quarters must already be in the DEV raw
bucket. The 30-minute freshness window can be satisfied by a manual forced
build made shortly before the job; the job does not compare `loaded_at` with
its own start time. It does not assert FLOAT64 latency columns or individual DQ
rules; the job's own DQ gate must pass for `SUCCESS` to be written. The
runbook documents manual forced checks and recovery after a table replacement.
