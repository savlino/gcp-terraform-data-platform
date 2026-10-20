import json
from pathlib import Path
from unittest.mock import MagicMock

import google.auth
import pyarrow as pa
import pytest
import requests

from ingest.discover import SourceObject

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def no_cloud_or_http(monkeypatch):
    monkeypatch.delenv("SCHEMAS_DIR", raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("Unit tests must not use network or ADC.")

    monkeypatch.setattr(requests.Session, "request", forbidden)
    monkeypatch.setattr(google.auth, "default", forbidden)


@pytest.fixture
def schema_path():
    return ROOT / "schemas" / "ookla_raw.json"


@pytest.fixture
def source_schema(schema_path):
    mapping = {"STRING": pa.string(), "FLOAT": pa.float64(), "INTEGER": pa.int64()}
    fields = json.loads(schema_path.read_text())
    return pa.schema([(field["name"], mapping[field["type"]]) for field in fields])


@pytest.fixture
def source():
    return SourceObject(
        "parquet/performance/type=mobile/year=2023/quarter=3/test.parquet",
        "mobile",
        2023,
        3,
        "test.parquet",
        "opaque-multipart-etag-7",
        12,
        "https://ookla-open-data.s3.us-west-2.amazonaws.com/"
        "parquet/performance/type=mobile/year=2023/quarter=3/test.parquet",
    )


@pytest.fixture
def response():
    result = MagicMock()
    result.__enter__.return_value = result
    return result
