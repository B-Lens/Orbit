"""
mongo_handler
=============

Provides :class:`MongoHandler`, responsible for MongoDB persistence of OHLCV
candles and the operational trade/accounting ledger.

The handler gracefully degrades when ``pymongo`` is not installed or the
MongoDB server is unreachable.

A pre-built :class:`pymongo.MongoClient` can be **injected** through the
constructor to share connections or simplify testing.
"""

import time
import locale
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

from orbit.core.exception_manager import ExceptionManager
from orbit.utils.utils import get_indian_time

try:  # pragma: no cover - dependency may be missing in test environment
    from pymongo import ASCENDING, MongoClient  # type: ignore
except Exception:  # pragma: no cover - handled gracefully if pymongo not installed
    MongoClient = None  # type: ignore
    ASCENDING = None  # type: ignore

logger = logging.getLogger("Orbit")

OHLCV_COLLECTION_NAME: str = "OHLCVData"
TESTNET_OHLCV_COLLECTION_NAME: str = "OHLCVDataTestnet"
TESTNET_FUTURES_API_URL: str = "https://demo-fapi.binance.com"


def _epoch_to_seconds(ts: int) -> int:
    """Normalise an epoch timestamp to **seconds**.

    Handles nanosecond, microsecond, millisecond, and second granularity.
    """
    ts = int(ts)
    if ts > 1_000_000_000_000_000_000:  # ns
        return ts // 1_000_000_000
    if ts > 1_000_000_000_000_000:  # µs
        return ts // 1_000_000
    if ts > 1_000_000_000_000:  # ms
        return ts // 1_000
    return ts


