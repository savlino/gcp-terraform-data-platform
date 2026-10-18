# GCP Terraform Data Platform

## Overview
TODO: Describe the project's purpose and scope.

## Architecture
TODO: Document the architecture that is implemented and verified.

## What is implemented
- Bootstrap Terraform configuration is checked in and passes local `terraform validate`; cloud resources have not been applied or verified.
- DEV infrastructure configuration: private raw bucket, `network_perf` dataset and three empty native table definitions. Local validation and mocked Terraform tests pass; DEV plan/apply and cloud behavior remain unverified. See [deployment and verification commands](infra/README.md).
- Runtime IAM configuration: dedicated ingestion service account, raw-bucket Object User, dataset Data Editor and project Job User. Optional developer impersonation is disabled by default and never granted in PROD. Local mock tests cover these rules; live permission checks remain unverified. See [security and verification](docs/security.md).

## Environments
TODO: Document the available environments and their configuration.

## CI/CD
TODO: Document the configured CI/CD workflows.

## Security model
TODO: Document the implemented identity, access, and secret-handling controls.

## Testing
TODO: Document the tests and commands that have been run.

## Cost and teardown
TODO: Document verified cost controls and teardown steps.

## How to reproduce
TODO: Add verified setup and deployment instructions.

## What is NOT here
- `ext_ookla_raw` (deferred until raw files exist), ingestion, transformations and populated marts.
- Networking, secrets, Cloud Run scheduling and CI/CD; TEST/PROD environment configuration and a Terraform deployment identity.
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
