from dataclasses import replace
from unittest.mock import MagicMock, PropertyMock

import pytest
import requests
from google.api_core.exceptions import Forbidden, PreconditionFailed

from ingest.state import Decision, pending
from ingest.transfer import CHUNK_SIZE, TransferSizeError, transfer


def test_missing_unchanged_and_etag_changed(source):
    bucket = MagicMock()
    bucket.get_blob.return_value = None
    assert pending(bucket, source) == Decision(source, 0, "missing")
    blob = MagicMock(
        metadata={"source_etag": source.etag, "source_size": str(source.size)},
        size=source.size,
        generation=23,
    )
    bucket.get_blob.return_value = blob
    assert pending(bucket, source) is None
    changed = replace(source, etag="opaque-not-an-md5-19")
    assert pending(bucket, changed) == Decision(changed, 23, "etag_changed")


def test_missing_metadata_or_size_mismatch_redownloads(source):
    bucket = MagicMock()
    blob = MagicMock(metadata=None, generation=4, size=source.size)
    bucket.get_blob.return_value = blob
    assert pending(bucket, source).reason == "etag_changed"
    blob.metadata = {"source_etag": source.etag, "source_size": str(source.size)}
    blob.size = source.size + 1
    assert pending(bucket, source).reason == "size_changed"


def test_permission_error_is_not_treated_as_missing(source):
    bucket = MagicMock()
    bucket.get_blob.side_effect = Forbidden("Denied")
    with pytest.raises(Forbidden):
        pending(bucket, source)


def prepare(source, response):
    session = MagicMock()
    session.get.return_value = response
    response.headers = {"ETag": f'"{source.etag}"', "Content-Length": str(source.size)}
    response.iter_content.return_value = iter([b"a" * 5, b"b" * 7])
    bucket = MagicMock()
    bucket.blob.return_value.size = source.size
    writer = bucket.blob.return_value.open.return_value
    return session, bucket, writer


def test_streams_bounded_chunks_metadata_and_generation(source, response):
    session, bucket, writer = prepare(source, response)
    type(response).content = PropertyMock(side_effect=AssertionError("Whole-body access forbidden"))
    transfer(session, bucket, Decision(source, 42, "etag_changed"))
    assert [c.args[0] for c in writer.write.call_args_list] == [b"a" * 5, b"b" * 7]
    writer.close.assert_called_once()
    writer.terminate.assert_not_called()
    blob = bucket.blob.return_value
    assert blob.metadata == {"source_etag": source.etag, "source_size": "12"}
    assert blob.open.call_args.kwargs["if_generation_match"] == 42
    assert blob.open.call_args.kwargs["chunk_size"] == CHUNK_SIZE
    response.iter_content.assert_called_once_with(chunk_size=CHUNK_SIZE)
    assert session.get.call_args.kwargs["stream"] is True
    assert session.get.call_args.kwargs["headers"]["If-Match"] == f'"{source.etag}"'


def test_overlong_stream_aborts_before_finalizing(source, response, monkeypatch):
    monkeypatch.setattr("ingest.transfer.time.sleep", lambda _: None)
    session, bucket, writer = prepare(source, response)
    response.iter_content.side_effect = lambda **kwargs: iter([b"a" * 13])
    with pytest.raises(TransferSizeError, match="exceeds Content-Length"):
        transfer(session, bucket, Decision(source, 0, "missing"))
    writer.write.assert_not_called()
    writer.close.assert_not_called()
    assert writer.terminate.call_count == 3


def test_truncated_stream_aborts_without_finalizing(source, response, monkeypatch):
    monkeypatch.setattr("ingest.transfer.time.sleep", lambda _: None)
    session, bucket, writer = prepare(source, response)
    response.iter_content.side_effect = lambda **kwargs: iter([b"short"])
    with pytest.raises(TransferSizeError, match="Truncated"):
        transfer(session, bucket, Decision(source, 0, "missing"))
    assert writer.terminate.call_count == 3
    writer.close.assert_not_called()


def test_midstream_timeout_retries_from_start(source, response, monkeypatch):
    sleep = MagicMock()
    monkeypatch.setattr("ingest.transfer.time.sleep", sleep)
    session, bucket, writer = prepare(source, response)
    response.iter_content.side_effect = [requests.Timeout("interrupted"), [b"a" * 12]]
    transfer(session, bucket, Decision(source, 0, "missing"))
    writer.terminate.assert_called_once()
    writer.close.assert_called_once()
    sleep.assert_called_once_with(1)


def test_source_etag_race_does_not_upload(source, response):
    session, bucket, writer = prepare(source, response)
    response.headers["ETag"] = '"different"'
    with pytest.raises(ValueError, match="ETag changed"):
        transfer(session, bucket, Decision(source, 0, "missing"))
    writer.write.assert_not_called()
    bucket.blob.assert_not_called()


def test_destination_race_is_not_overwritten(source, response):
    session, bucket, writer = prepare(source, response)
    writer.close.side_effect = PreconditionFailed("Concurrent write")
    with pytest.raises(PreconditionFailed):
        transfer(session, bucket, Decision(source, 0, "missing"))
    assert session.get.call_count == 1
    writer.terminate.assert_called_once()


def test_source_length_change_does_not_upload(source, response):
    session, bucket, writer = prepare(source, response)
    response.headers["Content-Length"] = "999"
    with pytest.raises(ValueError, match="Content-Length changed"):
        transfer(session, bucket, Decision(source, 0, "missing"))
    bucket.blob.assert_not_called()


def test_uploaded_size_mismatch_is_reported(source, response, monkeypatch):
    monkeypatch.setattr("ingest.transfer.time.sleep", lambda _: None)
    session, bucket, writer = prepare(source, response)
    response.iter_content.side_effect = lambda **kwargs: iter([b"a" * 12])
    bucket.blob.return_value.size = 13
    with pytest.raises(TransferSizeError, match="Uploaded size"):
        transfer(session, bucket, Decision(source, 0, "missing"))
