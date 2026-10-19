import json
import logging

import requests
from google.api_core.exceptions import GoogleAPIError
from google.auth.credentials import Credentials
from google.auth.exceptions import GoogleAuthError
from google.cloud import secretmanager

LOGGER = logging.getLogger(__name__)


def notify(credentials: Credentials, project: str, text: str) -> None:
    try:
        with secretmanager.SecretManagerServiceClient(credentials=credentials) as client:
            response = client.access_secret_version(
                request={"name": f"projects/{project}/secrets/alert-telegram/versions/latest"},
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
