from unittest.mock import MagicMock
from xml.sax.saxutils import escape

import pytest
import requests

from ingest.discover import discover, http_session, opaque_etag

NS = 'xmlns="http://s3.amazonaws.com/doc/2006-03-01/"'


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
    result = discover(session, "mobile", (2023, 3))
    assert [s.quarter for s in result] == [3, 4]
    assert result[0].etag == "opaque-7"
    assert result[0].destination == "ookla/type=mobile/year=2023/quarter=3/test.parquet"
    assert session.get.call_args_list[1].kwargs["params"]["continuation-token"] == "opaque+/=&"
    assert all(call.kwargs["timeout"] == (10, 120) for call in session.get.call_args_list)
    assert all("Authorization" not in call.kwargs for call in session.get.call_args_list)


@pytest.mark.parametrize("token", ["", "repeated"])
def test_missing_or_repeated_continuation_token(token):
    session = MagicMock()
    session.get.return_value = page([], "true", token)
    with pytest.raises(ValueError, match="continuation token"):
        discover(session, "mobile", (2023, 3))


def test_malformed_xml_and_http_errors_propagate():
    session = MagicMock()
    bad = page([])
    bad.raise_for_status.side_effect = requests.HTTPError("403")
    session.get.return_value = bad
    with pytest.raises(requests.HTTPError):
        discover(session, "mobile", (2023, 3))


def test_listing_without_truncation_flag_rejected():
    session = MagicMock()
    session.get.return_value = page([], "")
    with pytest.raises(ValueError, match="IsTruncated"):
        discover(session, "mobile", (2023, 3))


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
