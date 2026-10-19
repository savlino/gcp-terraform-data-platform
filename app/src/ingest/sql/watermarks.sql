SELECT network_type, quarter_start, source_etag, source_uri
FROM `{dataset}.load_log`
WHERE status = 'SUCCESS'
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY network_type, quarter_start ORDER BY loaded_at DESC
) = 1
