BEGIN TRANSACTION;
DELETE FROM `{dataset}.mart_quarterly_performance`
WHERE quarter_start >= @all_start;
INSERT INTO `{dataset}.mart_quarterly_performance`
  (network_type, quarter_start, quadkey_prefix, avg_d_mbps, avg_u_mbps,
   avg_lat_ms, avg_lat_down_ms, avg_lat_up_ms, tile_count, total_tests,
   download_qoq_pct, loaded_at)
WITH weighted AS (
  SELECT network_type, quarter_start, SUBSTR(quadkey, 1, @mart_zoom) AS quadkey_prefix,
    -- A plain average of tile averages overweights tiles with very few tests.
    SAFE_DIVIDE(SUM(avg_d_mbps * tests), SUM(tests)) AS avg_d_mbps,
    SAFE_DIVIDE(SUM(avg_u_mbps * tests), SUM(tests)) AS avg_u_mbps,
    SAFE_DIVIDE(SUM(IF(avg_lat_ms <= @latency_ceiling,
                       CAST(avg_lat_ms AS FLOAT64) * tests, 0)),
                SUM(IF(avg_lat_ms <= @latency_ceiling, tests, 0))) AS avg_lat_ms,
    SAFE_DIVIDE(SUM(IF(avg_lat_down_ms <= @latency_ceiling
                       AND NOT IS_NAN(avg_lat_down_ms) AND NOT IS_INF(avg_lat_down_ms),
                       avg_lat_down_ms * tests, 0)),
                SUM(IF(avg_lat_down_ms <= @latency_ceiling
                       AND NOT IS_NAN(avg_lat_down_ms) AND NOT IS_INF(avg_lat_down_ms),
                       tests, 0))) AS avg_lat_down_ms,
    SAFE_DIVIDE(SUM(IF(avg_lat_up_ms <= @latency_ceiling
                       AND NOT IS_NAN(avg_lat_up_ms) AND NOT IS_INF(avg_lat_up_ms),
                       avg_lat_up_ms * tests, 0)),
                SUM(IF(avg_lat_up_ms <= @latency_ceiling
                       AND NOT IS_NAN(avg_lat_up_ms) AND NOT IS_INF(avg_lat_up_ms),
                       tests, 0))) AS avg_lat_up_ms,
    COUNT(*) AS tile_count, SUM(tests) AS total_tests
  FROM `{dataset}.ookla_tiles`
  WHERE quarter_start >= @all_start
  GROUP BY network_type, quarter_start, quadkey_prefix
)
SELECT current_quarter.network_type, current_quarter.quarter_start, current_quarter.quadkey_prefix,
       current_quarter.avg_d_mbps, current_quarter.avg_u_mbps, current_quarter.avg_lat_ms,
       current_quarter.avg_lat_down_ms, current_quarter.avg_lat_up_ms, current_quarter.tile_count,
       current_quarter.total_tests,
       100 * SAFE_DIVIDE(current_quarter.avg_d_mbps - previous.avg_d_mbps,
                         previous.avg_d_mbps) AS download_qoq_pct,
       @loaded_at AS loaded_at
FROM weighted AS current_quarter
LEFT JOIN weighted AS previous
  ON current_quarter.network_type = previous.network_type
  AND current_quarter.quadkey_prefix = previous.quadkey_prefix
  AND previous.quarter_start = DATE_SUB(current_quarter.quarter_start, INTERVAL 3 MONTH);
COMMIT TRANSACTION;
