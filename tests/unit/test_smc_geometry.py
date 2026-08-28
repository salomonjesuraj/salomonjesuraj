"""Unit tests for api.smc_geometry.compute_smc_geometry -- "Institutional
Chart Overlay" sprint (2026-08-28).

The bar sequence in test_full_bullish_scenario_detects_bos_sweep_ob_and_targets
is hand-constructed and traced bar-by-bar against the real rules this
module mirrors (feature_engine.features.structure.update_structure /
features.ict.update_ict) so every asserted value is a hand-verified
consequence of those real rules, not a guessed expectation. See the
inline comments for the pivot-window arithmetic at each bar.
"""

from __future__ import annotations

import api.smc_geometry as smc_geometry
from api.smc_geometry import compute_smc_geometry


def _bar(t: int, o: float, h: float, lo: float, c: float) -> dict[str, float | int]:
    return {"time": t, "open": o, "high": h, "low": lo, "close": c, "volume": 1000}


# Bars 0-7: establish swing_low_1=95 (confirmed at bar 4, from bar 2's
# low) and swing_high_1=120 (confirmed at bar 7, from bar 5's high),
# with no break yet -- trend_state stays 0 (RANGE) through bar 7.
_RANGE_BARS = [
    _bar(1000, 105, 110, 100, 108),  # 0
    _bar(1060, 108, 112, 101, 110),  # 1
    _bar(1120, 110, 111, 95, 108),  # 2 -- swing-low candidate (low=95)
    _bar(1180, 108, 113, 102, 111),  # 3
    _bar(1240, 111, 114, 103, 112),  # 4 -- confirms bar 2 as swing_low_1=95
    _bar(1300, 112, 120, 104, 115),  # 5 -- swing-high candidate (high=120)
    _bar(1360, 115, 115, 105, 113),  # 6
    _bar(1420, 113, 116, 106, 114),  # 7 -- confirms bar 5 as swing_high_1=120
]

# Bars 8-10: a real sellside liquidity sweep + Order Block + Bullish BOS.
_IMPULSE_BARS = [
    # Wicks below swing_low_1 (95) but closes back above it (97) on a
    # down candle (97 < 100) -- sellside sweep + bullish OB candidate
    # (low=90, high=101).
    _bar(1480, 100, 101, 90, 97),  # 8
    # Holds the OB candidate pending: close (95) is neither above its
    # high (101, would validate) nor below its low (90, would fail it).
    _bar(1540, 97, 102, 91, 95),  # 9
    # Impulse: closes above the OB's high (101) -> validates it; closes
    # above swing_high_1 (120) with ATR still 0 during warmup (fewer
    # than the 14 true ranges needed) -> zero break-buffer -> fires
    # "Bullish BOS" (trend_state was 0, not -1, so BOS not CHOCH). Bar
    # 8's own low (90) is also confirmed as the new swing_low_1 by this
    # bar's own pivot window (bars 6-10), shifting the old 95 into
    # swing_low_2.
    _bar(1600, 100, 125, 99, 124),  # 10
]


def test_not_ready_with_fewer_bars_than_the_pivot_window_needs() -> None:
    result = compute_smc_geometry(_RANGE_BARS[:3])
    assert result["ready"] is False
    assert "5" in result["reason"]


def test_range_state_never_fabricates_a_directional_target_zone() -> None:
    """Swing points are both known by bar 7, but no break has fired yet
    -- trend_state is honestly 0 (RANGE), so target_zones must be
    None/None, not a guessed direction."""
    result = compute_smc_geometry(_RANGE_BARS)
    assert result["ready"] is True
    assert result["trend_state"] == 0
    assert result["swing_high_1"] == 120
    assert result["swing_low_1"] == 95
    assert result["target_zones"] == {"t2": None, "t3": None, "direction": None}
    assert result["bos_choch_events"] == []
    assert result["order_block_bullish"] is None
    # Both swing lows are known here (95 at bar 2, and only one so far --
    # swing_low_2 is still None) -- a real trendline needs TWO points on
    # the relevant side; RANGE state alone already rules it out honestly.
    assert result["trendlines"] == []


