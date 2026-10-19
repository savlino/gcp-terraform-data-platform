import math
import sqlite3

import pytest

from ingest.build import sql


def test_mart_alias_avoids_bigquery_current_keyword():
    text = sql("mart", "dataset")
    assert "FROM weighted AS current_quarter" in text
    assert "current." not in text


@pytest.mark.parametrize(
    "latencies,expected",
    [
        ((10001, 1, 1), (19, 370 / 109, 290 / 110)),
        ((1, 10001, 1), (290 / 110, 30, 290 / 110)),
        ((1, 1, 10001), (290 / 110, 370 / 109, 19)),
        ((None, None, None), (19, 30, 19)),
        ((10000, 10000, 10000), (1000190 / 110, 1000270 / 109, 1000190 / 110)),
        ((10001, 10001, 10001), (19, 30, 19)),
        ((10001, float("inf"), float("-inf")), (19, 30, 19)),
    ],
)
def test_mart_sql_weighting_null_denominators_outliers_and_adjacent_quarters(latencies, expected):
    # Execute the actual SELECT locally, adapting only BigQuery-specific functions.
    text = sql("mart", "dataset")
    select = text[text.index("WITH weighted AS") : text.index(";\nCOMMIT")]
    select = select.replace("`dataset.ookla_tiles`", "tiles")
    select = select.replace("IF(", "IIF(").replace("AS FLOAT64", "AS REAL")
    select = select.replace(
        "DATE_SUB(current_quarter.quarter_start, INTERVAL 3 MONTH)",
        "DATE(current_quarter.quarter_start, '-3 months')",
    )
    with sqlite3.connect(":memory:") as connection:
        connection.row_factory = sqlite3.Row
        connection.create_function(
            "SAFE_DIVIDE", 2, lambda x, y: x / y if x is not None and y else None
        )
        connection.create_function("IS_NAN", 1, lambda x: x is not None and math.isnan(x))
        connection.create_function("IS_INF", 1, lambda x: x is not None and math.isinf(x))
        connection.execute(
            "CREATE TABLE tiles (network_type TEXT, quarter_start TEXT, quadkey TEXT,"
            "avg_d_mbps REAL, avg_u_mbps REAL, avg_lat_ms INTEGER,"
            "avg_lat_down_ms REAL, avg_lat_up_ms REAL, tests INTEGER)"
        )
        connection.executemany(
            "INSERT INTO tiles VALUES (?,?,?,?,?,?,?,?,?)",
            [
                ("mobile", "2023-07-01", "0000000000000000", 10, 20, 10, None, 10, 1),
                ("mobile", "2023-07-01", "0000000000000001", 100, 200, 20, 30, 20, 9),
                ("mobile", "2023-07-01", "0000000000000002", 999, 999, *latencies, 100),
                ("mobile", "2023-10-01", "0000000000000000", 25, 50, None, 10001, float("inf"), 2),
                ("fixed", "2023-07-01", "0000000000000000", 8, 8, 10, 10, 10, 1),
                ("mobile", "2024-01-01", "0000000000000000", 50, 50, 10, 10, 10, 1),
                ("mobile", "2024-04-01", "0000000000000000", 100, 100, 10, 10, 10, 1),
                ("mobile", "2024-10-01", "0000000000000000", 200, 200, 10, 10, 10, 1),
            ],
        )
        rows = connection.execute(
            select,
            {
                "mart_zoom": 10,
                "all_start": "0001-01-01",
                "latency_ceiling": 10000,
                "loaded_at": "2026-01-01",
            },
        ).fetchall()
        baseline_parameters = {
            "mart_zoom": 10,
            "all_start": "0001-01-01",
            "latency_ceiling": 10000,
            "loaded_at": "2026-01-01",
        }
        low_rows = connection.execute(
            select, baseline_parameters | {"latency_ceiling": 50}
        ).fetchall()
        latency_columns = {"avg_lat_ms", "avg_lat_down_ms", "avg_lat_up_ms"}

        def non_latency_snapshot(results):
            return {
                (row["network_type"], row["quarter_start"], row["quadkey_prefix"]): {
                    key: row[key] for key in row.keys() if key not in latency_columns
                }
                for row in results
            }

        assert non_latency_snapshot(low_rows) == non_latency_snapshot(rows)
        if latencies == (10000, 10000, 10000):
            low_july = next(
                row
                for row in low_rows
                if row["network_type"] == "mobile" and row["quarter_start"] == "2023-07-01"
            )
            assert tuple(
                low_july[column] for column in ("avg_lat_ms", "avg_lat_down_ms", "avg_lat_up_ms")
            ) == (19, 30, 19)
            assert all(
                low_july[column]
                != next(
                    row[column]
                    for row in rows
                    if row["network_type"] == "mobile" and row["quarter_start"] == "2023-07-01"
                )
                for column in latency_columns
            )
        restored_rows = connection.execute(select, baseline_parameters).fetchall()
        assert [dict(row) for row in restored_rows] == [dict(row) for row in rows]
        connection.execute(
            "CREATE TABLE mart_quarterly_performance AS " + select,
            {
                "mart_zoom": 10,
                "all_start": "0001-01-01",
                "latency_ceiling": 10000,
                "loaded_at": "2026-01-01",
            },
        )
        connection.create_function("STARTS_WITH", 2, lambda value, prefix: value.startswith(prefix))
        verification = sql("verify_mart", "dataset").replace("`dataset.ookla_tiles`", "tiles")
        verification = verification.replace(
            "`dataset.mart_quarterly_performance`", "mart_quarterly_performance"
        )
        checked = connection.execute(
            verification,
            {
                "quarter_start": "2023-07-01",
                "network_type": "mobile",
                "prefix": "0000000000",
            },
        ).fetchone()
        assert checked["download_matches"]
        assert checked["expected_tiles"] == checked["tile_count"] == 3
        assert checked["expected_tests"] == checked["total_tests"] == 110
    by_key = {(row["network_type"], row["quarter_start"]): row for row in rows}
    july = by_key["mobile", "2023-07-01"]
    assert july["avg_d_mbps"] == pytest.approx((10 * 1 + 100 * 9 + 999 * 100) / 110)
    assert july["avg_u_mbps"] == pytest.approx((20 * 1 + 200 * 9 + 999 * 100) / 110)
    for column, value in zip(
        ("avg_lat_ms", "avg_lat_down_ms", "avg_lat_up_ms"), expected, strict=True
    ):
        assert july[column] == pytest.approx(value)
    assert july["tile_count"] == 3 and july["total_tests"] == 110
    october = by_key["mobile", "2023-10-01"]
    assert all(
        october[column] is None for column in ("avg_lat_ms", "avg_lat_down_ms", "avg_lat_up_ms")
    )
    assert october["avg_d_mbps"] == 25
    assert october["tile_count"] == 1 and october["total_tests"] == 2
    assert by_key["fixed", "2023-07-01"]["avg_d_mbps"] == 8
    assert by_key["mobile", "2023-10-01"]["download_qoq_pct"] is not None
    assert by_key["mobile", "2024-01-01"]["download_qoq_pct"] == pytest.approx(100)
    assert by_key["mobile", "2024-04-01"]["download_qoq_pct"] == pytest.approx(100)
    assert by_key["mobile", "2024-10-01"]["download_qoq_pct"] is None
