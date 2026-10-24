# Application architecture

The current application implements raw ingestion and curated builds. Terraform
defines the destination infrastructure; Python performs discovery, incremental
decisions, source schema validation, streaming transfer and staged transforms.
Local unit tests use synthetic
data and mocked cloud services; they do not establish that the live deployment
or cross-cloud transfer has been verified.

The source is public AWS S3 in `us-west-2`, destination storage is configured
in GCP `europe-southwest1`, and cross-cloud transfer uses public HTTPS without
AWS credentials.

## Data flow

```text
Public S3: ookla-open-data/parquet/performance/
    |
    | unsigned HTTPS ListObjectsV2, with pagination
    v
discover.py: source keys, quarters, opaque ETags and sizes
    |
    | MIN_QUARTER and optional --quarter scope
    v
state.py: compare with GCS source_etag/source_size metadata
    |
    | skip current objects, then apply --limit
    v
schema_check.py: anonymous Arrow S3 trailer/footer range reads
    |
    | validate against schemas/ookla_raw.json before upload
    v
transfer.py: streamed HTTPS GET -> GCS resumable upload
    |
    v
GCS raw: ookla/type=<type>/year=<year>/quarter=<quarter>/<file>
```

The CLI in [__main__.py](../app/src/ingest/__main__.py) orchestrates the pipeline;
[config.py](../app/src/ingest/config.py) reads environment configuration.
See [app configuration and behavior](../app/README.md) for the full contract.

## Incremental state and integrity

- Incremental sync reads GCS object metadata, not BigQuery `load_log`. A missing
  object, changed source ETag or inconsistent size makes an object pending.
- S3 ETags are opaque identifiers, not assumed to be MD5 checksums. Multipart
  ETags are compared as strings.
- `--quarter YYYY-QN` fixes the scope before destination checks. `--limit N`
  limits transfers per run; up-to-date files do not count. Dry-run reads source
  listings and destination metadata without downloading data or checking footers.
- Source schema checks read only the trailer/footer, with a 16 MiB footer limit.
  Transfers use 8 MiB chunks without buffering a whole file or writing it to disk.
- Source `If-Match`, response ETag and Content-Length checks detect changes
  relative to discovery. Destination generation preconditions prevent silently
  overwriting concurrent changes. Interrupted uploads are cancelled; successful
  uploads are checked against the expected size.
- JSON logs report plans, counts and failures; failures return a non-zero exit
  code. An unchanged object's generation is the no-content-overwrite signal.

## Identity and infrastructure boundaries

AWS reads are anonymous. GCS access uses ADC, optionally wrapped with
`google.auth.impersonated_credentials` when `IMPERSONATE_SERVICE_ACCOUNT` is set;
credentials are passed explicitly to the GCS client. No service-account keys or
source credentials are required.

Terraform passes one region variable to the raw bucket and `network_perf`
dataset, keeping their configured locations aligned for future external queries.
The runtime has bucket-scoped Object User, dataset-scoped Data Editor,
project-scoped Job User and access to the configured alert secret only.
The sync command currently uses only storage access; it does not read the secret.
See [security](security.md) for scope details and impersonation restrictions.

## Scheduler region decision

Cloud Scheduler uses a separate `scheduler_region` input, defaulting to
`europe-west1` (Belgium), while the data region remains `europe-southwest1`
(Madrid). Madrid is not listed among the supported Cloud Scheduler locations.
Belgium is a nearby supported European region identified by Google as a
low-carbon location; this is a placement choice, not a measured emissions claim.

The configured Scheduler resource stores the schedule and sends an authenticated
HTTPS request to the Cloud Run Jobs API URL targeting the job in Madrid.
Scheduler and the target job do not need to share a region: the job's execution,
raw bucket and BigQuery dataset stay in `europe-southwest1`. Moving the schedule
does not move or replicate the application data to Belgium.

The [ingest_job module](../modules/ingest_job/README.md) passes `scheduler_region`
only to Scheduler and `region` to Cloud Run and Artifact Registry; data resources
retain the same `region`. Validation checks location syntax, not service
availability; recheck the official location list before deployment. Scheduler
uses the distinct `ingest-scheduler` identity with an additive invoker grant on
this job only, an OAuth access token and the v2 Jobs `:run` endpoint.

