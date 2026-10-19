import json
import struct
from pathlib import Path

import pyarrow as pa
import pyarrow.fs as fs
import pyarrow.parquet as pq

MAX_FOOTER_BYTES = 16 * 1024 * 1024


def check_schema(actual: pa.Schema, schema_path: Path) -> None:
    expected = json.loads(schema_path.read_text(encoding="utf-8"))
    failures: list[str] = []
    for field in expected:
        name, expected_type = field["name"], field["type"]
        if actual.names.count(name) != 1:
            failures.append(f"{name}: missing or duplicated")
            continue
        actual_type = actual.field(name).type
        compatible = {
            "STRING": pa.types.is_string(actual_type) or pa.types.is_large_string(actual_type),
            "INTEGER": pa.types.is_signed_integer(actual_type),
            "FLOAT": pa.types.is_floating(actual_type) or pa.types.is_signed_integer(actual_type),
        }
        if expected_type not in compatible:
            raise ValueError(f"Unsupported source contract type: {expected_type}")
        if not compatible[expected_type]:
            failures.append(f"{name}: expected {expected_type}, got {actual_type}")
    if failures:
        raise ValueError("Incompatible source Parquet schema: " + "; ".join(failures))


def check_source(source_fs: fs.FileSystem, key: str, expected_size: int, schema_path: Path) -> None:
    # Read exactly the trailer and metadata range, never row groups.
    with source_fs.open_input_file("ookla-open-data/" + key) as stream:
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
