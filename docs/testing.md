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
| SQL integration | `pytest -m sql` | Transform, quality checks and mart against synthetic data in BigQuery | Proposed CI: `test` | Yes, DEV | Not configured; existing SQL tests use local SQLite and run with the unit suite |
| Read-path regression | A regression job after `apply:dev` | Per-file loads and normalized reads across both physical-schema eras (2023-Q3–2026-Q1 INT32 and 2026-Q2 onward DOUBLE) | Proposed CI: `verify` | Yes, DEV | Not configured; forced manual checks are documented in the [migration runbook](runbook.md#per-file-schema-migration) |

## Current CI and local commands

The current GitLab stages are `lint`, `test`, `plan`, `policy` and `apply`.
The SQL integration and regression rows above are future coverage, not
configured pipeline jobs. In particular, `.gitlab-ci.yml` has no `verify`
stage, `pytest -m sql` marker or DEV-backed SQL test job yet.

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

The proposed SQL job should target synthetic fixtures in DEV and must not
depend on or overwrite real Ookla partitions. Keep its GCP identity least
privileged and its query jobs bounded by `maximum_bytes_billed`; document
cleanup and any temporary tables. The local SQLite SQL tests remain useful
fast feedback but are not a substitute for BigQuery syntax or behavior.

The proposed post-`apply:dev` regression job should exercise both observed
physical Parquet eras using controlled DEV input: INT32 down/up latency and
DOUBLE down/up latency. It should verify successful loads, FLOAT64 curated
latencies and DQ/publication results, and fail if either era is skipped or
fails. Avoid treating a matching ETag/`SUCCESS` watermark as evidence that the
new read path ran; use a deliberate forced regression input and account for
the fact that a forced build mutates curated DEV data. The existing runbook
documents manual forced checks and recovery after a table replacement.

Neither proposed job has been run against GCP. Before enabling either one,
decide how the synthetic files/tables are isolated, how they are cleaned up,
what DEV permissions they need, and how the regression avoids damaging
curated data used by other DEV work.
