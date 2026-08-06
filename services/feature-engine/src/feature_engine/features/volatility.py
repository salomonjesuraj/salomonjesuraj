"""Volatility features — ATR, Bollinger Bands."""

import math

from feature_engine.state import SymbolState


def update_atr(state: SymbolState, high: float, low: float, close: float, period: int = 14):
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


def update_bollinger(state: SymbolState, close: float):
    """Bollinger Band prices buffer."""
    state.bb_prices.append(close)


def get_bollinger(state: SymbolState, period: int = 20, num_std: float = 2.0) -> tuple[float, float, float]:
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
