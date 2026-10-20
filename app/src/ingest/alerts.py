import json
import logging
import os
import re

import requests
from google.api_core.exceptions import GoogleAPIError
from google.auth.credentials import Credentials
from google.auth.exceptions import GoogleAuthError
from google.cloud import secretmanager

LOGGER = logging.getLogger(__name__)


def notify(credentials: Credentials, project: str, text: str) -> None:
    try:
        secret = os.environ.get("ALERT_SECRET", "alert-telegram")
        if re.fullmatch(r"[A-Za-z0-9_-]+", secret):
            secret = f"projects/{project}/secrets/{secret}"
        elif not re.fullmatch(r"projects/[a-z0-9-]+/secrets/[A-Za-z0-9_-]+", secret):
            raise ValueError("ALERT_SECRET must be a secret ID or full resource name.")
        with secretmanager.SecretManagerServiceClient(credentials=credentials) as client:
            response = client.access_secret_version(
                request={"name": f"{secret}/versions/latest"},
                timeout=30,
            )
        value = json.loads(response.payload.data.decode("utf-8"))
        token = value["bot_token"]
        chat_id = value["chat_id"]
        if not isinstance(token, str) or not token or not isinstance(chat_id, (str, int)):
            raise ValueError("Invalid Telegram secret shape")
        # Never log the URL, response body or exception: each may contain the bot token.
        with requests.Session() as session:
            result = session.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text[:4000]},
                timeout=(10, 30),
                allow_redirects=False,
            )
            with result:
                if result.status_code != 200 or result.json().get("ok") is not True:
                    LOGGER.warning("Telegram notification rejected; details suppressed")
    except (
        GoogleAPIError,
        GoogleAuthError,
        requests.RequestException,
        ValueError,
        KeyError,
        TypeError,
        UnicodeError,
        AttributeError,
    ):
        LOGGER.warning("Telegram notification failed; sensitive details suppressed")
