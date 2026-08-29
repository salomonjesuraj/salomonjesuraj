"""Unit tests for api.screener_hydrator's pure Squeeze Readiness / RVOL
calculators -- "Full Universe Batch Hydration Engine" sprint
(2026-08-29). The bulk Redis-pipeline hydration functions themselves
are real I/O this suite has no business calling; only the deterministic
math built on top of real daily bars is covered here.
"""

from __future__ import annotations

from api.screener_hydrator import compute_rvol, compute_squeeze_readiness


def _bar(high: float, low: float, close: float, volume: float = 1000.0) -> dict[str, float]:
    return {"time": 0.0, "high": high, "low": low, "close": close, "volume": volume}


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
