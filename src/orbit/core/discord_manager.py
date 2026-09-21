import os
import requests
import yaml

import logging
from orbit.utils.utils import get_indian_time
from dotenv import load_dotenv
from orbit.core.notification_feed import record_notification

load_dotenv()  # Load environment variables from .env file

logger = logging.getLogger("Orbit")

def _load_webhooks() -> dict:
    """
    Load webhook URLs from the YAML config file.

    Returns:
        dict: A dictionary mapping webhook keys to their URLs.

    Raises:
        FileNotFoundError: If the webhooks YAML file cannot be found.
        KeyError: If the YAML file does not contain a 'webhooks' key.
    """
    config_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "config", "webhooks.yaml"
    )
    config_path = os.path.abspath(config_path)

    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    return config["webhooks"]


class URLS:
    WEBHOOKS = _load_webhooks()

    @classmethod
    def get_url(cls, key: str) -> str:
        """
        Retrieve the webhook URL for the given key.

        Args:
            key (str): The key to identify the webhook.

        Returns:
            str: The webhook URL.

        Raises:
            ValueError: If the key does not exist in WEBHOOKS.
        """
        if key not in cls.WEBHOOKS:
            raise ValueError(f"Invalid webhook key: {key}")
        env_key = f"ORBIT_WEBHOOK_{key.upper()}"
        return os.getenv(env_key) or cls.WEBHOOKS[key]


class DiscordManager:
    """
    Manages sending notifications to Discord webhooks.
    """
    EMBED_COLOR = 16711680  # Red color constant
    MAX_CONTENT = 2000
    MAX_DESCRIPTION = 4096
    MAX_FIELD_VALUE = 1024
    MAX_FIELDS = 25
    MAX_TITLE = 256
    REQUEST_TIMEOUT_SECONDS = 10

    def __init__(self):
        pass

    @staticmethod
    def get_current_time() -> str:
        """
        Get the current time formatted as a string in Indian Standard Time.

        Returns:
            str: The formatted current time.
        """
        time = get_indian_time()
        return time.now().strftime("%d-%m-%y %H:%M")

    @staticmethod
    def _truncate(text: str, limit: int):
        if not text:
            return text, False
        if len(text) > limit:
            return text[:limit], True
        return text, False

    def send_to_webhook(self, key: str, data: str, description: str, fields: dict = None):
        if key not in ("alerts", "exception"):
            logger.debug("Discarding unsupported Discord notification for '%s'", key)
            return None

        try:
            url = URLS.get_url(key)
            if not url:
                logger.debug("Webhook '%s' is not configured; notification skipped", key)
                return None
            if data is None:
                data = ""

            truncated = False

            # 🔹 Truncate content
            data, was_cut = self._truncate(data, self.MAX_CONTENT)
            truncated = truncated or was_cut

            # 🔹 Truncate description
            description, was_cut = self._truncate(description, self.MAX_DESCRIPTION)
            truncated = truncated or was_cut

            processed_fields = []

            if fields:
                for k, v in fields.items():
                    name, cut_name = self._truncate(str(k), self.MAX_TITLE)
                    value, cut_val = self._truncate(str(v), self.MAX_FIELD_VALUE)

                    if cut_name or cut_val:
                        truncated = True

                    processed_fields.append({
                        "name": name,
                        "value": value,
                        "inline": True
                    })

            # 🔹 Respect max 25 fields
            if len(processed_fields) > self.MAX_FIELDS:
                truncated = True

            # 🔹 If anything was truncated, reserve a field for the warning
            if truncated:
                processed_fields = processed_fields[:self.MAX_FIELDS - 1]
                processed_fields.append({
                    "name": "⚠ Warning",
                    "value": "Message was truncated due to Discord size limits.",
                    "inline": False
                })

            embed = {
                "description": description,
                "color": self.EMBED_COLOR,
                "fields": processed_fields,
            }

            if description or processed_fields:
                embed["title"] = self.get_current_time()[:self.MAX_TITLE]

            payload = {
                "content": data,
                "embeds": [embed],
            }

            response = requests.post(
                url, json=payload, timeout=self.REQUEST_TIMEOUT_SECONDS
            )

            SUCCESS_CODES = {200, 204}

            if response.status_code not in SUCCESS_CODES:
                logger.error(
                    "Failed webhook | Status: %s | Response: %s | key: %s",
                    response.status_code,
                    response.text,
                    key,
                )
            else:
                record_notification(key, data, description, processed_fields)

            return response.status_code

        except Exception as e:
            logger.exception("Error sending webhook '%s': %s", key, str(e))

    # Webhook calls
    def send_alerts(self, data: str, description: str, fields: dict = None):
        return self.send_to_webhook("alerts", data, description, fields)

    def send_exception(self, data: str, description: str):
        return self.send_to_webhook("exception", data, description)
