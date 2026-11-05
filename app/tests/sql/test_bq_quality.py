import pyarrow as pa
import pytest
from bq_helpers import ERAS, LOADED_AT, partition, source_parquet, source_tiles, tile_row

from ingest.build import partition_parameters, sql
from ingest.quality import QualityError, evaluate

pytestmark = pytest.mark.sql

Q2 = partition("mobile", 2026, 2)


def quality(sql_env, tag, stage_id, raw_id=None):
    """Production quality.sql + evaluate(); raw_id defaults to the stage (source_rows = rows)."""
    tables = sql_env.target_tables(tag)
    rows = sql_env.run(
        sql("quality", sql_env.dataset, stage_id, raw_id or stage_id, tables),
        partition_parameters(Q2, sql_env.settings, LOADED_AT),
    )
    assert len(rows) == 1
    stats = rows[0]
    return evaluate(stats, sql_env.settings.minimum_row_percent, stats["previous_rows"])


def violations(result):
    return {rule.rule: (rule.severity, rule.violations) for rule in result.rules}


def test_quality_reports_exactly_the_planted_errors(sql_env):
    stage_id = sql_env.stage_table("quality_errors_stage")
    start = Q2.quarter_start
    sql_env.load_rows(
        stage_id,
        [
            tile_row("0331131300000000", start),
            tile_row("0331131300000000", start),  # duplicate: 1 extra row
            tile_row("0331131300000001", start, tests=3, devices=4),  # devices > tests
            tile_row("0331131300000002", start, avg_d_kbps=-100),  # negative speed
        ],
    )

    with pytest.raises(QualityError) as raised:
        quality(sql_env, "quality_errors", stage_id)

    assert violations(raised.value.result) == {
        "invalid_tests": ("ERROR", 0),
        "invalid_devices": ("ERROR", 1),
        "invalid_quadkeys": ("ERROR", 0),
        "duplicate_quadkeys": ("ERROR", 1),
        "invalid_speeds": ("ERROR", 1),
        "outlier_rows": ("WARN", 0),
        "null_down_rows": ("WARN", 0),
        "empty_region": ("ERROR", 0),
        # No previous quarter in ookla_tiles: required rows = ceil(0 * 50 / 100) = 0.
        "row_count_drop": ("ERROR", 0),
    }
    examples = {rule.rule: rule.example_key for rule in raised.value.result.rules}
    assert examples["invalid_devices"] == "0331131300000001"
    assert examples["duplicate_quadkeys"] == "0331131300000000"
    assert examples["invalid_speeds"] == "0331131300000002"


def test_quality_reports_empty_region_when_stage_filters_out_every_tile(sql_env):
    # Only the two outside-bbox tiles of the synthetic source.
    outside = [row for row in source_tiles(20, 30) if row["tile_x"] > 4.4 or row["tile_y"] > 43.9]
    raw_id, stage_id = sql_env.stage_parquet(
        "quality_empty", Q2, source_parquet(outside, pa.int32())
    )

    with pytest.raises(QualityError) as raised:
        quality(sql_env, "quality_empty", stage_id, raw_id)

    errors = {
        rule.rule: rule.violations
        for rule in raised.value.result.rules
        if rule.severity == "ERROR" and rule.violations
    }
    assert errors == {"empty_region": 1}
    assert raised.value.result.rows == 0
    assert raised.value.result.source_rows == 2


@pytest.mark.parametrize("era", ERAS)
def test_quality_counts_null_down_latency_as_warning_only(sql_env, era):
    kind, down, up = ERAS[era]
    raw_id, stage_id = sql_env.stage_parquet(
        f"quality_null_{era}", Q2, source_parquet(source_tiles(down, up), kind)
    )

    # No ERROR rule may fire, so evaluate() returns instead of raising QualityError.
    result = quality(sql_env, f"quality_null_{era}", stage_id, raw_id)

    # 3 inside rows; quadkeys ...01 and ...02 have NULL avg_lat_down_ms: 2.
    assert violations(result)["null_down_rows"] == ("WARN", 2)
    assert result.null_down_rows == 2
    # NULL is not an outlier: NULL > 10000 and IS_NAN(NULL) are NULL, COUNTIF skips them.
    assert violations(result)["outlier_rows"] == ("WARN", 0)
    assert [r.rule for r in result.rules if r.severity == "ERROR" and r.violations] == []
    assert (result.rows, result.source_rows) == (3, 5)
    examples = {rule.rule: rule.example_key for rule in result.rules}
    assert examples["null_down_rows"] == "0331131300000001"


def test_quality_latency_sentinel_is_a_warning_not_an_error(sql_env):
    stage_id = sql_env.stage_table("quality_latency_stage")
    sql_env.load_rows(
        stage_id,
        [
            tile_row("0331131300000000", Q2.quarter_start),
            # 2146959360 ms is far above latency_ceiling_ms = 10000.
            tile_row("0331131300000001", Q2.quarter_start, avg_lat_down_ms=2146959360.0),
        ],
    )

    result = quality(sql_env, "quality_latency", stage_id)

    assert violations(result)["outlier_rows"] == ("WARN", 1)
    assert [
        rule.rule for rule in result.rules if rule.severity == "ERROR" and rule.violations
    ] == []
    assert result.rows == 2
