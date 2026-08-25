import os
import json
import requests
import yaml

import logging
from orbit.utils.utils import get_indian_time
from dotenv import load_dotenv

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

    def __init__(self):
        pass

    @staticmethod
    def process_fields(fields: dict) -> list:
        """
        Process a dictionary of fields into the format required by Discord.

        Args:
            fields (dict): A dictionary of field names and values.

        Returns:
            list: A list of dictionaries formatted for Discord embeds.
        """
        return [{"name": key, "value": str(value), "inline": True} for key, value in fields.items()]

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

    def send_to_webhook(self, key: str, data: str, description: str, fields: dict = None, **kwargs):
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

            file_path = kwargs.get("file_path")

            if file_path:
                with open(file_path, 'rb') as f:
                    files = {'file': (os.path.basename(file_path), f)}
                    multipart_data = {'payload_json': json.dumps(payload)}
                    response = requests.post(url, data=multipart_data, files=files)
            else:
                response = requests.post(url, json=payload)

            SUCCESS_CODES = {200, 204}

            if response.status_code not in SUCCESS_CODES:
                # For active_trade_prices and websocket webhooks, log a warning instead of an error
                if key in ("active_trade_prices", "websocket", "active_trades"):
                    logger.warning(
                        f"Failed webhook | Status: {response.status_code} | Response: {response.text} | key: {key}"
                    )
                else:
                    logger.exception(
                        f"Failed webhook | Status: {response.status_code} | Response: {response.text} | key: {key}"
                    )

            return response.status_code

        except Exception as e:
            # For active_trade_prices and websocket webhooks, log a warning instead of an error
            if key in ("active_trade_prices", "websocket"):
                logger.warning("Error sending webhook '%s': %s", key, str(e))
            else:
                logger.exception("Error sending webhook '%s': %s", key, str(e))

    # Webhook calls
    def send_websocket_logs(self, data: str, description: str, fields: dict = None):
        return self.send_to_webhook("websocket", data, description, fields)

    def send_alerts(self, data: str, description: str, fields: dict = None):
        return self.send_to_webhook("alerts", data, description, fields)

    def send_market_sentiment(self, data: str, description: str, fields: dict = None):
        return self.send_to_webhook("market_sentiment", data, description, fields)

    def send_prediction(self, data: str, description: str, fields: dict = None, file_path: str = None):
        return self.send_to_webhook("ai_predictions", data, description, fields, file_path=file_path)

    def send_active_trade_prices(self, data: str, description: str, fields: dict = None):
        return self.send_to_webhook("active_trade_prices", data, description, fields)

    def send_true_alarm(self, data: str, description: str, fields: dict = None):
        return self.send_to_webhook("true_alarm", data, description, fields)

    def send_average_alarm(self, data: str, description: str, fields: dict = None):
        return self.send_to_webhook("average_alarm", data, description, fields)

    def send_false_alarm(self, data: str, description: str, fields: dict = None):
        return self.send_to_webhook("false_alarm", data, description, fields)

    def send_parameters(self, data: str, description: str, fields: dict = None):
        return self.send_to_webhook("params", data, description, fields)

    def send_exception(self, data: str, description: str):
        return self.send_to_webhook("exception", data, description)

    def send_exception_params_debug(self, data: str, description: str, fields: dict = None):
        return self.send_to_webhook("exception_params", data, description, fields)

    def send_logs(self, data: str, description: str, fields: dict = None):
        return self.send_to_webhook("logs", data, description, fields)

    def send_active_trades_info(self, data: str, description: str, fields: dict = None):
        return self.send_to_webhook("active_trades", data, description, fields)

    def send_signal_updates(self, data: str, description: str, fields: dict = None):
        return self.send_to_webhook("signal", data, description, fields)

    def send_sl_update_notifier(self, data: str, description: str, fields: dict = None):
        return self.send_to_webhook("sl_update", data, description, fields)

    def send_cooldown_update(self, data: str, description: str, fields: dict = None):
        return self.send_to_webhook("cooldown", data, description, fields)

    def send_levels_info(self, data: str, description: str, fields: dict = None):
        return self.send_to_webhook("levels_webhook", data, description, fields)

    def send_chart_to_webhook(self, file_path: str, data: str, description: str, fields: dict = None):
        return self.send_to_webhook("chart_signal", data, description, fields, file_path=file_path)
