from unittest.mock import MagicMock
from urllib.parse import quote
from xml.sax.saxutils import escape

import pytest
import requests

from ingest.config import SourceConfig
from ingest.discover import discover, http_session, opaque_etag

NS = 'xmlns="http://s3.amazonaws.com/doc/2006-03-01/"'
SOURCE = SourceConfig()


def page(keys, truncated="false", token=""):
    entries = "".join(
        f"<Contents><Key>{escape(key)}</Key><ETag>&quot;opaque-7&quot;</ETag>"
        "<Size>123</Size></Contents>"
        for key in keys
    )
    response = MagicMock()
    response.__enter__.return_value = response
    response.content = (
        f"<ListBucketResult {NS}>{entries}<IsTruncated>{truncated}</IsTruncated>"
        f"<NextContinuationToken>{escape(token)}</NextContinuationToken></ListBucketResult>"
    ).encode()
    return response


def key(year=2023, quarter=3, layer="mobile"):
    return f"parquet/performance/type={layer}/year={year}/quarter={quarter}/test.parquet"


def test_pagination_minimum_and_hive_path():
    session = MagicMock()
    session.get.side_effect = [
        page([key(2023, 2), key(2023, 4)], "true", "opaque+/=&"),
        page([key(), "parquet/performance/type=mobile/readme.txt"]),
    ]
    result = discover(session, SOURCE, "mobile", (2023, 3))
    assert [s.quarter for s in result] == [3, 4]
    assert result[0].etag == "opaque-7"
    assert result[0].destination == "ookla/type=mobile/year=2023/quarter=3/test.parquet"
    assert session.get.call_args_list[1].kwargs["params"]["continuation-token"] == "opaque+/=&"
    assert all(call.kwargs["timeout"] == (10, 120) for call in session.get.call_args_list)
    assert all("Authorization" not in call.kwargs for call in session.get.call_args_list)
    listing_url = "https://ookla-open-data.s3.us-west-2.amazonaws.com/"
    assert all(call.args[0] == listing_url for call in session.get.call_args_list)
    assert result[0].url == listing_url + quote(key(), safe="/")


def test_endpoint_override_used_for_listing_and_objects():
    source = SourceConfig(endpoint_override="https://s3.example.test/")
    session = MagicMock()
    session.get.return_value = page([key()])
    result = discover(session, source, "mobile", (2023, 3))
    assert session.get.call_args.args[0] == "https://s3.example.test/"
    assert result[0].url == "https://s3.example.test/" + quote(key(), safe="/")
    assert source.arrow_path(key()) == "ookla-open-data/" + key()


def test_object_url_is_percent_encoded():
    assert SourceConfig().object_url("a b/c+d.parquet").endswith("/a%20b/c%2Bd.parquet")


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://s3.example.test",
        "s3.example.test",
        "https://user:pw@s3.example.test",
        "https://s3.example.test/path",
        "https://s3.example.test/?q=1",
        "https://",
    ],
)
def test_endpoint_override_must_be_plain_https(endpoint, monkeypatch):
    monkeypatch.setenv("SOURCE_ENDPOINT", endpoint)
    monkeypatch.delenv("S3_REGION", raising=False)
    with pytest.raises(ValueError, match="SOURCE_ENDPOINT"):
        SourceConfig.from_env()


@pytest.mark.parametrize(
    ("variable", "value"),
    [("SOURCE_BUCKET", "evil.com/x"), ("SOURCE_BUCKET", "UPPER"), ("SOURCE_REGION", "us-west-2.x")],
)
def test_bucket_and_region_are_validated(variable, value, monkeypatch):
    monkeypatch.delenv("S3_REGION", raising=False)
    monkeypatch.delenv("SOURCE_ENDPOINT", raising=False)
    monkeypatch.setenv(variable, value)
    with pytest.raises(ValueError, match=variable):
        SourceConfig.from_env()


@pytest.mark.parametrize("token", ["", "repeated"])
def test_missing_or_repeated_continuation_token(token):
    session = MagicMock()
    session.get.return_value = page([], "true", token)
    with pytest.raises(ValueError, match="continuation token"):
        discover(session, SOURCE, "mobile", (2023, 3))


def test_malformed_xml_and_http_errors_propagate():
    session = MagicMock()
    bad = page([])
    bad.raise_for_status.side_effect = requests.HTTPError("403")
    session.get.return_value = bad
    with pytest.raises(requests.HTTPError):
        discover(session, SOURCE, "mobile", (2023, 3))


def test_listing_without_truncation_flag_rejected():
    session = MagicMock()
    session.get.return_value = page([], "")
    with pytest.raises(ValueError, match="IsTruncated"):
        discover(session, SOURCE, "mobile", (2023, 3))


@pytest.mark.parametrize("value", ['"abc-12"', "abc-12", "NotAnMD5"])
def test_etag_is_opaque(value):
    assert opaque_etag(value) == value.strip('"')


def test_http_retry_configuration():
    with http_session() as session:
        retry = session.get_adapter("https://").max_retries
        assert retry.total == 3
        assert retry.backoff_factor > 0
        assert 503 in retry.status_forcelist
        assert "GET" in retry.allowed_methods
