# DEV data-quality calibration

## Observed scope and decision

Measured on **2026-10-20**, starting at **17:10:13 UTC**, against
`dep-2610-dev.network_perf.ookla_tiles` in `europe-southwest1`.
These are full scans of the loaded regional data, not samples or mart averages.
The configured bounding box is longitude [-9.6, 4.4], latitude [35.8, 43.9].
This is not a scan of all global source tiles.

- **2,503,293 tile-quarter observations**, covering **26 type/quarter slices**:
  fixed and mobile, each with all 13 quarters from 2023-Q3 through 2026-Q3.
  Repeated quadkeys across quarters are separate observations.
- Fixed: **1,642,290** rows. Mobile: **861,003** rows.
- All 26 GCS raw Parquet objects matched a latest successful load-log row by
  source URI and opaque `source_etag`. Every curated slice count matched its
  `loaded_row_count`. This verifies coverage of the current raw inventory,
  not independent completeness against the public S3 listing.
- **24 immediately adjacent quarter comparisons**, 12 per network type.
  The first observed quarter in each type has no previous comparison.
- No latency non-finite or negative values were observed.

Approved DEV settings in [dev.tfvars](../infra/env/dev.tfvars):

| Setting | Previous | Selected | Evidence and interpretation |
| --- | ---: | ---: | --- |
| `minimum_row_percent` | 50 | **80** | Lowest observed ratio is 85.588536%; the guard allows a 20% drop, leaving 5.588536 percentage points below that historical minimum. All 24 observed adjacent pairs pass. |
| `latency_ceiling_ms` | 10000 | **10000 (retained)** | Highest observed latency is 9,997 ms; mobile upload p99.9 is 8,756.796 ms. Lowering the shared ceiling to 5,000 ms would exclude 20,872 upload and 11,561 download latency observations. |

The 80% margin is an operational choice informed by history, not a confidence
interval or a guarantee of future coverage. One shared latency ceiling applies
to three differently distributed metrics; the observed loaded-latency tail
does not support calibrating it from idle latency alone. A 10,000 ms ceiling
excludes zero values in this baseline and does not detect all unusual latency.
Reassess after future releases or changes to the region/source.

`row_count_drop` requires `current_rows >= ceil(previous_rows * 80 / 100)`,
using the same network type and exactly the preceding quarter. The 80% value
does not mean “allow an 80% drop.” Latency above the ceiling generates warnings
and is excluded **per latency metric** from weighted mart averages; curated rows
are retained. At exactly the ceiling, a finite value is included. Nulls are
excluded from the respective latency denominator.

Only the DEV row threshold changes here; application defaults and other
environments are unchanged. This calibration did not rebuild tables or the mart,
execute Cloud Run, change the image tag or unpause Scheduler.
**Terraform plan/apply were not run:** no separate authorized deployment service
account was provided. The runtime identity is not a Terraform deployer.

## Row counts and immediately preceding-quarter ratios

Ratios are `100 * current_rows / previous_rows`, not percentage change.
Rows shown here are regional curated counts, before any mart aggregation.

| Quarter | Fixed rows | Fixed previous % | Mobile rows | Mobile previous % |
| --- | ---: | ---: | ---: | ---: |
| 2023-Q3 | 133763 | N/A | 71457 | N/A |
| 2023-Q4 | 129611 | 96.896003 | 61159 | 85.588536 |
| 2024-Q1 | 126223 | 97.386024 | 62780 | 102.650468 |
| 2024-Q2 | 124769 | 98.848070 | 63055 | 100.438038 |
| 2024-Q3 | 130072 | 104.250254 | 69198 | 109.742288 |
| 2024-Q4 | 122792 | 94.403100 | 68934 | 99.618486 |
| 2025-Q1 | 116140 | 94.582709 | 62667 | 90.908695 |
| 2025-Q2 | 126888 | 109.254348 | 64790 | 103.387748 |
| 2025-Q3 | 132807 | 104.664744 | 65806 | 101.568143 |
| 2025-Q4 | 124510 | 93.752588 | 63169 | 95.992767 |
| 2026-Q1 | 119556 | 96.021203 | 64365 | 101.893334 |
| 2026-Q2 | 123959 | 103.682793 | 68657 | 106.668220 |
| 2026-Q3 | 131200 | 105.841448 | 74966 | 109.189158 |

