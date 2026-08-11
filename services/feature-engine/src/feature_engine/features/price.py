"""Price features — VWAP, EMAs, gap%, change%."""

import math

from feature_engine.state import SymbolState

# Minimum completed 1m bars before a session VWAP SD read is considered
# statistically meaningful rather than an early-session artifact (a single
# large print in the first minute can otherwise make variance look huge or
# near-zero) -- same "refuses to guess until enough data" discipline as IV
# Rank's 60-observation floor elsewhere in this codebase.
VWAP_SD_MIN_BARS = 5


def update_price_features(state: SymbolState, ltp: float, volume: int):
    """Update live session price features (day range and VWAP)."""

    # Day high/low
    state.day_high = max(state.day_high, ltp)
    if state.day_low == float("inf"):
        state.day_low = ltp
    else:
        state.day_low = min(state.day_low, ltp)

    # VWAP (incremental)
    if volume > state.vwap_denominator:
        delta_vol = volume - state.vwap_denominator
        state.vwap_numerator += ltp * delta_vol
        state.vwap_sq_numerator += (ltp * ltp) * delta_vol
        state.vwap_denominator = volume

def update_ema_features(state: SymbolState, close: float):
    """Advance EMAs once per completed candle."""
    for period in [5, 9, 20, 50]:
        if not state.ema_initialized.get(period, False):
            state.ema[period] = close
            state.ema_initialized[period] = True
        else:
            k = 2 / (period + 1)
            state.ema[period] = close * k + state.ema[period] * (1 - k)


def get_vwap(state: SymbolState) -> float:
    if state.vwap_denominator > 0:
        return state.vwap_numerator / state.vwap_denominator
    return state.ltp


def get_vwap_sd_bands(state: SymbolState) -> dict:
    """Session VWAP +/- 1 and 2 standard-deviation bands (volume-weighted
    variance, not a simple price stdev) -- explicit mean-reversion targets
    for vol_vwap_breakout and any strategy currently only checking
    above/below VWAP with no notion of "how stretched."

    variance = E[price^2] - E[price]^2, both volume-weighted, computed from
    the same incremental accumulators as VWAP itself so no separate rolling
    window is needed. Informational only (features_snapshot), same
    "not wired into live scoring until feature-ablation earns it" governance
    as every other Phase 1-10/13.x field.
    """
    denom = state.vwap_denominator
    ready = denom > 0 and state.completed_1m_bars >= VWAP_SD_MIN_BARS
    if denom <= 0:
        return {
            "vwap_stdev": None, "vwap_sd1_upper": None, "vwap_sd1_lower": None,
            "vwap_sd2_upper": None, "vwap_sd2_lower": None, "vwap_sd_ready": False,
        }

    vwap = state.vwap_numerator / denom
    variance = (state.vwap_sq_numerator / denom) - (vwap * vwap)
    # Floating-point accumulation can nudge a near-zero variance slightly
    # negative -- clamp rather than let sqrt raise.
    stdev = math.sqrt(variance) if variance > 0 else 0.0

    return {
        "vwap_stdev": round(stdev, 4),
        "vwap_sd1_upper": round(vwap + stdev, 2),
        "vwap_sd1_lower": round(vwap - stdev, 2),
        "vwap_sd2_upper": round(vwap + 2 * stdev, 2),
        "vwap_sd2_lower": round(vwap - 2 * stdev, 2),
        "vwap_sd_ready": ready,
    }


def get_gap_pct(state: SymbolState) -> float:
    if state.prev_close > 0 and state.day_open > 0:
        return (state.day_open - state.prev_close) / state.prev_close * 100
    return 0.0


def get_change_pct(state: SymbolState) -> float:
    if state.prev_close > 0:
        return (state.ltp - state.prev_close) / state.prev_close * 100
    return 0.0
