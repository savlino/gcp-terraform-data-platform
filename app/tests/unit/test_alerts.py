import json
import logging
from unittest.mock import MagicMock

import pytest
import requests
from google.api_core.exceptions import PermissionDenied

from ingest import alerts


@pytest.fixture
def telegram(monkeypatch):
    client = MagicMock()
    client.__enter__.return_value = client
    client.access_secret_version.return_value.payload.data = json.dumps(
        {"bot_token": "123456:synthetic-secret-token", "chat_id": "123"}
    ).encode()
    constructor = MagicMock(return_value=client)
    monkeypatch.setattr(alerts.secretmanager, "SecretManagerServiceClient", constructor)
    session = MagicMock()
    session.__enter__.return_value = session
    response = session.post.return_value
    response.__enter__.return_value = response
    response.status_code = 200
    response.json.return_value = {"ok": True}
    monkeypatch.setattr(alerts.requests, "Session", lambda: session)
    return client, session, constructor


def test_telegram_success(telegram):
    client, session, constructor = telegram
    credentials = MagicMock()
    alerts.notify(credentials, "dep-2610-dev", "DQ=PASS rows=10")
    constructor.assert_called_once_with(credentials=credentials)
    assert client.access_secret_version.call_args.kwargs["request"]["name"].endswith(
        "/secrets/alert-telegram/versions/latest"
    )
    assert session.post.call_args.kwargs["json"] == {"chat_id": "123", "text": "DQ=PASS rows=10"}
    assert session.post.call_args.kwargs["allow_redirects"] is False


@pytest.mark.parametrize("failure", ["transport", "secret", "bad_json", "api_rejection"])
def test_alert_failure_never_logs_sensitive_exception_or_url(telegram, caplog, failure):
    client, session, _ = telegram
    leaked = "https://api.telegram.org/bot123456:synthetic-secret-token/sendMessage"
    if failure == "transport":
        session.post.side_effect = requests.ConnectionError(leaked)
    elif failure == "secret":
        client.access_secret_version.side_effect = PermissionDenied(leaked)
    elif failure == "bad_json":
        client.access_secret_version.return_value.payload.data = leaked.encode()
    else:
        session.post.return_value.status_code = 403
    with caplog.at_level(logging.WARNING):
        alerts.notify(MagicMock(), "dep-2610-dev", "Summary")
    assert "suppressed" in caplog.text
    assert "synthetic-secret-token" not in caplog.text
    assert "api.telegram.org" not in caplog.text
