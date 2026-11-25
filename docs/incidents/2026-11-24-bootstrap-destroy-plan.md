# Bootstrap plan proposed deleting CI identities (near miss)

| | |
|---|---|
| Date | 2026-11-02 |
| Type | Near miss (plan reviewed, never applied) |
| Root module | bootstrap |
| Severity | None realized; potential High (CI for TEST and PROD broken, human access lost) |
| Status | Resolved |

## Summary

In a new PowerShell session a `terraform plan` of `bootstrap/` proposed `0 to add, 0 to change, 54 to destroy`.
The plan would have deleted the CI service accounts for TEST and PROD with all their roles and federation bindings,
and the human-access bindings in DEV and TEST. The code was correct; two input variables were not set in the new
session and silently fell back to defaults that removed resources. The plan was read before apply and not applied.

## Impact

None. Potential impact had the plan been applied:

- `ci-plan-test`, `ci-apply-test`, `ci-plan-prod`, `ci-apply-prod` deleted with their project roles, state bucket
  access, Artifact Registry read access and Workload Identity Federation bindings: every TEST and PROD pipeline job
  would have failed at authentication.
- Human access (`iam.serviceAccountTokenCreator`, `iap.tunnelResourceAccessor`) removed in DEV and TEST.
- Recreated service accounts get new unique IDs, so any binding referring to the old ones would have needed cleanup.

## Timeline (UTC)

| Time | Event |
|---|---|
| 09:30 | New PowerShell session; `terraform plan` run directly in `bootstrap/` (not through the wrapper) for the SQL test service account change |
| 09:35 | Plan shows 54 resources to destroy, none to add; apply not started |
| 09:56 | `developer_principal` found to be `null`; with it set, 50 destroys remain (all TEST/PROD CI resources) |
| 10:36 | CI environment set found to come from a variable whose default contained only `dev` |
| 14:12 | Fix merged: CI environments derived from `local.projects`, `developer_principal` required (c4323db321d6bdbae102936cc40a9a145d163cc2) |
| 15:03 | Wrapper plan: no destroys; only the planned SQL test service account resources to create |

## Root cause

Values that decide whether resources exist were supplied only as session environment variables (`TF_VAR_*`) during
step 10. In a new session they were missing, and the variables fell back to defaults:

- `developer_principal` defaulted to `null`, which removes all human-access bindings;
- the CI environment list defaulted to `dev` only, which removes the TEST and PROD CI identities.

## Contributing factors

| Barrier | Why it did not stop the plan |
|---|---|
| Required inputs | The variables had defaults, so a missing value was not an error |
| Single source of truth | The CI environment list duplicated `local.projects` as a separate variable that could diverge |
| Bootstrap wrapper with destroy guard | The plan was run with plain `terraform plan`, bypassing `scripts/tf.ps1 -Root bootstrap`, which would have failed on deletes not listed in `bootstrap/allow-destroy` |
| `check_plan.py` | Applies to `infra/` only; bootstrap has no CI pipeline and is applied locally |
| Reading the plan | **Worked**: the destroy count was noticed before apply |

## Detection

Manual review of the plan output before apply. Time to detection: immediate.

## Resolution

1. Plan discarded, nothing applied.
2. Inputs inspected with `terraform console` (`var.developer_principal`, `local.projects`, the `for_each` sources).
3. Code fixed so that the plan depends on the repository, not on the session (see Prevention).
4. Verified: a wrapper plan without `TF_VAR_developer_principal` stops with "No value for required variable";
   with it, the plan has no destroys.

## Prevention

| Change | Where | Status |
|---|---|---|
| CI environments derived from `local.projects` (every project except `ops`); variable `ci_environments` removed | MR !7 | Done |
| `developer_principal` has no default; an explicit `null` is required to disable human access | MR !7 | Done |
| Bootstrap planned and applied only through `scripts/tf.ps1 -Root bootstrap`, which fails on any delete not listed in `bootstrap/allow-destroy` | MR !7 | Done |
| Tests: CI environment keys asserted as literals (`dev`, `test`, `prod`, never `ops`); missing `developer_principal` fails | MR !7 | Done |
| Runbook: plain `terraform plan` only for diagnostics, its result is never applied | MR !7 | Done |

## Lessons

- A default value that makes resources disappear is a trap: if a value must be provided, its absence must be an error.
- Values that decide whether resources exist belong in code (derived) or in the repository, not in a shell session.
- A guard that has to be invoked deliberately is bypassed by habit; the default path must be the safe one.
- Reading the plan before apply is the last barrier, not the only one.