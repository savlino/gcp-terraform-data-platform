"""DEV-only BigQuery SQL tests.

Run with SQL_TEST_DATASET=<project>.<dataset> (the DEV `sql_tests` dataset) and ADC that can act as
ci-sqltest-dev. Every table gets a per-run prefix and is deleted in teardown; the dataset's one-hour
default table expiration is only a safety net.
"""

import os
import secrets
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from bq_helpers import DATASET, MAXIMUM_BYTES_BILLED, SCHEMAS, SqlEnv
from google.cloud import bigquery

from ingest.build_config import BuildConfig


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("SQL_TEST_DATASET"):
        return
    skip = pytest.mark.skip(reason="SQL_TEST_DATASET is not set; SQL tests run only in DEV")
    for item in items:
        if item.get_closest_marker("sql") is not None:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def sql_env() -> Iterator[SqlEnv]:
    target = os.environ["SQL_TEST_DATASET"].strip()
    if not DATASET.fullmatch(target):
        raise ValueError("SQL_TEST_DATASET must be <project_id>.<dataset_id>.")
    project, dataset_id = target.split(".", 1)
    client = bigquery.Client(project=project)
    prefix = f"t{datetime.now(UTC):%Y%m%d%H%M%S}_{secrets.token_hex(3)}_"
    settings = BuildConfig(
        dataset_id=dataset_id,
        location=client.get_dataset(target).location,
        bbox=(-9.6, 4.4, 35.8, 43.9),
        maximum_bytes_billed=MAXIMUM_BYTES_BILLED,
        mart_zoom=10,
        minimum_row_percent=50,
        latency_ceiling_ms=10000,
        schema_path=SCHEMAS / "ookla_tiles.json",
    )
    try:
        yield SqlEnv(client, target, prefix, settings)
    finally:
        # Delete by prefix, so tables of failed or interrupted tests are removed too.
        failures = []
        for table in client.list_tables(target):
            if table.table_id.startswith(prefix):
                try:
                    client.delete_table(table, not_found_ok=True)
                except Exception as error:
                    failures.append(f"{table.table_id}: {error}")
        if failures:
            raise RuntimeError("SQL test cleanup failed: " + "; ".join(failures))
