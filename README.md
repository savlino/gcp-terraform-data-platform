# GCP Terraform Data Platform

## Overview
TODO: Describe the project's purpose and scope.

## Architecture
See [application architecture](docs/architecture.md) for the implemented raw-ingestion flow and deferred stages.
The source is public AWS S3 in `us-west-2`, destination storage is configured in GCP `europe-southwest1`, and cross-cloud transfer uses public HTTPS without AWS credentials.

## What is implemented
- Private-network Terraform configuration: custom VPC (`REGIONAL` routing), Private Google Access subnet, logged deny-all ingress and IAP-only TCP 22 to tagged instances. Optional private Shielded/OS Login `e2-micro` defaults off. Human access belongs to locally applied bootstrap, not infra; OS Login authorization is a separate prerequisite. Cloud Run remains unattached. Local mock tests pass; default-network removal and live SSH checks remain manual. See [network checks and teardown](docs/runbook.md#private-network-and-iap-demo).
- GitLab keyless CI configuration: ops WIF pool/provider with exact project/audience checks, separate DEV plan/apply SAs, protected-ref apply trust, MR fmt/validate/TFLint/mock tests and DEV plan artifact. Bootstrap optionally grants human project-level Token Creator/IAP in DEV/TEST only. Shared state access and broad deployment-role trade-offs are documented. Local checks pass; live federation and MR execution remain unverified, and there are no CI apply jobs. See [setup and negative auth check](docs/runbook.md#gitlab-wif-bootstrap-and-mr-plans).
- Scheduled-ingestion Terraform configuration: immutable Artifact Registry tags with one delete policy for untagged versions older than seven days, bounded Cloud Run `run` task in the data region, job-scoped scheduler invocation and weekly OAuth Scheduler in `europe-west1`. Application/DQ settings come from variables; alert secrets are read by API using a resource name. Local validation and mock tests pass; registry push, deployment and live execution remain unverified. See [image-first deployment](docs/runbook.md#scheduled-cloud-run-ingestion). Tagged releases accumulate by design.
- Non-root Python 3.12 application image with packaged SQL and shared schemas, plus `run` orchestration (sync then build). Local Docker build and offline smoke checks pass; Cloud Run deployment and live combined execution remain unverified. See [container verification](app/README.md#container).
- Build CLI configuration and code: GCS/load-log ETag selection, regional staged transforms, quality gates, transactional type/quarter replacement, full weighted mart rebuild and token-safe Telegram summaries. Local tests pass; BigQuery execution and live alerts remain unverified. See [build configuration and guarantees](app/BUILD.md).
- Terraform configuration for `ext_ookla_raw`: pinned source schema from `schemas/ookla_raw.json` plus typed CUSTOM Hive partitions over the raw bucket and a required partition filter. Provider validation and mock tests pass; cloud creation and queries remain unverified.
- Bootstrap Terraform configuration is checked in and passes local `terraform validate`; cloud resources have not been applied or verified.
- DEV infrastructure configuration: private raw bucket, `network_perf` dataset and three empty native table definitions. Local validation and mocked Terraform tests pass; DEV plan/apply and cloud behavior remain unverified. See [deployment and verification commands](infra/README.md).
- Runtime IAM configuration: dedicated ingestion service account, raw-bucket Object User, dataset Data Editor and project Job User. Human impersonation is managed separately by bootstrap, never in PROD. Local mock tests cover runtime scoping; live permission checks remain unverified. See [security and verification](docs/security.md).
- Telegram alert secret container configuration (`alert-telegram`, JSON `{bot_token, chat_id}`): regional user-managed replication, standard labels and secret-scoped runtime access. No secret values or versions are managed by Terraform. Local validation/mock tests pass; live access, token-free cloud state and message delivery remain unverified. Manual add/test/rotate procedures: [runbook](docs/runbook.md).
- Raw ingestion CLI: paginated public S3 discovery, source-footer schema checks, bounded streaming to GCS, ETag/custom-metadata incremental decisions, dry-run and optional local impersonation. One validated source configuration (`SOURCE_BUCKET`, `SOURCE_REGION`, optional HTTPS-only `SOURCE_ENDPOINT`) feeds both regional HTTPS listing and Arrow footer reads; Terraform passes the same variable names to the Cloud Run Job. Unit tests and Ruff pass locally on Python 3.14; the full Python 3.12 unit suite and live transfers remain unverified. See [app configuration](app/README.md) and [DEV verification](docs/runbook.md#raw-ingestion).

## Environments
TODO: Document the available environments and their configuration.

## CI/CD
[GitLab configuration](.gitlab-ci.yml) defines pinned lint/test/DEV-plan jobs and
an optional unprotected-ref denial check. No apply jobs exist yet. Configure the
non-secret ops project number, protected refs, trusted runners and restricted
artifacts as described in the [runbook](docs/runbook.md#gitlab-wif-bootstrap-and-mr-plans).

## Security model
TODO: Document the implemented identity, access, and secret-handling controls.

## Testing
TODO: Document the tests and commands that have been run.

## Cost and teardown
Initial storage estimate for Artifact Registry image retention: [docs/cost.md](docs/cost.md).
TODO: Document verified cost controls and teardown steps.

## How to reproduce
TODO: Add verified setup and deployment instructions.

## What is NOT here
- Verified populated curated tables/marts and live transformation/quality behavior.
- Verified Cloud Run/Scheduler, private-network/IAP, live WIF and MR pipeline execution; CI apply jobs, TEST/PROD environment files and distributed application execution locking. CI identities are configuration only until bootstrap is applied. Backend prefixes do not isolate shared state-bucket access. Cloud Run VPC attachment is intentionally absent.
- Verified Telegram delivery, provisioned secret values, automated token rotation and the optional Terraform write-only secret-version path.
- Verified DEV deployment. No dataset default table expiration is configured.

## Data attribution
Planned source: [Speedtest by Ookla Open Data](https://github.com/teamookla/ookla-open-data),
provided by Ookla under [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/).
Non-commercial use only; attribution and ShareAlike obligations apply. No real
source data files are committed. Column mappings follow [the source schema](docs/source-schema.md).

## Known limitations
TODO: Record known limitations.

## Lessons learned
TODO: Record lessons learned during implementation.
