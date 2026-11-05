from datetime import date

import pytest
from bq_helpers import ERAS, LOADED_AT, partition, schema, source_parquet, source_tiles

pytestmark = pytest.mark.sql

# Era -> (quarter, raw autodetected latency type, quarter_start).
CASES = {
    "int32": (1, "INTEGER", date(2026, 1, 1)),  # Q1 starts in month 1*3-2 = 1
    "double": (2, "FLOAT", date(2026, 4, 1)),  # Q2 starts in month 2*3-2 = 4
}


@pytest.fixture(scope="module")
def staged(sql_env):
    result = {}
    for era, (kind, down, up) in ERAS.items():
        quarter, _, _ = CASES[era]
        parquet = source_parquet(source_tiles(down, up), kind)
        raw_id, stage_id = sql_env.stage_parquet(
            f"stage_{era}", partition("mobile", 2026, quarter), parquet
        )
        rows = sql_env.run(f"SELECT * FROM `{stage_id}` ORDER BY quadkey")
        result[era] = (sql_env.client.get_table(raw_id), sql_env.client.get_table(stage_id), rows)
    return result


@pytest.mark.parametrize("era", ERAS)
def test_stage_filters_bbox_converts_units_and_keeps_values(staged, era):
    raw, _, rows = staged[era]
    _, down, up = ERAS[era]
    _, raw_type, quarter_start = CASES[era]
    assert {f.name: f.field_type for f in raw.schema}["avg_lat_down_ms"] == raw_type

    # 5 source tiles; tile_x 10.0 > max_lon 4.4 and tile_y 50.0 > max_lat 43.9 are dropped: 3 rows.
    assert [row["quadkey"] for row in rows] == [
        "0331131300000000",
        "0331131300000001",
        "0331131300000002",
    ]
    for row in rows:
        assert row["network_type"] == "mobile"
        assert row["quarter_start"] == quarter_start
        assert row["avg_d_kbps"] == 50000
        assert row["avg_u_kbps"] == 10000
        assert row["avg_d_mbps"] == pytest.approx(50.0)  # 50000 kbps / 1000
        assert row["avg_u_mbps"] == pytest.approx(10.0)  # 10000 kbps / 1000
        assert row["avg_lat_ms"] == pytest.approx(15.0)
        assert row["tests"] == 12
        assert row["devices"] == 4
        assert row["loaded_at"] == LOADED_AT
    assert rows[0]["avg_lat_down_ms"] == pytest.approx(down)
    assert rows[0]["avg_lat_up_ms"] == pytest.approx(up)


@pytest.mark.parametrize("era", ERAS)
def test_stage_keeps_null_latency_as_null(staged, era):
    rows = {row["quadkey"]: row for row in staged[era][2]}
    _, _, up = ERAS[era]
    # `is None`, not approx: NULL must not become 0 (COALESCE) or any other value.
    assert rows["0331131300000001"]["avg_lat_down_ms"] is None
    assert rows["0331131300000001"]["avg_lat_up_ms"] is None
    assert rows["0331131300000002"]["avg_lat_down_ms"] is None
    assert rows["0331131300000002"]["avg_lat_up_ms"] == pytest.approx(up)
    # 2 of 3 rows NULL down, 1 of 3 NULL up.
    assert sum(row["avg_lat_down_ms"] is None for row in rows.values()) == 2
    assert sum(row["avg_lat_up_ms"] is None for row in rows.values()) == 1


def test_both_eras_give_identical_target_types(staged):
    expected = {f.name: f.field_type for f in schema("ookla_tiles")}
    for era in ERAS:
        assert {f.name: f.field_type for f in staged[era][1].schema} == expected