Fixed ratios range from 93.752588% to 109.254348%; mobile ratios from
85.588536% to 109.742288%.

## Exact latency percentiles

Units: **milliseconds**. These are exact BigQuery `PERCENTILE_CONT`
percentiles of per-tile averages, with linear interpolation, pooled across
loaded quarters. They are **not** percentiles of individual Speedtest tests,
not test-weighted percentiles, and not approximate quantiles. Null/non-finite
values are excluded from percentile calculations; counts are reported separately.
Percentiles are rounded to three decimal places below.

| Scope | Metric | Finite observations | Nulls | p50 | p95 | p99 | p99.9 | Maximum |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| All | `avg_lat_ms` | 2503293 | 0 | 19 | 69 | 160 | 681 | 2941 |
| All | `avg_lat_down_ms` | 2481207 | 22086 | 355 | 1998 | 3864 | 7535 | 9995 |
| All | `avg_lat_up_ms` | 2485547 | 17746 | 373 | 2696 | 4775 | 7815.454 | 9997 |
| Fixed | `avg_lat_ms` | 1642290 | 0 | 13 | 47 | 157 | 694 | 2941 |
| Fixed | `avg_lat_down_ms` | 1633217 | 9073 | 225 | 1311 | 2821 | 5984.568 | 9986 |
| Fixed | `avg_lat_up_ms` | 1633444 | 8846 | 216 | 1657 | 3369 | 6335.557 | 9997 |
| Mobile | `avg_lat_ms` | 861003 | 0 | 31 | 89 | 162 | 486 | 2550 |
| Mobile | `avg_lat_down_ms` | 847990 | 13013 | 752 | 2747 | 4998.110 | 8678.011 | 9995 |
| Mobile | `avg_lat_up_ms` | 852103 | 8900 | 953 | 3735 | 5848 | 8756.796 | 9996 |

Candidate-ceiling counts across both types (strictly greater than each ceiling):

| Metric | >1000 ms | >2000 ms | >5000 ms | >10000 ms |
| --- | ---: | ---: | ---: | ---: |
| `avg_lat_ms` | 222 | 13 | 0 | 0 |
| `avg_lat_down_ms` | 423137 | 123734 | 11561 | 0 |
| `avg_lat_up_ms` | 587946 | 230715 | 20872 | 0 |

Counts for different metrics can overlap on the same row; do not sum them as
distinct outlier tiles.

## Query provenance and reproduction

Queries used explicit `maximum_bytes_billed = 10737418240` (10 GiB each),
location `europe-southwest1`, disabled result caching and a dry-run estimate
before execution. They ran via ADC-backed impersonation of
`ingest-runtime@dep-2610-dev.iam.gserviceaccount.com`; no service-account keys.
Only aggregate results and inventory metadata were retrieved; no raw data files.

| Query | BigQuery job ID | Bytes processed | Bytes billed |
| --- | --- | ---: | ---: |
| Counts / load-log reconciliation | `ca995a68-2264-4a26-be59-e044d7f710c7` | 38415313 | 38797312 |
| Latency distributions / candidate counts | `2f87f24a-d9fc-4e3b-928f-d305c7dc56e2` | 98170774 | 98566144 |

An earlier partition-inventory query processed/billed 10,485,760 bytes
(job `896196a4-16a9-4c51-9121-066e9c74017f`). A first counts-query dry run
failed validation on an ambiguous join and was corrected before execution.
Billing bytes are not a monetary cost statement or a whole-run budget.

The core row-ratio query (BigQuery Standard SQL):

```sql
WITH counts AS (
  SELECT network_type, quarter_start, COUNT(*) AS row_count
  FROM `dep-2610-dev.network_perf.ookla_tiles`
  WHERE quarter_start >= DATE '2023-07-01'
  GROUP BY network_type, quarter_start
)
SELECT c.network_type, c.quarter_start, c.row_count,
       p.row_count AS previous_rows,
       100 * SAFE_DIVIDE(c.row_count, p.row_count) AS previous_percent
FROM counts c
LEFT JOIN counts p
  ON c.network_type = p.network_type
 AND p.quarter_start = DATE_SUB(c.quarter_start, INTERVAL 3 MONTH)
ORDER BY c.network_type, c.quarter_start;
```

