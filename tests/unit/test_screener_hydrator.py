"""Unit tests for api.screener_hydrator's pure Squeeze Readiness / RVOL
calculators -- "Full Universe Batch Hydration Engine" sprint
(2026-08-29) -- plus one real-pipeline test of hydrate_smc_universe's
own SMC_UNIVERSE_KEY expiry (review fix, 2026-08-29), using a fake
Redis that resolves queued pipeline commands against its own in-memory
store the same way test_oi_buildup_map.py's own established fake does,
generalized to also cover the read pipe (zrange/hgetall) and write pipe
(delete/hset/expire) this module's async functions actually issue.
"""

from __future__ import annotations

from typing import Any

import msgpack
from api.routes.screener import SMC_UNIVERSE_KEY
from api.screener_hydrator import (
    SMC_UNIVERSE_TTL_SEC,
    compute_rvol,
    compute_squeeze_readiness,
    hydrate_smc_universe,
)


def _bar(high: float, low: float, close: float, volume: float = 1000.0) -> dict[str, float]:
    return {"time": 0.0, "high": high, "low": low, "close": close, "volume": volume}


class _FakePipeline:
    """Queues (method, args, kwargs) and resolves each against the
    store's CURRENT state at execute() time -- the same real-pipeline
    semantics test_oi_buildup_map.py's own fake already uses for one
    method, generalized here to the handful of others hydrate_smc_
    universe itself calls."""

    def __init__(self, store: _FakeRedis) -> None:
        self._store = store
        self._queued: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def zrange(self, key: str, start: int, stop: int) -> _FakePipeline:
        self._queued.append(("zrange", (key, start, stop), {}))
        return self

    def hgetall(self, key: str) -> _FakePipeline:
        self._queued.append(("hgetall", (key,), {}))
        return self

    def hget(self, key: str, field: str) -> _FakePipeline:
        self._queued.append(("hget", (key, field), {}))
        return self

    def delete(self, key: str) -> _FakePipeline:
        self._queued.append(("delete", (key,), {}))
        return self

    def hset(self, key: str, mapping: dict[str, str]) -> _FakePipeline:
        self._queued.append(("hset", (key,), {"mapping": mapping}))
        return self

    def expire(self, key: str, ttl: int) -> _FakePipeline:
        self._queued.append(("expire", (key, ttl), {}))
        return self

    async def execute(self) -> list[Any]:
        return [self._store._apply(name, args, kwargs) for name, args, kwargs in self._queued]


class _FakeRedis:
    """Enough of the real async redis client for hydrate_smc_universe:
    infusion:symbols (direct hgetall, msgpack-encoded per real
    convention), per-symbol daily-bar zsets/tick/feature hashes (via
    the read pipe), and infusion:futures:* (SCAN + pipelined HGET,
    compute_oi_buildup_map's own real call shape)."""

    def __init__(self, symbols: tuple[str, ...] = ()) -> None:
        self._symbols = symbols
        self.hashes: dict[str, dict[str, str]] = {}
        self.expirations: dict[str, int] = {}

    async def hgetall(self, key: str) -> dict[bytes, bytes]:
        if key == "infusion:symbols":
            return {
                s.encode(): msgpack.packb({"symbol": s}, use_bin_type=True) for s in self._symbols
            }
        return {}

    async def scan(self, cursor: int, match: str, count: int) -> tuple[int, list[bytes]]:
        return 0, []

    def pipeline(self, transaction: bool = False) -> _FakePipeline:
        return _FakePipeline(self)

    def _apply(self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        key = args[0]
        if name == "zrange":
            return []
        if name == "hgetall":
            return {}
        if name == "hget":
            return None
        if name == "delete":
            self.hashes.pop(key, None)
            self.expirations.pop(key, None)
            return 1
        if name == "hset":
            self.hashes.setdefault(key, {}).update(kwargs["mapping"])
            return len(kwargs["mapping"])
        if name == "expire":
            self.expirations[key] = args[1]
            return 1
        raise NotImplementedError(name)


def test_squeeze_readiness_is_none_with_too_few_bars() -> None:
    assert compute_squeeze_readiness([_bar(101, 99, 100)] * 10) is None


def test_squeeze_readiness_is_100_for_a_perfectly_flat_close_series() -> None:
    """Constant closes (100 every bar) -> Bollinger stdev is exactly 0,
    so bb_width is 0 -- the deepest possible compression relative to any
    real (non-zero) Keltner width. High/low constant at 101/99 with a
    constant close gives a constant true range of 2 every bar (the
    |high-low| term dominates since prev_close never differs), so
    ATR=2, kc_width = 2*1.5*2 = 6 -- a real, non-degenerate channel."""
    bars = [_bar(101, 99, 100)] * 21
    assert compute_squeeze_readiness(bars) == 100.0


def test_squeeze_readiness_is_0_when_bollinger_is_wider_than_keltner() -> None:
    """Closes alternating far apart (70/130) make the Bollinger stdev
    (and so bb_width) huge relative to the same tight high/low range's
    real ATR/Keltner width -- squeeze_ratio >= 1, honestly 0 (no
    squeeze), never a negative or fabricated intermediate number."""
    bars = [_bar(101, 99, 70 if i % 2 == 0 else 130) for i in range(21)]
    assert compute_squeeze_readiness(bars) == 0.0


def test_rvol_is_none_with_too_few_bars() -> None:
    assert compute_rvol([_bar(101, 99, 100, volume=1000)] * 10) is None


def test_rvol_is_none_when_historical_average_volume_is_zero() -> None:
    bars = [_bar(101, 99, 100, volume=0)] * 20 + [_bar(101, 99, 100, volume=5000)]
    assert compute_rvol(bars) is None


def test_rvol_compares_the_latest_bar_against_the_trailing_20_bar_average() -> None:
    # 20 historical sessions at 1000 shares, then a session at 3500 --
    # 3500 / 1000 = 3.5x.
    bars = [_bar(101, 99, 100, volume=1000.0) for _ in range(20)] + [
        _bar(101, 99, 100, volume=3500.0)
    ]
    assert compute_rvol(bars) == 3.5


def test_rvol_ignores_bars_older_than_the_trailing_window() -> None:
    """A much larger volume sitting OUTSIDE the trailing 20-session
    window must not pull the average up -- only the most recent 20
    historical sessions (excluding the current one) count."""
    stale_huge_bar = _bar(101, 99, 100, volume=1_000_000.0)
    bars = (
        [stale_huge_bar]
        + [_bar(101, 99, 100, volume=1000.0) for _ in range(20)]
        + [_bar(101, 99, 100, volume=2000.0)]
    )
    assert compute_rvol(bars) == 2.0


async def test_smc_universe_key_gets_a_real_expiry_after_hydration() -> None:
    """SMC_UNIVERSE_KEY previously had no TTL at all -- if this loop ever
    stopped or failed repeatedly, /api/screener/fno would keep serving
    arbitrarily stale SMC/RVOL/OB-FVG data forever. Runs the real
    hydrate_smc_universe against a fake Redis that resolves queued
    pipeline commands the same way the real client does, then checks
    the write pipeline's own real EXPIRE call landed -- not just that
    the TTL constant exists."""
    redis = _FakeRedis(symbols=("RELIANCE",))
    await hydrate_smc_universe(redis)
    assert redis.expirations[SMC_UNIVERSE_KEY] == SMC_UNIVERSE_TTL_SEC
