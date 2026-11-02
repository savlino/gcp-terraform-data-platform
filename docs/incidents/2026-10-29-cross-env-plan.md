# 2026-10-29: TEST plan applied to DEV state

**Key takeaway:** a guard that lives on only one path does not protect the
other path. Every barrier against this change existed, but each was either off
by design in DEV, only enforced in CI, or (in the case of P10) switched off
"temporarily" for all environments at once. A protection lifted briefly and
broadly is off exactly when changes are riskiest; here it did not even matter,
because the apply bypassed CI entirely.

Severity: DEV destroyed, no TEST/PROD impact. Status: DEV restored; follow-ups
listed at the end.

## What happened

A plan meant for TEST (`-var-file=env/test.tfvars`) was created and applied
locally while the default data directory `infra\.terraform` was still
initialised against the DEV backend (`infra/dev`); `TF_DATA_DIR` was not set.
For every resource in DEV state, the TEST configuration changed `project` from
`dep-2610-dev` to `dep-2610-test`, which forces replacement. Terraform replaces
by destroying first, so the apply deleted each DEV resource and then failed to
create it in TEST with `409 Already Exists`.

## Impact

- **DEV:** all 17 managed resources deleted: dataset `network_perf` with its
  tables and data, raw bucket with its objects, runtime service account,
  network/subnet/firewalls, `alert-telegram` secret with its versions, Cloud
  Run job, Scheduler job and IAM members.
- **TEST/PROD:** none. Their state objects were last written on 2026-10-27.
  Creates in `dep-2610-test` failed with 409; the final DEV state contained no
  managed resources, so no TEST resource was created through it.
- **Data:** DEV curated and raw data lost; reloaded from the public source.
- **Alerts:** DEV Telegram alerts silently failed after recovery, because the
  recreated secret had no version (found on 2026-10-30).

## Timeline (UTC)

| Time | Event |
| --- | --- |
| 2026-10-18 | DEV created with `deletion_protection = false`, `force_destroy = true`, `delete_contents_on_destroy = true` (destroyable DEV by design). |
| 10-29 11:02 | `1cb6b71`: `--allow-destroy` added to the shared CI policy command, disabling P10 for all environments. |
| 10-29 17:25 | Last DEV state version with 17 managed resources (serial 26). |
| 10-29 17:28-17:29 | Three more DEV state versions; the last (serial 29) has no managed resources. Local apply output shows 409s for dataset, service account, network, secret and bucket in `dep-2610-test`. |
| 10-29 evening | Diagnosis from GCS object versions of the state and a read-only DEV inventory. DEV recreated through `plan:dev` -> `policy:dev` -> `apply:dev`. |
| 10-29 22:45 | `7737025`: unconditional `--allow-destroy` removed from CI; lessons learned updated. |
| 10-30 10:44-19:59 | Three `Telegram notification failed` warnings from `dev-ingest`; secret `alert-telegram` found with zero versions. |

## Root cause

Operator error: the plan was built with TEST variables against DEV state. Nothing
in the configuration ties the selected backend to `var.env` / `var.project_id`,
and a local shell silently reuses whatever backend the data directory was last
initialised with.

## Why no barrier stopped it

| Barrier | Why it did not stop the change |
| --- | --- |
| State matches tfvars | No automated check exists; it relied on the operator. The plan itself showed every resource "must be replaced" with `project` changing from DEV to TEST, but the plan was applied without that being noticed. |
| CI plan/apply path | CI initialises a fresh workspace with the environment's backend for each job and applies only a saved plan from the same commit. It could not have produced this plan, but it was bypassed: the apply ran locally with the operator's own credentials. |
| `check_plan.py` P10 | Runs only in the CI policy job, so it never saw the local plan. In addition, from 11:02 to 22:45 the CI command carried an unconditional `--allow-destroy`, so P10 was off in every environment: a one-off migration exception had been applied globally. |
| `deletion_protection` | Off in DEV since 2026-10-18 by design, not lifted for this migration (the `ext_ookla_raw` removal used `removed { destroy = false }` precisely to avoid unprotecting). It also covers only BigQuery tables and the Cloud Run job; the bucket, dataset, network, service account and secret have no equivalent setting, so it could not have saved most of DEV. |
| 409 Already Exists | A detection, not a barrier: it stopped the creates in TEST only after the destroys in DEV had succeeded. |

## How it was detected

The operator saw 409 errors on apply and reported them. The DEV deletion was
not visible in that output; it was confirmed by listing object versions of
`gs://dep-2610-ops-tf-state/infra/dev/default.tfstate` and comparing resource
addresses between versions, then by a read-only inventory of `dep-2610-dev`.

## How it was resolved

1. The applied plan file was deleted and not reused.
2. TEST state was checked to be untouched.
3. DEV was recreated from `main` through the CI pipeline (creates only).
4. DEV data was reloaded by running the ingest job.
5. The `alert-telegram` secret version must be added again by hand (Terraform
   manages only the container).

## What we change

| Change | Status |
| --- | --- |
| Remove the unconditional `--allow-destroy` from CI; a destroy override is per pipeline and reviewed for every plan it affects. | Done (`7737025`) |
| Local runbook commands set `TF_DATA_DIR` per environment, `init -reconfigure` with the environment backend, and show `terraform state list` before planning. | Done ([runbook](../runbook.md#per-file-schema-migration)) |
| Lessons-learned entries on cross-environment plans and on not persisting the P10 bypass. | Done ([lessons learned](../lessons-learned.md)) |
| Applies to environment states go through CI only; a local apply is break-glass and must run `check_plan.py` on the plan first. | Agreed, procedural |
| New policy rule: fail when a resource's `project` changes between `before` and `after`, or does not match the environment's project. This would have failed this plan even with P10 disabled. | Implemented as P12 in `check_plan.py` (`--project-id`); CI passes `dep-2610-<env>`; not yet run in a live pipeline |
| Local plan/apply only through `scripts/tf.ps1`: per-environment `TF_DATA_DIR`, backend, tfvars and project, policy check before apply, 30-minute single-use plan. | Implemented ([runbook](../runbook.md#local-terraform-only-through-scriptstfps1)); not yet run against real state |
| Replace the global destroy override with an address-specific allowance, so a migration can allow one resource without disabling P10 for everything else. | Implemented locally: `--allow-destroy-file` exempts listed addresses from P10/P13 and `tf.ps1` uses only the file. CI still has only the per-pipeline global `ALLOW_DESTROY` for P10 |
| Fail plans that relax `deletion_protection`/`force_destroy` on stateful resources, so an environment-wide flip cannot be used to delete one resource. | Implemented as P13 (address-specific exception only); runbook rule added; not yet run in a live pipeline |
| Runbook step: after an environment is recreated, add the `alert-telegram` version and verify one alert. | Proposed |
