import json
import logging
from unittest.mock import MagicMock

from orbit.core.command_center import (
    CommandCenterLogHandler,
    REDIS_KEY_COMMAND_CENTER_EXCEPTIONS,
    REDIS_KEY_COMMAND_CENTER_LOGS,
    REDIS_KEY_SENTIMENT_HISTORY,
    read_observability,
    read_positions,
    read_runtime_state,
    read_sentiment,
    read_sentiment_history,
    record_exception,
    record_runtime_activity,
    record_sentiment_snapshot,
    runtime_activity_key,
)
from orbit.core.redis_manager import (
    REDIS_KEY_MARKET_SENTIMENT,
    TRADE_KEY_PREFIX,
    runtime_heartbeat_key,
)


class FakePipeline:
    def __init__(self, client: "FakeRedis") -> None:
        self.client = client
        self.operations: list[tuple[str, tuple[object, ...]]] = []

    def lpush(self, *args: object) -> "FakePipeline":
        self.operations.append(("lpush", args))
        return self

    def set(self, *args: object) -> "FakePipeline":
        self.operations.append(("set", args))
        return self

    def ltrim(self, *args: object) -> "FakePipeline":
        self.operations.append(("ltrim", args))
        return self

    def execute(self) -> None:
        for method, args in self.operations:
            getattr(self.client, method)(*args)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def setex(self, key: str, _ttl: int, value: str) -> None:
        self.values[key] = value

    def get(self, key: object) -> str | None:
        return self.values.get(str(key))

    def scan_iter(self, pattern: str):
        prefix = pattern.removesuffix("*")
        yield from (key for key in self.values if key.startswith(prefix))

    def pipeline(self, transaction: bool = False) -> FakePipeline:
        return FakePipeline(self)

    def lpush(self, key: object, value: object) -> None:
        self.lists.setdefault(str(key), []).insert(0, str(value))

    def ltrim(self, key: object, start: object, stop: object) -> None:
        self.lists[str(key)] = self.lists.get(str(key), [])[int(start):int(stop) + 1]

    def lrange(self, key: str, start: int, stop: int) -> list[str]:
        return self.lists.get(key, [])[start:stop + 1]


def test_reads_runtime_positions_and_sentiment_from_live_state() -> None:
    client = FakeRedis()
    client.set(runtime_heartbeat_key("worker-1"), "2026-09-06T00:00:00+00:00")
    record_runtime_activity(client, "analyzing_signal", "BTCUSDT", "worker-1")
    client.set(
        f"{TRADE_KEY_PREFIX}decision-1",
        json.dumps(
            {
                "trade_id": "decision-1",
                "symbol": "BTCUSDT",
                "positionSide": "BUY",
                "quantity": 0.2,
                "price": 60_000,
                "current_price": 61_000,
                "stop_loss_price": 59_000,
                "sl_order_id": "sl-1",
                "target": 63_000,
            }
        ),
    )
    client.set(REDIS_KEY_MARKET_SENTIMENT, "BULLISH")
    record_sentiment_snapshot(
        client,
        {
            "sentiment": "BULLISH",
            "effective_sentiment": "BULLISH",
            "confidence": 0.84,
            "provider": "test-provider",
            "explanation": "Broad positive momentum",
            "signal_action": "unchanged",
        },
    )

    runtime = read_runtime_state(client, ["worker-1"])
    positions = read_positions(client)
    sentiment = read_sentiment(client)

    assert runtime["status"] == "online"
    assert runtime["current_activity"] == "analyzing_signal"
    assert positions[0]["unrealized_pnl"] == 200.0
    assert positions[0]["protection_status"] == "unverified"
    assert sentiment["effective"] == "BULLISH"
    assert sentiment["confidence"] == 0.84
    assert read_sentiment_history(client)[0]["effective"] == "BULLISH"
    assert REDIS_KEY_SENTIMENT_HISTORY in client.lists


def test_structured_logs_and_exceptions_are_bounded_ui_sources() -> None:
    client = FakeRedis()
    handler = CommandCenterLogHandler(client)
    logger = logging.getLogger("Orbit.command-center-test")
    record = logger.makeRecord(
        logger.name,
        logging.WARNING,
        __file__,
        1,
        "Position price is stale",
        (),
        None,
    )
    handler.emit(record)
    error = RuntimeError("exchange timeout")
    record_exception(client, error, "monitor_order", "traceback text")

    logs, exceptions = read_observability(client, 10, 10)

    assert logs[0]["level"] == "WARNING"
    assert logs[0]["message"] == "Position price is stale"
    assert exceptions[0]["type"] == "RuntimeError"
    assert exceptions[0]["context"] == "monitor_order"
    assert REDIS_KEY_COMMAND_CENTER_LOGS in client.lists
    assert REDIS_KEY_COMMAND_CENTER_EXCEPTIONS in client.lists
    assert read_observability(client, 0, 0) == ([], [])


def test_observability_writes_do_not_interrupt_trading_on_redis_failure() -> None:
    client = MagicMock()
    client.setex.side_effect = RuntimeError("redis down")
    client.set.side_effect = RuntimeError("redis down")
    client.pipeline.side_effect = RuntimeError("redis down")

    record_runtime_activity(client, "starting")
    record_sentiment_snapshot(client, {"sentiment": "NEUTRAL"})
    record_exception(client, ValueError("bad"), "test", "trace")

    client.setex.assert_called_once()
    assert client.pipeline.call_count == 2
    assert runtime_activity_key("default") in client.setex.call_args.args
