"""Unit tests for api.structure_signal -- "Structure & Breakout Suite"
Phase 1/2 (2026-08-29). Every test below exercises a real pure function
with hand-verified math (bias scoring, trigger selection, candle
confirmation, risk levels, interpretation labels) -- no Redis, no async,
matching this session's own established "hand-trace and verify, don't
guess" discipline. The trendline fixture's real output (swing highs/
lows, trend_state, trendline value) was independently confirmed by
running api.smc_geometry.compute_smc_geometry() against this exact bar
sequence before being encoded here, not predicted from memory.
"""

from __future__ import annotations

from api.smc_geometry import compute_smc_geometry
from api.structure_signal import (
    StructureSignalConfig,
    SubscoreReads,
    Trigger,
    _fast_range_trigger,
    _swing_zone_trigger,
    _trendline_trigger,
    build_interpretation_label,
    candle_confirms,
    compute_dominant_bias,
    compute_risk_levels,
    compute_setup_scores,
)

CONFIG = StructureSignalConfig()


def _bar(
    time: float, open_: float, high: float, low: float, close: float, volume: float = 1000.0
) -> dict[str, float]:
    return {"time": time, "open": open_, "high": high, "low": low, "close": close, "volume": volume}


# ─────────────────────── A. Bias Engine ───────────────────────


def test_bullish_bias_is_selected_when_six_of_seven_conditions_agree() -> None:
    """close=110 > ema200=100, HTF uptrend, Supertrend bullish, RSI 60
    (>=55), no volatility read, RVOL 1.5 (>=1.2), close > VWAP -- 6 of 7
    bullish conditions true (only volatility is false). The mirrored
    bearish read on these SAME numbers only has RVOL agreeing (RVOL
    itself isn't directional), giving bear=1 -- edge of 5 clears the
    default min_bias_edge of 1 easily."""
    reads = SubscoreReads(
        close=110.0,
        ema200=100.0,
        htf_trend_state=1,
        supertrend="BULL",
        rsi14=60.0,
        mfi=None,
        squeeze_readiness=None,
        bb_width_expanding=False,
        rvol=1.5,
        vwap=105.0,
    )
    bull, bear = compute_setup_scores(reads, CONFIG)
    assert (bull, bear) == (6, 1)
    assert compute_dominant_bias(bull, bear, CONFIG) == "BULLISH"


def test_bearish_bias_is_selected_when_six_of_seven_conditions_agree() -> None:
    """Mirror of the bullish case: close=90 < ema200=100, HTF downtrend,
    Supertrend bearish, RSI 40 (<=45), RVOL 1.5, close < VWAP -- 6 of 7
    bearish conditions true, only volatility false. Bull side only gets
    the RVOL agreement -> bull=1, bear=6, edge of 5."""
    reads = SubscoreReads(
        close=90.0,
        ema200=100.0,
        htf_trend_state=-1,
        supertrend="BEAR",
        rsi14=40.0,
        mfi=None,
        squeeze_readiness=None,
        bb_width_expanding=False,
        rvol=1.5,
        vwap=95.0,
    )
    bull, bear = compute_setup_scores(reads, CONFIG)
    assert (bull, bear) == (1, 6)
    assert compute_dominant_bias(bull, bear, CONFIG) == "BEARISH"


def test_no_clear_bias_when_scores_are_tied() -> None:
    """Equal scores on both sides -- edge is 0, below the default
    min_bias_edge of 1 -- must show NO_CLEAR_BIAS regardless of how high
    either raw score is, per the spec's own "avoid trade signal" rule."""
    assert compute_dominant_bias(5, 5, CONFIG) == "NO_CLEAR_BIAS"
    assert compute_dominant_bias(4, 4, CONFIG) == "NO_CLEAR_BIAS"


def test_no_clear_bias_when_scores_are_close_but_not_tied() -> None:
    """bull=4, bear=3: edge is only 1... wait, edge=1 >= min_bias_edge=1
    actually clears the default edge -- this is deliberately the
    boundary case, asserting the real >= semantics rather than a
    stricter >, matching the spec's own "greater than ... by the
    configured bias edge" wording read as >=edge, not >edge."""
    assert compute_dominant_bias(4, 3, CONFIG) == "BULLISH"
    # A genuinely-close case that fails the edge: bull=4, bear=4.
    assert compute_dominant_bias(4, 4, CONFIG) == "NO_CLEAR_BIAS"


