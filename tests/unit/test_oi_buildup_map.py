"""Unit tests for api.futures.compute_oi_buildup_map -- Sniper HUD
Zone 3's bulk OI-buildup read (2026-08-27), a single SCAN + pipelined
HGET instead of a 208-request fan-out.
"""

from __future__ import annotations

from typing import Any

from api.futures import compute_oi_buildup_map


class _FakePipeline:
    def __init__(self, hashes: dict[str, dict[str, Any]]) -> None:
        self._hashes = hashes
        self._queued: list[str] = []

    def hget(self, key: str | bytes, field: str) -> None:
        self._queued.append(key.decode() if isinstance(key, bytes) else key)

    async def execute(self) -> list[bytes | None]:
        out: list[bytes | None] = []
        for key in self._queued:
            row = self._hashes.get(key)
            val = row.get("oi_buildup") if row else None
            out.append(val.encode() if isinstance(val, str) else val)
        return out


class _FakeRedis:
    """Enough of the real async redis client for this one function:
    a single-page SCAN (real Redis paginates via cursor; this fake
    returns everything in one page, cursor=0, which is a valid -- if
    small -- SCAN response shape) plus a pipeline of HGETs."""

    def __init__(self, hashes: dict[str, dict[str, Any]]) -> None:
        self._hashes = hashes

    async def scan(self, cursor: int, match: str, count: int) -> tuple[int, list[bytes]]:
        prefix = match.rstrip("*")
        keys = [k.encode() for k in self._hashes if k.startswith(prefix)]
        return 0, keys

    def pipeline(self, transaction: bool = True) -> _FakePipeline:
        return _FakePipeline(self._hashes)


async def test_returns_oi_buildup_for_every_symbol_with_a_futures_row() -> None:
    redis = _FakeRedis(
        {
            "infusion:futures:RELIANCE": {"oi_buildup": "LONG_BUILDUP"},
            "infusion:futures:TCS": {"oi_buildup": "SHORT_BUILDUP"},
            "infusion:futures:master": {"not": "a symbol row"},
        }
    )
    result = await compute_oi_buildup_map(redis)
    assert result == {"RELIANCE": "LONG_BUILDUP", "TCS": "SHORT_BUILDUP"}


async def test_master_key_is_never_treated_as_a_symbol() -> None:
    redis = _FakeRedis({"infusion:futures:master": {"oi_buildup": "should_never_appear"}})
    result = await compute_oi_buildup_map(redis)
    assert result == {}


async def test_a_symbol_with_no_futures_row_yet_is_simply_absent() -> None:
    """Not NEUTRAL, not any other guess -- just missing from the map,
    same "absence over fabrication" posture as classify_oi_buildup's
    own None-input case."""
    redis = _FakeRedis({})
    result = await compute_oi_buildup_map(redis)
    assert result == {}
