"""Helpers for the DEV BigQuery SQL tests: synthetic rows, table creation and loads."""

import io
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from google.cloud import bigquery

from ingest.build import TARGET_TABLES, RawPartition, partition_parameters, query, sql
from ingest.build_config import BuildConfig

SCHEMAS = Path(__file__).resolve().parents[3] / "schemas"
DATASET = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]\.[A-Za-z_][A-Za-z0-9_]{0,1023}")
# DML bills at least 10 MB per referenced table; publish.sql runs four DML statements.
MAXIMUM_BYTES_BILLED = 100 * 1024**2
LABELS = {"project": "gcp-tf-portfolio", "env": "dev", "managed_by": "pytest-sql"}
# Mirrors local.tables in modules/bigquery/main.tf, so require_partition_filter is exercised.
TARGET_LAYOUT = {
    "ookla_tiles": (True, ["network_type", "quadkey"]),
    "load_log": (False, ["network_type", "quarter_start"]),
    "mart_quarterly_performance": (True, ["network_type", "quadkey_prefix"]),
}
LOADED_AT = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


def schema(name: str) -> list[bigquery.SchemaField]:
    fields = json.loads((SCHEMAS / f"{name}.json").read_text(encoding="utf-8"))
    return [bigquery.SchemaField.from_api_repr(field) for field in fields]


def partition(network_type: str, year: int, quarter: int) -> RawPartition:
    return RawPartition(
        network_type,
        year,
        quarter,
        f"gs://sql-test/ookla/type={network_type}/year={year}/quarter={quarter}/t.parquet",
        f"ookla/type={network_type}/year={year}/quarter={quarter}/t.parquet",
        f"etag-{network_type}-{year}q{quarter}",
        1,
    )


def tile_row(
    quadkey: str, quarter_start: date, network_type: str = "mobile", **values: Any
) -> dict:
    """A valid ookla_tiles row inside the default bbox; keyword arguments override columns."""
    row = {
        "network_type": network_type,
        "quarter_start": quarter_start,
        "quadkey": quadkey,
        "tile": "POLYGON((-3.70 40.40, -3.69 40.40, -3.69 40.41, -3.70 40.41, -3.70 40.40))",
        "tile_x": -3.7,
        "tile_y": 40.4,
        "avg_d_kbps": 50000,
        "avg_u_kbps": 10000,
        "avg_lat_ms": 20.0,
        "avg_lat_down_ms": 30.0,
        "avg_lat_up_ms": 40.0,
        "tests": 10,
        "devices": 5,
        "avg_d_mbps": 50.0,
        "avg_u_mbps": 10.0,
        "loaded_at": LOADED_AT,
    }
    row.update(values)
    return row


MADRID = "POLYGON((-3.70 40.40, -3.69 40.40, -3.69 40.41, -3.70 40.41, -3.70 40.40))"
# Physical avg_lat_down/up_ms type and non-NULL values per source era.
ERAS = {"int32": (pa.int32(), 20, 30), "double": (pa.float64(), 20.5, 30.5)}


def source_tiles(latency_down: float, latency_up: float) -> list[dict[str, Any]]:
    """Five source tiles: three inside the Iberian bbox, two outside.

    Inside: one complete tile, one with down and up latency NULL, one with only down NULL.
    So the inside rows hold 2 NULL avg_lat_down_ms and 1 NULL avg_lat_up_ms.
    """
    inside = {
        "quadkey": "0331131300000000",
        "tile": MADRID,
        "tile_x": -3.7,
        "tile_y": 40.4,
        "avg_d_kbps": 50000,
        "avg_u_kbps": 10000,
        "avg_lat_ms": 15,
        "avg_lat_down_ms": latency_down,
        "avg_lat_up_ms": latency_up,
        "tests": 12,
        "devices": 4,
    }
    return [
        inside,
        dict(inside, quadkey="0331131300000001", avg_lat_down_ms=None, avg_lat_up_ms=None),
        dict(inside, quadkey="0331131300000002", avg_lat_down_ms=None),
        # East of max_lon 4.4 and north of max_lat 43.9: both outside the bbox.
        dict(inside, quadkey="1202000000000000", tile_x=10.0),
        dict(inside, quadkey="0313000000000000", tile_y=50.0),
    ]


