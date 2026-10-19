import io
import json
import struct
from unittest.mock import MagicMock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from ingest.schema_check import check_schema, check_source


def parquet_bytes(schema):
    table = pa.Table.from_arrays(
        [pa.array([None] * 100, type=field.type) for field in schema], schema=schema
    )
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink)
    return sink.getvalue().to_pybytes()


@pytest.mark.parametrize("missing_column", ["devices", "tile_x"])
def test_missing_column_in_synthetic_parquet_fails(source_schema, schema_path, missing_column):
    schema = source_schema.remove(source_schema.get_field_index(missing_column))
    data = parquet_bytes(schema)
    filesystem = MagicMock()
    filesystem.open_input_file.return_value = SizedBytes(data)
    with pytest.raises(ValueError, match=rf"{missing_column}: missing"):
        check_source(filesystem, "synthetic.parquet", len(data), schema_path)


class SizedBytes(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.reads = []

    def size(self):
        return len(self.getvalue())

    def read(self, size=-1):
        self.reads.append((self.tell(), size))
        assert size >= 0, "Unbounded reads are forbidden."
        return super().read(size)


def test_only_footer_is_read(source_schema, schema_path):
    data = parquet_bytes(source_schema)
    stream = SizedBytes(data)
    filesystem = MagicMock()
    filesystem.open_input_file.return_value = stream
    check_source(filesystem, "synthetic.parquet", len(data), schema_path)
    assert len(stream.reads) == 2
    assert stream.reads[0] == (len(data) - 8, 8)
    footer_start, footer_length = stream.reads[1]
    assert footer_start > 4
    assert footer_start + footer_length == len(data) - 8
    filesystem.open_input_file.assert_called_once_with("ookla-open-data/synthetic.parquet")


def test_compatible_old_and_new_source_types(source_schema, schema_path):
    fields = [
        pa.field(f.name, pa.int32() if f.name.startswith("avg_lat_") else f.type)
        for f in source_schema
    ]
    check_schema(pa.schema(fields), schema_path)
    check_schema(source_schema, schema_path)
    check_schema(
        source_schema.set(
            source_schema.get_field_index("quadkey"), pa.field("quadkey", pa.large_string())
        ),
        schema_path,
    )


def test_incompatible_type_fails(source_schema, schema_path):
    schema = source_schema.set(
        source_schema.get_field_index("tests"), pa.field("tests", pa.string())
    )
    with pytest.raises(ValueError, match="tests: expected INTEGER"):
        check_schema(schema, schema_path)


def test_changed_size_fails_before_footer_read(source_schema, schema_path):
    data = parquet_bytes(source_schema)
    filesystem = MagicMock()
    filesystem.open_input_file.return_value = SizedBytes(data)
    with pytest.raises(ValueError, match="Source size changed"):
        check_source(filesystem, "synthetic.parquet", len(data) + 1, schema_path)


def test_invalid_trailer_rejected(schema_path):
    filesystem = MagicMock()
    filesystem.open_input_file.return_value = SizedBytes(b"not parquet at all")
    with pytest.raises(ValueError, match="Invalid Parquet trailer"):
        check_source(filesystem, "synthetic.parquet", 18, schema_path)


def test_raw_contract_matches_native_source_columns(schema_path):
    raw = json.loads(schema_path.read_text())
    native = json.loads(schema_path.with_name("ookla_tiles.json").read_text())
    native_types = {f["name"]: f["type"] for f in native}
    assert len(raw) == 11
    assert all(native_types[f["name"]] == f["type"] for f in raw)


def test_oversized_footer_is_rejected_before_allocation(schema_path):
    data = b"PAR1" + b"payload" + struct.pack("<I", 20 * 1024 * 1024) + b"PAR1"
    filesystem = MagicMock()
    stream = SizedBytes(data)
    filesystem.open_input_file.return_value = stream
    with pytest.raises(ValueError, match="oversized"):
        check_source(filesystem, "synthetic.parquet", len(data), schema_path)
    assert stream.reads == [(len(data) - 8, 8)]
