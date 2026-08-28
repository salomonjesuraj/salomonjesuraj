"""Unit tests for alerter.engine.AlerterEngine._deliver_position_warning's
redundant cooldown gate -- P0/P1 audit fix (2026-08-28). See
POSITION_WARNING_REDUNDANT_COOLDOWN_PREFIX's own module-level comment in
engine.py for why this second gate exists alongside api/broker_sync.py's
own real cooldown.

DeliveryGate and the formatter callback are never touched by
_deliver_position_warning (it bypasses both by design), so this suite
passes lightweight placeholders for them rather than standing up a real
DeliveryGate -- this test's own scope is the cooldown gate + delivery
outcome, not the gate machinery a different signal path already has its
own tests for.
"""

from __future__ import annotations

from typing import Any

from alerter.config import AlerterSettings
from alerter.engine import POSITION_WARNING_REDUNDANT_COOLDOWN_PREFIX, AlerterEngine
from alerter.telegram import TelegramClient


class _FakePipeline:
    def __init__(self, log: list[str]) -> None:
        self._log = log
        self._queued: list[tuple[str, Any]] = []

    def lpush(self, key: str, value: str) -> None:
        self._queued.append(("lpush", value))

    def ltrim(self, key: str, start: int, end: int) -> None:
        self._queued.append(("ltrim", None))

    async def execute(self) -> list[Any]:
        for op, value in self._queued:
            if op == "lpush":
                self._log.insert(0, value)
        return []


class _FakeRedis:
    """Enough of the real async redis client for this method: a plain
    string-key existence/TTL store (exists/setex) plus a pipeline for
    the delivery log -- same _FakePipeline shape test_oi_buildup_map.py's
    own fake already established for a pipelined-write test."""

    def __init__(self) -> None:
        self._strings: dict[str, str] = {}
        self.delivery_log: list[str] = []

    async def exists(self, key: str) -> int:
        return 1 if key in self._strings else 0

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self._strings[key] = value

    def pipeline(self, transaction: bool = True) -> _FakePipeline:
        return _FakePipeline(self.delivery_log)


def _engine(redis: _FakeRedis) -> AlerterEngine:
    settings = AlerterSettings()
    return AlerterEngine(
        redis=redis,  # type: ignore[arg-type]
        settings=settings,
        gate=None,  # type: ignore[arg-type]  -- never touched by this method
        formatter_fn=lambda _payload: "",  # never touched by this method
        telegram_client=TelegramClient(bot_token="", settings=settings),  # dry-run: no real HTTP
    )


def _payload(symbol: str = "RELIANCE") -> dict[str, Any]:
    return {
        "signal_id": "sig-1",
        "symbol": symbol,
        "message": "<b>EXIT IMMEDIATELY</b> RELIANCE",
        "conviction_grade": "A",
        "tags": ["STRUCTURAL_BREAK"],
    }


async def test_first_position_warning_for_a_symbol_is_delivered() -> None:
    redis = _FakeRedis()
    engine = _engine(redis)
    await engine._deliver_position_warning(_payload())
    assert engine._alerts_sent == 1
    assert redis._strings[f"{POSITION_WARNING_REDUNDANT_COOLDOWN_PREFIX}RELIANCE"] == "1"


async def test_second_warning_for_the_same_symbol_within_cooldown_is_blocked() -> None:
    """The exact defence-in-depth scenario this fix targets: a duplicate
    position_warning event reaches this method a second time (e.g. an
    at-least-once stream redelivery) with broker_sync.py's own upstream
    cooldown state untouched -- this gate alone must still stop the
    second Telegram send."""
    redis = _FakeRedis()
    engine = _engine(redis)
    await engine._deliver_position_warning(_payload())
    await engine._deliver_position_warning(_payload())
    assert engine._alerts_sent == 1


async def test_cooldown_is_scoped_per_symbol_not_global() -> None:
    redis = _FakeRedis()
    engine = _engine(redis)
    await engine._deliver_position_warning(_payload("RELIANCE"))
    await engine._deliver_position_warning(_payload("TCS"))
    assert engine._alerts_sent == 2
