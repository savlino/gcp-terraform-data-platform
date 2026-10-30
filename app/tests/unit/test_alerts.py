import json
import logging
from unittest.mock import MagicMock

import pytest
import requests
from google.api_core.exceptions import PermissionDenied

from ingest import alerts


@pytest.fixture
def telegram(monkeypatch):
    monkeypatch.delenv("ALERT_SECRET", raising=False)
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
    assert session.post.call_args.kwargs["json"] == {
        "chat_id": "123",
        "text": "[project=dep-2610-dev]\nDQ=PASS rows=10",
    }
    assert session.post.call_args.kwargs["allow_redirects"] is False


@pytest.mark.parametrize("env", ["dev", "test", "prod"])
@pytest.mark.parametrize("text", ["Build DQ=PASS; rows=10", "Build FAILED; rule=mart_rebuild"])
def test_notification_identifies_project_for_success_and_failure(telegram, env, text):
    _, session, _ = telegram
    alerts.notify(MagicMock(), f"dep-2610-{env}", text)
    assert session.post.call_args.kwargs["json"]["text"] == f"[project=dep-2610-{env}]\n{text}"


def test_long_notification_keeps_project_within_length_limit(telegram):
    _, session, _ = telegram
    alerts.notify(MagicMock(), "dep-2610-prod", "x" * 5000)
    text = session.post.call_args.kwargs["json"]["text"]
    assert text.startswith("[project=dep-2610-prod]\n")
    assert len(text) == 4000


@pytest.mark.parametrize("secret", ["custom-alert", "projects/dep-2610-dev/secrets/custom-alert"])
def test_alert_secret_selected_by_environment(telegram, monkeypatch, secret):
    client, _, _ = telegram
    monkeypatch.setenv("ALERT_SECRET", secret)
    alerts.notify(MagicMock(), "dep-2610-dev", "Summary")
    assert client.access_secret_version.call_args.kwargs["request"]["name"] == (
        "projects/dep-2610-dev/secrets/custom-alert/versions/latest"
    )


@pytest.mark.parametrize("secret", ["", " ", "https://invalid", "alert/versions/latest"])
def test_invalid_alert_secret_is_safely_reported(telegram, monkeypatch, caplog, secret):
    client, session, _ = telegram
    monkeypatch.setenv("ALERT_SECRET", secret)
    with caplog.at_level(logging.WARNING):
        alerts.notify(MagicMock(), "dep-2610-dev", "Summary")
    client.access_secret_version.assert_not_called()
    session.post.assert_not_called()
    assert "sensitive details suppressed" in caplog.text


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