# ─────────────────────── B. Breakout Trigger Engine ───────────────────────


def test_fast_range_trigger_buy_above_recent_high_plus_atr_buffer() -> None:
    """12-bar lookback (the default), highs of 100..111, ATR=2.0, buffer
    0.20 -> trigger = 111 + 2.0*0.20 = 111.4."""
    bars = [_bar(i, 95 + i, 100 + i, 95 + i, 98 + i) for i in range(12)]
    trigger = _fast_range_trigger(bars, atr=2.0, bullish=True, config=CONFIG)
    assert trigger == Trigger("BUY_ABOVE", 111.4, "fast_range")


def test_fast_range_trigger_sell_below_recent_low_minus_atr_buffer() -> None:
    """Mirrored: lows descending from 95 down to 84 over 12 bars, ATR=2.0
    -> trigger = 84 - 2.0*0.20 = 83.6."""
    bars = [_bar(i, 100 - i, 105 - i, 95 - i, 98 - i) for i in range(12)]
    trigger = _fast_range_trigger(bars, atr=2.0, bullish=False, config=CONFIG)
    assert trigger == Trigger("SELL_BELOW", 83.6, "fast_range")


def test_fast_range_trigger_is_none_with_too_few_bars() -> None:
    bars = [_bar(i, 100, 101, 99, 100) for i in range(5)]
    assert _fast_range_trigger(bars, atr=2.0, bullish=True, config=CONFIG) is None


# Shared fixture for swing_zone + trendline tests: a real, hand-verified
# downtrend -- run once through the real compute_smc_geometry() before
# writing these assertions (not predicted from memory). Confirmed real
# output: trend_state=-1, swing_high_1=105.0, swing_high_2=110.0,
# swing_low_1=70.0, swing_low_2=80.0, one bearish trendline from
# (t=120, 110.0) to (t=720, 101.67).
_DOWNTREND_PRICES = [
    (95, 97, 94, 96),
    (96, 99, 95, 98),
    (98, 110, 97, 100),  # peak A -> swing_high_2
    (100, 102, 90, 95),
    (95, 96, 85, 90),
    (90, 93, 80, 85),  # trough A
    (85, 90, 83, 88),
    (88, 95, 86, 92),
    (92, 105, 91, 100),  # peak B -> swing_high_1 (lower high)
    (100, 103, 95, 98),
    (98, 99, 70, 75),  # bearish break of trough A + trough B candidate
    (75, 80, 72, 76),
    (76, 82, 74, 78),
]
_DOWNTREND_BARS = [
    {"time": i * 60, "open": o, "high": h, "low": low, "close": c, "volume": 1000.0}
    for i, (o, h, low, c) in enumerate(_DOWNTREND_PRICES)
]


def test_smc_geometry_fixture_is_the_real_confirmed_downtrend() -> None:
    """Guards the shared fixture above -- if api.smc_geometry's own real
    behavior ever changes, this fails LOUDLY here instead of the swing/
    trendline tests below failing for a confusing, indirect reason."""
    geo = compute_smc_geometry(_DOWNTREND_BARS)
    assert geo["trend_state"] == -1
    assert geo["swing_high_1"] == 105.0
    assert geo["swing_high_2"] == 110.0
    assert geo["swing_low_1"] == 70.0
    assert geo["trendlines"] == [
        {
            "direction": "bearish",
            "points": [{"time": 120, "value": 110.0}, {"time": 720, "value": 101.67}],
        }
    ]


def test_swing_zone_trigger_uses_the_real_confirmed_pivot_levels() -> None:
    geo = compute_smc_geometry(_DOWNTREND_BARS)
    buy_trigger = _swing_zone_trigger(geo, atr=1.0, bullish=True, config=CONFIG)
    assert buy_trigger == Trigger("BUY_ABOVE", round(105.0 + 1.0 * 0.20, 2), "swing_zone")
    sell_trigger = _swing_zone_trigger(geo, atr=1.0, bullish=False, config=CONFIG)
    assert sell_trigger == Trigger("SELL_BELOW", round(70.0 - 1.0 * 0.20, 2), "swing_zone")


