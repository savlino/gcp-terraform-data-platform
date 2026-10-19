WITH expected AS (
  SELECT SAFE_DIVIDE(SUM(avg_d_mbps * tests), SUM(tests)) AS expected_download,
         COUNT(*) AS expected_tiles, SUM(tests) AS expected_tests
  FROM `{dataset}.ookla_tiles`
  WHERE quarter_start = @quarter_start AND network_type = @network_type
    AND STARTS_WITH(quadkey, @prefix)
)
SELECT expected.*, mart.avg_d_mbps, mart.tile_count, mart.total_tests,
       ABS(expected_download - mart.avg_d_mbps) <= 1e-9 AS download_matches
FROM expected
JOIN `{dataset}.mart_quarterly_performance` AS mart
  ON mart.quarter_start = @quarter_start AND mart.network_type = @network_type
  AND mart.quadkey_prefix = @prefix
