BEGIN TRANSACTION;
DELETE FROM `{dataset}.ookla_tiles`
WHERE quarter_start = @quarter_start AND network_type = @network_type;
INSERT INTO `{dataset}.ookla_tiles`
  (network_type, quarter_start, quadkey, tile, tile_x, tile_y,
   avg_d_kbps, avg_u_kbps, avg_lat_ms, avg_lat_down_ms, avg_lat_up_ms,
   tests, devices, avg_d_mbps, avg_u_mbps, loaded_at)
SELECT network_type, quarter_start, quadkey, tile, tile_x, tile_y,
       avg_d_kbps, avg_u_kbps, avg_lat_ms, avg_lat_down_ms, avg_lat_up_ms,
       tests, devices, avg_d_mbps, avg_u_mbps, loaded_at
FROM `{stage}`;
DELETE FROM `{dataset}.load_log`
WHERE quarter_start = @quarter_start AND network_type = @network_type;
INSERT INTO `{dataset}.load_log`
  (network_type, quarter_start, source_uri, source_etag,
   source_row_count, loaded_row_count, status, loaded_at)
VALUES (@network_type, @quarter_start, @source_uri, @source_etag,
        @source_rows, @loaded_rows, 'SUCCESS', @loaded_at);
COMMIT TRANSACTION;
