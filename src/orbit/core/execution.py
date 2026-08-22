"""Execution-environment configuration for Orbit.

Trading environments are deliberately explicit. Each asset uses either Binance
Futures Testnet or live execution; missing and invalid modes are rejected.
"""

from dataclasses import dataclass, field
from enum import Enum
import os
from pathlib import Path

import yaml


class ExecutionMode(str, Enum):
    TESTNET = "testnet"
    LIVE = "live"


FUTURES_TESTNET_URL = "https://demo-fapi.binance.com"
DEFAULT_STRATEGY_CONFIG = Path(__file__).parents[3] / "config" / "strategies.yaml"


@dataclass(frozen=True)
class ExecutionSettings:
    asset_modes: dict[str, ExecutionMode] = field(default_factory=dict)

    @property
    def can_submit_orders(self) -> bool:
        return any(
            mode in (ExecutionMode.TESTNET, ExecutionMode.LIVE)
            for mode in self.asset_modes.values()
        )

    def mode_for(self, symbol: str) -> ExecutionMode:
        """Return the independently configured execution mode for ``symbol``."""
        try:
            return self.asset_modes[symbol.upper()]
        except KeyError as exc:
            raise ValueError(f"No execution mode configured for {symbol.upper()}") from exc

    def can_submit_orders_for(self, symbol: str) -> bool:
        return self.mode_for(symbol) in (ExecutionMode.TESTNET, ExecutionMode.LIVE)

    @property
    def active_modes(self) -> frozenset[ExecutionMode]:
        return frozenset(self.asset_modes.values())

    @classmethod
    def from_config(
        cls, strategy_config: Path | str = DEFAULT_STRATEGY_CONFIG
    ) -> "ExecutionSettings":
        """Load per-symbol order modes from ``config/strategies.yaml``.

        Every asset must explicitly select Testnet or live execution.
        """
        path = Path(strategy_config)
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise RuntimeError(f"Could not load strategy configuration: {path}") from exc

        strategies = document.get("strategies")
        if not isinstance(strategies, dict) or not strategies:
            raise ValueError(
                "Strategy configuration must define a non-empty strategies map"
            )

        asset_modes: dict[str, ExecutionMode] = {}
        for raw_symbol, item in strategies.items():
            symbol = str(raw_symbol).strip().upper()
            if not symbol or not isinstance(item, dict):
                raise ValueError("Each strategy must define a valid symbol and settings")
            try:
                mode = ExecutionMode(str(item["execution_mode"]).lower())
            except (KeyError, ValueError, AttributeError) as exc:
                raise ValueError(
                    f"Strategy {symbol} must define execution_mode: testnet or live"
                ) from exc
            if mode not in (ExecutionMode.TESTNET, ExecutionMode.LIVE):
                raise ValueError(
                    f"Strategy {symbol} uses {mode.value}; only testnet or live is allowed"
                )
            asset_modes[symbol] = mode

        monitored_assets = document.get("monitored_assets", {})
        if not isinstance(monitored_assets, dict):
            raise ValueError("monitored_assets must be a symbol-to-mode map")
        for raw_symbol, raw_mode in monitored_assets.items():
            symbol = str(raw_symbol).strip().upper()
            if not symbol or symbol in asset_modes:
                raise ValueError(f"Invalid or duplicate monitored asset: {symbol}")
            try:
                mode = ExecutionMode(str(raw_mode).lower())
            except (ValueError, AttributeError) as exc:
                raise ValueError(
                    f"Monitored asset {symbol} must define testnet or live mode"
                ) from exc
            if mode not in (ExecutionMode.TESTNET, ExecutionMode.LIVE):
                raise ValueError(
                    f"Monitored asset {symbol} uses {mode.value}; "
                    "only testnet or live is allowed"
                )
            asset_modes[symbol] = mode

        active_modes = frozenset(asset_modes.values())
        if ExecutionMode.TESTNET in active_modes and not (
            os.getenv("BINANCE_TESTNET_API_KEY")
            and os.getenv("BINANCE_TESTNET_SECRET_KEY")
        ):
            raise RuntimeError("Binance testnet credentials are required")
        if ExecutionMode.LIVE in active_modes and not (
            os.getenv("BINANCE_API_KEY") and os.getenv("BINANCE_SECRET_KEY")
        ):
            raise RuntimeError("Binance live credentials are required")

        return cls(asset_modes)