def source_parquet(rows: list[Mapping[str, Any]], latency: pa.DataType) -> pa.Table:
    """Synthetic Ookla source file; `latency` is the physical type of avg_lat_down/up_ms."""
    fields = [
        ("quadkey", pa.string()),
        ("tile", pa.string()),
        ("tile_x", pa.float64()),
        ("tile_y", pa.float64()),
        ("avg_d_kbps", pa.int64()),
        ("avg_u_kbps", pa.int64()),
        ("avg_lat_ms", pa.int64()),
        ("avg_lat_down_ms", latency),
        ("avg_lat_up_ms", latency),
        ("tests", pa.int64()),
        ("devices", pa.int64()),
    ]
    return pa.table(
        {name: pa.array([row[name] for row in rows], type=kind) for name, kind in fields}
    )


@dataclass
class SqlEnv:
    client: bigquery.Client
    dataset: str
    prefix: str
    settings: BuildConfig

    def table_id(self, name: str) -> str:
        return f"{self.dataset}.{self.prefix}{name}"

    def create_table(
        self,
        name: str,
        fields: Sequence[bigquery.SchemaField] | None = None,
        partitioned: bool = False,
        clustering: list[str] | None = None,
    ) -> str:
        table_id = self.table_id(name)
        table = bigquery.Table(table_id, schema=fields)
        table.labels = LABELS
        if partitioned:
            table.time_partitioning = bigquery.TimePartitioning(field="quarter_start")
            table.require_partition_filter = True
        table.clustering_fields = clustering
        self.client.create_table(table)
        return table_id

    def target_tables(self, tag: str) -> dict[str, str]:
        """Create the three production tables for one test; returns logical -> table name."""
        names = {}
        for table in TARGET_TABLES:
            partitioned, clustering = TARGET_LAYOUT[table]
            self.create_table(f"{tag}_{table}", schema(table), partitioned, clustering)
            names[table] = f"{self.prefix}{tag}_{table}"
        return names

    def stage_table(self, name: str) -> str:
        return self.create_table(name, schema("ookla_tiles"))

    def stage_parquet(self, tag: str, source: RawPartition, parquet: pa.Table) -> tuple[str, str]:
        """Load synthetic Parquet into a raw table and run production stage.sql on it."""
        raw_id = self.create_table(f"{tag}_raw")
        self.load_parquet(raw_id, parquet)
        stage_id = self.stage_table(f"{tag}_stage")
        self.run(
            sql("stage", self.dataset, stage_id, raw_id),
            partition_parameters(source, self.settings, LOADED_AT),
            destination=stage_id,
            collect=False,
        )
        return raw_id, stage_id

    def load_rows(self, table_id: str, rows: list[Mapping[str, Any]]) -> None:
        job_config = bigquery.LoadJobConfig(
            schema=self.client.get_table(table_id).schema,
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            create_disposition=bigquery.CreateDisposition.CREATE_NEVER,
        )
        self.client.load_table_from_json(
            [_json_ready(row) for row in rows],
            table_id,
            job_config=job_config,
            location=self.settings.location,
        ).result()

    def load_parquet(self, table_id: str, table: pa.Table) -> None:
        buffer = io.BytesIO()
        pq.write_table(table, buffer)
        buffer.seek(0)
        # Same load settings as production raw staging, but from memory instead of GCS.
        self.client.load_table_from_file(
            buffer,
            table_id,
            job_config=bigquery.LoadJobConfig(
                source_format=bigquery.SourceFormat.PARQUET,
                autodetect=True,
                write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
                create_disposition=bigquery.CreateDisposition.CREATE_NEVER,
            ),
            location=self.settings.location,
        ).result()

    def run(
        self,
        text: str,
        parameters: list[bigquery.ScalarQueryParameter] | None = None,
        destination: str | None = None,
        collect: bool = True,
    ) -> list[dict[str, Any]]:
        # Production query(): maximum_bytes_billed is set on every query job.
        rows = query(self.client, self.settings, text, parameters or [], destination, collect)
        return [dict(row.items()) for row in rows]


def _json_ready(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value.isoformat() if isinstance(value, date | datetime) else value
        for key, value in row.items()
    }
