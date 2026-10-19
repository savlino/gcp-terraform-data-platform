from dataclasses import dataclass

from google.cloud import storage

from ingest.discover import SourceObject


@dataclass(frozen=True)
class Decision:
    source: SourceObject
    generation: int
    reason: str


def pending(bucket: storage.Bucket, source: SourceObject) -> Decision | None:
    blob = bucket.get_blob(source.destination, timeout=60)
    if blob is None:
        return Decision(source, 0, "missing")
    metadata = blob.metadata or {}
    if metadata.get("source_etag") != source.etag:
        reason = "etag_changed"
    elif blob.size != source.size or metadata.get("source_size") != str(source.size):
        reason = "size_changed"
    else:
        return None
    if blob.generation is None:
        raise ValueError(f"GCS returned no generation for {source.destination}")
    return Decision(source, int(blob.generation), reason)
