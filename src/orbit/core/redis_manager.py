"""
redis_manager
=============

Provides :class:`RedisManager`, a centralised Redis access layer.

All Redis operations used across the codebase are encapsulated here so
that:

* Connection configuration lives in one place.
* Key naming conventions are enforced consistently.
* Error handling / logging is uniform.
* Sub-classes (OrderManager, TradeChecker, SignalAnalyzer, Croner) simply
  inherit :class:`RedisManager` instead of holding a raw
  ``redis.StrictRedis`` attribute.

Key schema
----------
``trade:{trade_id}``                – serialised trade dict (JSON string)
``order:{order_id}``                – trade_id string
``{symbol}``                        – cooldown ISO-8601 timestamp
``market_sentiments``               – current sentiment label
``sentiment:pending_label``         – expiring candidate regime awaiting confirmation
``sentiment:pending_base``          – directional regime the candidate is measured against
``sentiment:pending_count``         – expiring consecutive-observation count
``sentiment:last_run_slot``         – dated half-hour slot of last analysis
``sentiment:run_slot_lease``        – in-progress analysis slot lease
``sentiment:last_news_fetch``       – ISO-8601 last news fetch time
``sentiment:last_reddit_fetch``     – ISO-8601 last Reddit fetch time
``sentiment:last_twitter_fetch``    – ISO-8601 last Twitter fetch time
"""

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, Iterator, Optional

import redis

logger = logging.getLogger("Orbit")

# ---------------------------------------------------------------------------
# Key prefixes / names
# ---------------------------------------------------------------------------

TRADE_KEY_PREFIX: str = "trade:"
ORDER_KEY_PREFIX: str = "order:"

REDIS_KEY_MARKET_SENTIMENT: str = "market_sentiments"
REDIS_KEY_PENDING_SENTIMENT: str = "sentiment:pending_label"
REDIS_KEY_PENDING_SENTIMENT_BASE: str = "sentiment:pending_base"
REDIS_KEY_PENDING_SENTIMENT_COUNT: str = "sentiment:pending_count"
REDIS_KEY_SENTIMENT_LAST_RUN_SLOT: str = "sentiment:last_run_slot"
REDIS_KEY_SENTIMENT_RUN_SLOT_LEASE: str = "sentiment:run_slot_lease"
REDIS_KEY_LAST_NEWS_FETCH: str = "sentiment:last_news_fetch"
REDIS_KEY_LAST_REDDIT_FETCH: str = "sentiment:last_reddit_fetch"
REDIS_KEY_LAST_TWITTER_FETCH: str = "sentiment:last_twitter_fetch"

# TTL for timestamp keys (48 hours)
_TIMESTAMP_TTL: int = 172_800

# Pending observations must remain recent across half-hour confirmation windows.
_PENDING_SENTIMENT_TTL: int = 7_200
_SENTIMENT_RUN_LEASE_TTL: int = 3_600


def _trade_key(trade_id: str) -> str:
    return f"{TRADE_KEY_PREFIX}{trade_id}"


def _order_key(order_id: str) -> str:
    return f"{ORDER_KEY_PREFIX}{order_id}"


# ---------------------------------------------------------------------------
# RedisManager
# ---------------------------------------------------------------------------