The percentile calculation can be reproduced with this equivalent core query;
the executed query additionally counted null/non-finite/negative values and
candidate-ceiling exceedances reported above:

```sql
WITH metrics AS (
  SELECT network_type, metric, value
  FROM `dep-2610-dev.network_perf.ookla_tiles`,
  UNNEST([
    STRUCT('avg_lat_ms' AS metric, CAST(avg_lat_ms AS FLOAT64) AS value),
    STRUCT('avg_lat_down_ms' AS metric, avg_lat_down_ms AS value),
    STRUCT('avg_lat_up_ms' AS metric, avg_lat_up_ms AS value)
  ])
  WHERE quarter_start >= DATE '2023-07-01'
), scoped AS (
  SELECT network_type AS scope, metric, value FROM metrics
  UNION ALL
  SELECT 'all' AS scope, metric, value FROM metrics
)
SELECT DISTINCT scope, metric,
  PERCENTILE_CONT(IF(IS_NAN(value) OR IS_INF(value), NULL, value), 0.5)
    OVER (PARTITION BY scope, metric) AS p50,
  PERCENTILE_CONT(IF(IS_NAN(value) OR IS_INF(value), NULL, value), 0.95)
    OVER (PARTITION BY scope, metric) AS p95,
  PERCENTILE_CONT(IF(IS_NAN(value) OR IS_INF(value), NULL, value), 0.99)
    OVER (PARTITION BY scope, metric) AS p99,
  PERCENTILE_CONT(IF(IS_NAN(value) OR IS_INF(value), NULL, value), 0.999)
    OVER (PARTITION BY scope, metric) AS p999
FROM scoped
ORDER BY scope, metric;
```

Use the same billing cap and location when reproducing either query. Current
counts can change after new loads; the tables above are a dated baseline, not
live assertions. Do not omit the partition predicate.

## Terraform rollout

Local checks do not deploy the threshold. Once an authorized **separate**
Terraform deployer SA is available, use **PowerShell on Windows**, repository root:

```powershell
$deployer = Read-Host 'Existing Terraform deployment service account email'
if (-not $deployer -or $deployer -match '^ingest-(runtime|scheduler)@') {
  throw 'A separate Terraform deployer identity is required'
}
$previousImpersonation = $env:GOOGLE_IMPERSONATE_SERVICE_ACCOUNT
$env:GOOGLE_IMPERSONATE_SERVICE_ACCOUNT = $deployer
try {
  terraform -chdir=infra plan '-var-file=env/dev.tfvars' -out=dq-dev.tfplan
  if ($LASTEXITCODE -ne 0) { throw 'DQ plan failed; do not apply' }
  # Review before applying. Expect only the job env threshold 50 -> 80.
  # Stop on unrelated changes, IAM changes, replacements or deletions.
  terraform -chdir=infra apply dq-dev.tfplan
  if ($LASTEXITCODE -ne 0) { throw 'DQ apply failed' }
  terraform -chdir=infra plan '-var-file=env/dev.tfvars' -detailed-exitcode
  if ($LASTEXITCODE -ne 0) { throw 'Post-apply plan is not clean' }
} finally {
  if ($null -eq $previousImpersonation) {
    Remove-Item Env:\GOOGLE_IMPERSONATE_SERVICE_ACCOUNT -ErrorAction SilentlyContinue
  } else {
    $env:GOOGLE_IMPERSONATE_SERVICE_ACCOUNT = $previousImpersonation
  }
}
```

The backend must already be configured for `infra/dev`; its authentication is
separate from provider impersonation. Use deployer-impersonated ADC for backend
access as described in [setup](setup.md), not runtime ADC.
The current working tree also contains earlier external-table and image/job
configuration changes: if they appear in the plan, review them separately
instead of applying them as an incidental threshold update.

## Validation performed

- `terraform fmt -check infra\env\dev.tfvars` and `terraform -chdir=infra validate`
  passed. Validation is not a cloud plan.
- All 10 existing quality unit tests passed.
- The application evaluator passed all 26 observed slice counts at the selected
  80% threshold. An exact boundary check accepted 80/100 and rejected 79/100 with
  `row_count_drop`.
- A live BigQuery dry run without a `quarter_start` predicate was rejected for
  the required partition filter. It did not execute a data scan.
- No infrastructure apply or deployment verification was performed.