class MongoHandler(ExceptionManager):
    """Storage and retrieval of OHLCV candle data in MongoDB.

    Args:
        uri: MongoDB connection string.
        db_name: Target database name.
        mongo_client: An optional pre-built :class:`pymongo.MongoClient`.
            When provided, *uri* is ignored and this client is used directly.
        read_only: Bind collections without creating or migrating indexes. Used
            by read-only API consumers that must not mutate MongoDB metadata.
    """

    def __init__(
        self,
        uri: Optional[str] = None,
        db_name: str = "orbit",
        mongo_client: Any = None,
        read_only: bool = False,
    ) -> None:
        super().__init__()
        self.read_only = read_only

        if MongoClient is None:
            logger.warning("pymongo is not installed; MongoDB features are disabled.")
            self.collection = None
            return

        try:
            uri = uri or os.getenv("MONGODB_URI", "mongodb://localhost:27017")
            self._mongo_client = mongo_client or MongoClient(
                uri, serverSelectionTimeoutMS=1000
            )
            self.db = self._mongo_client[db_name]
            self.collection = self.db[OHLCV_COLLECTION_NAME]
            self.testnet_collection = self.db[TESTNET_OHLCV_COLLECTION_NAME]
            self.decision_collection = self.db["trade_decisions"]
            self.trade_lifecycle_collection = self.db["trade_lifecycle"]
            self.trade_metrics_collection = self.db["trade_metrics"]
            self.income_collection = self.db["futures_income"]
            if read_only:
                return
            self.collection.create_index(
                [
                    ("symbol", ASCENDING),
                    ("interval", ASCENDING),
                    ("timestamp", ASCENDING),
                ],
                unique=True,
            )
            self.testnet_collection.create_index(
                [
                    ("symbol", ASCENDING),
                    ("interval", ASCENDING),
                    ("timestamp", ASCENDING),
                ],
                unique=True,
            )
            self.decision_collection.create_index("decision_id", unique=True)
            self.decision_collection.create_index(
                [("symbol", ASCENDING), ("timestamp", ASCENDING)]
            )
            self.trade_lifecycle_collection.create_index("trade_id", unique=True)
            self.trade_lifecycle_collection.create_index(
                [("execution_mode", ASCENDING), ("closed_at", ASCENDING)]
            )
            self.trade_metrics_collection.create_index("execution_mode", unique=True)
            legacy_income_index = "tranId_1_incomeType_1"
            if legacy_income_index in self.income_collection.index_information():
                self.income_collection.drop_index(legacy_income_index)
            self.income_collection.create_index(
                [
                    ("execution_mode", ASCENDING),
                    ("tranId", ASCENDING),
                    ("incomeType", ASCENDING),
                ],
                unique=True,
            )
            self.income_collection.create_index(
                [("execution_mode", ASCENDING), ("time", ASCENDING)]
            )
        except Exception as exc:
            logger.exception(f"Error initializing MongoDB: {exc}")
            self.collection = None

    # ------------------------------------------------------------------
    # Collection management
    # ------------------------------------------------------------------

    def clear_ohlcv_collection(self) -> None:
        """Delete **all** OHLCV documents from the collection."""
        if self.collection is None:
            logger.warning("Mongo collection not available.")
            return
        try:
            result = self.collection.delete_many({})
            logger.info(
                f"Deleted {result.deleted_count} documents from OHLCV collection."
            )
        except Exception as exc:
            self.handle_exception(exc, "Error clearing OHLCV collection")

    def clear_symbol_data(self, symbol: str, interval: str = "15m") -> None:
        """Delete OHLCV data for a single *symbol* / *interval* pair."""
        if self.collection is None:
            logger.warning("Mongo collection not available.")
            return
        try:
            result = self.collection.delete_many(
                {"symbol": symbol, "interval": interval}
            )
            logger.info(
                f"Deleted {result.deleted_count} records for {symbol} ({interval})."
            )
        except Exception as exc:
            self.handle_exception(exc, f"Error clearing data for {symbol}")

    def clear_old_data(self, days: int = 60) -> None:
        """Delete OHLCV documents whose ``expireAt`` is older than *days* ago."""
        if self.collection is None:
            logger.warning("Mongo collection not available.")
            return
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            result = self.collection.delete_many({"expireAt": {"$lt": cutoff}})
            logger.info(f"Deleted {result.deleted_count} old records.")
        except Exception as exc:
            self.handle_exception(exc, "Error clearing old data")

    # ------------------------------------------------------------------
    # Data retrieval
    # ------------------------------------------------------------------

    def _ohlcv_collection(self, execution_mode: str) -> Any:
        if execution_mode == "testnet":
            return getattr(self, "testnet_collection", None)
        return self.collection

    def get_mongo_historical_data(
        self, symbol: str, interval: str = "15m", execution_mode: str = "live"
    ) -> pd.DataFrame:
        """Retrieve the last 15 days of OHLCV data from MongoDB.

        Args:
            symbol: Trading pair (e.g. ``"BTCUSDT"``).
            interval: Candle interval string (default ``"15m"``).

        Returns:
            A :class:`~pandas.DataFrame` with OHLCV columns, or an empty
            frame when no data is available.
        """
        collection = self._ohlcv_collection(execution_mode)
        if collection is None:
            return pd.DataFrame()
        try:
            end_ts = int(time.time())
            start_ts = end_ts - 15 * 24 * 3600
            query = {
                "symbol": symbol,
                "interval": interval,
                "timestamp": {"$gte": start_ts},
            }
            cursor = collection.find(query, {"_id": 0}).sort(
                "timestamp", ASCENDING
            )
            items = list(cursor)
            if not items:
                logger.info(
                    f"No historical data found in MongoDB for {symbol} ({interval})."
                )
                return pd.DataFrame()
            return pd.DataFrame(items)
        except Exception as exc:
            self.handle_exception(exc, f"Error retrieving historical data for {symbol}")
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # Binance API helpers
    # ------------------------------------------------------------------

    def get_binance_klines(
        self,
        symbol: str,
        interval: str,
        start_time: int,
        end_time: int,
        execution_mode: str = "live",
    ) -> List[Any]:
        """Fetch kline (candlestick) data from the Binance REST API.

        Automatically retries up to 5 times on transient network errors and
        selects the US or global endpoint based on the system locale.

        Args:
            symbol: Trading pair.
            interval: Kline interval (e.g. ``"15m"``).
            start_time: Start timestamp in **milliseconds**.
            end_time: End timestamp in **milliseconds**.

        Returns:
            A list of raw kline arrays, or an empty list on failure.
        """
        if execution_mode == "testnet":
            base_url = os.getenv("BINANCE_FUTURES_TESTNET_URL", TESTNET_FUTURES_API_URL)
            url = f"{base_url.rstrip('/')}/fapi/v1/klines"
        else:
            lang, _ = locale.getdefaultlocale()
            url = (
                "https://api.binance.us/api/v3/klines"
                if lang == "en_US"
                else "https://api.binance.com/api/v3/klines"
            )
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": 1000,
            "startTime": start_time,
            "endTime": end_time,
        }

        retries, max_retries = 0, 5
        while retries < max_retries:
            try:
                response = requests.get(url, params=params, timeout=10)
                response.raise_for_status()
                return response.json()
            except requests.exceptions.HTTPError as e:
                if (
                    e.response.status_code >= 400
                    and e.response.status_code < 500
                    and e.response.status_code != 429
                ):
                    self.handle_exception(
                        Exception(
                            f"Client error {e.response.status_code} for {symbol}: {e.response.text}"
                        ),
                        f"Fetching {symbol}",
                    )
                    return []
                retries += 1
                logger.warning(
                    f"Retrying Binance API call for {symbol}. Attempt {retries}"
                )
                time.sleep(1)
            except requests.RequestException:
                retries += 1
                logger.warning(
                    f"Retrying Binance API call for {symbol}. Attempt {retries}"
                )
                time.sleep(1)

        self.handle_exception(
            Exception("Max retries reached for Binance API"), f"Fetching {symbol}"
        )
        return []

    def data_collector(
        self,
        symbol: str,
        interval: str = "15m",
        start_time: Optional[int] = None,
        execution_mode: str = "live",
    ) -> pd.DataFrame:
        """Collect OHLCV data from Binance for *symbol* and return a DataFrame.

        Args:
            symbol: Trading pair.
            interval: Candle interval.
            start_time: Epoch **milliseconds**; defaults to 90 days ago.

        Returns:
            A timestamp-indexed :class:`~pandas.DataFrame` with OHLCV columns.
        """
        end_time = int(datetime.now().timestamp() * 1000)
        start_time = start_time or (end_time - (90 * 24 * 60 * 60 * 1000))

        logger.info(
            f"Starting data collection for {symbol} from {datetime.utcfromtimestamp(start_time / 1000)}"
        )

        all_data = []
        current_time = start_time

        while current_time < end_time:
            data = self.get_binance_klines(
                symbol,
                interval,
                start_time=current_time,
                end_time=end_time,
                execution_mode=execution_mode,
            )
            if not data:
                logger.warning(
                    f"No data found for {symbol} at {datetime.utcfromtimestamp(current_time / 1000)}"
                )
                break
            all_data.extend(data)
            current_time = data[-1][0] + 1
            time.sleep(0.5)

        if not all_data:
            return pd.DataFrame()

        df = pd.DataFrame(
            all_data,
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "close_time",
                "quote_asset_volume",
                "num_trades",
                "taker_buy_base_asset_volume",
                "taker_buy_quote_asset_volume",
                "ignore",
            ],
        )

        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df = (
            df[["timestamp", "open", "high", "low", "close", "volume"]]
            .astype(
                {
                    "open": "float",
                    "high": "float",
                    "low": "float",
                    "close": "float",
                    "volume": "float",
                }
            )
            .set_index("timestamp")
        )

        return df

    def handle_mongo_data(
        self, symbol: str, execution_mode: str = "live"
    ) -> pd.DataFrame:
        """Load cached data from Mongo, fetch missing candles, persist, and return.

        This is the primary entry-point used by strategies and the trade
        checker to obtain up-to-date historical data.

        Args:
            symbol: Trading pair (e.g. ``"BTCUSDT"``).

        Returns:
            A timestamp-indexed :class:`~pandas.DataFrame` with OHLCV columns.
        """
        existing_data = self.get_mongo_historical_data(
            symbol, interval="15m", execution_mode=execution_mode
        )

        required_start_time = None
        if not existing_data.empty:
            # Older versions persisted a nanosecond index after dividing it by
            # 1,000, so Mongo may contain microseconds instead of seconds.
            # Normalise each value before asking pandas to create ns timestamps;
            # otherwise pandas interprets those legacy values as seconds and
            # raises OutOfBoundsDatetime.
            existing_data["timestamp"] = pd.to_datetime(
                existing_data["timestamp"].map(_epoch_to_seconds), unit="s"
            )
            existing_data = existing_data.set_index("timestamp")
            required_start_time = existing_data.index.max() + timedelta(minutes=15)
            logger.info(
                f"{symbol}: Existing data loaded, last timestamp (Opening candle time): {existing_data.index.max()}"
            )
        else:
            logger.info(f"{symbol}: No existing data found in MongoDB.")

        timestamp_ms = (
            int(required_start_time.timestamp() * 1000) if required_start_time else None
        )
        new_data = self.data_collector(
            symbol,
            interval="15m",
            start_time=timestamp_ms,
            execution_mode=execution_mode,
        )
        new_data = new_data.iloc[:-1]

        historical_data = (
            pd.concat([existing_data, new_data]).drop_duplicates()
            if not existing_data.empty
            else new_data
        )
        if historical_data.empty:
            logger.warning(f"No historical data found for {symbol}")
            return historical_data

        if not new_data.empty:
            historical_data_db = new_data.copy().reset_index()
            historical_data_db["timestamp"] = (
                historical_data_db["timestamp"].astype("int64") // 1_000_000_000
            )
            self.store_historical_data(
                symbol, historical_data_db, execution_mode=execution_mode
            )

        return historical_data

    # ------------------------------------------------------------------
    # Data persistence
    # ------------------------------------------------------------------

    def store_trade_decision(self, record: Dict[str, Any]) -> None:
        """Persist an immutable strategy decision, including rejected signals."""
        collection = getattr(self, "decision_collection", None)
        if collection is None:
            logger.warning("Mongo trade_decisions collection not available.")
            return
        try:
            collection.update_one(
                {"decision_id": record["decision_id"]},
                {"$setOnInsert": record},
                upsert=True,
            )
        except Exception as exc:
            self.handle_exception(exc, "Error storing trade decision")

    def append_decision_event(self, decision_id: str, event: Dict[str, Any]) -> None:
        """Append an execution transition without rewriting the original decision."""
        collection = getattr(self, "decision_collection", None)
        if collection is None or not decision_id:
            return
        event = {"timestamp": datetime.now(timezone.utc), **event}
        query: Dict[str, Any] = {"decision_id": decision_id}
        event_id = event.get("event_id")
        if event_id:
            query["execution_events.event_id"] = {"$ne": event_id}
        try:
            collection.update_one(query, {"$push": {"execution_events": event}})
        except Exception as exc:
            self.handle_exception(exc, "Error appending trade decision event")

    @staticmethod
    def _distribution(values: List[float]) -> Dict[str, float]:
        """Return average and linearly interpolated P95/P99 values."""
        ordered = sorted(float(value) for value in values)
        if not ordered:
            return {"average": 0.0, "p95": 0.0, "p99": 0.0, "count": 0}

        def percentile(fraction: float) -> float:
            position = (len(ordered) - 1) * fraction
            lower = int(position)
            upper = min(lower + 1, len(ordered) - 1)
            return ordered[lower] + (ordered[upper] - ordered[lower]) * (
                position - lower
            )

        return {
            "average": sum(ordered) / len(ordered),
            "p95": percentile(0.95),
            "p99": percentile(0.99),
            "count": len(ordered),
        }

    def store_trade_exit(self, record: Dict[str, Any]) -> bool:
        """Persist a completed trade and idempotently advance aggregate metrics."""
        lifecycle = getattr(self, "trade_lifecycle_collection", None)
        metrics = getattr(self, "trade_metrics_collection", None)
        if lifecycle is None or metrics is None:
            logger.warning("Mongo trade lifecycle collections not available.")
            return False
        try:
            lifecycle.update_one(
                {"trade_id": record["trade_id"]},
                {"$setOnInsert": {**record, "metrics_status": "pending"}},
                upsert=True,
            )
            stored = lifecycle.find_one(
                {"trade_id": record["trade_id"]}, {"metrics_status": 1}
            )
            if stored and stored.get("metrics_status") == "recorded":
                return True
            execution_mode = str(record["execution_mode"])
            completed_trades = list(
                lifecycle.find(
                    {"execution_mode": execution_mode},
                    {"duration_seconds": 1, "pnl": 1},
                )
            )
            duration_samples = [
                float(trade["duration_seconds"])
                for trade in completed_trades
                if "duration_seconds" in trade
            ]
            pnl_samples = [
                float(trade["pnl"]) for trade in completed_trades if "pnl" in trade
            ]
            sample_count = len(completed_trades)
            metrics.update_one(
                {"execution_mode": execution_mode},
                {
                    "$setOnInsert": {
                        "execution_mode": execution_mode,
                        "sample_count": 0,
                    }
                },
                upsert=True,
            )
            metrics.update_one(
                {
                    "execution_mode": execution_mode,
                    "$or": [
                        {"sample_count": {"$lte": sample_count}},
                        {"sample_count": {"$exists": False}},
                    ],
                },
                {
                    "$set": {
                        "execution_mode": execution_mode,
                        "updated_at": datetime.now(timezone.utc),
                        "sample_count": sample_count,
                        "active_trade_duration_seconds": self._distribution(
                            duration_samples
                        ),
                        "winning_trade_pnl": self._distribution(
                            [pnl for pnl in pnl_samples if pnl > 0]
                        ),
                        "losing_trade_pnl": self._distribution(
                            [pnl for pnl in pnl_samples if pnl < 0]
                        ),
                    },
                    "$unset": {
                        "computed_version": "",
                        "sample_version": "",
                        "recorded_trade_ids": "",
                        "duration_samples": "",
                        "winning_pnl_samples": "",
                        "losing_pnl_samples": "",
                    },
                },
            )
            lifecycle.update_one(
                {"trade_id": record["trade_id"]},
                {"$set": {"metrics_status": "recorded"}},
            )
            return True
        except Exception as exc:
            self.handle_exception(exc, "Error storing completed trade metrics")
            return False

    def store_trade_reconciliation_block(self, record: Dict[str, Any]) -> bool:
        """Persist a terminal audit row when an exit cannot be attributed safely."""
        lifecycle = getattr(self, "trade_lifecycle_collection", None)
        if lifecycle is None:
            return False
        try:
            lifecycle.update_one(
                {"trade_id": record["trade_id"]},
                {"$setOnInsert": record},
                upsert=True,
            )
            return True
        except Exception as exc:
            self.handle_exception(exc, "Error storing blocked trade reconciliation")
            return False

    def get_trade_decisions(
        self,
        start: datetime,
        end: datetime,
        execution_mode: Optional[str] = None,
        include_event_window: bool = False,
    ) -> List[Dict[str, Any]]:
        """Return decision-ledger rows for a half-open UTC reporting window."""
        collection = getattr(self, "decision_collection", None)
        if collection is None:
            return []
        window = {"$gte": start, "$lt": end}
        query: Dict[str, Any] = (
            {
                "$or": [
                    {"timestamp": window},
                    {"execution_events.timestamp": window},
                ]
            }
            if include_event_window
            else {"timestamp": window}
        )
        if execution_mode:
            query["execution_mode"] = execution_mode
        try:
            return list(collection.find(query, {"_id": 0}).sort("timestamp", ASCENDING))
        except Exception as exc:
            self.handle_exception(exc, "Error reading trade decisions")
            return []

    def get_recent_trade_decisions(self, limit: int = 25) -> List[Dict[str, Any]]:
        """Return the newest strategy decisions for the command-center UI."""
        collection = getattr(self, "decision_collection", None)
        if collection is None:
            return []
        try:
            return list(
                collection.find({}, {"_id": 0})
                .sort("timestamp", -1)
                .limit(max(0, limit))
            )
        except Exception as exc:
            if getattr(self, "read_only", False):
                logger.warning("Error reading recent trade decisions: %s", exc)
            else:
                self.handle_exception(exc, "Error reading recent trade decisions")
            return []

    def get_income_records(
        self, start_ms: int, end_ms: int, execution_mode: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Return immutable exchange-income rows for a half-open time window."""
        collection = getattr(self, "income_collection", None)
        if collection is None:
            return []
        try:
            query: Dict[str, Any] = {"time": {"$gte": start_ms, "$lt": end_ms}}
            if execution_mode:
                query["execution_mode"] = execution_mode
            return list(collection.find(query, {"_id": 0}).sort("time", ASCENDING))
        except Exception as exc:
            self.handle_exception(exc, "Error reading futures income records")
            return []

    def store_income_records(
        self, records: List[Dict[str, Any]], execution_mode: str = "unknown"
    ) -> None:
        """Upsert Binance income rows used for fee-aware return accounting."""
        collection = getattr(self, "income_collection", None)
        if collection is None:
            logger.warning("Mongo futures_income collection not available.")
            return
        for record in records:
            try:
                stored_record = {**record, "execution_mode": execution_mode}
                identity = {
                    "execution_mode": execution_mode,
                    "tranId": record.get("tranId"),
                    "incomeType": record.get("incomeType"),
                }
                collection.update_one(
                    identity, {"$setOnInsert": stored_record}, upsert=True
                )
            except Exception as exc:
                self.handle_exception(exc, "Error storing futures income record")

    def get_daily_net_pnl(self) -> float:
        """Return today's net Futures income from the locally synced ledger."""
        collection = getattr(self, "income_collection", None)
        if collection is None:
            return 0.0
        start_ms = int(
            datetime.now(timezone.utc)
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .timestamp()
            * 1000
        )
        try:
            return sum(
                float(row.get("income", 0) or 0)
                for row in collection.find({"time": {"$gte": start_ms}}, {"income": 1})
            )
        except Exception as exc:
            self.handle_exception(exc, "Error calculating daily net PnL")
            return 0.0

    def store_historical_data(
        self,
        symbol: str,
        df: pd.DataFrame,
        interval: str = "15m",
        execution_mode: str = "live",
    ) -> None:
        """Persist OHLCV rows into MongoDB with a 60-day TTL.

        Duplicate timestamps (per symbol/interval) are silently ignored
        thanks to the unique index.

        Args:
            symbol: Trading pair.
            df: DataFrame with at least ``timestamp``, ``open``, ``high``,
                ``low``, ``close``, ``volume`` columns.
            interval: Candle interval string.
        """
        collection = self._ohlcv_collection(execution_mode)
        if collection is None or df.empty:
            if df.empty:
                logger.info(f"No data to store for {symbol} ({interval}).")
            return
        try:
            records = []
            for _, row in df.iterrows():
                ts_sec = _epoch_to_seconds(row["timestamp"])
                expire_at = datetime.fromtimestamp(ts_sec, tz=timezone.utc) + timedelta(
                    days=60
                )
                records.append(
                    {
                        "symbol": symbol,
                        "interval": interval,
                        "timestamp": ts_sec,
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": float(row["volume"]),
                        "expireAt": expire_at,
                    }
                )
            if records:
                collection.insert_many(records, ordered=False)
                logger.info(
                    f"Stored {len(records)} records for {symbol} ({interval}) in MongoDB."
                )
        except Exception as exc:
            self.handle_exception(exc, f"Error storing data for {symbol}")

    def close(self) -> None:
        """Close the MongoDB connection."""
        if hasattr(self, "_mongo_client") and self._mongo_client is not None:
            self._mongo_client.close()
            logger.info("MongoDB connection closed.")
