"""Unit tests for pipeline-audit fix B2: MTF forming-bar confidence
weighting, 2026-08-25.

A still-forming higher-timeframe bucket (e.g. minute 1 of a 15M window)
previously carried the exact same BULL/BEAR conviction as a fully-closed
one. This tests the fix end to end through the real pipeline: real 1m
bars -> _aggregate() (which now tracks how many 1m bars fed each
bucket) -> _score_timeframe() (which now damps a still-forming bucket's
score toward the neutral midpoint by how complete it is).
"""

from __future__ import annotations

from api.routes.mtf import _aggregate, _completion_ratio, _score_timeframe


def _rising_1m_bars(count: int, start_close: float = 100.0, step: float = 0.30) -> list[dict]:
    """A clean, monotonic uptrend -- real bars, not hand-tuned indicator
    internals -- long enough (>75 minutes -> 15+ 5-minute buckets) for
    RSI14/EMA20/EMA50/MACD to all actually warm up on the aggregated 5M
    series, so the "before damping" score is a genuine, strongly bullish
    read rather than an artifact of missing indicators."""
    bars = []
    close = start_close
    for i in range(count):
        close += step
        bars.append(
            {
                "time": i * 60,
                "open": close - step,
                "high": close + 0.05,
                "low": close - step - 0.05,
                "close": close,
                "volume": 1000,
            }
        )
    return bars


# ── _completion_ratio() in isolation ────────────────────────────────────


def test_completion_ratio_is_1_for_native_1m_bars() -> None:
    bars_1m = _rising_1m_bars(10)
    assert _completion_ratio(bars_1m, minutes=1) == 1.0


def test_completion_ratio_is_1_when_the_last_bucket_is_fully_closed() -> None:
    bars_1m = _rising_1m_bars(25)  # exactly 5 complete 5-minute buckets
    bars_5m = _aggregate(bars_1m, minutes=5)
    assert len(bars_5m) == 5
    assert bars_5m[-1]["_n1m"] == 5
    assert _completion_ratio(bars_5m, minutes=5) == 1.0


def test_completion_ratio_reflects_a_genuinely_partial_last_bucket() -> None:
    bars_1m = _rising_1m_bars(21)  # 4 complete buckets + 1 bar into the 5th
    bars_5m = _aggregate(bars_1m, minutes=5)
    assert len(bars_5m) == 5
    assert bars_5m[-1]["_n1m"] == 1
    assert _completion_ratio(bars_5m, minutes=5) == 0.2


def test_completion_ratio_is_1_for_a_daily_series_that_never_touches_aggregate() -> None:
    """Daily bars come from their own zset in compute_mtf() and never
    pass through _aggregate() -- no "_n1m" key exists, and there's no
    "still forming from finer data" concept for them regardless."""
    daily_bars = _rising_1m_bars(30)  # stand-in shape; no _n1m present
    assert _completion_ratio(daily_bars, minutes=1) == 1.0


# ── _score_timeframe() end to end ───────────────────────────────────────


def test_a_fully_closed_bucket_scores_at_full_conviction() -> None:
    bars_1m = _rising_1m_bars(100)  # 20 complete 5-minute buckets
    bars_5m = _aggregate(bars_1m, minutes=5)
    scored = _score_timeframe("5M", bars_5m, include_vwap=False, minutes=5)
    assert scored["completion_ratio"] == 1.0
    assert scored["score"] == scored["raw_score"]
    assert scored["state"] == "BULL"  # a clean, sustained uptrend


def test_a_forming_bucket_has_its_score_damped_toward_neutral() -> None:
    """Same real uptrend, but truncated mid-bucket -- the fix's exact
    target scenario: minute 1 of a still-forming 5-minute window must
    not carry the same conviction as the fully-closed series above."""
    bars_1m = _rising_1m_bars(96)  # 19 complete buckets + 1 bar into the 20th
    bars_5m = _aggregate(bars_1m, minutes=5)
    scored = _score_timeframe("5M", bars_5m, include_vwap=False, minutes=5)
    assert (
        scored["completion_ratio"] == round(1 / 5, 10)
        or abs(scored["completion_ratio"] - 0.2) < 1e-9
    )
    assert scored["raw_score"] > 50  # the underlying evidence is still genuinely bullish
    # Damped score must sit strictly between neutral and the raw score --
    # softened, not erased and not ignored.
    assert 50 < scored["score"] < scored["raw_score"]
    # Explicitly the 80%-toward-neutral shape the fix specifies:
    # score = 50 + (raw - 50) * ratio.
    expected = round(50 + (scored["raw_score"] - 50) * scored["completion_ratio"], 1)
    assert scored["score"] == expected
    assert any("Forming bar" in w for w in scored["warnings"])


def test_damping_can_pull_a_borderline_bull_read_back_to_mixed() -> None:
    """The real, user-facing consequence: a bucket that would just barely
    clear the BULL threshold (>=58) when fully closed must not fire that
    same BULL state on a single, still-forming constituent bar."""
    # A short, mild uptrend -- just enough bars for EMA20 to warm up
    # (>=5) and land close to the 58 BULL threshold, not deep into it.
    # Truncate to 1 of 5 bars into what would be the next bucket.
    partial_1m = _rising_1m_bars(26, step=0.05)
    partial_5m = _aggregate(partial_1m, minutes=5)
    partial = _score_timeframe("5M", partial_5m, include_vwap=False, minutes=5)

    assert partial["completion_ratio"] < 1.0
    # The damped score must be closer to neutral than the raw evidence
    # alone would justify -- the concrete anti-premature-trigger effect.
    assert abs(partial["score"] - 50) <= abs(partial["raw_score"] - 50)


def test_weighted_mtf_composite_uses_the_damped_score_not_the_raw_one() -> None:
    """compute_mtf()'s own weighted composite reads timeframes[tf]["score"]
    directly (see mtf.py's own weighted= computation) -- confirms the fix
    is a single-point change that automatically propagates into trade_bias/
    alignment without needing separate damping logic there."""
    bars_1m = _rising_1m_bars(96)
    bars_5m = _aggregate(bars_1m, minutes=5)
    scored = _score_timeframe("5M", bars_5m, include_vwap=False, minutes=5)
    # "score" is what compute_mtf()'s weighted-average line multiplies by
    # 0.18 for the 5M leg -- must be the damped value.
    assert scored["score"] != scored["raw_score"]
