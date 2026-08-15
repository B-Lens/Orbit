"""Execution-environment configuration for Orbit.

Trading environments are deliberately explicit. The global default is always
``paper``; testnet and live promotion are authorized independently per asset.
"""

from dataclasses import dataclass, field
from enum import Enum
import os


class ExecutionMode(str, Enum):
    PAPER = "paper"
    TESTNET = "testnet"
    LIVE = "live"


FUTURES_TESTNET_URL = "https://demo-fapi.binance.com"


@dataclass(frozen=True)
class ExecutionSettings:
    asset_modes: dict[str, ExecutionMode] = field(default_factory=dict)
    live_assets: frozenset[str] = frozenset()

    @property
    def can_submit_orders(self) -> bool:
        return any(
            mode in (ExecutionMode.TESTNET, ExecutionMode.LIVE)
            for mode in self.asset_modes.values()
        )

    def mode_for(self, symbol: str) -> ExecutionMode:
        """Return the independently configured execution mode for ``symbol``."""
        return self.asset_modes.get(symbol.upper(), ExecutionMode.PAPER)

    def can_submit_orders_for(self, symbol: str) -> bool:
        return self.mode_for(symbol) in (ExecutionMode.TESTNET, ExecutionMode.LIVE)

    @property
    def active_modes(self) -> frozenset[ExecutionMode]:
        return frozenset({ExecutionMode.PAPER, *self.asset_modes.values()})

    @classmethod
    def from_env(cls) -> "ExecutionSettings":
        raw_asset_modes = os.getenv("ORBIT_ASSET_EXECUTION_MODES", "")
        asset_modes: dict[str, ExecutionMode] = {}
        for entry in filter(None, (item.strip() for item in raw_asset_modes.split(","))):
            try:
                symbol, raw_asset_mode = (part.strip() for part in entry.split(":", 1))
                asset_mode = ExecutionMode(raw_asset_mode.lower())
            except (ValueError, AttributeError) as exc:
                raise ValueError(
                    "ORBIT_ASSET_EXECUTION_MODES must use SYMBOL:paper|testnet|live entries"
                ) from exc
            symbol = symbol.upper()
            if not symbol or symbol in asset_modes:
                raise ValueError("Each asset execution mode must name a unique symbol")
            asset_modes[symbol] = asset_mode

        live_assets = frozenset(
            symbol.strip().upper()
            for symbol in os.getenv("ORBIT_LIVE_ASSETS", "").split(",")
            if symbol.strip()
        )
        configured_live_assets = {
            symbol for symbol, asset_mode in asset_modes.items()
            if asset_mode is ExecutionMode.LIVE
        }
        if configured_live_assets != live_assets:
            raise RuntimeError(
                "ORBIT_LIVE_ASSETS must exactly match assets configured for live trading"
            )

        active_modes = set(asset_modes.values())
        if ExecutionMode.TESTNET in active_modes:
            testnet_key = os.getenv("BINANCE_TESTNET_API_KEY")
            testnet_secret = os.getenv("BINANCE_TESTNET_SECRET_KEY")
            if not (testnet_key and testnet_secret):
                raise RuntimeError(
                    "Binance testnet credentials are required for testnet assets"
                )
        if ExecutionMode.LIVE in active_modes and not (
            os.getenv("BINANCE_API_KEY") and os.getenv("BINANCE_SECRET_KEY")
        ):
            raise RuntimeError("Binance live credentials are required for live assets")

        return cls(asset_modes, live_assets)
