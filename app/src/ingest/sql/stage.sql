SELECT
  @network_type AS network_type,
  @quarter_start AS quarter_start,
  CAST(quadkey AS STRING) AS quadkey,
  CAST(tile AS STRING) AS tile,
  CAST(tile_x AS FLOAT64) AS tile_x,
  CAST(tile_y AS FLOAT64) AS tile_y,
  CAST(avg_d_kbps AS INT64) AS avg_d_kbps,
  CAST(avg_u_kbps AS INT64) AS avg_u_kbps,
  CAST(avg_lat_ms AS INT64) AS avg_lat_ms,
  CAST(avg_lat_down_ms AS FLOAT64) AS avg_lat_down_ms,
  CAST(avg_lat_up_ms AS FLOAT64) AS avg_lat_up_ms,
  CAST(tests AS INT64) AS tests,
  CAST(devices AS INT64) AS devices,
  CAST(avg_d_kbps AS FLOAT64) / 1000 AS avg_d_mbps,
  CAST(avg_u_kbps AS FLOAT64) / 1000 AS avg_u_mbps,
  @loaded_at AS loaded_at
FROM `{dataset}.ext_ookla_raw`
WHERE type = @network_type AND year = @year AND quarter = @quarter
  AND _FILE_NAME = @source_uri
  AND tile_x BETWEEN @min_lon AND @max_lon
  AND tile_y BETWEEN @min_lat AND @max_lat
