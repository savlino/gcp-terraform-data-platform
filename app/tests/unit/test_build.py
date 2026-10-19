from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from google.api_core.exceptions import Conflict

from ingest import build as module
from ingest.build import (
    BuildError,
    RawPartition,
    build,
    publish_partition,
    query,
    raw_partitions,
    sql,
)
from ingest.build_config import BuildConfig
from ingest.config import Config
from ingest.quality import QualityError, QualityResult


@pytest.fixture
def build_setup(monkeypatch, schema_path):
    monkeypatch.delenv("NATIVE_SCHEMA_PATH", raising=False)
    for key in (
        "BIGQUERY_DATASET",
        "REGION",
        "REGION_BBOX",
        "MAXIMUM_BYTES_BILLED",
        "MART_ZOOM",
        "MINIMUM_ROW_PERCENT",
        "LATENCY_CEILING_MS",
    ):
        monkeypatch.delenv(key, raising=False)
    config = Config("dep-2610-dev", "dep-2610-dev-raw", (2023, 3), "us-west-2", schema_path, None)
    settings = BuildConfig.from_env()
    part = RawPartition(
        "mobile",
        2023,
        3,
        "gs://dep-2610-dev-raw/ookla/type=mobile/year=2023/quarter=3/test.parquet",
        "ookla/type=mobile/year=2023/quarter=3/test.parquet",
        "opaque-7",
        123,
    )
    blob = MagicMock()
    blob.name, blob.generation, blob.size = part.name, 123, 12
    blob.metadata = {"source_etag": "opaque-7", "source_size": "12"}
    gcs, bq = MagicMock(), MagicMock()
    gcs.list_blobs.return_value = [blob]
    gcs.bucket.return_value.get_blob.return_value = blob
    return config, settings, part, gcs, bq


def stats(**changes):
    values = dict(
        row_count=10,
        source_rows=100,
        invalid_tests=0,
        invalid_devices=0,
        invalid_quadkeys=0,
        duplicate_quadkeys=0,
        invalid_speeds=0,
        outlier_rows=1,
        null_down_rows=2,
        previous_rows=20,
    )
    values.update(changes)
    for rule in (
        "invalid_tests",
        "invalid_devices",
        "invalid_quadkeys",
        "duplicate_quadkeys",
        "invalid_speeds",
        "outlier_rows",
        "null_down_rows",
    ):
        values[rule + "_example"] = "0" * 16 if values[rule] else None
    return values


def responses(bq, *, watermark=None, quality=None):
    def result(text, **kwargs):
        job = MagicMock()
        if "QUALIFY ROW_NUMBER()" in text:
            job.result.return_value = watermark or []
        elif "invalid_devices" in text:
            job.result.return_value = [quality or stats()]
        else:
            job.result.return_value = []
        return job

    bq.query.side_effect = result


def test_staging_expiry_quality_before_publish_and_billing(build_setup):
    config, settings, part, gcs, bq = build_setup
    responses(bq)
    now = datetime.now(UTC)
    result = publish_partition(gcs, bq, config, settings, "dep-2610-dev.network_perf", part)
    assert result.rows == 10
    table = bq.create_table.call_args.args[0]
    assert table.table_id == "stg_ookla_tiles_mobile_2023q3"
    assert now + timedelta(hours=23) < table.expires < now + timedelta(hours=25)
    calls = bq.query.call_args_list
    assert "FROM `dep-2610-dev.network_perf.ext_ookla_raw`" in calls[0].args[0]
    assert "invalid_devices" in calls[1].args[0]
    assert "BEGIN TRANSACTION" in calls[2].args[0]
    for call in calls:
        assert call.kwargs["job_config"].maximum_bytes_billed == settings.maximum_bytes_billed
        assert call.kwargs["location"] == settings.location
    stage_job = calls[0].kwargs["job_config"]
    assert stage_job.write_disposition == "WRITE_TRUNCATE"
    assert stage_job.destination.table_id == part.stage_name
    assert calls[2].kwargs["job_config"].destination is None
    bq.delete_table.assert_called_once()


def test_bad_devices_stops_before_curated_or_load_log_mutation(build_setup):
    config, settings, part, gcs, bq = build_setup
    responses(bq, quality=stats(invalid_devices=1))
    with pytest.raises(QualityError, match="invalid_devices"):
        publish_partition(gcs, bq, config, settings, "dep-2610-dev.network_perf", part)
    assert len(bq.query.call_args_list) == 2
    assert all("DELETE FROM" not in call.args[0] for call in bq.query.call_args_list)
    bq.delete_table.assert_called_once()


