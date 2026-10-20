# GCP Terraform Data Platform

## Overview
TODO: Describe the project's purpose and scope.

## Architecture
See [application architecture](docs/architecture.md) for the implemented raw-ingestion flow and deferred stages.
The source is public AWS S3 in `us-west-2`, destination storage is configured in GCP `europe-southwest1`, and cross-cloud transfer uses public HTTPS without AWS credentials.

## What is implemented
- Scheduled-ingestion Terraform configuration: immutable Artifact Registry tags with one delete policy for untagged versions older than seven days, bounded Cloud Run `run` task in the data region, job-scoped scheduler invocation and weekly OAuth Scheduler in `europe-west1`. Application/DQ settings come from variables; alert secrets are read by API using a resource name. Local validation and mock tests pass; registry push, deployment and live execution remain unverified. See [image-first deployment](docs/runbook.md#scheduled-cloud-run-ingestion). Tagged releases accumulate by design.
- Non-root Python 3.12 application image with packaged SQL and shared schemas, plus `run` orchestration (sync then build). Local Docker build and offline smoke checks pass; Cloud Run deployment and live combined execution remain unverified. See [container verification](app/README.md#container).
- Build CLI configuration and code: GCS/load-log ETag selection, regional staged transforms, quality gates, transactional type/quarter replacement, full weighted mart rebuild and token-safe Telegram summaries. Local tests pass; BigQuery execution and live alerts remain unverified. See [build configuration and guarantees](app/BUILD.md).
- Terraform configuration for `ext_ookla_raw`: autodetected Parquet with typed CUSTOM Hive partitions over the raw bucket and a required partition filter. Provider validation and mock tests pass; cloud creation and queries remain unverified.
- Bootstrap Terraform configuration is checked in and passes local `terraform validate`; cloud resources have not been applied or verified.
- DEV infrastructure configuration: private raw bucket, `network_perf` dataset and three empty native table definitions. Local validation and mocked Terraform tests pass; DEV plan/apply and cloud behavior remain unverified. See [deployment and verification commands](infra/README.md).
- Runtime IAM configuration: dedicated ingestion service account, raw-bucket Object User, dataset Data Editor and project Job User. Optional developer impersonation is disabled by default and never granted in PROD. Local mock tests cover these rules; live permission checks remain unverified. See [security and verification](docs/security.md).
- Telegram alert secret container configuration (`alert-telegram`, JSON `{bot_token, chat_id}`): regional user-managed replication, standard labels and secret-scoped runtime access. No secret values or versions are managed by Terraform. Local validation/mock tests pass; live access, token-free cloud state and message delivery remain unverified. Manual add/test/rotate procedures: [runbook](docs/runbook.md).
- Raw ingestion CLI: paginated public S3 discovery, source-footer schema checks, bounded streaming to GCS, ETag/custom-metadata incremental decisions, dry-run and optional local impersonation. One validated source configuration (`SOURCE_BUCKET`, `SOURCE_REGION`, optional HTTPS-only `SOURCE_ENDPOINT`) feeds both regional HTTPS listing and Arrow footer reads; Terraform passes the same variable names to the Cloud Run Job. Unit tests and Ruff pass locally on Python 3.14; the full Python 3.12 unit suite and live transfers remain unverified. See [app configuration](app/README.md) and [DEV verification](docs/runbook.md#raw-ingestion).

## Environments
TODO: Document the available environments and their configuration.

## CI/CD
TODO: Document the configured CI/CD workflows.

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
- Verified Cloud Run/Scheduler deployment and execution, custom networking and CI/CD; TEST/PROD environment files, a Terraform deployment identity and distributed execution locking.
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
