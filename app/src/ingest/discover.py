import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LOGGER = logging.getLogger(__name__)
SOURCE_ROOT = "https://ookla-open-data.s3.amazonaws.com/"
TIMEOUT = (10, 120)
KEY_PATTERN = re.compile(
    r"parquet/performance/type=(mobile|fixed)/year=([0-9]{4})/"
    r"quarter=([1-4])/([^/]+\.parquet)"
)


@dataclass(frozen=True)
class SourceObject:
    key: str
    network_type: str
    year: int
    quarter: int
    filename: str
    etag: str
    size: int

    @property
    def destination(self) -> str:
        return (
            f"ookla/type={self.network_type}/year={self.year}/"
            f"quarter={self.quarter}/{self.filename}"
        )

    @property
    def url(self) -> str:
        return SOURCE_ROOT + quote(self.key, safe="/")


def opaque_etag(value: str) -> str:
    """Remove HTTP quoting only; multipart ETags are not content hashes."""
    return value.removeprefix('"').removesuffix('"')


def http_session() -> requests.Session:
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def discover(
    session: requests.Session, network_type: str, min_quarter: tuple[int, int]
) -> list[SourceObject]:
    params = {"list-type": "2", "prefix": f"parquet/performance/type={network_type}/"}
    found: dict[str, SourceObject] = {}
    tokens: set[str] = set()
    while True:
        with session.get(SOURCE_ROOT, params=params, timeout=TIMEOUT) as response:
            response.raise_for_status()
            root = ET.fromstring(response.content)
        for item in root.findall("{*}Contents"):
            key = item.findtext("{*}Key", "")
            match = KEY_PATTERN.fullmatch(key)
            if match is None:
                LOGGER.warning("Ignoring non-Parquet or unrecognized source key: %s", key)
                continue
            layer, year_text, quarter_text, filename = match.groups()
            year, quarter = int(year_text), int(quarter_text)
            if layer != network_type:
                raise ValueError(f"Unexpected source type in listing: {key}")
            if (year, quarter) < min_quarter:
                continue
            etag = opaque_etag(item.findtext("{*}ETag", ""))
            size = int(item.findtext("{*}Size", "-1"))
            if not etag or size <= 0:
                raise ValueError(f"Missing ETag or positive size for {key}")
            source = SourceObject(key, layer, year, quarter, filename, etag, size)
            if key in found and found[key] != source:
                raise ValueError(f"Source changed during pagination: {key}; rerun sync.")
            found[key] = source
        truncated = root.findtext("{*}IsTruncated", "")
        if truncated == "false":
            break
        if truncated != "true":
            raise ValueError("S3 listing is missing a valid IsTruncated flag.")
        token = root.findtext("{*}NextContinuationToken", "")
        if not token or token in tokens:
            raise ValueError("S3 listing returned a missing or repeated continuation token.")
        tokens.add(token)
        params["continuation-token"] = token
    return sorted(found.values(), key=lambda source: (source.year, source.quarter, source.key))
