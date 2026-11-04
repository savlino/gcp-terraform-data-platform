# Bootstrap

This root is applied locally with user Application Default Credentials (ADC).
Do not use service-account keys. It enables APIs, creates the ops state bucket
and budget, and owns GitLab federation/CI identities and optional human grants.
CI never applies this root.

## Before starting

- Check the latest major version of the [Google provider in the Terraform Registry](https://registry.terraform.io/providers/hashicorp/google/latest). `versions.tf` currently uses `~> 8.0`; update that major constraint if the Registry's current major has changed.
- Confirm that the billing account uses EUR. The budget defaults to EUR 20 and Google requires the specified currency to match the billing account's currency. If it does not use EUR, pass the matching code with `-var="budget_currency_code=..."`.
- The default bucket name, `dep-2610-ops-tf-state`, must be globally unique. If you change `state_bucket_name`, also change the literal `bucket` value in `backend.tf`.
- Ensure the four projects already exist and are linked to the intended billing account.

## Required permissions

The user applying this configuration needs:

- `roles/resourcemanager.projectCreator` at the applicable organization or folder if they still need to create projects.
- `roles/billing.user` on the billing account and `roles/resourcemanager.projectBillingManager` on each project to link projects to billing.
- `roles/serviceusage.serviceUsageAdmin` on all four projects to enable APIs.
- `roles/storage.admin` on the ops project to create/manage the state bucket.
- `roles/billing.costsManager` on the billing account to create the budget (or a broader billing role containing `billing.budgets.create`).
- `roles/serviceusage.serviceUsageConsumer` on the ops project for the provider's quota/billing project override.
- Permission to read project metadata and obtain project numbers for all four projects.
- Artifact Registry administration in ops and permission to generate the Cloud
  Run service identities in DEV/TEST/PROD. `google-beta` is constrained to
  `~> 8.7.0` for the documented beta-only service-identity resource; the stable
  provider constraint remains unchanged.
- Ops workload identity pool/provider administration and service-account
  creation/IAM management; project-IAM administration on selected environment
  projects and DEV/TEST for optional human grants. Review the
  [role catalogue and trust trade-offs](../docs/security.md#gitlab-wif-and-ci-role-catalogue).

Use the narrowest applicable scope and verify current role permissions in Google Cloud IAM before applying.

## APIs enabled

| API | Project(s) | Reason |
| --- | --- | --- |
| `cloudresourcemanager.googleapis.com` | All four | Read project metadata and numbers. |
| `serviceusage.googleapis.com` | All four | Enable required APIs and attribute API usage. |
| `iam.googleapis.com` | All four | Manage service accounts and IAM used by the platform. |
| `iamcredentials.googleapis.com` | All four | Support short-lived credentials for keyless service-account impersonation. |
| `sts.googleapis.com` | Ops | Exchange GitLab ID tokens through Workload Identity Federation. |
| `storage.googleapis.com` | All four | Create the ops state bucket and environment raw-data buckets. |
| `billingbudgets.googleapis.com` | Ops | Create and manage the portfolio budget. |
| `compute.googleapis.com` | Dev, test, prod | Create VPCs, firewall rules, and the optional demo VM. |
| `bigquery.googleapis.com` | Dev, test, prod | Create datasets, tables, and run data processing queries. |
| `run.googleapis.com` | Dev, test, prod | Run the ingestion workload as a Cloud Run Job. |
| `cloudscheduler.googleapis.com` | Dev, test, prod | Trigger the weekly ingestion job. |
| `iap.googleapis.com` | Dev, test, prod | Enable optional IAP TCP access to the demo VM. |
| `secretmanager.googleapis.com` | Dev, test, prod | Store the alert webhook secret. |
| `artifactregistry.googleapis.com` | Ops, dev, test, prod | Ops hosts shared images; environment API activation is retained for compatibility. |
| `logging.googleapis.com` | Dev, test, prod | Provide access to application and managed-service logs. |

## Apply using local state first

This one-time run, before the state bucket exists, is the only bootstrap plan and
apply outside [scripts/tf.ps1](../scripts/tf.ps1). Once the state is in GCS,
bootstrap is planned and applied only with `.\scripts\tf.ps1 -Root bootstrap
-Action plan|apply`. Inputs come from `TF_VAR_*` or a gitignored
`personal.auto.tfvars`, deletes are guarded by
`bootstrap/allow-destroy`, and the plan is single-use for 30 minutes. See the
[runbook](../docs/runbook.md#bootstrap-only-through-scriptstfps1--root-bootstrap).

**PowerShell 7 on Windows**, from the repository root:

```powershell
gcloud auth application-default login
gcloud auth application-default set-quota-project dep-2610-ops
Set-Location .\bootstrap

# Terraform must not see backend.tf during the initial local-state run.
Move-Item .\backend.tf .\backend.tf.disabled
terraform init
terraform fmt -check
terraform validate
$env:TF_VAR_developer_principal = Read-Host 'Required user:<email> principal'
terraform plan -var="billing_account_id=YOUR-BILLING-ACCOUNT-ID" -var="gitlab_project_path=YOUR-NAMESPACE/YOUR-PROJECT" -out=bootstrap.tfplan
terraform apply bootstrap.tfplan
```

Replace both placeholders with the existing billing account ID and exact GitLab
project path. Do not commit credentials or access tokens. If the account is not
denominated in EUR, add `-var="budget_currency_code=THE-ACCOUNT-CURRENCY"` to plan.
A saved-plan apply already includes its input values.

The bucket lifecycle rule deletes noncurrent object versions once five newer versions exist. Keep `state_bucket_force_destroy` false.

## Migrate the local state to GCS

After the first apply has successfully created the bucket, restore the GCS backend configuration and migrate the local state. Stay in `bootstrap/` and run:

```powershell
Move-Item .\backend.tf.disabled .\backend.tf
terraform init -migrate-state
```

When Terraform asks whether to copy existing state to the new backend, answer `yes`. Verify the state is now remote:

```powershell
terraform state list
gcloud storage ls gs://dep-2610-ops-tf-state/bootstrap/
```

The GCS backend state object is expected under the `bootstrap/` prefix (normally `bootstrap/default.tfstate`). Do not delete the local state file until you have confirmed that the remote state exists and Terraform can read it.

```powershell
terraform plan -var="billing_account_id=YOUR-BILLING-ACCOUNT-ID" -var="gitlab_project_path=YOUR-NAMESPACE/YOUR-PROJECT"
# Expected: No changes.
Remove-Item terraform.tfstate, terraform.tfstate.backup -ErrorAction SilentlyContinue
```

## API activation fallback

`billingbudgets.googleapis.com` is included in the Terraform-managed ops APIs. If the initial apply fails because the Cloud Billing Budget API is not enabled, **PowerShell 7**:

```powershell
gcloud services enable billingbudgets.googleapis.com --project=dep-2610-ops
```

The caller needs permission to enable the API. Re-run `terraform apply` after the command succeeds.

## Verify

**PowerShell 5.1 or 7**, from the repository root:

```powershell
gcloud storage buckets describe gs://dep-2610-ops-tf-state --format="yaml(name,versioning,iamConfiguration.uniformBucketLevelAccess,iamConfiguration.publicAccessPrevention)"
gcloud storage ls gs://dep-2610-ops-tf-state/bootstrap/
$env:TF_VAR_billing_account_id = 'YOUR-BILLING-ACCOUNT-ID'
$env:TF_VAR_gitlab_project_path = 'YOUR-NAMESPACE/YOUR-PROJECT'
.\scripts\tf.ps1 -Root bootstrap -Action plan
```

Confirm object versioning is enabled, public access prevention is enforced, and a state object exists under `bootstrap/`. In the Cloud Console, confirm the budget covers the four project IDs and is visible in **Billing > Budgets & alerts**. The plan should report `0 to add, 0 to change, 0 to destroy`.

Negative check: run a plan with a non-three-letter currency code; variable validation must reject it before any changes:

```powershell
$env:TF_VAR_budget_currency_code = 'EURO'
.\scripts\tf.ps1 -Root bootstrap -Action plan   # must throw: terraform plan failed
Remove-Item Env:\TF_VAR_budget_currency_code
```

## GitLab federation and human access

Required input `gitlab_project_path` is the exact, case-sensitive path, for
example `savlino/gcp-terraform-data-platform`, without `https://gitlab.com/`.
`local.ci_environments` derives every project except ops from `local.projects`.
It creates `ci-plan-<env>` and `ci-apply-<env>` in ops for DEV, TEST and PROD;
there is no input variable to select a smaller set. Existing environment and
`<env>/<role>` resource keys are preserved.
The optional `enable_sql_test_access` flag defaults to `false`. Set it to
`true` to create `ci-sqltest-dev`, its project-path WIF grant and DEV
project-level BigQuery Job User. Bootstrap does **not** grant dataset access:
the `sql_tests` dataset is created by infra, which is applied later, so infra
also owns the dataset-level Data Editor grant (`sql_test_principal` in
`infra/env/dev.tfvars`). Apply bootstrap first; an infra apply that grants a
not-yet-created account fails. The SQL test account has no state-bucket grant.
Its email is the `sqltest_sa_email` output (null while the flag is false).
Set `sql_test_principal` in `infra/env/dev.tfvars` to
`serviceAccount:<sqltest_sa_email>`.

`developer_principal` has no default: supply `user:<email>` or explicit HCL
`null` to disable human access. Groups are rejected. Set
`TF_VAR_developer_principal` for a user, or use the gitignored
`bootstrap/personal.auto.tfvars` (relative to the repository root), including
`developer_principal = null` for the deliberate no-access choice. The string
`"null"` in an environment variable is not HCL null for this string input.
`human_access_environments` defaults to `["dev", "test"]` and can only
contain non-prod environments. When set, exactly two project-level roles are
granted in those selected non-prod projects, regardless of the CI environment
selection or demo VM flag:
`roles/iam.serviceAccountTokenCreator` and `roles/iap.tunnelResourceAccessor`.
There are no human PROD grants. These broad grants are intentional; OS Login
authorization is a separate existing/manual prerequisite. No human access
variable or resource remains in infra/IAM/network modules.

Outputs `gitlab_wif_provider`, `gitlab_wif_audience`, `project_numbers`,
`ci_plan_service_accounts` and `ci_apply_service_accounts` supply non-secret
GitLab setup values. The audience has `https://iam.googleapis.com/`; the provider
argument to `create-cred-config` does not. Both CI SAs have Object User on the
**whole** state bucket, not isolated backend prefixes. Apply roles include
powerful environment IAM and secret administration.

For an existing deployment, apply bootstrap first using its existing backend;
do not repeat the initial local-state migration. Then review/apply infra to
remove old resource-scoped human IAM. See the ordered
[migration, GitLab settings, exact auth snippet and negative test](../docs/runbook.md#gitlab-wif-bootstrap-and-mr-plans).
Local mock tests verify trust/grants/input rejection; they do not establish live
GitLab federation. No cloud bootstrap apply or MR pipeline is claimed here.

## Shared ingestion image repository

[registry.tf](registry.tf) owns the regional Docker repository `ops-ingest` in
ops, with standard ops labels, immutable tags, an untagged-after-seven-days
cleanup policy and `prevent_destroy`. `cleanup_policy_dry_run` is enabled for
the initial apply to observe candidates before any deletions. `artifact_registry_region`
defaults to `europe-southwest1`. The `ingest_image_repository` output is the
tag/digest-free image path used by every environment; no environment state
lookup is required.

Bootstrap generates Cloud Run service identities for **all three** environment
projects, then assigns each
`roles/artifactregistry.reader` on this repository only. The email format is
`service-<project-number>@serverless-robot-prod.iam.gserviceaccount.com`;
the exported `cloud_run_service_agents` map exposes the generated identities.
Enabled CI apply identities get the same scoped Reader for deployment checks,
not Writer/Admin. Runtime accounts get no registry role. Normal Google-managed
Cloud Run service-agent project roles must remain intact.

Repositories cannot be moved across projects. Existing environment repositories
are retained during the infra state migration, not deleted or imported into ops.
Copy the chosen release, verify its digest and change the job image only after
bootstrap and publishing succeed. See the
[migration and negative checks](../docs/runbook.md#shared-ops-registry-migration).

## References

- [Google provider: Billing Budget](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/billing_budget)
- [Google provider: Storage Bucket](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/storage_bucket)
- [Terraform GCS backend](https://developer.hashicorp.com/terraform/language/backend/gcs)
- [Cloud Billing budgets](https://docs.cloud.google.com/billing/docs/how-to/budgets)
