# BigQuery module

Creates the `network_perf` dataset, three empty native tables and the
`ext_ookla_raw` external table. No dataset
default table expiration, table expiration or partition expiration is configured.
No IAM grants or data-loading/query jobs are created.

| Input | Purpose | Default |
| --- | --- | --- |
| `project_id` | Existing environment project | Required |
| `env` | Environment label (`dev`, `test`, `prod`) | Required |
| `location` | Same regional location as raw storage | Required |
| `raw_bucket_name` | Raw bucket name from the storage module output | Required |
| `deletion_protection` | Prevent Terraform table deletion | `true` |
| `delete_contents_on_destroy` | Allow dataset deletion with contents | `false` |

Outputs: `dataset_id`, `table_ids`.

Native schemas are loaded from the repository's [schemas directory](../../schemas/).
Keep that directory when consuming this local module.

| Table | Grain / purpose | Partitioning | Clustering |
| --- | --- | --- | --- |
| `ookla_tiles` | One tile per network type and quarter; source metrics plus Mbps and load timestamp | DAY on `quarter_start`, filter required | `network_type`, `quadkey` |
| `load_log` | One successful watermark per network type and quarter, including source URI/ETag and source/loaded row counts | None (small cross-quarter lookup) | `network_type`, `quarter_start` |
| `mart_quarterly_performance` | One quadkey prefix per network type and quarter; weighted metrics and download QoQ percent | DAY on `quarter_start`, filter required | `network_type`, `quadkey_prefix` |

Each quarter occupies the daily partition for its quarter start date. Later transforms must
validate dates are quarter starts and replace only the selected network type,
not overwrite the other type in the same partition. The application must enforce
grain uniqueness and watermark updates; Terraform only creates schemas.

The latency-under-load columns use FLOAT (BigQuery FLOAT64), matching
[the source schema](../../docs/source-schema.md). INTEGER maps to INT64 and
STRING represents the source Arrow large_string columns. `tile` remains WKT,
not a BigQuery GEOGRAPHY value. Derived speeds use decimal Mbps (`kbps / 1000`).
Weighted mart latencies use FLOAT even where the source metric is INTEGER.
The application's [build command](../../app/BUILD.md) implements aggregations,
quality checks and transactional data population; live BigQuery execution is
still unverified. Terraform creates the tables but does not run those jobs.

`ext_ookla_raw` pins the shared [raw schema](../../schemas/ookla_raw.json) and
uses PARQUET over `gs://<raw>/ookla/*`, with CUSTOM Hive partition types declared by
`gs://<raw>/ookla/{type:STRING}/{year:INTEGER}/{quarter:INTEGER}`.
The Hive options require a partition filter. Labels and the existing
`deletion_protection` variable apply to this table as well.
This avoids autodetect drift when source quarters mix physical Parquet types
for nullable latency columns. Terraform manages the external table; the
application does not create it.
