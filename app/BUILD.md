# Curated build, quality and mart

`python -m ingest build --type mobile --quarter 2023-Q3` processes raw GCS
objects already written by sync. It does not call AWS or create the external
table. Terraform must have created `ext_ookla_raw` and the native tables.

## Configuration

Build shares `PROJECT_ID`, `RAW_BUCKET`, `MIN_QUARTER` and optional
`IMPERSONATE_SERVICE_ACCOUNT` with sync. Credentials are explicitly passed to
both Storage and BigQuery clients, and Secret Manager when sending an alert.

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `BIGQUERY_DATASET` | `network_perf` | Dataset identifier (not a full SQL name) |
| `REGION` | `europe-southwest1` | Query job location; must match the dataset |
| `REGION_BBOX` | `{"min_lon":-9.6,"max_lon":4.4,"min_lat":35.8,"max_lat":43.9}` | Inclusive centroid filter in WGS84; copy the Terraform bbox if changed |
| `MAXIMUM_BYTES_BILLED` | `10737418240` (10 GiB) | Explicit cap on every submitted query job, including watermark reads and scripts |
| `MART_ZOOM` | `10` | Quadkey-prefix length, 1 through 16 |
| `MINIMUM_ROW_PERCENT` | `50` | Minimum regional rows as a percentage of the immediately preceding quarter's curated rows for the same type |
| `LATENCY_CEILING_MS` | `10000` | Warning ceiling and per-column exclusion threshold for mart latency averages |
| `NATIVE_SCHEMA_PATH` | `ookla_tiles.json` under `SCHEMAS_DIR`, or source checkout's `schemas/` if unset | Shared schema for temporary staging tables; this file-level override takes precedence |
| `ALERT_SECRET` | `alert-telegram` | Secret ID or full `projects/<project>/secrets/<id>` name; read `versions/latest` through the Secret Manager API, never injected as a secret value |

The 50% and 10,000 ms defaults were selected as start ing values, not derived from a
production baseline. Adjust them deliberately with DEV evidence. Changing the
bbox or native transform requires `--force` to reprocess matching-ETag quarters;
the ETag watermark does not fingerprint configuration or SQL.
At startup, before credential discovery, the CLI logs an explicit allowlist of
effective configuration, including the final `maximum_bytes_billed` value in
bytes. It does not dump environment variables, credentials or secret payloads.
Set `MAXIMUM_BYTES_BILLED` to override the cap; `MAX_BYTES_BILLED` is not supported.
Staging tables use the project suffix as the `env` label, consistent with the
supplied `dep-2610-dev/test/prod` naming convention.

## Work selection and staging

- Read raw GCS metadata and the latest `SUCCESS` row per network type/quarter
  in `load_log`. Compare the opaque `source_etag` (and source URI). Never compare
  a GCS checksum or ETag to the S3 identifier.
- `--type mobile|fixed|all` and optional `--quarter YYYY-QN` scope work.
  `--force` ignores a matching watermark, but does not bypass DQ.
  Missing/inconsistent raw metadata, duplicate files for one type/quarter, and
  unavailable explicit quarters fail rather than selecting an arbitrary file.
- `--dry-run` lists decisions using a **real billed watermark SELECT** and GCS
  reads. It creates no stages, changes no data, rebuilds no mart and sends no
  alerts. It is a work preview, not BigQuery's query-validation dry run.
- Create `stg_ookla_tiles_<type>_<yyyy>q<q>` with the native schema and a one-day
  expiration before submitting the transform. An existing stage name fails;
  it is not overwritten. Clean up owned stages on success or failure; expiration
  is the fallback after process termination.
- The parameterized transform filters exact Hive type/year/quarter, exact
  `_FILE_NAME`, and bbox coordinates. It adds quarter/type/load timestamp and
  converts kbps to decimal Mbps. Only staging uses `WRITE_TRUNCATE`; stage query
  results are not downloaded into Python.

## Data quality

`quality.py` evaluates a single aggregate result; no source rows are fetched
into Python.

`QualityResult.rules` contains each rule's name, severity, violation count and
one deterministic example quadkey (the minimum offending key). Passing row rules
have no example; a NULL quadkey is represented as `<NULL>`. Duplicate violations
count extra rows beyond the first occurrence of each non-null key; NULL keys are
reported by the invalid-quadkey rule instead. Aggregate rules have no example:
`empty_region` counts one failed condition, and `row_count_drop` counts missing
rows below the required threshold, rounded up to a whole row.
All rules are logged with counts and examples, including on failure.
An ERROR still prevents publication and raises `QualityError`; its `.result`
retains the complete report, including WARN rules.