def test_trendline_trigger_is_valid_while_price_has_not_crossed_it() -> None:
    """The real bearish (resistance) trendline sits at 101.67 on the
    final bar. Price at 95 hasn't traded up through it yet -- a real,
    still-actionable BUY_ABOVE trigger."""
    geo = compute_smc_geometry(_DOWNTREND_BARS)
    trigger = _trendline_trigger(geo, ltp=95.0, bullish=True)
    assert trigger == Trigger("BUY_ABOVE", 101.67, "trendline")


def test_trendline_trigger_is_stale_once_price_has_already_crossed_it() -> None:
    """Same real trendline, same geometry -- but price at 103 has
    already traded above the 101.67 resistance line. Per the spec's own
    "do not keep using stale trendline triggers after price has already
    crossed them" rule, this must NOT be offered as a trigger anymore."""
    geo = compute_smc_geometry(_DOWNTREND_BARS)
    trigger = _trendline_trigger(geo, ltp=103.0, bullish=True)
    assert trigger is None


def test_trendline_trigger_does_not_offer_the_wrong_direction() -> None:
    """This fixture's only real trendline is bearish (a resistance line,
    for a BUY trigger) -- there is no real ascending support line yet,
    so a SELL_BELOW trendline trigger must honestly be None, not
    fabricated from the one real line that exists for the other side."""
    geo = compute_smc_geometry(_DOWNTREND_BARS)
    assert _trendline_trigger(geo, ltp=95.0, bullish=False) is None


# ─────────────────────── C. Candle Structure Confirmation ───────────────────────


def test_bullish_candle_confirmation_passes_a_real_strong_breakout_candle() -> None:
    """Range 10 (95-105), close at 104 -> close_location = 9/10 = 0.90
    (>=0.75), body = |104-96|/10 = 0.80 (>=0.45), close(104) > open(96),
    and close(104) > trigger(100)."""
    bar = _bar(0, open_=96, high=105, low=95, close=104)
    assert candle_confirms(bar, trigger_price=100.0, bullish=True, config=CONFIG) is True


def test_bullish_candle_confirmation_fails_a_weak_indecisive_body() -> None:
    """Same range and close location, but open is right next to close --
    body ratio 0.05, well under the 0.45 floor."""
    bar = _bar(0, open_=103, high=105, low=95, close=104)
    assert candle_confirms(bar, trigger_price=100.0, bullish=True, config=CONFIG) is False


def test_bearish_candle_confirmation_passes_a_real_strong_breakdown_candle() -> None:
    """Range 10 (95-105), close at 96 -> close_location = 1/10 = 0.10
    (<=0.25), body = |96-104|/10 = 0.80, close(96) < open(104), and
    close(96) < trigger(100)."""
    bar = _bar(0, open_=104, high=105, low=95, close=96)
    assert candle_confirms(bar, trigger_price=100.0, bullish=False, config=CONFIG) is True


def test_bearish_candle_confirmation_fails_when_close_is_above_trigger() -> None:
    """A real strong-bodied down candle, but it never actually closed
    below the trigger price -- must not confirm."""
    bar = _bar(0, open_=104, high=105, low=95, close=101)
    assert candle_confirms(bar, trigger_price=100.0, bullish=False, config=CONFIG) is False


# ─────────────────────── E. Risk Engine ───────────────────────


def test_strict_sl_uses_structure_support_when_it_is_tighter_than_the_atr_stop() -> None:
    """entry=100, atr=2.0, strict_stop_max_atr=1.15 -> ATR stop at
    100 - 2.0*1.15 = 97.7. Structure support at 98 is ABOVE (tighter
    than) that -- SL must be the tighter 98, per max(support, atr_stop)."""
    risk = compute_risk_levels(
        entry=100.0, structure_level=98.0, atr=2.0, bullish=True, config=CONFIG
    )
    assert risk is not None
    assert risk.sl == 98.0
    assert risk.risk_per_share == 2.0
    assert (risk.tp1, risk.tp2, risk.tp3) == (103.0, 105.0, 107.0)


