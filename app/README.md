# Raw ingestion

Python src-layout application: `python -m ingest sync --type mobile|fixed|all`.
The default schema path resolves from the source checkout, independently of the
working directory. The examples below run from the **repository root**.
The sync command handles raw files only. The separate
[`build` command](BUILD.md) performs regional transforms, staged quality checks,
transactional publication, mart rebuilds and Telegram summaries.
`python -m ingest run` executes sync followed by build, stopping if sync fails.
The [Dockerfile](Dockerfile) uses this combined command by default.

## Configuration

| Environment variable | Meaning | Default |
| --- | --- | --- |
| `PROJECT_ID` | Destination environment GCP project | Required |
| `RAW_BUCKET` | Raw bucket name, without `gs://` | Required |
| `MIN_QUARTER` | Inclusive earliest quarter, `YYYY-QN`, at least 2023 Q3 | `2023-Q3` |
| `SOURCE_BUCKET` | Public source S3 bucket | `ookla-open-data` |
| `SOURCE_REGION` | Source bucket's AWS region, not the GCP destination region | `us-west-2` |
| `SOURCE_ENDPOINT` | Optional HTTPS origin overriding `https://{SOURCE_BUCKET}.s3.{SOURCE_REGION}.amazonaws.com` for listing/download (e.g. tests); must be a plain `https://` origin | Unset |
| `SCHEMAS_DIR` | Shared schema directory for sync and build | Source checkout's `schemas/`; `/app/schemas` in Docker |
| `SOURCE_SCHEMA_PATH` | Path to the JSON allowed-types source contract; explicit relative paths use the working directory | `source_contract.json` under `SCHEMAS_DIR`, or source checkout's `schemas/` if unset |
| `IMPERSONATE_SERVICE_ACCOUNT` | Optional runtime SA email for local impersonation | Unset: use ADC directly |

An explicitly empty impersonation variable is an error, not a fallback to your
user identity. With a non-empty value, ADC supplies source credentials to
`google.auth.impersonated_credentials.Credentials`; the resulting credentials
are passed explicitly to `storage.Client`. No keys are needed. In managed
environments, leave this variable unset and use the attached runtime identity.
No `.env` files are loaded. Avoid debug HTTP logging and never put credentials
in configuration values.

One `SourceConfig` object (in `config.py`) supplies the bucket and region to both
the HTTPS listing/download and the Arrow footer reader. Bucket, region and
endpoint are validated at startup because they form a hostname. The former
`S3_REGION` variable is rejected with an error rather than silently ignored.
`SOURCE_ENDPOINT` affects only HTTPS listing/download; Arrow footer reads always
use `SOURCE_BUCKET` + `SOURCE_REGION`.

Without overrides, schema lookup assumes the repository's src layout (including
an editable install). For standalone installations, set `SCHEMAS_DIR`; the
container sets it to `/app/schemas`. `SOURCE_SCHEMA_PATH` and `NATIVE_SCHEMA_PATH`
take precedence for their respective files. Explicit relative paths still use
the working directory. An invalid explicit path fails rather than falling back
to another contract; an explicitly empty `SCHEMAS_DIR` is rejected.

## Combined command

`run` accepts `--type mobile|fixed|all`, `--quarter YYYY-QN`, `--dry-run` and
`--force`. Both stages use the same type/quarter scope; `--force` affects build
only, never forces a raw overwrite. There is no `--limit` on `run`: use `sync`
separately for a bounded initial transfer. Any stage failure returns non-zero;
successful raw transfers are not rolled back if build fails.

`run --dry-run` previews sync and then build against the **currently existing**
raw objects and watermarks, not hypothetical uploads. It performs cloud reads
and a billed watermark query, but no transfers, writes or alerts. A quarter
not yet in GCS cannot be previewed by build. Sync failures are logged and stop
the run before build and its notification handling.

## Container

Build from the **repository root** using **PowerShell on Windows** with Docker
Desktop's Linux engine:

```powershell
docker build -f app\Dockerfile -t ookla-ingest:local .
docker run --rm --network none ookla-ingest:local --help
docker run --rm --network none ookla-ingest:local run --help
```

The image uses `python:3.12-slim`, unbuffered output, disabled bytecode writes and
non-root user `ingest` (UID 10001). A regular `pip install --no-cache-dir .`
installs the package, including the SQL declared as setuptools package data.
Shared schemas are copied to `/app/schemas`. The root
[.dockerignore](../.dockerignore) allowlists application source/build metadata
and schema JSON, excluding virtualenvs, Git/Terraform files, local credential
directories and environment files from the context. No credentials are copied
or generated. Do not place secrets inside application source or schema files.

Credential-free smoke test (**PowerShell**, repository root):

```powershell
@'
import json
import os
import sys
from importlib.resources import files
from pathlib import Path
from ingest.config import Config
from ingest.build_config import BuildConfig

assert sys.version_info[:2] == (3, 12)
assert os.getuid() == 10001
os.environ.update(PROJECT_ID="smoke", RAW_BUCKET="smoke-raw")
assert Config.from_env().schema_path == Path("/app/schemas/source_contract.json")
assert BuildConfig.from_env().schema_path == Path("/app/schemas/ookla_tiles.json")
for config in (Config.from_env(), BuildConfig.from_env()):
    assert json.loads(config.schema_path.read_text())
for name in ("watermarks", "stage", "quality", "publish", "mart", "verify_mart"):
    assert files("ingest").joinpath("sql", name + ".sql").read_text().strip()
assert not list(Path("/app").rglob(".venv"))
assert not list(Path("/app").rglob(".env*"))
assert not Path.home().joinpath(".config/gcloud/application_default_credentials.json").exists()
print("Container smoke checks passed")
'@ | docker run --rm -i --network none --entrypoint python ookla-ingest:local -
if ($LASTEXITCODE -ne 0) { throw 'Container smoke checks failed' }

# Negative test: default run must fail before ADC discovery when config is absent.
docker run --rm --network none ookla-ingest:local
if ($LASTEXITCODE -ne 1) { throw 'Expected missing configuration to exit 1' }
```

For Cloud Run, attach the runtime service account and leave
`IMPERSONATE_SERVICE_ACCOUNT` unset. Do not bake or copy ADC files into the image.
Cloud Run deployment and live combined execution are not verified here.

## Behavior and boundaries

1. Unsigned HTTPS S3 ListObjectsV2 requests enumerate source keys, following XML
   continuation tokens and rejecting malformed/repeated pagination. Quarters
   before `MIN_QUARTER` are omitted. Objects are ordered oldest-first.
2. Read each destination object's GCS custom metadata. Transfer when missing,
   when `source_etag` differs, or when metadata/actual size differs from source.
   **S3 ETags are opaque identifiers**, including multipart ETags; no MD5
   inference or comparison to GCS ETags is made.
3. For selected objects, use anonymous `pyarrow.fs.S3FileSystem` with `SOURCE_REGION`
   and `SOURCE_BUCKET`
   to read only the eight-byte Parquet trailer and the footer metadata range.
   No row groups are read. Metadata larger than 16 MiB is explicitly rejected.
   Required source columns are checked against
   [source_contract.json](../schemas/source_contract.json) before any GCS upload.
   The contract lists exact allowed footer types: down/up latency permits
   int32 or double; other numeric columns retain their observed types.
   Arrow string/large_string both represent the UTF-8 string columns.
   A mismatch reports the column, found type and allowed types.
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
requests and ADC discovery. No real data is committed. The local assistant test suite used the existing Python 3.14 virtualenv.
The Docker build and offline Python 3.12 smoke checks passed; the full unit suite
has not been run under Python 3.12.

See [DEV runbook](../docs/runbook.md#raw-ingestion) for first transfer,
idempotence, changed-ETag and negative checks. Live sync has not been run by the
assistant; source transfers store global files and can incur storage costs.
