"""Momentum features — RSI, MACD, Stochastic, CCI."""

from feature_engine.state import SymbolState


def update_rsi(state: SymbolState, ltp: float, period: int = 14):
    """Incremental RSI using Wilder's smoothing."""
    if state.rsi_prev_close == 0:
        state.rsi_prev_close = ltp
        return

    change = ltp - state.rsi_prev_close
    gain = max(change, 0)
    loss = abs(min(change, 0))
    state.rsi_prev_close = ltp

    if not state.rsi_initialized:
        state.rsi_gains.append(gain)
        state.rsi_losses.append(loss)
        state.rsi_warmup_count += 1

        if state.rsi_warmup_count >= period:
            state.rsi_avg_gain = sum(state.rsi_gains) / period
            state.rsi_avg_loss = sum(state.rsi_losses) / period
            state.rsi_initialized = True
            state.rsi_gains.clear()
            state.rsi_losses.clear()
    else:
        state.rsi_avg_gain = (state.rsi_avg_gain * (period - 1) + gain) / period
        state.rsi_avg_loss = (state.rsi_avg_loss * (period - 1) + loss) / period


def get_rsi(state: SymbolState) -> float:
    if not state.rsi_initialized:
        return 50.0
    if state.rsi_avg_loss == 0:
        return 100.0
    rs = state.rsi_avg_gain / state.rsi_avg_loss
    return 100 - (100 / (1 + rs))


def update_macd(state: SymbolState, ltp: float, fast: int = 12, slow: int = 26, sig: int = 9):
    """Incremental MACD."""
    k_fast = 2 / (fast + 1)
    k_slow = 2 / (slow + 1)
    k_sig = 2 / (sig + 1)

    if not state.macd_initialized:
        state.ema_fast = ltp
        state.ema_slow = ltp
        state.ema_signal = 0.0
        state.macd_initialized = True
    else:
        state.ema_fast = ltp * k_fast + state.ema_fast * (1 - k_fast)
        state.ema_slow = ltp * k_slow + state.ema_slow * (1 - k_slow)
        macd_line = state.ema_fast - state.ema_slow
        state.ema_signal = macd_line * k_sig + state.ema_signal * (1 - k_sig)


def get_macd(state: SymbolState) -> tuple[float, float, float]:
    """Returns (macd_line, signal_line, histogram)."""
    macd_line = state.ema_fast - state.ema_slow
    return macd_line, state.ema_signal, macd_line - state.ema_signal


def update_stochastic(state: SymbolState, high: float, low: float, close: float):
    """Stochastic %K and %D."""
    state.stoch_highs.append(high)
    state.stoch_lows.append(low)

    if len(state.stoch_highs) >= 14:
        highest = max(state.stoch_highs)
        lowest = min(state.stoch_lows)
        denom = highest - lowest
        k = ((close - lowest) / denom * 100) if denom > 0 else 50.0
        state.stoch_k_values.append(k)


def get_stochastic(state: SymbolState) -> tuple[float, float]:
    """Returns (%K, %D)."""
    if not state.stoch_k_values:
        return 50.0, 50.0
    k = state.stoch_k_values[-1]
    d = sum(state.stoch_k_values) / len(state.stoch_k_values)
    return k, d


def update_cci(state: SymbolState, high: float, low: float, close: float, period: int = 20):
    """Commodity Channel Index."""
    typical = (high + low + close) / 3
    state.cci_typical_prices.append(typical)


def get_cci(state: SymbolState) -> float:
    if len(state.cci_typical_prices) < 2:
        return 0.0
    mean = sum(state.cci_typical_prices) / len(state.cci_typical_prices)
    mad = sum(abs(p - mean) for p in state.cci_typical_prices) / len(state.cci_typical_prices)
    if mad == 0:
        return 0.0
    typical = state.cci_typical_prices[-1]
    return (typical - mean) / (0.015 * mad)
