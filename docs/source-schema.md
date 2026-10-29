# Source Parquet schema

The supplied footer scan covers every quarter from 2023-Q3 through 2026-Q3
for both `mobile` and `fixed` (26 files). The physical type change starts in
**2026-Q2** for both network types. Other columns are stable in that scan.
This is footer evidence, not proof of row-level quality or future schemas.

| Column | 2023-Q3 through 2026-Q1 | 2026-Q2 through 2026-Q3 | `ookla_tiles` target |
| --- | --- | --- | --- |
| `quadkey` | large_string | large_string | STRING |
| `tile` | large_string | large_string | STRING (WKT) |
| `tile_x` | double | double | FLOAT64 |
| `tile_y` | double | double | FLOAT64 |
| `avg_d_kbps` | int64 | int64 | INT64 |
| `avg_u_kbps` | int64 | int64 | INT64 |
| `avg_lat_ms` | int64 | int64 | FLOAT64 |
| `avg_lat_down_ms` | int32 | double | FLOAT64 |
| `avg_lat_up_ms` | int32 | double | FLOAT64 |
| `tests` | int64 | int64 | INT64 |
| `devices` | int64 | int64 | INT64 |

Names above use Arrow's footer type notation: `int32`/`int64` represent
Parquet INT32/INT64 and `double` represents DOUBLE. Arrow `string` and
`large_string` are accepted representations of the same UTF-8 Parquet string
columns, not evidence of another source schema change.

The machine-readable [source contract](../schemas/source_contract.json)
lists allowed types per required column, independently of the
[curated schema](../schemas/ookla_tiles.json). Sync checks each selected file's
footer before upload and reports the column, found type and allowed types on
a mismatch. Extra columns are tolerated; required columns cannot be missing
or duplicated. Nullability is checked by row-level DQ where applicable.

A single wildcard external schema cannot decode both latency eras reliably.
Each selected raw object is therefore loaded independently with Parquet
autodetection into an expiring BigQuery raw stage, then SQL casts normalize
numeric columns to the curated types. No quarter-based type guess is used.
See [architecture](architecture.md#curated-build-pipeline).

Future unobserved types require inspection and a deliberate contract change;
they are not accepted just because BigQuery could coerce them.