def test_environment_byte_limit_applies_to_every_build_query(build_setup, monkeypatch):
    config, _, _, gcs, bq = build_setup
    monkeypatch.setenv("MAXIMUM_BYTES_BILLED", "123456789")
    settings = BuildConfig.from_env()
    responses(bq)
    build(gcs, bq, config, settings, "mobile", (2023, 3), False, False)
    assert settings.maximum_bytes_billed == 123456789
    assert bq.query.call_count == 5
    for call in bq.query.call_args_list:
        assert call.kwargs["job_config"].maximum_bytes_billed == 123456789


def test_changed_raw_generation_stops_before_publish(build_setup):
    config, settings, part, gcs, bq = build_setup
    responses(bq)
    gcs.bucket.return_value.get_blob.return_value.generation = 124
    with pytest.raises(ValueError, match="changed during build"):
        publish_partition(gcs, bq, config, settings, "dep-2610-dev.network_perf", part)
    assert len(bq.query.call_args_list) == 2


def test_existing_stage_fails_without_deleting_another_run_stage(build_setup):
    config, settings, part, gcs, bq = build_setup
    bq.create_table.side_effect = Conflict("Already exists")
    with pytest.raises(Conflict):
        publish_partition(gcs, bq, config, settings, "dep-2610-dev.network_perf", part)
    bq.delete_table.assert_not_called()
    bq.query.assert_not_called()


def test_failed_publish_is_surfaced_and_stage_cleaned(build_setup):
    config, settings, part, gcs, bq = build_setup
    responses(bq)
    original = bq.query.side_effect

    def fail_publish(text, **kwargs):
        if "BEGIN TRANSACTION" in text:
            raise RuntimeError("Transaction failed")
        return original(text, **kwargs)

    bq.query.side_effect = fail_publish
    with pytest.raises(RuntimeError, match="Transaction failed"):
        publish_partition(gcs, bq, config, settings, "dep-2610-dev.network_perf", part)
    bq.delete_table.assert_called_once()


@pytest.mark.parametrize("force,expected", [(False, 0), (True, 1)])
def test_watermark_etag_selection_force_and_mart_recovery(
    build_setup, monkeypatch, force, expected
):
    config, settings, part, gcs, bq = build_setup
    responses(
        bq,
        watermark=[
            dict(
                network_type="mobile",
                quarter_start=date(2023, 7, 1),
                source_etag=part.etag,
                source_uri=part.uri,
            )
        ],
    )
    publish = MagicMock(return_value=QualityResult(10, 100, 1, 2, ()))
    monkeypatch.setattr(module, "publish_partition", publish)
    build(gcs, bq, config, settings, "mobile", (2023, 3), force, False)
    assert publish.call_count == expected
    assert "mart_quarterly_performance" in bq.query.call_args.args[0]


def test_unchanged_summary_does_not_claim_dq_pass(build_setup):
    config, settings, part, gcs, bq = build_setup
    responses(
        bq,
        watermark=[
            dict(
                network_type=part.network_type,
                quarter_start=part.quarter_start,
                source_etag=part.etag,
                source_uri=part.uri,
            )
        ],
    )
    summary = build(gcs, bq, config, settings, "mobile", (2023, 3), False, False)
    assert "DQ=NOT_RUN" in summary
    assert "null_down_share=n/a" in summary
    assert "outlier_rows=0" in summary


def test_changed_etag_selects_only_that_raw_quarter(build_setup, monkeypatch):
    config, settings, part, gcs, bq = build_setup
    responses(
        bq,
        watermark=[
            dict(
                network_type="mobile",
                quarter_start=part.quarter_start,
                source_etag="old",
                source_uri=part.uri,
            )
        ],
    )
    publish = MagicMock(return_value=QualityResult(10, 100, 1, 2, ()))
    monkeypatch.setattr(module, "publish_partition", publish)
    build(gcs, bq, config, settings, "mobile", (2023, 3), False, False)
    assert publish.call_args.args[-1] == part


def test_dry_run_reads_watermarks_only(build_setup):
    config, settings, part, gcs, bq = build_setup
    responses(bq)
    build(gcs, bq, config, settings, "mobile", (2023, 3), True, True)
    bq.create_table.assert_not_called()
    bq.delete_table.assert_not_called()
    bq.query.assert_called_once()
    assert "QUALIFY" in bq.query.call_args.args[0]


def test_success_summary_includes_duration_and_warnings(build_setup, monkeypatch):
    config, settings, _, gcs, bq = build_setup
    responses(bq)
    clock = iter([100.0, 112.5])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(clock))
    summary = build(gcs, bq, config, settings, "mobile", (2023, 3), False, False)
    assert "duration_s=12.50" in summary
    assert "DQ=PASS" in summary and "rows=10" in summary
    assert "mobile 2023-Q3" in summary
    assert "outlier_rows=1" in summary
    assert "null_down_rows=2" in summary and "null_down_share=20.00%" in summary


