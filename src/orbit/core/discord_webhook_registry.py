import os
from dotenv import load_dotenv

load_dotenv()


class DiscordWebhookRegistry:
    """
    Central registry for Discord webhook URLs.

    Webhooks are loaded once at import time from environment variables.
    All access is controlled through :meth:`get_url`, preventing direct
    exposure of raw webhook strings throughout the codebase.

    Future improvement: swap the ``_load`` method body to pull from a
    secret manager (AWS Secrets Manager, HashiCorp Vault, etc.) without
    touching any other file.
    """

    _WEBHOOK_ENV_KEYS: dict[str, str] = {
        "logs":                 "LOGS_WEBHOOK",
        "params":               "PARAMS_WEBHOOK",
        "active_trades":        "ACTIVE_TRADES_WEBHOOK",
        "signal":               "SIGNAL_WEBHOOK",
        "exception":            "EXCEPTION_WEBHOOK",
        "exception_params":     "EXCEPTION_PARAMS_WEBHOOK",
        "cooldown":             "COOLDOWN_WEBHOOK",
        "sl_update":            "SL_UPDATE_WEBHOOK",
        "true_alarm":           "TRUE_ALARM_WEBHOOK",
        "false_alarm":          "FALSE_ALARM_WEBHOOK",
        "active_trade_prices":  "ACTIVE_TRADE_PRICES_WEBHOOK",
        "ai_predictions":       "AI_PREDICTIONS_WEBHOOK",
        "average_alarm":        "AVERAGE_ALARM_WEBHOOK",
        "market_sentiment":     "MARKET_SENTIMENT_WEBHOOK",
        "alerts":               "ALERTS_WEBHOOK",
        "websocket":            "WEBSOCKET_WEBHOOK",
        "chart_signal":         "CHART_SIGNAL_WEBHOOK",
        "levels_webhook":       "LEVELS_WEBHOOK",
    }

    def __init__(self) -> None:
        self._registry: dict[str, str] = self._load()

    def _load(self) -> dict[str, str]:
        """
        Load webhook URLs from environment variables.

        Only keys whose environment variable is actually set are stored;
        missing variables are silently skipped so that partial deployments
        (e.g. staging) do not raise errors at startup.
        """
        registry: dict[str, str] = {}
        for chat_key, env_key in self._WEBHOOK_ENV_KEYS.items():
            value = os.getenv(env_key)
            if value:
                registry[chat_key] = value
        return registry

    def get_url(self, key: str) -> str:
        """
        Return the webhook URL for *key*.

        Args:
            key: Logical chat name (e.g. ``"logs"``, ``"signal"``).

        Returns:
            The webhook URL string.

        Raises:
            ValueError: If *key* is not registered or its URL is missing.
        """
        if key not in self._registry:
            raise ValueError(
                f"Discord webhook '{key}' is not registered. "
                f"Available keys: {sorted(self._registry.keys())}"
            )
        return self._registry[key]

    def registered_keys(self) -> list[str]:
        """Return a sorted list of all registered chat keys."""
        return sorted(self._registry.keys())

    def is_registered(self, key: str) -> bool:
        """Return ``True`` if *key* has a configured webhook URL."""
        return key in self._registry
