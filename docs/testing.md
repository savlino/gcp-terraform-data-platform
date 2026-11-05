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
| SQL integration | `pytest -m sql tests/sql` (from `app/`) | Production `stage`, `quality`, `mart` and `publish` SQL on synthetic data in BigQuery | Locally against DEV; proposed CI: `test` | Yes, DEV (`sql_tests` dataset) | Tests exist and skip without `SQL_TEST_DATASET`; no CI job; not yet run against GCP |
| Read-path regression | A regression job after `apply:dev` | Per-file loads and normalized reads across both physical-schema eras (2023-Q3–2026-Q1 INT32 and 2026-Q2 onward DOUBLE) | Proposed CI: `verify` | Yes, DEV | Not configured; forced manual checks are documented in the [migration runbook](runbook.md#per-file-schema-migration) |

## Current CI and local commands

The current GitLab stages are `lint`, `test`, `plan`, `policy` and `apply`.
The SQL integration and regression rows above are not configured pipeline
jobs. `.gitlab-ci.yml` has no `verify` stage and no DEV-backed SQL test job;
the `sql` marker and tests in `app/tests/sql/` exist but run only manually.

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

The current `python:lint-test` job still runs `pytest app/tests/unit` only, so
CI does not yet exercise collection of `app/tests/sql`.

The local SQLite tests in `tests/unit` remain fast feedback but are not a
substitute for BigQuery syntax or behavior.

The proposed post-`apply:dev` regression job should exercise both observed
physical Parquet eras using controlled DEV input: INT32 down/up latency and
DOUBLE down/up latency. It should verify successful loads, FLOAT64 curated
latencies and DQ/publication results, and fail if either era is skipped or
fails. Avoid treating a matching ETag/`SUCCESS` watermark as evidence that the
new read path ran; use a deliberate forced regression input and account for
the fact that a forced build mutates curated DEV data. The existing runbook
documents manual forced checks and recovery after a table replacement.

Neither proposed CI job exists or has been run against GCP. Before enabling either one,
decide how the synthetic files/tables are isolated, how they are cleaned up,
what DEV permissions they need, and how the regression avoids damaging
curated data used by other DEV work.
