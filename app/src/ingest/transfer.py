import logging
import time

import requests
from google.api_core import exceptions as gcp_errors
from google.cloud import storage

from ingest.discover import TIMEOUT, opaque_etag
from ingest.state import Decision

CHUNK_SIZE = 8 * 1024 * 1024
LOGGER = logging.getLogger(__name__)


class TransferSizeError(ValueError):
    pass


def transfer(session: requests.Session, bucket: storage.Bucket, decision: Decision) -> None:
    for attempt in range(3):
        try:
            _transfer_once(session, bucket, decision)
            return
        except (
            requests.ConnectionError,
            requests.Timeout,
            requests.exceptions.ChunkedEncodingError,
            gcp_errors.TooManyRequests,
            gcp_errors.ServerError,
            TransferSizeError,
        ):
            if attempt == 2:
                raise
            LOGGER.warning("Retrying transfer of %s (attempt %d)", decision.source.key, attempt + 2)
            time.sleep(2**attempt)


def _transfer_once(session: requests.Session, bucket: storage.Bucket, decision: Decision) -> None:
    source = decision.source
    headers = {"If-Match": f'"{source.etag}"', "Accept-Encoding": "identity"}
    with session.get(source.url, headers=headers, stream=True, timeout=TIMEOUT) as response:
        response.raise_for_status()
        if opaque_etag(response.headers.get("ETag", "")) != source.etag:
            raise ValueError(f"Source ETag changed: {source.key}; rerun sync.")
        if response.headers.get("Content-Encoding", "identity") != "identity":
            raise ValueError(f"Unexpected HTTP content encoding for {source.key}")
        content_length = int(response.headers.get("Content-Length", "-1"))
        if content_length != source.size:
            raise ValueError(f"Source Content-Length changed: {source.key}; rerun sync.")
        blob = bucket.blob(source.destination)
        blob.metadata = {"source_etag": source.etag, "source_size": str(source.size)}
        writer = blob.open(
            "wb",
            chunk_size=CHUNK_SIZE,
            if_generation_match=decision.generation,
            timeout=120,
        )
        try:
            transferred = 0
            for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                transferred += len(chunk)
                if transferred > content_length:
                    raise TransferSizeError(f"Source exceeds Content-Length: {source.key}")
                writer.write(chunk)
            if transferred != content_length:
                raise TransferSizeError(f"Truncated source stream: {source.key}")
            writer.close()
        except Exception:
            # Do not finalize a partial upload or replace an existing valid object.
            writer.terminate()
            raise
        blob.reload(timeout=60)
        if blob.size != content_length:
            raise TransferSizeError(f"Uploaded size does not match Content-Length: {source.key}")
