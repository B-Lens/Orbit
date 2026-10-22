# Core Module Architecture

## Overview

The `src/orbit/core` module is the operational heart of the Orbit automated trading system. It coordinates market signal generation, Binance Futures order lifecycle management, real-time position monitoring, hourly sentiment scheduling, and structured exception reporting — all wired together through a multi-threaded, dependency-injected architecture.

---

## Module Map

| File | Class(es) | Responsibility |
|---|---|---|
| `main.py` | `BinanceAutomation` | Top-level controller; spawns and monitors all daemon threads |
| `signal_analyzer.py` | `SignalAnalyzer` | Fetches OHLCV data, runs strategy, filters signals, yields trade dicts |
| `trade_checker.py` | `TradeChecker` | Monitors open positions; manages SL/TP lifecycle via Redis + WebSocket |
| `order_manager.py` | `OrderManager` | Places, modifies, and cancels Binance Futures orders |
| `authentication_manager.py` | `AuthenticationManager` | Builds and exposes Binance Spot + Futures API clients |
| `mongo_handler.py` | `MongoHandler` | OHLCV candle storage/retrieval; contradict and simulation records |
| `sentimen_cron.py` | `Croner` | Hourly sentiment-analysis scheduler; caches result in Redis |
| `contradict_simulator.py` | `ContradictSimulator` | Simulates skipped trades in background threads; persists outcomes |
| `exception_manager.py` | `ExceptionManager` | Centralised exception handling with Discord webhook reporting |
| `discord_manager.py` | `DiscordManager`, `URLS` | Sends structured embeds to named Discord webhooks |
| `plugins.py` | _(functions)_ | Swing-high/low detection; swing-based SL calculation |

---

## Class Inheritance Tree