| Severity | Rule |
| --- | --- |
| ERROR | Non-empty regional stage; `tests > 0` and not null |
| ERROR | Non-null `devices`, with `1 <= devices <= tests` |
| ERROR | Quadkey consists of exactly 16 digits in `0..3`, unique per stage |
| ERROR | Non-null download/upload speeds, both nonnegative |
| ERROR | Regional row count is not below `MINIMUM_ROW_PERCENT` of the immediately previous quarter, when that quarter has rows |
| WARN | Count tiles with any latency above the ceiling or non-finite loaded-latency values; retain all tiles in the mart, exclude each invalid value only from its own latency average |
| WARN | Report the null share of `avg_lat_down_ms`; sparsity does not fail a build |

For the first available quarter, or a missing immediately previous quarter, the
relative row-count check is skipped; the empty-region check still applies.
Exactly 50% passes with the default; less than 50% fails.

## Publication and recovery

After DQ passes, recheck the raw object's generation/ETag, then run one
multi-statement transaction:

1. DELETE only `quarter_start = ... AND network_type = ...` from `ookla_tiles`.
2. INSERT the validated stage using an explicit column list.
3. Replace the matching `load_log` row with `SUCCESS`, source ETag/URI,
   source and regional row counts, and timestamp.
4. COMMIT.

This preserves the other network type in the same date partition and makes
the watermark atomic with curated publication. A DQ failure submits no native
or load-log writes for that partition. Earlier successful quarters in the same
run remain committed; there is no all-quarters transaction.

After selected quarters, rebuild the **whole mart** in a separate DELETE+INSERT
transaction. This also runs when no ETags changed, so rerunning can repair a
previous mart failure after curated publication. The whole-table operations
use an explicit all-dates partition filter to satisfy required-filter tables.
A failed mart transaction leaves the previous mart intact; the CLI exits nonzero.

If BigQuery reports `Unexpected keyword CURRENT`, use the corrected mart SQL
with the `current_quarter` alias. SQLite numerical tests do not enforce BigQuery
keyword rules. After updating the code, rerun build without `--force`: committed
curated quarters are skipped when their ETags match, and the mart is rebuilt.

Mart metrics are weighted by tests, not plain averages of tile averages.
Each latency metric includes only its own non-null, finite values at or below
the ceiling in both numerator and denominator; no contributors yields NULL.
Speeds, tile counts and total tests include all tiles regardless of latency.
QoQ download percent uses only the exact preceding quarter for that type/prefix;
a missing quarter or zero previous speed yields NULL. Counts and metrics are
stable on rerun; `loaded_at` is operational metadata and may change.

Run only one sync/build writer per environment at a time. Deterministic stages
reject same-slice overlap, but do not provide a global lock. The generation
recheck narrows, but cannot eliminate, races with concurrent sync or external
writers. Changed raw files after publication will be selected on a later run.
Mixed physical Parquet types in the external table can still fail BigQuery
reads; casts do not normalize a file that the external reader cannot decode.

## Telegram

Secret `alert-telegram` must contain JSON with `bot_token` and `chat_id`.
After success, send rows, DQ status, loaded quarters and build duration in seconds
(monotonic clock) through Bot API `sendMessage`. WARN totals cover only partitions
loaded in this run: outlier rows, null-down rows and their weighted-by-row null
share. Unchanged partitions are not rechecked; with none selected, DQ is
`NOT_RUN` and null share is `n/a`.
DQ failures identify the actual type, quarter and each failed ERROR rule with
its count. Other partition failures use `partition_processing`; a whole-mart
failure uses `type=all`, `quarter=all`, `rule=mart_rebuild`. Failures before a
partition is selected report the requested scope and `build_setup_or_discovery`.
These notifications never include raw exception messages or source URLs.
Secret/HTTP failures only log fixed warnings and never fail the data build.
No request URL, token, response body or exception text from notification handling
is logged. Redirects are disabled. Dry-run does not access the secret.

See [the runbook](../docs/runbook.md#curated-build) for DEV commands.
Local mocks and adapted SQLite SQL checks pass; BigQuery execution, transactions
and delivery still need live verification. Python 3.12 execution is unverified
on this machine; local tests run under Python 3.14.