def test_dq_failure_notification_has_actual_partition_and_rules(build_setup):
    config, settings, _, gcs, bq = build_setup
    responses(bq, quality=stats(invalid_devices=2, invalid_tests=1))
    with pytest.raises(BuildError) as error:
        build(gcs, bq, config, settings, "all", None, False, False)
    message = error.value.notification
    assert "type=mobile" in message and "quarter=2023-Q3" in message
    assert "invalid_devices(2)" in message and "invalid_tests(1)" in message
    assert isinstance(error.value.__cause__, QualityError)
    assert bq.query.call_count == 3


@pytest.mark.parametrize("phase", ["partition", "mart"])
def test_processing_failure_notification_suppresses_exception(build_setup, monkeypatch, phase):
    config, settings, _, gcs, bq = build_setup
    responses(bq)
    secret = "https://example.invalid/synthetic-token"
    if phase == "partition":
        monkeypatch.setattr(
            module, "publish_partition", MagicMock(side_effect=RuntimeError(secret))
        )
    else:
        original = bq.query.side_effect

        def fail_mart(text, **kwargs):
            if "WITH weighted AS" in text:
                raise RuntimeError(secret)
            return original(text, **kwargs)

        bq.query.side_effect = fail_mart
    with pytest.raises(BuildError) as error:
        build(gcs, bq, config, settings, "mobile", (2023, 3), False, False)
    assert secret not in error.value.notification
    assert ("partition_processing" if phase == "partition" else "mart_rebuild") in str(error.value)


def test_no_global_file_materialization_for_stage_query(build_setup):
    _, settings, _, _, bq = build_setup
    rows = MagicMock()
    rows.__iter__.side_effect = AssertionError("Do not download staging rows")
    bq.query.return_value.result.return_value = rows
    assert query(bq, settings, "stage", [], destination="p.d.stg", collect=False) == []
    bq.query.return_value.result.assert_called_once_with(max_results=0)


def test_duplicate_or_missing_raw_metadata_rejected(build_setup):
    config, _, _, gcs, _ = build_setup
    blob = gcs.list_blobs.return_value[0]
    gcs.list_blobs.return_value = [blob, blob]
    with pytest.raises(ValueError, match="Multiple raw files"):
        raw_partitions(gcs, config, "all", None)
    gcs.list_blobs.return_value = [blob]
    blob.metadata = {}
    with pytest.raises(ValueError, match="metadata"):
        raw_partitions(gcs, config, "all", None)


def test_raw_scope_and_missing_quarter(build_setup):
    config, _, part, gcs, _ = build_setup
    assert raw_partitions(gcs, config, "mobile", (2023, 3)) == [part]
    with pytest.raises(ValueError, match="no raw objects"):
        raw_partitions(gcs, config, "fixed", (2023, 3))


@pytest.mark.parametrize(
    "key,value",
    [
        ("MART_ZOOM", "17"),
        ("MINIMUM_ROW_PERCENT", "101"),
        ("LATENCY_CEILING_MS", "nan"),
        ("MAXIMUM_BYTES_BILLED", "0"),
        ("REGION_BBOX", '{"min_lon":10,"max_lon":-10,"min_lat":35,"max_lat":44}'),
        ("BIGQUERY_DATASET", "bad`identifier"),
    ],
)
def test_build_config_validation(build_setup, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        BuildConfig.from_env()


def test_sql_transaction_and_filter_contracts():
    publish = sql("publish", "project.dataset", "project.dataset.stage")
    assert "WHERE quarter_start = @quarter_start AND network_type = @network_type" in publish
    assert (
        publish.index("INSERT INTO `project.dataset.ookla_tiles`")
        < publish.index("INSERT INTO `project.dataset.load_log`")
        < publish.index("COMMIT TRANSACTION")
    )
    assert publish.startswith("BEGIN TRANSACTION;")
    stage = sql("stage", "project.dataset")
    assert "type = @network_type AND year = @year AND quarter = @quarter" in stage
    assert "tile_x BETWEEN @min_lon AND @max_lon" in stage
    assert "_FILE_NAME = @source_uri" in stage
    assert "{16}" in sql("quality", "project.dataset", "stage")
    watermarks = sql("watermarks", "project.dataset")
    assert "WHERE status = 'SUCCESS'" in watermarks
    assert "ORDER BY loaded_at DESC" in watermarks
