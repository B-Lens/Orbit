#!/usr/bin/env python3
"""Smoke-test Binance Futures Testnet order submission for every testnet asset."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Sequence

from binance.error import ClientError
from dotenv import load_dotenv

from orbit.core.execution import ExecutionMode
from orbit.core.order_manager import OrderManager


@dataclass(frozen=True)
class ProbeResult:
    symbol: str
    status: str
    detail: str


def _error_detail(error: Exception) -> str:
    if isinstance(error, ClientError):
        code = getattr(error, "error_code", "unknown")
        message = getattr(error, "error_message", str(error))
        return f"Binance error {code}: {message}"
    return f"{type(error).__name__}: {error}"


def check_symbol(
    manager: OrderManager, symbol: str, price_discount_percent: float
) -> ProbeResult:
    """Exercise the production limit-order path and cancel an accepted order."""
    try:
        order_history = manager.get_open_orders(symbol)
        regular_open_orders = [
            order
            for order in order_history
            if order.get("status") in {"NEW", "PARTIALLY_FILLED"}
        ]
        conditional_open_orders = manager.get_conditional_open_orders(symbol)
    except Exception as error:  # pylint: disable=broad-exception-caught
        return ProbeResult(
            symbol,
            "FAILED",
            f"open-order check failed: {_error_detail(error)}",
        )

    open_order_count = len(regular_open_orders) + len(conditional_open_orders)
    if open_order_count:
        return ProbeResult(
            symbol,
            "SKIPPED",
            f"{open_order_count} existing open order(s); no probe submitted",
        )

    try:
        current_price = manager.get_symbol_price(symbol)
        limit_price = current_price * (1.0 - price_discount_percent / 100.0)
        stop_loss = limit_price * 0.99
        target = limit_price * 1.02
        order, _, _ = manager.place_order(
            risk_management=manager.config["risk_management"],
            symbol=symbol,
            side="BUY",
            price=limit_price,
            sl=stop_loss,
            target=target,
            leverage=int(manager.config["FUTURE_LEVERAGE"]),
            ros=True,
        )
    except Exception as error:  # pylint: disable=broad-exception-caught
        return ProbeResult(
            symbol,
            "FAILED",
            f"submission failed: {_error_detail(error)}",
        )

    if not order or order.get("orderId") is None:
        return ProbeResult(
            symbol,
            "FAILED",
            "production place_order rejected the order; inspect Orbit logs for details",
        )

    order_id = order["orderId"]
    cancel_response = manager.cancel_order(symbol, int(order_id))
    if not cancel_response:
        return ProbeResult(
            symbol,
            "FAILED",
            f"order {order_id} was accepted but cancellation failed; cancel it manually",
        )
    return ProbeResult(symbol, "PASSED", f"order {order_id} accepted and cancelled")


def run_checks(
    manager: OrderManager,
    price_discount_percent: float,
    requested_symbols: Sequence[str] | None = None,
) -> list[ProbeResult]:
    """Run probes for requested or all configured testnet assets."""
    if not 0 < price_discount_percent < 100:
        raise ValueError("price discount must be greater than 0 and less than 100")
    testnet_symbols = sorted(
        symbol
        for symbol, mode in manager.execution_settings.asset_modes.items()
        if mode is ExecutionMode.TESTNET
    )
    if requested_symbols:
        requested = {symbol.strip().upper() for symbol in requested_symbols}
        unavailable = requested - set(testnet_symbols)
        if unavailable:
            raise ValueError(
                "Symbols are not configured for testnet: " + ", ".join(sorted(unavailable))
            )
        testnet_symbols = [symbol for symbol in testnet_symbols if symbol in requested]

    return [
        check_symbol(manager, symbol, price_discount_percent)
        for symbol in testnet_symbols
    ]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--discount-percent",
        type=float,
        default=10.0,
        help="limit BUY price below current price (default: 10)",
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        help="optional subset of configured testnet symbols",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    load_dotenv()
    try:
        manager = OrderManager()
        results = run_checks(manager, args.discount_percent, args.symbols)
    except Exception as error:  # pylint: disable=broad-exception-caught
        print(f"ERROR: {_error_detail(error)}", file=sys.stderr)
        return 2

    for result in results:
        print(f"{result.status:7} {result.symbol}: {result.detail}")
    passed = sum(result.status == "PASSED" for result in results)
    skipped = sum(result.status == "SKIPPED" for result in results)
    failed = sum(result.status == "FAILED" for result in results)
    print(f"\nSummary: {passed} passed, {skipped} skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
