"""Execution-environment configuration for Orbit.

Trading environments are deliberately explicit.  The safe default is ``paper``;
live order submission additionally requires a separate acknowledgement variable.
"""

from dataclasses import dataclass
from enum import Enum
import os


class ExecutionMode(str, Enum):
    PAPER = "paper"
    TESTNET = "testnet"
    LIVE = "live"


FUTURES_TESTNET_URL = "https://demo-fapi.binance.com"


@dataclass(frozen=True)
class ExecutionSettings:
    mode: ExecutionMode
    api_key: str | None
    secret_key: str | None
    futures_base_url: str | None

    @property
    def can_submit_orders(self) -> bool:
        return self.mode in (ExecutionMode.TESTNET, ExecutionMode.LIVE)

    @classmethod
    def from_env(cls) -> "ExecutionSettings":
        raw_mode = os.getenv("ORBIT_EXECUTION_MODE", ExecutionMode.PAPER.value).lower()
        try:
            mode = ExecutionMode(raw_mode)
        except ValueError as exc:
            raise ValueError(
                "ORBIT_EXECUTION_MODE must be one of: paper, testnet, live"
            ) from exc

        if mode is ExecutionMode.TESTNET:
            api_key = os.getenv("BINANCE_TESTNET_API_KEY")
            secret_key = os.getenv("BINANCE_TESTNET_SECRET_KEY")
            base_url = os.getenv("BINANCE_FUTURES_TESTNET_URL", FUTURES_TESTNET_URL)
        else:
            # Keep the legacy misspelling as a temporary compatibility fallback.
            api_key = os.getenv("BINANCE_API_KEY") or os.getenv("BINANE_API_KEY")
            secret_key = os.getenv("BINANCE_SECRET_KEY") or os.getenv("SECRET_KEY")
            base_url = None

        if mode is ExecutionMode.LIVE and os.getenv("ORBIT_LIVE_TRADING_ACK") != "I_UNDERSTAND":
            raise RuntimeError(
                "Live trading is locked. Set ORBIT_LIVE_TRADING_ACK=I_UNDERSTAND explicitly."
            )
        if mode in (ExecutionMode.TESTNET, ExecutionMode.LIVE) and not (api_key and secret_key):
            raise RuntimeError(f"Binance credentials are required in {mode.value} mode")

        return cls(mode, api_key, secret_key, base_url)
