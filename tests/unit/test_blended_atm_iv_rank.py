"""Unit tests for api.routes.market._blended_atm_iv_rank -- P0 audit
Phase 4 (2026-08-28), the Omni-Screener's real "blended near-term ATM
IV Rank" column.

Exercises the real _iv_rank() this function reuses verbatim (not a
mock of it), so the fake Redis below implements every command that
function actually calls: lrange/sismember/lpush/ltrim/sadd/expire.
Seeding 60+ history values per contract_key up front means the
"already seen today" sismember check is what decides whether a call
is a read-only rank lookup or a first-write-of-the-day -- both paths
are exercised here since real production traffic hits both.
"""

from __future__ import annotations

from typing import Any

from api.routes.market import _blended_atm_iv_rank


class _FakeRedis:
    def __init__(self) -> None:
        self._lists: dict[str, list[str]] = {}
        self._sets: dict[str, set[str]] = {}

    def seed_history(self, contract_key: str, values: list[float]) -> None:
        key = f"infusion:option-iv-history:{contract_key}"
        self._lists[key] = [str(v) for v in values]
        # Mark "already seen today" so _iv_rank's read-only path runs --
        # otherwise every call would also append current_iv, shifting
        # the fixture's own known min/max out from under the test.
        seen_key = f"infusion:option-iv-history-seen:{contract_key}"
        self._sets[seen_key] = {"already-seeded"}

    async def lrange(self, key: str, start: int, end: int) -> list[bytes]:
        items = self._lists.get(key) or []
        stop = len(items) if end == -1 else end + 1
        return [item.encode() for item in items[start:stop]]

    async def sismember(self, key: str, member: str) -> bool:
        return member in (self._sets.get(key) or set())

    async def sadd(self, key: str, member: str) -> None:
        self._sets.setdefault(key, set()).add(member)

    async def lpush(self, key: str, value: str) -> None:
        self._lists.setdefault(key, []).insert(0, value)

    async def ltrim(self, key: str, start: int, end: int) -> None:
        items = self._lists.get(key) or []
        self._lists[key] = items[start : end + 1]

    async def expire(self, key: str, seconds: int) -> None:
        pass


def _row(strike: float, call_iv: float, put_iv: float, call_key: str, put_key: str) -> Any:
    return {
        "strike_price": strike,
        "call_options": {
            "instrument_key": call_key,
            "option_greeks": {"iv": call_iv},
        },
        "put_options": {
            "instrument_key": put_key,
            "option_greeks": {"iv": put_iv},
        },
    }


async def test_blends_call_and_put_when_both_have_enough_history() -> None:
    redis = _FakeRedis()
    # 60 values spanning 10-30; current call iv=25 -> rank 75%, put iv=15 -> rank 25%.
    redis.seed_history("NSE_FO|CALL1", [10.0] * 30 + [30.0] * 30)
    redis.seed_history("NSE_FO|PUT1", [10.0] * 30 + [30.0] * 30)
    rows = [_row(1000.0, 25.0, 15.0, "NSE_FO|CALL1", "NSE_FO|PUT1")]

    rank, history_count = await _blended_atm_iv_rank(redis, rows, spot=1000.0)

    assert rank == 50.0  # (75 + 25) / 2
    assert history_count == 60


async def test_uses_the_single_side_that_has_enough_history() -> None:
    redis = _FakeRedis()
    redis.seed_history("NSE_FO|CALL1", [0.0] * 30 + [100.0] * 30)
    # Put side has no history at all.
    rows = [_row(1000.0, 100.0, 15.0, "NSE_FO|CALL1", "NSE_FO|PUT_NO_HISTORY")]

    rank, history_count = await _blended_atm_iv_rank(redis, rows, spot=1000.0)

    assert rank == 100.0
    assert history_count == 60


async def test_honestly_none_when_neither_side_has_enough_history() -> None:
    redis = _FakeRedis()
    redis.seed_history("NSE_FO|CALL1", [10.0, 20.0])  # only 2 observations
    rows = [_row(1000.0, 25.0, 15.0, "NSE_FO|CALL1", "NSE_FO|PUT1")]

    rank, history_count = await _blended_atm_iv_rank(redis, rows, spot=1000.0)

    assert rank is None
    assert history_count == 2


async def test_picks_the_strike_nearest_spot_as_atm() -> None:
    redis = _FakeRedis()
    # Only the 1000-strike row's call contract is seeded with history --
    # the 900-strike row's contract keys are deliberately never seeded.
    redis.seed_history("NSE_FO|CALL_ATM", [0.0] * 30 + [100.0] * 30)
    rows = [
        _row(900.0, 100.0, 0.0, "NSE_FO|CALL_FAR", "NSE_FO|PUT_FAR"),
        _row(1000.0, 100.0, 0.0, "NSE_FO|CALL_ATM", "NSE_FO|PUT_ATM"),
    ]

    rank, _ = await _blended_atm_iv_rank(redis, rows, spot=1005.0)

    # A wrong ATM pick (900, the unseeded row) would hit contract keys
    # with zero history and return None instead.
    assert rank is not None


async def test_none_and_zero_when_spot_is_unknown() -> None:
    redis = _FakeRedis()
    rank, history_count = await _blended_atm_iv_rank(redis, [], spot=0.0)
    assert rank is None
    assert history_count == 0
