import os
import json
import requests
import logging

from orbit.utils.utils import get_indian_time
from orbit.core.discord_webhook_registry import DiscordWebhookRegistry

logger = logging.getLogger("Orbit")

# Module-level singleton registry — one place to swap in a secret manager.
_webhook_registry = DiscordWebhookRegistry()


class DiscordManager:
    """
    Manages sending notifications to Discord webhooks.

    Webhook URLs are resolved exclusively through :class:`DiscordWebhookRegistry`,
    keeping raw URLs out of environment variables and away from the rest of the
    codebase.
    """

    EMBED_COLOR = 16711680  # Red
    MAX_CONTENT = 2000
    MAX_DESCRIPTION = 4096
    MAX_FIELD_VALUE = 1024
    MAX_FIELDS = 25
    MAX_TITLE = 256

    def __init__(self) -> None:
        self._registry = _webhook_registry

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def process_fields(fields: dict) -> list:
        """
        Convert a plain dict into the list-of-dicts format Discord expects.

        Args:
            fields: Mapping of field name → value.

        Returns:
            List of Discord embed field dicts.
        """
        return [
            {"name": key, "value": str(value), "inline": True}
            for key, value in fields.items()
        ]

    @staticmethod
    def get_current_time() -> str:
        """Return the current Indian Standard Time as a formatted string."""
        time = get_indian_time()
        return time.now().strftime("%d-%m-%y %H:%M")

    @staticmethod
    def _truncate(text: str, limit: int):
        if not text:
            return text, False
        if len(text) > limit:
            return text[:limit], True
        return text, False

    # ------------------------------------------------------------------
    # Core dispatch
    # ------------------------------------------------------------------

    def send_to_webhook(
        self,
        key: str,
        data: str,
        description: str,
        fields: dict = None,
        **kwargs,
    ):
        """
        Build a Discord embed payload and POST it to the webhook for *key*.

        Args:
            key:         Logical chat name resolved via :class:`DiscordWebhookRegistry`.
            data:        Top-level ``content`` string (appears above the embed).
            description: Embed description text.
            fields:      Optional dict of name → value pairs rendered as embed fields.
            **kwargs:    ``file_path`` – local path to attach as a file upload.

        Returns:
            HTTP status code returned by Discord, or ``None`` on error.
        """
        try:
            url = self._registry.get_url(key)

            if data is None:
                data = ""

            truncated = False

            data, was_cut = self._truncate(data, self.MAX_CONTENT)
            truncated = truncated or was_cut

            description, was_cut = self._truncate(description, self.MAX_DESCRIPTION)
            truncated = truncated or was_cut

            processed_fields = []

            if fields:
                for k, v in fields.items():
                    name, cut_name = self._truncate(str(k), self.MAX_TITLE)
                    value, cut_val = self._truncate(str(v), self.MAX_FIELD_VALUE)

                    if cut_name or cut_val:
                        truncated = True

                    processed_fields.append(
                        {"name": name, "value": value, "inline": True}
                    )

            if len(processed_fields) > self.MAX_FIELDS:
                processed_fields = processed_fields[: self.MAX_FIELDS]
                truncated = True

            if truncated:
                processed_fields.append(
                    {
                        "name": "⚠ Warning",
                        "value": "Message was truncated due to Discord size limits.",
                        "inline": False,
                    }
                )

            embed = {
                "description": description,
                "color": self.EMBED_COLOR,
                "fields": processed_fields,
            }

            if description or processed_fields:
                embed["title"] = self.get_current_time()[: self.MAX_TITLE]

            payload = {"content": data, "embeds": [embed]}

            file_path = kwargs.get("file_path")

            if file_path:
                with open(file_path, "rb") as f:
                    files = {"file": (os.path.basename(file_path), f)}
                    multipart_data = {"payload_json": json.dumps(payload)}
                    response = requests.post(url, data=multipart_data, files=files)
            else:
                response = requests.post(url, json=payload)

            if response.status_code != 204:
                logger.error(
                    "Failed webhook | Status: %s | Response: %s",
                    response.status_code,
                    response.text,
                )

            return response.status_code

        except Exception as e:
            logger.exception("Error sending webhook '%s': %s", key, str(e))

    # ------------------------------------------------------------------
    # Named webhook helpers
    # ------------------------------------------------------------------

    def send_websocket_logs(self, data: str, description: str, fields: dict = None):
        return self.send_to_webhook("websocket", data, description, fields)

    def send_alerts(self, data: str, description: str, fields: dict = None):
        return self.send_to_webhook("alerts", data, description, fields)

    def send_market_sentiment(self, data: str, description: str, fields: dict = None):
        return self.send_to_webhook("market_sentiment", data, description, fields)

    def send_prediction(
        self,
        data: str,
        description: str,
        fields: dict = None,
        file_path: str = None,
    ):
        return self.send_to_webhook(
            "ai_predictions", data, description, fields, file_path=file_path
        )

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

    def send_chart_to_webhook(
        self,
        file_path: str,
        data: str,
        description: str,
        fields: dict = None,
    ):
        return self.send_to_webhook(
            "chart_signal", data, description, fields, file_path=file_path
        )
