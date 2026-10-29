import json
import re
import sqlite3

import pytest

from ingest.build import sql
from ingest.quality import QualityError, evaluate


class CountIf:
    def __init__(self):
        self.count = 0

    def step(self, value):
        self.count += bool(value)

    def finalize(self):
        return self.count


def local_sql(text):
    return (
        text.replace("`dataset.", "`")
        .replace("r'^[0-3]", "'^[0-3]")
        .replace("DATE_SUB(@quarter_start, INTERVAL 3 MONTH)", "DATE(@quarter_start, '-3 months')")
    )


def test_bad_synthetic_partition_stops_and_valid_publish_preserves_other_type(schema_path):
    native = json.loads(schema_path.with_name("ookla_tiles.json").read_text())
    log_schema = json.loads(schema_path.with_name("load_log.json").read_text())
    with sqlite3.connect(":memory:") as connection:
        connection.row_factory = sqlite3.Row
        connection.create_aggregate("COUNTIF", 1, CountIf)
        connection.create_function(
            "REGEXP_CONTAINS", 2, lambda value, pattern: bool(re.search(pattern, value or ""))
        )
        connection.create_function("IS_NAN", 1, lambda value: False)
        connection.create_function("IS_INF", 1, lambda value: False)
        for name, fields in (("ookla_tiles", native), ("stage", native), ("load_log", log_schema)):
            columns = ", ".join(
                f"{field['name']} " + ("REAL" if field["type"] in ("INTEGER", "FLOAT") else "TEXT")
                for field in fields
            )
            connection.execute(f"CREATE TABLE {name} ({columns})")
        connection.execute("CREATE TABLE raw_stage (quadkey TEXT)")
        row = dict(
            network_type="mobile",
            quarter_start="2023-07-01",
            quadkey="0" * 16,
            tile="POLYGON EMPTY",
            tile_x=-3,
            tile_y=40,
            avg_d_kbps=10000,
            avg_u_kbps=5000,
            avg_lat_ms=20,
            avg_lat_down_ms=30,
            avg_lat_up_ms=40,
            tests=2,
            devices=3,
            avg_d_mbps=10,
            avg_u_mbps=5,
            loaded_at="2026-01-01",
        )
        names = ", ".join(row)
        placeholders = ", ".join(":" + name for name in row)
        for layer in ("fixed", "mobile"):
            connection.execute(
                f"INSERT INTO ookla_tiles ({names}) VALUES ({placeholders})",
                row | {"network_type": layer, "devices": 1},
            )
        connection.execute(f"INSERT INTO stage ({names}) VALUES ({placeholders})", row)
        parameters = dict(
            network_type="mobile",
            quarter_start="2023-07-01",
            source_uri="gs://raw/file",
            source_etag="opaque-7",
            year=2023,
            quarter=3,
            loaded_at="2026-01-02",
            latency_ceiling=10000,
            source_rows=1,
            loaded_rows=1,
        )
        connection.execute("INSERT INTO raw_stage VALUES ('0000000000000000')")
        quality_sql = local_sql(sql("quality", "dataset", "stage", "raw_stage"))
        bad_stats = dict(connection.execute(quality_sql, parameters).fetchone())
        assert bad_stats["invalid_devices"] == 1
        assert bad_stats["invalid_devices_example"] == "0" * 16
        assert bad_stats["invalid_tests_example"] is None
        connection.execute(
            "UPDATE stage SET tests=0, avg_d_kbps=-1, avg_lat_ms=10001, avg_lat_down_ms=NULL"
        )
        invalid_stats = dict(connection.execute(quality_sql, parameters).fetchone())
        for rule in ("invalid_tests", "invalid_speeds", "outlier_rows", "null_down_rows"):
            assert invalid_stats[rule] == 1
            assert invalid_stats[rule + "_example"] == "0" * 16
        connection.execute("UPDATE stage SET quadkey=NULL")
        null_stats = dict(connection.execute(quality_sql, parameters).fetchone())
        assert null_stats["invalid_quadkeys_example"] == "<NULL>"
        assert null_stats["duplicate_quadkeys"] == 0
        connection.execute("DELETE FROM stage")
        connection.execute(f"INSERT INTO stage ({names}) VALUES ({placeholders})", row)
        connection.execute(f"INSERT INTO stage ({names}) VALUES ({placeholders})", row)
        duplicates = dict(connection.execute(quality_sql, parameters).fetchone())
        assert duplicates["duplicate_quadkeys"] == 1
        assert duplicates["duplicate_quadkeys_example"] == "0" * 16
        connection.execute("DELETE FROM stage")
        connection.execute(f"INSERT INTO stage ({names}) VALUES ({placeholders})", row)
        before = list(connection.execute("SELECT * FROM ookla_tiles"))
        with pytest.raises(QualityError, match="invalid_devices"):
            evaluate(bad_stats, 50, bad_stats["previous_rows"])
        assert list(connection.execute("SELECT * FROM ookla_tiles")) == before
        assert connection.execute("SELECT COUNT(*) FROM load_log").fetchone()[0] == 0

        connection.execute("UPDATE stage SET devices=1, loaded_at='2026-01-02'")
        good_stats = dict(connection.execute(quality_sql, parameters).fetchone())
        assert evaluate(good_stats, 50, 0).rows == 1
        connection.commit()
        publish_sql = local_sql(sql("publish", "dataset", "stage"))
        for statement in publish_sql.split(";"):
            if statement.strip():
                connection.execute(statement, parameters)
        rows = connection.execute(
            "SELECT network_type, loaded_at FROM ookla_tiles ORDER BY network_type"
        ).fetchall()
        assert [tuple(r) for r in rows] == [("fixed", "2026-01-01"), ("mobile", "2026-01-02")]
        assert connection.execute("SELECT source_etag FROM load_log").fetchone()[0] == "opaque-7"


def test_stage_casts_every_numeric_source_column_to_native_type(schema_path):
    native = json.loads(schema_path.with_name("ookla_tiles.json").read_text())
    contract = json.loads(schema_path.read_text())
    source_names = {field["name"] for field in contract}
    rendered = sql("stage", "dataset", raw_stage="dataset.stg_raw_mobile_2026q2")
    numeric = {
        field["name"]: {"INTEGER": "INT64", "FLOAT": "FLOAT64"}[field["type"]]
        for field in native
        if field["name"] in source_names and field["type"] in ("INTEGER", "FLOAT")
    }
    assert len(numeric) == 9
    for name, target in numeric.items():
        assert f"CAST({name} AS {target}) AS {name}" in rendered
    for name in ("avg_lat_ms", "avg_lat_down_ms", "avg_lat_up_ms"):
        assert numeric[name] == "FLOAT64"
    assert "CAST(avg_d_kbps AS FLOAT64) / 1000 AS avg_d_mbps" in rendered
    assert "CAST(avg_u_kbps AS FLOAT64) / 1000 AS avg_u_mbps" in rendered
