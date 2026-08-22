"""Execution-environment configuration for Orbit.

Trading environments are deliberately explicit. The global default is always
``paper``; testnet and live promotion are authorized independently per asset.
"""

from dataclasses import dataclass, field
from enum import Enum
import os
from pathlib import Path

import yaml


class ExecutionMode(str, Enum):
    PAPER = "paper"
    TESTNET = "testnet"
    LIVE = "live"


FUTURES_TESTNET_URL = "https://demo-fapi.binance.com"
DEFAULT_STRATEGY_CONFIG = Path(__file__).parents[3] / "config" / "strategies.yaml"


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
    def from_config(
        cls, strategy_config: Path | str = DEFAULT_STRATEGY_CONFIG
    ) -> "ExecutionSettings":
        """Load per-symbol order modes from ``config/strategies.yaml``.

        The current rollout is Testnet-only. Any missing or non-Testnet mode is
        rejected at startup rather than silently falling back to another order
        environment.
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
                    f"Strategy {symbol} must define execution_mode: testnet"
                ) from exc
            if mode is not ExecutionMode.TESTNET:
                raise ValueError(
                    f"Strategy {symbol} uses {mode.value}; only testnet is allowed"
                )
            asset_modes[symbol] = mode

        if not (
            os.getenv("BINANCE_TESTNET_API_KEY")
            and os.getenv("BINANCE_TESTNET_SECRET_KEY")
        ):
            raise RuntimeError("Binance testnet credentials are required")

        return cls(asset_modes)
