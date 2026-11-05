from datetime import date

import pytest
from bq_helpers import LOADED_AT, tile_row

from ingest.build import mart_parameters, sql

pytestmark = pytest.mark.sql

Q4_2025 = date(2025, 10, 1)
Q1 = date(2026, 1, 1)
Q2 = date(2026, 4, 1)
# mart_zoom = 10: the prefix is the first 10 quadkey characters.
P1, P2, P3, P4 = "0331131300", "0331131301", "0331131302", "0331131303"


def tile(prefix, suffix, quarter_start, tests, d, u, lat, down, up):
    return tile_row(
        prefix + suffix,
        quarter_start,
        tests=tests,
        avg_d_kbps=int(d * 1000),
        avg_u_kbps=int(u * 1000),
        avg_d_mbps=d,
        avg_u_mbps=u,
        avg_lat_ms=lat,
        avg_lat_down_ms=down,
        avg_lat_up_ms=up,
        devices=1,
    )


@pytest.fixture(scope="module")
def mart(sql_env):
    tables = sql_env.target_tables("mart")
    rows = []
    for prefix, down in ((P1, 50.0), (P2, 2146959360.0)):
        rows += [
            tile(prefix, "000000", Q1, 10, 32.0, 8.0, 25.0, 30.0, 40.0),
            tile(prefix, "000000", Q2, 10, 100.0, 20.0, 20.0, 30.0, 40.0),
            tile(prefix, "000001", Q2, 30, 20.0, 10.0, 40.0, down, 60.0),
        ]
    # P3 has Q4 2025 and Q2 2026 but no Q1 2026: Q2 has no adjacent previous quarter.
    rows += [
        tile(P3, "000000", Q4_2025, 5, 10.0, 5.0, 20.0, 30.0, 40.0),
        tile(P3, "000000", Q2, 5, 20.0, 5.0, 20.0, 30.0, 40.0),
    ]
    # P4 Q2: tiles with NULL latency (input NULL, not an outlier).
    rows += [
        tile(P4, "000000", Q2, 10, 100.0, 20.0, 20.0, 30.0, 40.0),
        tile(P4, "000001", Q2, 30, 20.0, 10.0, 40.0, None, None),
        tile(P4, "000002", Q2, 20, 50.0, 5.0, 50.0, None, 70.0),
    ]
    sql_env.load_rows(sql_env.table_id("mart_ookla_tiles"), rows)
    sql_env.run(
        sql("mart", sql_env.dataset, tables=tables),
        mart_parameters(sql_env.settings, LOADED_AT),
        collect=False,
    )
    result = sql_env.run(
        f"SELECT * FROM `{sql_env.dataset}.{tables['mart_quarterly_performance']}` "
        "WHERE quarter_start >= DATE '2025-01-01'"
    )
    return {(row["quadkey_prefix"], row["quarter_start"]): row for row in result}


def test_mart_download_is_weighted_by_tests(mart):
    row = mart[(P1, Q2)]
    # (100 * 10 + 20 * 30) / (10 + 30) = 1600 / 40 = 40.0; a plain average would be 60.0.
    assert row["avg_d_mbps"] == pytest.approx(40.0)
    # (20 * 10 + 10 * 30) / 40 = 500 / 40 = 12.5
    assert row["avg_u_mbps"] == pytest.approx(12.5)
    # (20 * 10 + 40 * 30) / 40 = 1400 / 40 = 35.0
    assert row["avg_lat_ms"] == pytest.approx(35.0)
    # (30 * 10 + 50 * 30) / 40 = 1800 / 40 = 45.0
    assert row["avg_lat_down_ms"] == pytest.approx(45.0)
    # (40 * 10 + 60 * 30) / 40 = 2200 / 40 = 55.0
    assert row["avg_lat_up_ms"] == pytest.approx(55.0)
    assert row["tile_count"] == 2
    assert row["total_tests"] == 40
    assert row["network_type"] == "mobile"
    assert row["loaded_at"] == LOADED_AT


def test_latency_outlier_changes_only_the_affected_latency_average(mart):
    clean, outlier = mart[(P1, Q2)], mart[(P2, Q2)]
    # The 2146959360 ms tile (30 tests) is excluded from the down average: 30 * 10 / 10 = 30.0.
    assert outlier["avg_lat_down_ms"] == pytest.approx(30.0)
    assert clean["avg_lat_down_ms"] == pytest.approx(45.0)
    for column in ("tile_count", "total_tests"):
        assert outlier[column] == clean[column]
    for column in ("avg_d_mbps", "avg_u_mbps", "avg_lat_ms", "avg_lat_up_ms", "download_qoq_pct"):
        assert outlier[column] == pytest.approx(clean[column])


def test_quarter_over_quarter_uses_only_the_adjacent_previous_quarter(mart):
    # P1: Q1 download 32.0, Q2 download 40.0 -> 100 * (40 - 32) / 32 = 25.0
    assert mart[(P1, Q2)]["download_qoq_pct"] == pytest.approx(25.0)
    # No Q4 2025 rows for P1, so its Q1 has no previous quarter.
    assert mart[(P1, Q1)]["download_qoq_pct"] is None
    # P3 skips Q1 2026: Q2 must not be compared with Q4 2025.
    assert mart[(P3, Q2)]["download_qoq_pct"] is None
    assert mart[(P3, Q4_2025)]["download_qoq_pct"] is None
    assert len(mart) == 7


def test_null_latency_is_excluded_from_latency_averages_only(mart):
    row = mart[(P4, Q2)]
    # Down: only the first tile is non-NULL -> 30 * 10 / 10 = 30.0.
    # (NULL tests in the denominator would give 300 / 60 = 5.0, the same as COALESCE(x, 0).)
    assert row["avg_lat_down_ms"] == pytest.approx(30.0)
    # Up: tiles 1 and 3 -> (40 * 10 + 70 * 20) / (10 + 20) = 1800 / 30 = 60.0
    # (NULL tests in the denominator would give 1800 / 60 = 30.0).
    assert row["avg_lat_up_ms"] == pytest.approx(60.0)
    # avg_lat_ms has no NULLs: (20 * 10 + 40 * 30 + 50 * 20) / 60 = 2400 / 60 = 40.0
    assert row["avg_lat_ms"] == pytest.approx(40.0)
    # Speeds, tiles and tests include the NULL-latency tiles:
    # down (100 * 10 + 20 * 30 + 50 * 20) / 60 = 2600 / 60 = 43.333...
    assert row["avg_d_mbps"] == pytest.approx(2600 / 60)
    # up (20 * 10 + 10 * 30 + 5 * 20) / 60 = 600 / 60 = 10.0
    assert row["avg_u_mbps"] == pytest.approx(10.0)
    assert row["tile_count"] == 3
    assert row["total_tests"] == 60  # 10 + 30 + 20
