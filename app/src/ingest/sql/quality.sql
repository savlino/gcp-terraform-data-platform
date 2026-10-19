WITH flagged AS (
  SELECT quadkey,
    tests IS NULL OR tests <= 0 AS invalid_tests,
    devices IS NULL OR devices < 1 OR devices > tests AS invalid_devices,
    quadkey IS NULL OR NOT REGEXP_CONTAINS(quadkey, r'^[0-3]{{16}}$') AS invalid_quadkeys,
    ROW_NUMBER() OVER (PARTITION BY quadkey ORDER BY quadkey) > 1
      AND quadkey IS NOT NULL AS duplicate_quadkeys,
    avg_d_kbps IS NULL OR avg_u_kbps IS NULL
      OR avg_d_kbps < 0 OR avg_u_kbps < 0 AS invalid_speeds,
    avg_lat_ms > @latency_ceiling OR avg_lat_down_ms > @latency_ceiling
      OR avg_lat_up_ms > @latency_ceiling
      OR IS_NAN(avg_lat_down_ms) OR IS_INF(avg_lat_down_ms)
      OR IS_NAN(avg_lat_up_ms) OR IS_INF(avg_lat_up_ms) AS outlier_rows,
    avg_lat_down_ms IS NULL AS null_down_rows
  FROM `{stage}`
)
SELECT
  COUNT(*) AS row_count,
  COUNTIF(invalid_tests) AS invalid_tests,
  MIN(CASE WHEN invalid_tests THEN COALESCE(quadkey, '<NULL>') END) AS invalid_tests_example,
  COUNTIF(invalid_devices) AS invalid_devices,
  MIN(CASE WHEN invalid_devices THEN COALESCE(quadkey, '<NULL>') END) AS invalid_devices_example,
  COUNTIF(invalid_quadkeys) AS invalid_quadkeys,
  MIN(CASE WHEN invalid_quadkeys THEN COALESCE(quadkey, '<NULL>') END) AS invalid_quadkeys_example,
  COUNTIF(duplicate_quadkeys) AS duplicate_quadkeys,
  MIN(CASE WHEN duplicate_quadkeys THEN quadkey END) AS duplicate_quadkeys_example,
  COUNTIF(invalid_speeds) AS invalid_speeds,
  MIN(CASE WHEN invalid_speeds THEN COALESCE(quadkey, '<NULL>') END) AS invalid_speeds_example,
  COUNTIF(outlier_rows) AS outlier_rows,
  MIN(CASE WHEN outlier_rows THEN COALESCE(quadkey, '<NULL>') END) AS outlier_rows_example,
  COUNTIF(null_down_rows) AS null_down_rows,
  MIN(CASE WHEN null_down_rows THEN COALESCE(quadkey, '<NULL>') END) AS null_down_rows_example,
  (SELECT COUNT(*) FROM `{dataset}.ookla_tiles`
   WHERE quarter_start = DATE_SUB(@quarter_start, INTERVAL 3 MONTH)
     AND network_type = @network_type) AS previous_rows,
  (SELECT COUNT(*) FROM `{dataset}.ext_ookla_raw`
   WHERE type = @network_type AND year = @year AND quarter = @quarter
     AND _FILE_NAME = @source_uri) AS source_rows
FROM flagged