def test_strict_sl_falls_back_to_the_atr_cap_when_structure_is_further_away() -> None:
    """Same entry/ATR, but structure support at 95 is BELOW (further
    from entry than) the 97.7 ATR cap -- the ATR cap wins, since real
    structure may only ever tighten the stop, never widen it."""
    risk = compute_risk_levels(
        entry=100.0, structure_level=95.0, atr=2.0, bullish=True, config=CONFIG
    )
    assert risk is not None
    assert risk.sl == 97.7
    assert risk.risk_per_share == round(100.0 - 97.7, 2)
    assert (risk.tp1, risk.tp2, risk.tp3) == (103.45, 105.75, 108.05)


def test_strict_sl_mirrors_correctly_for_a_short_setup() -> None:
    """entry=100, atr=2.0 -> ATR stop at 100+2.3=102.3. Structure
    resistance at 101 is tighter (closer to entry) -- SL = min(101,
    102.3) = 101, risk=1, targets subtract R multiples from entry."""
    risk = compute_risk_levels(
        entry=100.0, structure_level=101.0, atr=2.0, bullish=False, config=CONFIG
    )
    assert risk is not None
    assert risk.sl == 101.0
    assert risk.risk_per_share == 1.0
    assert (risk.tp1, risk.tp2, risk.tp3) == (98.5, 97.5, 96.5)


def test_risk_levels_is_none_when_structure_sits_on_the_wrong_side_of_entry() -> None:
    """A long setup whose "support" is actually above entry would
    produce zero or negative risk -- must return None, never a
    fabricated/inverted stop."""
    risk = compute_risk_levels(
        entry=100.0, structure_level=101.0, atr=2.0, bullish=True, config=CONFIG
    )
    assert risk is None


# ─────────────────────── D. Visual interpretation label ───────────────────────


def test_interpretation_label_matches_the_spec_own_bullish_example() -> None:
    label = build_interpretation_label(
        dominant_bias="BULLISH",
        trigger=Trigger("BUY_ABOVE", 3948.50, "fast_range"),
        bull_score=6,
        bear_score=3,
        momentum_watch=True,
    )
    assert label == [
        "BUY SIDE ONLY",
        "Go above 3948.50",
        "Quality B:6/7 S:3/7",
        "Watch BUY Momentum",
    ]


def test_interpretation_label_says_breakout_confirmed_once_the_candle_has_confirmed() -> None:
    """Caught live: a real armed AXISBANK signal on 2026-08-29 had
    candle_confirmed=True but the label still said "Awaiting
    confirmation candle" -- factually wrong once the candle already
    confirmed. Fixed by giving this state its own real text rather than
    falling through the momentum_watch/not-yet-confirmed branches the
    spec's own three examples never actually covered."""
    label = build_interpretation_label(
        dominant_bias="BULLISH",
        trigger=Trigger("BUY_ABOVE", 1260.07, "swing_zone"),
        bull_score=6,
        bear_score=2,
        momentum_watch=False,
        candle_confirmed=True,
    )
    assert label == [
        "BUY SIDE ONLY",
        "Go above 1260.07",
        "Quality B:6/7 S:2/7",
        "Breakout Confirmed",
    ]


def test_interpretation_label_matches_the_spec_own_bearish_example() -> None:
    label = build_interpretation_label(
        dominant_bias="BEARISH",
        trigger=Trigger("SELL_BELOW", 3928.20, "fast_range"),
        bull_score=2,
        bear_score=6,
        momentum_watch=True,
    )
    assert label == [
        "SELL SIDE ONLY",
        "Go below 3928.20",
        "Quality B:2/7 S:6/7",
        "Watch SELL Momentum",
    ]


def test_interpretation_label_matches_the_spec_own_wait_example() -> None:
    label = build_interpretation_label(
        dominant_bias="NO_CLEAR_BIAS",
        trigger=None,
        bull_score=4,
        bear_score=4,
        momentum_watch=False,
    )
    assert label == [
        "WAIT",
        "No clean breakout level",
        "Quality B:4/7 S:4/7",
        "No Clear Bias",
    ]
