"""Live-session bug fix (2026-08-26, the morning after Aug expiry):
current_month_contract() used to trust the cached instruments master
to have already dropped an expired contract, and just returned
contracts[0]. Verified live that assumption is false -- the just-
expired contract was still contracts[0], and Upstox's Full Market
Quote endpoint returns `{"data": {}}` for it, which silently zeroed
out oi_buildup for all 208 F&O underlyings for the whole session. See
api/futures.py's own current_month_contract() docstring for the full
live-verification trail.
"""

from __future__ import annotations

import time

from api.futures import current_month_contract


def _contract(expiry_ms: int, symbol: str) -> dict[str, object]:
    return {
        "instrument_key": f"NSE_FO|{symbol}",
        "trading_symbol": symbol,
        "expiry": expiry_ms,
        "lot_size": 30,
    }


def test_skips_an_already_expired_front_month_contract() -> None:
    """The exact live scenario: the nearest-sorted contract expired
    yesterday and is still contracts[0]; the next month's contract
    hasn't expired and must be selected instead."""
    now_ms = int(time.time() * 1000)
    expired = _contract(now_ms - 12 * 3600 * 1000, "BANKNIFTY26AUGFUT")
    next_month = _contract(now_ms + 30 * 24 * 3600 * 1000, "BANKNIFTY26SEPFUT")
    far_month = _contract(now_ms + 60 * 24 * 3600 * 1000, "BANKNIFTY26OCTFUT")

    picked = current_month_contract([expired, next_month, far_month])

    assert picked is not None
    assert picked["trading_symbol"] == "BANKNIFTY26SEPFUT"


def test_picks_nearest_when_front_month_still_live() -> None:
    """Normal mid-cycle case: contracts[0] hasn't expired yet, so it's
    still the right pick -- this fix must not change that path."""
    now_ms = int(time.time() * 1000)
    front = _contract(now_ms + 5 * 24 * 3600 * 1000, "BANKNIFTY26AUGFUT")
    next_month = _contract(now_ms + 35 * 24 * 3600 * 1000, "BANKNIFTY26SEPFUT")

    picked = current_month_contract([front, next_month])

    assert picked is not None
    assert picked["trading_symbol"] == "BANKNIFTY26AUGFUT"


def test_falls_back_to_nearest_when_every_contract_is_expired() -> None:
    """Edge case: the master cache hasn't refreshed yet on a brand-new
    expiry day and every listed contract is stale. Better to return
    the nearest one (matches the pre-fix behavior) than None -- the
    quote fetch will just come back empty, same as any other day the
    master lags reality, rather than dropping the underlying from the
    sweep entirely."""
    now_ms = int(time.time() * 1000)
    only = _contract(now_ms - 3600 * 1000, "BANKNIFTY26AUGFUT")

    picked = current_month_contract([only])

    assert picked is not None
    assert picked["trading_symbol"] == "BANKNIFTY26AUGFUT"


def test_empty_contract_list_returns_none() -> None:
    assert current_month_contract([]) is None


def test_ignores_a_contract_with_a_non_numeric_expiry() -> None:
    """Defensive: fetch_futures_master() only ever emits numeric epoch-ms
    expiries, but the isinstance guard should skip anything malformed
    rather than crash the comparison."""
    now_ms = int(time.time() * 1000)
    malformed = _contract("", "BROKEN")  # type: ignore[arg-type]
    valid = _contract(now_ms + 10 * 24 * 3600 * 1000, "BANKNIFTY26SEPFUT")

    picked = current_month_contract([malformed, valid])

    assert picked is not None
    assert picked["trading_symbol"] == "BANKNIFTY26SEPFUT"
