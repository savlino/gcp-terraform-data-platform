import json
import struct
from pathlib import Path

import pyarrow as pa
import pyarrow.fs as fs
import pyarrow.parquet as pq

from ingest.config import SourceConfig

MAX_FOOTER_BYTES = 16 * 1024 * 1024


def check_schema(actual: pa.Schema, schema_path: Path) -> None:
    expected = json.loads(schema_path.read_text(encoding="utf-8"))
    failures: list[str] = []
    for field in expected:
        name, allowed_types = field["name"], field["types"]
        if actual.names.count(name) != 1:
            failures.append(f"{name}: missing or duplicated")
            continue
        actual_type = str(actual.field(name).type)
        if actual_type not in allowed_types:
            failures.append(
                f"{name}: found {actual_type}, allowed types: {', '.join(allowed_types)}"
            )
    if failures:
        raise ValueError("Incompatible source Parquet schema: " + "; ".join(failures))


def source_filesystem(source_config: SourceConfig) -> fs.S3FileSystem:
    # SOURCE_ENDPOINT applies to HTTPS only; Arrow always uses bucket + region.
    return fs.S3FileSystem(
        anonymous=True,
        region=source_config.region,
        connect_timeout=10,
        request_timeout=120,
        retry_strategy=fs.AwsStandardS3RetryStrategy(max_attempts=4),
    )


def check_source(
    source_fs: fs.FileSystem,
    source_config: SourceConfig,
    key: str,
    expected_size: int,
    schema_path: Path,
) -> None:
    # Read exactly the trailer and metadata range, never row groups.
    with source_fs.open_input_file(source_config.arrow_path(key)) as stream:
        size = stream.size()
        if size != expected_size or size < 12:
            raise ValueError(f"Source size changed or invalid Parquet file: {key}; rediscover.")
        stream.seek(size - 8)
        trailer = stream.read(8)
        if len(trailer) != 8 or trailer[4:] != b"PAR1":
            raise ValueError(f"Invalid Parquet trailer: {key}")
        length = struct.unpack("<I", trailer[:4])[0]
        if length <= 0 or length > MAX_FOOTER_BYTES or length > size - 12:
            raise ValueError(f"Invalid or oversized Parquet footer: {key}")
        stream.seek(size - 8 - length)
        footer = stream.read(length)
        if len(footer) != length:
            raise ValueError(f"Truncated Parquet footer: {key}")
    schema = pq.read_schema(pa.BufferReader(b"PAR1" + footer + trailer))
    check_schema(schema, schema_path)
