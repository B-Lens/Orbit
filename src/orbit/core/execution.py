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
    mode: ExecutionMode
    api_key: str | None
    secret_key: str | None
    futures_base_url: str | None
    asset_modes: dict[str, ExecutionMode] = field(default_factory=dict)
    live_assets: frozenset[str] = frozenset()

    @property
    def can_submit_orders(self) -> bool:
        return any(
            mode in (ExecutionMode.TESTNET, ExecutionMode.LIVE)
            for mode in {self.mode, *self.asset_modes.values()}
        )

    def mode_for(self, symbol: str) -> ExecutionMode:
        """Return the independently configured execution mode for ``symbol``."""
        return self.asset_modes.get(symbol.upper(), self.mode)

    def can_submit_orders_for(self, symbol: str) -> bool:
        return self.mode_for(symbol) in (ExecutionMode.TESTNET, ExecutionMode.LIVE)

    @property
    def active_modes(self) -> frozenset[ExecutionMode]:
        return frozenset({self.mode, *self.asset_modes.values()})

    @classmethod
    def from_env(cls) -> "ExecutionSettings":
        raw_mode = os.getenv("ORBIT_EXECUTION_MODE", ExecutionMode.PAPER.value).lower()
        try:
            mode = ExecutionMode(raw_mode)
        except ValueError as exc:
            raise ValueError(
                "ORBIT_EXECUTION_MODE must be one of: paper, testnet, live"
            ) from exc
        if mode is not ExecutionMode.PAPER:
            raise RuntimeError(
                "ORBIT_EXECUTION_MODE is a paper-only default; configure testnet or "
                "live execution per asset with ORBIT_ASSET_EXECUTION_MODES"
            )

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

        active_modes = {mode, *asset_modes.values()}
        if ExecutionMode.TESTNET in active_modes:
            api_key = os.getenv("BINANCE_TESTNET_API_KEY")
            secret_key = os.getenv("BINANCE_TESTNET_SECRET_KEY")
            base_url = os.getenv("BINANCE_FUTURES_TESTNET_URL", FUTURES_TESTNET_URL)
        else:
            # Keep the legacy misspelling as a temporary compatibility fallback.
            api_key = os.getenv("BINANCE_API_KEY") or os.getenv("BINANE_API_KEY")
            secret_key = os.getenv("BINANCE_SECRET_KEY") or os.getenv("SECRET_KEY")
            base_url = None

        if ExecutionMode.TESTNET in active_modes and not (api_key and secret_key):
            raise RuntimeError("Binance testnet credentials are required for testnet assets")
        if ExecutionMode.LIVE in active_modes and not (
            os.getenv("BINANCE_API_KEY") and os.getenv("BINANCE_SECRET_KEY")
        ):
            raise RuntimeError("Binance live credentials are required for live assets")

        return cls(mode, api_key, secret_key, base_url, asset_modes, live_assets)
