# Raw ingestion

Python src-layout application: `python -m ingest sync --type mobile|fixed|all`.
The default schema path resolves from the source checkout, independently of the
working directory. The examples below run from the **repository root**.
Only the raw sync stage is implemented: no BigQuery client, `load_log` access,
transform, region filtering, alerts or application container is added here.

## Configuration

| Environment variable | Meaning | Default |
| --- | --- | --- |
| `PROJECT_ID` | Destination environment GCP project | Required |
| `RAW_BUCKET` | Raw bucket name, without `gs://` | Required |
| `MIN_QUARTER` | Inclusive earliest quarter, `YYYY-QN`, at least 2023 Q3 | `2023-Q3` |
| `S3_REGION` | Public source S3 region, not the GCP destination region | `us-west-2` |
| `SOURCE_SCHEMA_PATH` | Path to the shared JSON source contract; explicit relative paths use the working directory | `schemas/ookla_raw.json` in the source checkout |
| `IMPERSONATE_SERVICE_ACCOUNT` | Optional runtime SA email for local impersonation | Unset: use ADC directly |

An explicitly empty impersonation variable is an error, not a fallback to your
user identity. With a non-empty value, ADC supplies source credentials to
`google.auth.impersonated_credentials.Credentials`; the resulting credentials
are passed explicitly to `storage.Client`. No keys are needed. In managed
environments, leave this variable unset and use the attached runtime identity.
No `.env` files are loaded. Avoid debug HTTP logging and never put credentials
in configuration values.

The default schema lookup assumes the repository's src layout (including an
editable install). For a standalone package or container without that layout,
set `SOURCE_SCHEMA_PATH` explicitly to the deployed schema file. An invalid
explicit path fails rather than falling back to another contract.

## Behavior and boundaries

1. Unsigned HTTPS S3 ListObjectsV2 requests enumerate source keys, following XML
   continuation tokens and rejecting malformed/repeated pagination. Quarters
   before `MIN_QUARTER` are omitted. Objects are ordered oldest-first.
2. Read each destination object's GCS custom metadata. Transfer when missing,
   when `source_etag` differs, or when metadata/actual size differs from source.
   **S3 ETags are opaque identifiers**, including multipart ETags; no MD5
   inference or comparison to GCS ETags is made.
3. For selected objects, use anonymous `pyarrow.fs.S3FileSystem` with `S3_REGION`
   to read only the eight-byte Parquet trailer and the footer metadata range.
   No row groups are read. Metadata larger than 16 MiB is explicitly rejected.
   Required source columns are checked against [ookla_raw.json](../schemas/ookla_raw.json)
   before any GCS upload. STRING accepts Arrow string/large_string; INTEGER
   accepts signed integers; FLOAT accepts floating-point or signed integers
   (older quarters store the latency-under-load columns as int32).
   Extra columns and nullable values are allowed; this is not row-level DQ.
4. Stream HTTPS GET in 8 MiB chunks to a GCS resumable writer under
   `ookla/type=<type>/year=<year>/quarter=<quarter>/<filename>`. Set
   `source_etag` and `source_size` metadata on the same upload. `If-Match` and
   ETag/Content-Length checks detect source changes since listing. Generation
   preconditions prevent overwriting a destination changed since inspection.
5. Abort incomplete uploads before finalization. Verify streamed byte count
   equals source Content-Length before close and uploaded object size afterward.
   Retry transient streams up to three attempts with backoff; HTTP listings/GET
   responses, Arrow S3 reads and GCS SDK operations also have bounded retries.
   Permission failures, generation conflicts and schema mismatches fail the run.
   Cancellation failure is surfaced, not silently ignored.

`--limit N` limits transfers per run; up-to-date files do not count.
Thus rerunning `--limit 1` with a backlog transfers the *next* pending
object, not the already-current one. There is no deletion of old objects by the
app; Terraform's non-prod raw lifecycle still applies. Objects removed by that
lifecycle will be downloaded again if in scope. No whole Parquet file is held
in memory or written to disk. Buffers are bounded by chunk/footer size rather
than file size.

`--quarter 2023-Q3` restricts sync (including dry-run) to that exact quarter,
before GCS metadata checks and the transfer limit. Both layers are included
when `--type all` is selected. The quarter must be at or after `MIN_QUARTER`;
invalid or unavailable quarters fail explicitly. It does not force an overwrite:
matching objects are still skipped. For a targeted refresh after an ETag change,
use the same flag; there is no unconditional force-reload option.

`--dry-run` lists the selected plan and reads GCS metadata but performs no
uploads, source-footer reads or data GETs. It needs GCS read permissions; it is
not an offline operation or proof of schema compatibility.

Logs are JSON (locally and in Cloud Run), containing plan/counts and failures.
Any error produces a non-zero exit code. Existing matching objects are not
footer-validated again. Physical schema compatibility across quarters in a
BigQuery external table is deferred atm; permissive native typing alone
does not solve mixed Parquet schemas.

## Install and local checks

**PowerShell 7 on Windows**, repository root. Python 3.12 is the project runtime.
If `py -3.12` fails, install that runtime before using these commands.

```powershell
py -3.12 -m venv app\.venv
& app\.venv\Scripts\python.exe -m pip install -e 'app[dev]'
& app\.venv\Scripts\python.exe -m pytest app\tests\unit -q
& app\.venv\Scripts\python.exe -m ruff check app
& app\.venv\Scripts\python.exe -m ruff format --check app
```

Tests generate tiny synthetic Parquet in memory, mock HTTP/GCS and block network
requests and ADC discovery. No real data is committed. The local assistant run
used the existing Python 3.14 virtualenv; Python 3.12 execution is still unverified.

See [DEV runbook](../docs/runbook.md#raw-ingestion) for first transfer,
idempotence, changed-ETag and negative checks. Live sync has not been run by the
assistant; source transfers store global files and can incur storage costs.