class RedisManager:
    """Centralised Redis access layer.

    All Redis I/O for the trading system is routed through this class.
    Sub-classes receive a fully configured client and a consistent set of
    helper methods without needing to know about key naming or error
    handling.

    Args:
        redis_client: An optional pre-built ``redis.StrictRedis`` instance.
            When ``None`` a default ``localhost:6379/db=0`` connection is
            created.
    """

    def __init__(
        self,
        redis_client: Optional[redis.StrictRedis] = None,
    ) -> None:
        self.redis_client: redis.StrictRedis = redis_client or redis.StrictRedis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            db=int(os.getenv("REDIS_DB", "0")),
            decode_responses=True,
        )

    # ------------------------------------------------------------------
    # Generic primitives
    # ------------------------------------------------------------------

    def redis_get(self, key: str) -> Optional[str]:
        """Return the string value stored at *key*, or ``None``."""
        try:
            return self.redis_client.get(key)
        except Exception as e:
            logger.exception(f"[Redis] GET {key!r} failed: {e}")
            return None

    def redis_set(self, key: str, value: str) -> None:
        """Set *key* to *value* with no expiry."""
        try:
            self.redis_client.set(key, value)
        except Exception as e:
            logger.exception(f"[Redis] SET {key!r} failed: {e}")

    def redis_setex(self, key: str, ttl: int, value: str) -> None:
        """Set *key* to *value* with a TTL of *ttl* seconds."""
        try:
            self.redis_client.setex(key, ttl, value)
        except Exception as e:
            logger.exception(f"[Redis] SETEX {key!r} failed: {e}")

    def redis_delete(self, key: str) -> None:
        """Delete *key* from Redis."""
        try:
            self.redis_client.delete(key)
        except Exception as e:
            logger.exception(f"[Redis] DELETE {key!r} failed: {e}")

    def redis_scan_iter(self, match: str) -> Iterator[str]:
        """Yield all keys matching *match* pattern."""
        try:
            yield from self.redis_client.scan_iter(match)
        except Exception as e:
            logger.exception(f"[Redis] SCAN {match!r} failed: {e}")

    # ------------------------------------------------------------------
    # Trade mappings  (trade:{trade_id} → JSON)
    # ------------------------------------------------------------------

    def save_trade(self, trade_id: str, trade: Dict[str, Any]) -> None:
        """Persist *trade* dict under ``trade:{trade_id}``."""
        try:
            self.redis_client.set(_trade_key(trade_id), json.dumps(trade))
        except Exception as e:
            logger.exception(f"[Redis] save_trade({trade_id}) failed: {e}")

    def load_trade(self, trade_id: str) -> Optional[Dict[str, Any]]:
        """Load and deserialise the trade stored at ``trade:{trade_id}``."""
        try:
            raw = self.redis_client.get(_trade_key(trade_id))
            if raw:
                return json.loads(raw)
        except Exception as e:
            logger.exception(f"[Redis] load_trade({trade_id}) failed: {e}")
        return None

    def delete_trade(self, trade_id: str) -> None:
        """Remove ``trade:{trade_id}`` from Redis."""
        try:
            self.redis_client.delete(_trade_key(trade_id))
        except Exception as e:
            logger.exception(f"[Redis] delete_trade({trade_id}) failed: {e}")

    def update_trade_fields(self, trade_id: str, updates: Dict[str, Any]) -> None:
        """Merge *updates* into the persisted trade dict and re-save."""
        trade = self.load_trade(trade_id) or {}
        trade.update(updates)
        self.save_trade(trade_id, trade)

    def merge_trade_fields(self, trade_id: str, updates: Dict[str, Any]) -> None:
        """Atomically merge fields without erasing concurrent lifecycle state."""
        script = """
        local current = redis.call('GET', KEYS[1])
        local trade = {}
        if current then
            trade = cjson.decode(current)
        end
        local updates = cjson.decode(ARGV[1])
        for key, value in pairs(updates) do
            trade[key] = value
        end
        redis.call('SET', KEYS[1], cjson.encode(trade))
        return 1
        """
        try:
            self.redis_client.eval(script, 1, _trade_key(trade_id), json.dumps(updates))
        except Exception as error:
            logger.exception(
                "[Redis] merge_trade_fields(%s) failed: %s", trade_id, error
            )

    def delete_trade_with_orders(self, trade_id: str) -> None:
        """Remove ``trade:{trade_id}`` and all associated ``order:*`` keys.

        Reads ``sl_order_id`` and ``tp_order_id`` from the stored trade
        before deleting.
        """
        try:
            trade = self.load_trade(trade_id)
            if trade:
                for field in ("sl_order_id", "tp_order_id"):
                    oid = trade.get(field)
                    if oid:
                        self.redis_client.delete(_order_key(str(oid)))
            self.redis_client.delete(_trade_key(trade_id))
        except Exception as e:
            logger.exception(f"[Redis] delete_trade_with_orders({trade_id}) failed: {e}")

    def scan_trade_keys(self) -> Iterator[str]:
        """Yield all ``trade:*`` keys currently in Redis."""
        yield from self.redis_scan_iter(f"{TRADE_KEY_PREFIX}*")

    # ------------------------------------------------------------------
    # Order mappings  (order:{order_id} → trade_id)
    # ------------------------------------------------------------------

    def register_order(self, order_id: str, trade_id: str) -> None:
        """Map ``order:{order_id}`` → *trade_id*."""
        try:
            self.redis_client.set(_order_key(str(order_id)), trade_id)
        except Exception as e:
            logger.exception(f"[Redis] register_order({order_id}) failed: {e}")

    def deregister_order(self, order_id: str) -> None:
        """Remove the ``order:{order_id}`` key."""
        try:
            self.redis_client.delete(_order_key(str(order_id)))
        except Exception as e:
            logger.exception(f"[Redis] deregister_order({order_id}) failed: {e}")

    def trade_id_for_order(self, order_id: str) -> Optional[str]:
        """Return the *trade_id* that owns *order_id*, or ``None``."""
        try:
            return self.redis_client.get(_order_key(str(order_id)))
        except Exception as e:
            logger.exception(f"[Redis] trade_id_for_order({order_id}) failed: {e}")
        return None

    # ------------------------------------------------------------------
    # Cooldown helpers  ({symbol} → ISO-8601 cooldown-end timestamp)
    # ------------------------------------------------------------------

    def get_cooldown(self, symbol: str) -> Optional[str]:
        """Return the ISO-8601 cooldown-end timestamp for *symbol*, or ``None``."""
        return self.redis_get(symbol)

    def set_cooldown(self, symbol: str, cooldown_end_iso: str) -> None:
        """Persist the cooldown-end timestamp for *symbol*."""
        self.redis_set(symbol, cooldown_end_iso)

    # ------------------------------------------------------------------
    # Market sentiment  (market_sentiments → label string)
    # ------------------------------------------------------------------

    def get_market_sentiment(self) -> Optional[str]:
        """Return the cached market-sentiment label, or ``None``."""
        return self.redis_get(REDIS_KEY_MARKET_SENTIMENT)

    def set_market_sentiment(self, sentiment: str) -> None:
        """Cache the current market-sentiment label."""
        self.redis_set(REDIS_KEY_MARKET_SENTIMENT, sentiment)

    def record_pending_sentiment(
        self,
        sentiment: str,
        base_sentiment: str,
        confirmations_required: int,
    ) -> Optional[tuple[int, bool]]:
        """Atomically record and, at threshold, commit a candidate regime.

        ``None`` means another scheduler changed the market regime after it was
        read by the caller. Otherwise returns ``(count, was_committed)``.
        """
        script = """
        if redis.call('GET', KEYS[1]) ~= ARGV[1] then
            return 0
        end
        local count = 1
        if redis.call('GET', KEYS[2]) == ARGV[2]
            and redis.call('GET', KEYS[3]) == ARGV[1] then
            count = tonumber(redis.call('GET', KEYS[4]) or '0') + 1
        end
        if count >= tonumber(ARGV[4]) then
            redis.call('SET', KEYS[1], ARGV[2])
            redis.call('DEL', KEYS[2], KEYS[3], KEYS[4])
            return -count
        end
        redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
        redis.call('SET', KEYS[3], ARGV[1], 'EX', ARGV[3])
        redis.call('SET', KEYS[4], count, 'EX', ARGV[3])
        return count
        """
        try:
            count = int(self.redis_client.eval(
                script,
                4,
                REDIS_KEY_MARKET_SENTIMENT,
                REDIS_KEY_PENDING_SENTIMENT,
                REDIS_KEY_PENDING_SENTIMENT_BASE,
                REDIS_KEY_PENDING_SENTIMENT_COUNT,
                base_sentiment,
                sentiment,
                str(_PENDING_SENTIMENT_TTL),
                str(confirmations_required),
            ))
            if count == 0:
                return None
            return abs(count), count < 0
        except Exception as e:
            logger.exception("[Redis] Atomic pending-sentiment update failed: %s", e)
            return None

    def set_market_sentiment_if_current(
        self, expected: Optional[str], sentiment: str
    ) -> bool:
        """Atomically change regime only if its cached value is still expected."""
        script = """
        local current = redis.call('GET', KEYS[1]) or ''
        if current ~= ARGV[1] then
            return 0
        end
        redis.call('SET', KEYS[1], ARGV[2])
        redis.call('DEL', KEYS[2], KEYS[3], KEYS[4])
        return 1
        """
        try:
            return bool(self.redis_client.eval(
                script,
                4,
                REDIS_KEY_MARKET_SENTIMENT,
                REDIS_KEY_PENDING_SENTIMENT,
                REDIS_KEY_PENDING_SENTIMENT_BASE,
                REDIS_KEY_PENDING_SENTIMENT_COUNT,
                expected or "",
                sentiment,
            ))
        except Exception as e:
            logger.exception("[Redis] Conditional market-sentiment update failed: %s", e)
            return False

    def set_market_sentiment_and_clear_pending(self, sentiment: str) -> None:
        """Atomically establish a market regime and discard older evidence."""
        script = """
        redis.call('SET', KEYS[1], ARGV[1])
        redis.call('DEL', KEYS[2], KEYS[3], KEYS[4])
        return 1
        """
        try:
            self.redis_client.eval(
                script,
                4,
                REDIS_KEY_MARKET_SENTIMENT,
                REDIS_KEY_PENDING_SENTIMENT,
                REDIS_KEY_PENDING_SENTIMENT_BASE,
                REDIS_KEY_PENDING_SENTIMENT_COUNT,
                sentiment,
            )
        except Exception as e:
            logger.exception("[Redis] Atomic market-sentiment update failed: %s", e)

    def clear_pending_sentiment(self) -> None:
        """Clear a candidate regime after it is accepted or invalidated."""
        try:
            self.redis_client.delete(
                REDIS_KEY_PENDING_SENTIMENT,
                REDIS_KEY_PENDING_SENTIMENT_BASE,
                REDIS_KEY_PENDING_SENTIMENT_COUNT,
            )
        except Exception as e:
            logger.exception("[Redis] Clearing pending sentiment failed: %s", e)

    # ------------------------------------------------------------------
    # Half-hour run tracking
    # ------------------------------------------------------------------

    def claim_sentiment_run_slot(self, slot: int) -> bool:
        """Atomically lease an incomplete half-hour analysis slot.

        Any active lease is preserved, including across a slot boundary, so an
        older in-flight analysis cannot finish after and overwrite a newer one.
        The completed-slot marker prevents repeated observations. Redis errors
        fail closed.
        """
        script = """
        if redis.call('GET', KEYS[1]) == ARGV[1] then
            return 0
        end
        if redis.call('GET', KEYS[2]) then
            return 0
        end
        redis.call('SET', KEYS[2], ARGV[1], 'EX', ARGV[2])
        return 1
        """
        try:
            return bool(
                self.redis_client.eval(
                    script,
                    2,
                    REDIS_KEY_SENTIMENT_LAST_RUN_SLOT,
                    REDIS_KEY_SENTIMENT_RUN_SLOT_LEASE,
                    str(slot),
                    str(_SENTIMENT_RUN_LEASE_TTL),
                )
            )
        except Exception as e:
            logger.exception("[Redis] Claiming sentiment run slot failed: %s", e)
            return False

    def complete_sentiment_run_slot(self, slot: int) -> bool:
        """Mark a leased slot complete and clear its lease atomically."""
        script = """
        if redis.call('GET', KEYS[1]) ~= ARGV[1] then
            return 0
        end
        redis.call('SET', KEYS[2], ARGV[1])
        redis.call('DEL', KEYS[1])
        return 1
        """
        try:
            return bool(
                self.redis_client.eval(
                    script,
                    2,
                    REDIS_KEY_SENTIMENT_RUN_SLOT_LEASE,
                    REDIS_KEY_SENTIMENT_LAST_RUN_SLOT,
                    str(slot),
                )
            )
        except Exception as e:
            logger.exception("[Redis] Completing sentiment run slot failed: %s", e)
            return False

    def release_sentiment_run_slot(self, slot: int) -> bool:
        """Release a failed analysis lease without disturbing another worker."""
        script = """
        if redis.call('GET', KEYS[1]) ~= ARGV[1] then
            return 0
        end
        redis.call('DEL', KEYS[1])
        return 1
        """
        try:
            return bool(
                self.redis_client.eval(
                    script, 1, REDIS_KEY_SENTIMENT_RUN_SLOT_LEASE, str(slot)
                )
            )
        except Exception as e:
            logger.exception("[Redis] Releasing sentiment run slot failed: %s", e)
            return False

    # ------------------------------------------------------------------
    # Sentiment fetch timestamps
    # ------------------------------------------------------------------

    def get_last_fetch_times(
        self,
    ) -> tuple[Optional[datetime], Optional[datetime], Optional[datetime]]:
        """Load last-fetch timestamps from Redis.

        Returns:
            Tuple of ``(last_news_fetch, last_reddit_fetch, last_twitter_fetch)``.
            Any element is ``None`` when not yet stored.
        """
        last_news_fetch: Optional[datetime] = None
        last_reddit_fetch: Optional[datetime] = None
        last_twitter_fetch: Optional[datetime] = None

        try:
            raw_news = self.redis_client.get(REDIS_KEY_LAST_NEWS_FETCH)
            if raw_news:
                last_news_fetch = datetime.fromisoformat(raw_news)

            raw_reddit = self.redis_client.get(REDIS_KEY_LAST_REDDIT_FETCH)
            if raw_reddit:
                last_reddit_fetch = datetime.fromisoformat(raw_reddit)

            raw_twitter = self.redis_client.get(REDIS_KEY_LAST_TWITTER_FETCH)
            if raw_twitter:
                last_twitter_fetch = datetime.fromisoformat(raw_twitter)

        except Exception as e:
            logger.exception(f"[Redis] get_last_fetch_times failed: {e}")

        return last_news_fetch, last_reddit_fetch, last_twitter_fetch

    def save_last_fetch_times(
        self,
        last_news_fetch: Optional[str],
        last_reddit_fetch: Optional[str],
        last_twitter_fetch: Optional[str],
    ) -> None:
        """Persist last-fetch timestamps to Redis with a 48-hour TTL.

        Args:
            last_news_fetch: ISO-8601 string for the last news fetch time.
            last_reddit_fetch: ISO-8601 string for the last Reddit fetch time.
            last_twitter_fetch: ISO-8601 string for the last Twitter fetch time.
        """
        try:
            if last_news_fetch:
                self.redis_client.setex(
                    REDIS_KEY_LAST_NEWS_FETCH, _TIMESTAMP_TTL, last_news_fetch
                )
            if last_reddit_fetch:
                self.redis_client.setex(
                    REDIS_KEY_LAST_REDDIT_FETCH, _TIMESTAMP_TTL, last_reddit_fetch
                )
            if last_twitter_fetch:
                self.redis_client.setex(
                    REDIS_KEY_LAST_TWITTER_FETCH, _TIMESTAMP_TTL, last_twitter_fetch
                )
        except Exception as e:
            logger.exception(f"[Redis] save_last_fetch_times failed: {e}")
