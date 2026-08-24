"""Volume features — OBV, relative volume, volume SMA."""

from feature_engine.state import SymbolState


def update_obv(state: SymbolState, close: float, volume: int) -> None:
    """On-Balance Volume -- incremental."""
    if state.obv_prev_close == 0:
        state.obv_prev_close = close
        return

    if close > state.obv_prev_close:
        state.obv += volume
    elif close < state.obv_prev_close:
        state.obv -= volume
    # close == prev: OBV unchanged

    state.obv_prev_close = close


def get_relative_volume(state: SymbolState) -> float:
    """Cumulative session volume / 20-session average at the same minute."""
    if not state.volume_profile_ready or state.last_tick_exchange_ms <= 0:
        return 0.0
    # NSE session starts at 09:15 IST. Epoch time is converted by adding IST.
    minute_of_day = ((state.last_tick_exchange_ms // 60000) + 330) % 1440
    session_minute = max(0, minute_of_day - (9 * 60 + 15))
    expected = state.volume_profile.get(session_minute, 0.0)
    if expected <= 0:
        return 0.0
    return state.session_cumulative_volume / expected


def get_volume_sma(state: SymbolState) -> float:
    if not state.volume_history:
        return 0.0
    return sum(state.volume_history) / len(state.volume_history)
