"""Price features — VWAP, EMAs, gap%, change%."""

from feature_engine.state import SymbolState


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


def get_gap_pct(state: SymbolState) -> float:
    if state.prev_close > 0 and state.day_open > 0:
        return (state.day_open - state.prev_close) / state.prev_close * 100
    return 0.0


def get_change_pct(state: SymbolState) -> float:
    if state.prev_close > 0:
        return (state.ltp - state.prev_close) / state.prev_close * 100
    return 0.0
