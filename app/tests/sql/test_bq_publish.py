from datetime import UTC, datetime

import pytest
from bq_helpers import partition, tile_row
from google.cloud import bigquery

from ingest.build import partition_parameters, sql

pytestmark = pytest.mark.sql

NEW_LOADED_AT = datetime(2026, 11, 5, 8, 30, tzinfo=UTC)


def test_replacing_mobile_quarter_leaves_fixed_and_other_quarters_unchanged(sql_env):
    tables = sql_env.target_tables("replace")
    tiles = f"{sql_env.dataset}.{tables['ookla_tiles']}"
    q1 = partition("mobile", 2026, 1).quarter_start
    q2 = partition("mobile", 2026, 2)
    sql_env.load_rows(
        tiles,
        [
            tile_row("0331131300000000", q2.quarter_start, avg_d_kbps=1000),
            tile_row("0331131300000001", q2.quarter_start, avg_d_kbps=2000),
            tile_row("0331131300000000", q2.quarter_start, "fixed", avg_d_kbps=3000),
            tile_row("0331131300000002", q2.quarter_start, "fixed", avg_d_kbps=4000),
            tile_row("0331131300000000", q1, avg_d_kbps=5000),
        ],
    )

    def snapshot(where):
        return sql_env.run(
            f"SELECT * FROM `{tiles}` WHERE quarter_start >= DATE '2025-01-01' AND {where} "
            "ORDER BY quadkey"
        )

    fixed_before = snapshot("network_type = 'fixed'")
    mobile_q1_before = snapshot(f"network_type = 'mobile' AND quarter_start = '{q1}'")

    stage_id = sql_env.stage_table("replace_stage")
    new_rows = [
        tile_row(f"033113130000001{i}", q2.quarter_start, avg_d_kbps=9000, loaded_at=NEW_LOADED_AT)
        for i in range(3)
    ]
    sql_env.load_rows(stage_id, new_rows)
    parameters = partition_parameters(q2, sql_env.settings, NEW_LOADED_AT)
    parameters += [
        # source_rows 4 vs loaded_rows 3: the log must store the passed counts, not recount.
        bigquery.ScalarQueryParameter("source_rows", "INT64", 4),
        bigquery.ScalarQueryParameter("loaded_rows", "INT64", 3),
    ]
    sql_env.run(sql("publish", sql_env.dataset, stage_id, tables=tables), parameters, collect=False)

    assert snapshot("network_type = 'fixed'") == fixed_before
    assert len(fixed_before) == 2
    assert snapshot(f"network_type = 'mobile' AND quarter_start = '{q1}'") == mobile_q1_before
    mobile_q2 = snapshot(f"network_type = 'mobile' AND quarter_start = '{q2.quarter_start}'")
    assert [row["quadkey"] for row in mobile_q2] == [
        "0331131300000010",
        "0331131300000011",
        "0331131300000012",
    ]
    assert {row["avg_d_kbps"] for row in mobile_q2} == {9000}
    log = sql_env.run(f"SELECT * FROM `{sql_env.dataset}.{tables['load_log']}`")
    assert len(log) == 1
    assert log[0]["network_type"] == "mobile"
    assert log[0]["quarter_start"] == q2.quarter_start
    assert log[0]["source_etag"] == q2.etag
    assert (log[0]["source_row_count"], log[0]["loaded_row_count"]) == (4, 3)
    assert log[0]["status"] == "SUCCESS"
    assert log[0]["loaded_at"] == NEW_LOADED_AT