def test_full_bullish_scenario_detects_bos_sweep_ob_and_targets() -> None:
    bars = _RANGE_BARS + _IMPULSE_BARS
    result = compute_smc_geometry(bars)

    assert result["ready"] is True
    assert result["trend_state"] == 1
    assert result["trend_text"] == "UPTREND (HH/HL)"

    # Bar 8's own low (90) became the new confirmed swing_low_1 at bar
    # 10's own pivot check; the old 95 shifted into swing_low_2.
    assert result["swing_high_1"] == 120
    assert result["swing_low_1"] == 90
    assert result["swing_low_2"] == 95

    assert result["bos_choch_events"] == [
        {"time": 1600, "price": 120, "label": "Bullish BOS", "direction": "bullish"}
    ]
    assert result["liquidity_sweeps"] == [{"time": 1480, "side": "sellside", "price": 95}]

    assert result["order_block_bullish"] == {"low": 90, "high": 101, "validated": True}
    assert result["order_block_bearish"] is None

    # 1.618 / 2.618 extension of the final swing range (90 -> 120).
    target_zones = result["target_zones"]
    assert target_zones["direction"] == "bullish"
    assert target_zones["t2"] == round(90 + 30 * 1.618, 2)
    assert target_zones["t3"] == round(90 + 30 * 2.618, 2)

    # "TradingView Parity" sprint (2026-08-29): ascending trendline
    # through the two most recent real confirmed swing LOWS -- bar 2's
    # 95 (t=1120, the older, now swing_low_2) and bar 8's 90 (t=1480,
    # confirmed as the new swing_low_1 by bar 10's own pivot check),
    # projected forward to the final bar (t=1600).
    assert result["trendlines"] == [
        {
            "direction": "bullish",
            "points": [
                {"time": 1120, "value": 95.0},
                {
                    "time": 1600,
                    "value": round(95.0 + (90.0 - 95.0) / (1480 - 1120) * (1600 - 1120), 2),
                },
            ],
        }
    ]


# Continues _IMPULSE_BARS with a genuine reversal: price holds near the
# breakout (11-13, none of which disturb swing_low_1=90) then crashes
# (14), closing well below swing_low_1 -- a second real, distinct break
# event ("Bearish CHOCH", since trend_state is 1 going in).
_REVERSAL_BARS = [
    _bar(1660, 118, 119, 100, 115),  # 11
    _bar(1720, 115, 116, 101, 116),  # 12
    _bar(1780, 116, 117, 102, 117),  # 13
    _bar(1840, 115, 116, 75, 80),  # 14 -- crashes below swing_low_1 (90)
]


def test_two_real_break_events_both_appear_uncapped() -> None:
    """Baseline for the capping test below -- confirms this bar sequence
    really does produce two distinct, real events before any capping is
    involved."""
    result = compute_smc_geometry(_RANGE_BARS + _IMPULSE_BARS + _REVERSAL_BARS)
    assert result["trend_state"] == -1
    assert [e["label"] for e in result["bos_choch_events"]] == ["Bullish BOS", "Bearish CHOCH"]


def test_bearish_trendline_uses_the_two_most_recent_swing_highs() -> None:
    """Mirror of the bullish trendline case: once the reversal flips
    trend_state to -1, the relevant side becomes swing HIGHS -- bar 5's
    120 (t=1300, the older, now swing_high_2) and bar 10's 125 (t=1600,
    confirmed as the new swing_high_1), projected to the final bar
    (t=1840)."""
    result = compute_smc_geometry(_RANGE_BARS + _IMPULSE_BARS + _REVERSAL_BARS)
    assert result["swing_high_1"] == 125
    assert result["swing_high_2"] == 120
    assert result["trendlines"] == [
        {
            "direction": "bearish",
            "points": [
                {"time": 1300, "value": 120.0},
                {
                    "time": 1840,
                    "value": round(120.0 + (125.0 - 120.0) / (1600 - 1300) * (1840 - 1300), 2),
                },
            ],
        }
    ]


def test_event_lists_are_capped_to_the_most_recent_max_events(monkeypatch) -> None:
    """Payload/marker-clutter cap -- see MAX_EVENTS's own module-level
    comment. With the cap lowered to 1, only the more recent of the two
    real events from the baseline above should survive."""
    monkeypatch.setattr(smc_geometry, "MAX_EVENTS", 1)
    result = compute_smc_geometry(_RANGE_BARS + _IMPULSE_BARS + _REVERSAL_BARS)
    assert [e["label"] for e in result["bos_choch_events"]] == ["Bearish CHOCH"]
