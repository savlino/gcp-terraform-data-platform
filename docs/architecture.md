# Application architecture

The current application implements raw ingestion only. Terraform defines the
destination infrastructure; Python performs discovery, incremental decisions,
source schema validation and streaming transfer. Local unit tests use synthetic
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

## Not implemented in the application

BigQuery external-table creation, `load_log` processing, regional filtering,
native-table transforms, mart aggregation, data-quality checks and Telegram
alerts are deferred. Terraform defines the native table schemas and secret
container, but sync neither populates these tables nor sends notifications.
Cloud Run execution, scheduling, custom networking and CI/CD are also not
implemented. Raw files are global; bounding-box configuration does not yet
reduce transferred data.

See the [runbook](runbook.md#raw-ingestion) for manual DEV verification
and [lessons learned](lessons-learned.md) for idempotence-test pitfalls.