- [Cloud Scheduler locations](https://docs.cloud.google.com/scheduler/docs/locations)
- [Cloud Scheduler release notes](https://docs.cloud.google.com/scheduler/docs/release-notes)

## Curated build pipeline

Terraform defines `ext_ookla_raw` with a pinned shared schema
([schemas/ookla_raw.json](../schemas/ookla_raw.json)), typed CUSTOM Hive
partitions and a required Hive partition filter. This avoids autodetect drift
across source quarters; live creation and queries remain unverified.
The sync application does not create or query the external table.

The separate `build` command compares GCS `source_etag` metadata with the latest
successful load-log row, with optional type/quarter selection and forced rebuild.
A parameterized query filters the external Hive partition, file URI and bbox
into a one-day staging table. Quality checks run before any curated mutation.
A transaction replaces only that type/quarter in `ookla_tiles` and updates its
watermark; it never truncates a shared quarter partition.

A second transaction rebuilds the whole mart with test-weighted averages,
latency-outlier exclusion and QoQ comparisons only between adjacent quarters.
It runs even without changed raw ETags to recover a previously failed mart build.
Telegram summaries use the JSON secret and Bot API without logging credential-
bearing URLs or notification exception text. Notification failures are warnings.
Build dry-run reads watermarks but performs no data writes or notifications.
See [build behavior and recovery](../app/BUILD.md) for thresholds, transaction
boundaries and single-writer limitations. This path has local test coverage,
not verified live BigQuery execution.

## Application container

The [Dockerfile](../app/Dockerfile) builds from the repository root on
`python:3.12-slim`, installs the application with packaged SQL, and copies shared
schemas to `/app/schemas` via `SCHEMAS_DIR`. It runs as non-root UID 10001 with
unbuffered output and bytecode writes disabled. The allowlisted build context
excludes local credentials and virtualenvs; authentication is supplied at runtime,
not embedded in image layers.

The default `python -m ingest run` executes sync then build with the same
type/quarter scope and stops on sync failure. These stages are not one transaction:
if build fails, successful raw transfers remain available for a retry. A combined
dry-run previews build against existing raw files, not the files sync would upload.
See [container verification](../app/README.md#container) for offline smoke checks.

## Managed execution

Artifact Registry has immutable tags and one cleanup policy: delete untagged
versions older than seven days (failed/partial push leftovers). Tagged releases
are intentionally retained and may accumulate. `image_tag` is selected in
environment tfvars.
`enable_ingest_job` defaults false for image-first provisioning: apply the
repository, push the image, then enable the job/schedule. No automatic image
build or push is part of Terraform.

The job retains the Python image entrypoint and uses `args = ["run"]`, one
task and parallelism one, attached runtime SA, configurable deadline/memory and
zero task retries by default. Root variables supply all non-secret application
settings, including bbox, minimum quarter, billing cap and DQ thresholds;
bucket, dataset and alert-secret names come from module outputs.
`ALERT_SECRET` contains only the secret resource name. The app reads its latest
version via Secret Manager API, not Cloud Run secret environment injection.

The weekly UTC schedule defaults active in DEV, paused in TEST/PROD, with an
explicit pause override. Scheduler launch acceptance does not imply pipeline
completion. Zero configured Scheduler retries and one task do not guarantee
exactly-once/non-overlapping runs; manual and scheduled runs must be serialized.
No VPC connector is attached: source and Telegram traffic use public HTTPS,
without adding Cloud NAT or other networking in this step.

One vCPU / 1 GiB / one hour is an initial bounded-streaming estimate, not
measured whole-file sizing: no source-size benchmark is recorded. Unchanged
ETags still incur metadata/watermark reads, a whole-mart rebuild and an alert.
See the [deployment runbook](runbook.md#scheduled-cloud-run-ingestion) for the
two-stage rollout, operator permissions, cost cautions and live verification.

## Private network demonstration

The [network module](../modules/network/README.md) defines a custom VPC, regional
subnet with Private Google Access, explicit deny-all ingress and an IAP-only
TCP 22 exception for `iap-ssh` tagged instances. The subnet CIDR is an input;
root defaults are DEV `10.10.0.0/24`, TEST `10.20.0.0/24`, PROD `10.30.0.0/24`.
This VPC is a separate security demonstration: Cloud Run remains unattached,
without a VPC connector or direct VPC egress configuration.

Only `enable_iap_demo_vm = true` creates the private Shielded `e2-micro` demo.
DEV zone is `europe-southwest1-a`; modules receive region/zone as inputs.
The VM has OS Login, no external IP or attached service account, and an
auto-deleting boot disk. Locally applied bootstrap can grant an explicit human
principal project-level Token Creator and IAP access in DEV/TEST, independent of
the VM flag; CI-managed infra has no human access input or resources. OS Login
authorization remains a separate prerequisite. PROD gets no bootstrap human
grants. The flag defaults false because
European VM/disk usage is billable; disable and apply immediately after testing.
The default network must be removed manually, not by Terraform.
See [verification and cleanup](runbook.md#private-network-and-iap-demo).

## GitLab deployment identities

Locally applied bootstrap owns the ops-project `gitlab` pool, `gitlab-com` OIDC
provider and per-environment `ci-plan-*` / `ci-apply-*` accounts (DEV first).
The provider checks the exact GitLab project path and explicit audience; plan
trust selects that project and apply trust additionally requires a protected ref.
GitLab jobs exchange short-lived ID tokens for SA credentials, never keys.
MR lint/mock tests run without cloud credentials; same-project DEV plans use
the plan identity. There are no apply jobs yet. Shared state-bucket access and
the breadth of predefined deployment roles are deliberate limitations, described
in [security](security.md#gitlab-wif-and-ci-role-catalogue).

## Not implemented

Live Cloud Run/Scheduler and private-network/IAP verification remain unverified;
live WIF/pipeline execution, CI applies and a distributed application execution
lock are not verified/implemented. Raw files are global;
regional filtering applies during the build and does not reduce global transfer.

See the [runbook](runbook.md#raw-ingestion) for manual DEV verification
and [lessons learned](lessons-learned.md) for idempotence-test pitfalls.
