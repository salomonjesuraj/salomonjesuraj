"""Per-symbol mutable state for feature computation."""

from dataclasses import dataclass, field
from collections import deque


@dataclass
class OHLCBar:
    """A single OHLC bar."""
    open: float = 0.0
    high: float = 0.0
    low: float = float("inf")
    close: float = 0.0
    volume: int = 0
    vwap_numerator: float = 0.0          # sum(price * volume)
    vwap_denominator: int = 0            # sum(volume)
    tick_count: int = 0
    bar_start_ms: int = 0                # exchange timestamp of bar start


@dataclass
class SymbolState:
    """Mutable state for a single symbol across the trading session."""

    symbol: str

    # Latest values
    ltp: float = 0.0
    prev_close: float = 0.0
    day_open: float = 0.0
    day_high: float = 0.0
    day_low: float = float("inf")
    volume: int = 0
    previous_cumulative_volume: int = 0
    session_cumulative_volume: int = 0
    session_date: str = ""
    oi: int = 0
    best_bid: float = 0.0
    best_ask: float = 0.0
    total_buy_qty: int = 0
    total_sell_qty: int = 0

    # VWAP accumulators
    vwap_numerator: float = 0.0
    vwap_denominator: int = 0

    # EMA state (keyed by period)
    ema: dict[int, float] = field(default_factory=dict)
    ema_initialized: dict[int, bool] = field(default_factory=dict)

    # RSI state
    rsi_avg_gain: float = 0.0
    rsi_avg_loss: float = 0.0
    rsi_prev_close: float = 0.0
    rsi_initialized: bool = False
    rsi_warmup_count: int = 0
    rsi_gains: list[float] = field(default_factory=list)
    rsi_losses: list[float] = field(default_factory=list)

    # MACD state
    ema_fast: float = 0.0
    ema_slow: float = 0.0
    ema_signal: float = 0.0
    macd_initialized: bool = False

    # ATR state
    atr_values: deque = field(default_factory=lambda: deque(maxlen=14))
    atr: float = 0.0
    atr_prev_close: float = 0.0

    # Bollinger
    bb_prices: deque = field(default_factory=lambda: deque(maxlen=20))

    # Stochastic
    stoch_highs: deque = field(default_factory=lambda: deque(maxlen=14))
    stoch_lows: deque = field(default_factory=lambda: deque(maxlen=14))
    stoch_k_values: deque = field(default_factory=lambda: deque(maxlen=3))

    # CCI
    cci_typical_prices: deque = field(default_factory=lambda: deque(maxlen=20))

    # OBV
    obv: float = 0.0
    obv_prev_close: float = 0.0

    # Volume tracking
    volume_history: deque = field(default_factory=lambda: deque(maxlen=20))
    volume_profile: dict[int, float] = field(default_factory=dict)
    volume_profile_ready: bool = False
    volume_profile_checked_us: int = 0
    history_seed_checked_us: int = 0
    history_seeded: bool = False

    # Bar builders
    bar_1m: OHLCBar = field(default_factory=OHLCBar)
    bar_5m: OHLCBar = field(default_factory=OHLCBar)
    bar_15m: OHLCBar = field(default_factory=OHLCBar)
    recent_1m_bars: deque = field(default_factory=lambda: deque(maxlen=20))
    completed_1m_bars: int = 0
    last_completed_1m_ms: int = 0

    # Timing
    last_tick_exchange_ms: int = 0
    last_feature_compute_us: int = 0
