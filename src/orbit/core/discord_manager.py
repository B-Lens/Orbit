import os
import sys
import json
import requests

import logging
from config.config import *
from orbit.utils.utils import *

logger = logging.getLogger("Orbit")

class URLS:
    WEBHOOKS = {
        "logs": os.getenv("LOGS_WEBHOOK"),
        "params": os.getenv("PARAMS_WEBHOOK"),
        "active_trades": os.getenv("ACTIVE_TRADES_WEBHOOK"),
        "signal": os.getenv("SIGNAL_WEBHOOK"),
        "exception": os.getenv("EXCEPTION_WEBHOOK"),
        "exception_params": os.getenv("EXCEPTION_PARAMS_WEBHOOK"),
        "cooldown": os.getenv("COOLDOWN_WEBHOOK"),
        "sl_update": os.getenv("SL_UPDATE_WEBHOOK"),
        "true_alarm": os.getenv("TRUE_ALARM_WEBHOOK"),
        "false_alarm": os.getenv("FALSE_ALARM_WEBHOOK"),
        "active_trade_prices": os.getenv("ACTIVE_TRADE_PRICES_WEBHOOK"),
        "ai_predictions": os.getenv("AI_PREDICTIONS_WEBHOOK"),
        "average_alarm": os.getenv("AVERAGE_ALARM_WEBHOOK"),
        "market_sentiment": os.getenv("MARKET_SENTIMENT_WEBHOOK"),
        "alerts": os.getenv("ALERTS_WEBHOOK"),
        "websocket": os.getenv("WEBSOCKET_WEBHOOK"),
        "chart_signal": os.getenv("CHART_SIGNAL_WEBHOOK"),
        "levels_webhook": os.getenv("LEVELS_WEBHOOK"),
    }

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
        return cls.WEBHOOKS[key]


class DiscordManager:
    """
    Manages sending notifications to Discord webhooks.
    """
    EMBED_COLOR = 16711680  # Red color constant

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

    def send_to_webhook(self, key: str, data: str, description: str, fields: dict = None, **kwargs) -> None:
        """
        Send a message to the specified Discord webhook.

        Args:
            key (str): The webhook key.
            data (str): The content of the message.
            description (str): The description for the embed.
            fields (dict, optional): Additional fields for the embed. Defaults to None.
        """
        try:
            url = URLS.get_url(key)
            if fields:
                fields = self.process_fields(fields)

            payload = {
                "content": data,
                "embeds": [
                    {
                        "title": self.get_current_time(),
                        "description": description,
                        "color": self.EMBED_COLOR,
                        "fields": fields,
                    }
                ],
            }

            file_path = kwargs.get("file_path")
            if file_path:
                with open(file_path, 'rb') as f:
                    files = {
                        'file': (os.path.basename(file_path), f)
                    }
                    # multipart/form-data requires payload_json as string
                    multipart_data = {
                        'payload_json': json.dumps(payload)
                    }
                    response = requests.post(url, data=multipart_data, files=files)
            else:
                response = requests.post(
                    url,
                    data=json.dumps(payload),
                    headers={"Content-Type": "application/json"},
                )
                status_code = response.status_code
                if status_code != 204:
                    logger.error(f"Failed to send to webhook  Status Code: {status_code},  Response Text: {response.text}")  
                    
            return status_code
        except Exception as e:
            logger.error("An error occurred while sending to webhook '%s': %s", key, str(e))

    # Webhook calls
    def send_websocket_logs(self, data: str, description: str, fields: dict = None):
        return self.send_to_webhook("websocket", data, description, fields)

    def send_alerts(self, data: str, description: str, fields: dict = None):
        return self.send_to_webhook("alerts", data, description, fields)

    def send_market_sentiment(self, data: str, description: str, fields: dict = None):
        return self.send_to_webhook("market_sentiment", data, description, fields)

    def send_prediction(self, data: str, description: str, fields: dict = None, file_path: str=None):
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
    
    def send_chart_to_webhook(self, file_path: str, data: str, description:str, fields: dict = None):
        return self.send_to_webhook("chart_signal", data, description, fields, file_path=file_path )
