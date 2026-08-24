"""Volatility features — ATR, Bollinger Bands."""

import math

from feature_engine.state import SymbolState


def update_atr(state: SymbolState, high: float, low: float, close: float, period: int = 14) -> None:
    """Average True Range -- incremental."""
    if state.atr_prev_close == 0:
        state.atr_prev_close = close
        return

    tr = max(
        high - low,
        abs(high - state.atr_prev_close),
        abs(low - state.atr_prev_close),
    )
    state.atr_prev_close = close

    state.atr_values.append(tr)
    if len(state.atr_values) >= period:
        if state.atr == 0:
            state.atr = sum(state.atr_values) / len(state.atr_values)
        else:
            state.atr = (state.atr * (period - 1) + tr) / period


def update_bollinger(state: SymbolState, close: float) -> None:
    """Bollinger Band prices buffer."""
    state.bb_prices.append(close)


def get_bollinger(
    state: SymbolState, period: int = 20, num_std: float = 2.0
) -> tuple[float, float, float]:
    """Returns (upper, lower, width)."""
    if len(state.bb_prices) < 2:
        return state.ltp * 1.02, state.ltp * 0.98, 0.04

    prices = list(state.bb_prices)
    mean = sum(prices) / len(prices)
    variance = sum((p - mean) ** 2 for p in prices) / len(prices)
    std = math.sqrt(variance) if variance > 0 else 0

    upper = mean + num_std * std
    lower = mean - num_std * std
    width = (upper - lower) / mean if mean > 0 else 0

    return upper, lower, width


def update_supertrend(
    state: SymbolState, high: float, low: float, close: float, atr: float, factor: float = 3.0
) -> None:
    """ATR-band flip Supertrend — matches Pine's `ta.supertrend(factor, atrLen)`.

    Reuses the existing `state.atr` (same ATR the rest of the engine already
    computes) rather than maintaining a second ATR series.
    """
    if atr <= 0:
        return
    mid = (high + low) / 2.0
    basic_upper = mid + factor * atr
    basic_lower = mid - factor * atr

    if not state.st_initialized:
        state.st_final_upper = basic_upper
        state.st_final_lower = basic_lower
        state.st_bullish = close >= basic_lower
        state.st_prev_close = close
        state.st_initialized = True
        return

    final_upper = (
        basic_upper
        if (basic_upper < state.st_final_upper or state.st_prev_close > state.st_final_upper)
        else state.st_final_upper
    )
    final_lower = (
        basic_lower
        if (basic_lower > state.st_final_lower or state.st_prev_close < state.st_final_lower)
        else state.st_final_lower
    )

    bullish = (close >= final_lower) if state.st_bullish else (close > final_upper)

    state.st_final_upper = final_upper
    state.st_final_lower = final_lower
    state.st_bullish = bullish
    state.st_prev_close = close


def get_supertrend(state: SymbolState) -> tuple[float, bool]:
    """Returns (supertrend_level, is_bullish)."""
    level = state.st_final_lower if state.st_bullish else state.st_final_upper
    return level, state.st_bullish
