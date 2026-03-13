import time
from datetime import datetime, timedelta, timezone
import pandas as pd
import locale
import requests

try:  # pragma: no cover - dependency may be missing in test environment
    from pymongo import MongoClient, ASCENDING  # type: ignore
except Exception:  # pragma: no cover - handled gracefully if pymongo not installed
    MongoClient = None  # type: ignore
    ASCENDING = None  # type: ignore

import os
import sys
import logging
from orbit.core.exception_manager import ExceptionManager
from typing import List, Optional, Dict, Iterator

logger = logging.getLogger("Orbit")


OHLCV_COLLECTION_NAME = "OHLCVData"

def epoch_to_seconds(ts: int) -> int:
    """Normalize epoch to seconds (handles ns/µs/ms/s)."""
    ts = int(ts)
    if ts > 1_000_000_000_000_000_000:
        return ts // 1_000_000_000
    if ts > 1_000_000_000_000_000:
        return ts // 1_000_000
    if ts > 1_000_000_000_000:
        return ts // 1_000
    return ts

class MongoHandler(ExceptionManager):
    """Handle storage and retrieval of OHLCV data in MongoDB."""

    def __init__(self, uri: str = "mongodb://localhost:27017", db_name: str = "orbit"):
        super().__init__()
        if MongoClient is None:
            logger.warning("pymongo is not installed; MongoDB features are disabled.")
            self.collection = None
            return
        try:
            self.client = MongoClient(uri, serverSelectionTimeoutMS=1000)
            self.db = self.client[db_name]
            self.collection = self.db[OHLCV_COLLECTION_NAME]
            # Ensure indexes exist; ignore errors if server unreachable
            self.collection.create_index(
                [("symbol", ASCENDING), ("interval", ASCENDING), ("timestamp", ASCENDING)],
                unique=True,
            )
        except Exception as exc:
            logger.error(f"Error initializing MongoDB: {exc}")
            self.collection = None

    def clear_ohlcv_collection(self) -> None:
        """Delete all OHLCV data from MongoDB."""
        if self.collection is None:
            logger.warning("Mongo collection not available.")
            return

        try:
            result = self.collection.delete_many({})
            logger.info(f"Deleted {result.deleted_count} documents from OHLCV collection.")
        except Exception as exc:
            self.handle_exception(exc, "Error clearing OHLCV collection")

    def clear_symbol_data(self, symbol: str, interval: str = "15m") -> None:
        """Delete OHLCV data for a specific symbol."""
        if self.collection is None:
            logger.warning("Mongo collection not available.")
            return

        try:
            result = self.collection.delete_many({
                "symbol": symbol,
                "interval": interval
            })
            logger.info(f"Deleted {result.deleted_count} records for {symbol} ({interval}).")
        except Exception as exc:
            self.handle_exception(exc, f"Error clearing data for {symbol}")

    def clear_old_data(self, days: int = 60) -> None:
        """Delete OHLCV data older than specified days."""
        if self.collection is None:
            logger.warning("Mongo collection not available.")
            return

        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)

            result = self.collection.delete_many({
                "expireAt": {"$lt": cutoff}
            })

            logger.info(f"Deleted {result.deleted_count} old records.")
        except Exception as exc:
            self.handle_exception(exc, "Error clearing old data")

    def get_mongo_historical_data(self, symbol: str, interval: str = "15m") -> pd.DataFrame:
        """Retrieve historical OHLCV data from MongoDB."""
        if self.collection is None:
            return pd.DataFrame()
        try:
            end_ts = int(time.time())
            start_ts = end_ts - 15 * 24 * 3600
            query = {
                "symbol": symbol,
                "interval": interval,
                "timestamp": {"$gte": start_ts},
            }
            cursor = self.collection.find(query, {"_id": 0}).sort("timestamp", ASCENDING)
            items = list(cursor)
            if not items:
                logger.info(f"No historical data found in MongoDB for {symbol} ({interval}).")
                return pd.DataFrame()
            df = pd.DataFrame(items)
            return df
        except Exception as exc:
            self.handle_exception(exc, f"Error retrieving historical data for {symbol}")
            return pd.DataFrame()
        
    
    def get_binance_klines(self, symbol: str, interval: str, start_time: int, end_time: int):
        lang, _ = locale.getdefaultlocale()
        url = "https://api.binance.us/api/v3/klines" if lang == "en_US" else "https://api.binance.com/api/v3/klines"
        params = {"symbol": symbol, "interval": interval, "limit": 1000, "startTime": start_time, "endTime": end_time}

        retries, max_retries = 0, 5
        while retries < max_retries:
            try:
                response = requests.get(url, params=params, timeout=10)
                response.raise_for_status()
                return response.json()
            except requests.RequestException as e:
                retries += 1
                logging.warning(f"Retrying Binance API call for {symbol}. Attempt {retries}")
                time.sleep(1)

        self.handle_exception(Exception("Max retries reached for Binance API"), f"Fetching {symbol}")
        return []

    def data_collector(self, symbol: str, interval: str = '15m', start_time: Optional[int] = None) -> pd.DataFrame:
        end_time = int(datetime.now().timestamp() * 1000)
        start_time = start_time or (end_time - (90 * 24 * 60 * 60 * 1000))  # 90 days

        logging.info(f"Starting data collection for {symbol} from {datetime.utcfromtimestamp(start_time / 1000)}")

        all_data = []
        current_time = start_time

        while current_time < end_time:
            data = self.get_binance_klines(symbol, interval, start_time=current_time, end_time=end_time)
            if not data:
                logging.warning(f"No data found for {symbol} at {datetime.utcfromtimestamp(current_time / 1000)}")
                break
            all_data.extend(data)
            current_time = data[-1][0] + 1
            time.sleep(0.5)

        if not all_data:
            return pd.DataFrame()

        df = pd.DataFrame(all_data, columns=[
            "timestamp", "open", "high", "low", "close", "volume",
            "close_time", "quote_asset_volume", "num_trades",
            "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore"
        ])

        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df = df[["timestamp", "open", "high", "low", "close", "volume"]].astype({
            "open": "float", "high": "float", "low": "float", "close": "float", "volume": "float"
        }).set_index("timestamp")

        return df
        
    def handle_mongo_data(self, symbol: str) -> pd.DataFrame:
        existing_data = self.get_mongo_historical_data(symbol, interval="15m")

        required_start_time = None
        if not existing_data.empty:
            existing_data["timestamp"] = pd.to_datetime(existing_data["timestamp"], unit='ms')
            existing_data = existing_data.set_index("timestamp")
            required_start_time = existing_data.index.max() + timedelta(minutes=15)
            logging.info(f"{symbol}: Existing data loaded, last timestamp: {existing_data.index.max()}")
        else:
            logging.info(f"{symbol}: No existing data found in MongoDB.")

        timestamp_ms = int(required_start_time.timestamp() * 1000) if required_start_time else None
        new_data = self.data_collector(symbol, interval="15m", start_time=timestamp_ms)
        new_data = new_data.iloc[:-1]

        historical_data = pd.concat([existing_data, new_data]).drop_duplicates() if not existing_data.empty else new_data
        if historical_data.empty:
            logging.warning(f"No historical data found for {symbol}")
            return historical_data

        historical_data_db = new_data.copy().reset_index()
        historical_data_db["timestamp"] = (
            historical_data_db["timestamp"]
            .astype("int64") // 1_000_000
        )

        self.store_historical_data(symbol, historical_data_db)

        return historical_data

    def store_historical_data(self, symbol: str, df: pd.DataFrame, interval: str = "15m") -> None:
        """Store OHLCV data in MongoDB with a 60-day TTL."""
        if self.collection is None or df.empty:
            if df.empty:
                logger.info(f"No data to store for {symbol} ({interval}).")
            return
        try:
            records = []
            for _, row in df.iterrows():
                # expire_at = datetime.utcfromtimestamp(int(row["timestamp"])) + timedelta(days=60)
                ts_sec = epoch_to_seconds(row["timestamp"])
                expire_at = datetime.fromtimestamp(ts_sec, tz=timezone.utc) + timedelta(days=60)
                records.append(
                    {
                        "symbol": symbol,
                        "interval": interval,
                        "timestamp": int(row["timestamp"]),
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": float(row["volume"]),
                        "expireAt": expire_at,
                    }
                )
            if records:
                self.collection.insert_many(records, ordered=False)
                logger.info(f"Stored {len(records)} records for {symbol} ({interval}) in MongoDB.")
        except Exception as exc:
            self.handle_exception(exc, f"Error storing data for {symbol}")
