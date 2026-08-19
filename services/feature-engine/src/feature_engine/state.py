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
    # sum(price^2 * delta_volume) -- alongside vwap_numerator/denominator this
    # gives session variance without needing to know VWAP at each historical
    # tick: variance = vwap_sq_numerator/vwap_denominator - vwap^2 (see
    # features/price.py get_vwap_sd_bands). Same session-boundary reset as
    # the other VWAP accumulators.
    vwap_sq_numerator: float = 0.0

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

    # ADX / DI+ / DI- (Wilder smoothing, matches Pine's ta.dmi)
    adx_prev_high: float = 0.0
    adx_prev_low: float = 0.0
    adx_prev_close: float = 0.0
    adx_tr_smooth: float = 0.0
    adx_plus_dm_smooth: float = 0.0
    adx_minus_dm_smooth: float = 0.0
    adx_dx_values: list = field(default_factory=list)
    adx_value: float = 0.0
    adx_warmup_count: int = 0
    adx_initialized: bool = False

    # Supertrend (ATR-band flip, matches Pine's ta.supertrend)
    st_final_upper: float = 0.0
    st_final_lower: float = 0.0
    st_bullish: bool = True
    st_prev_close: float = 0.0
    st_initialized: bool = False

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

    # NSE delivery % (Phase 13.5) -- next-day signal, T's session data
    # published by NSE post-market and captured by nse-scraper (see
    # nse_scraper/delivery.py). Re-checked on a throttle (not once-and-done
    # like volume_profile_ready above) so a new trading day's figure is
    # picked up without needing a service restart -- delivery_trade_date
    # tracks which session the cached value is actually for.
    delivery_pct: float = 0.0
    delivery_pct_avg_20d: float | None = None
    delivery_avg_days: int = 0
    delivery_trade_date: str = ""
    delivery_checked_us: int = 0

    # Heiken-Ashi (Phase 13.7) -- see features/heiken_ashi.py.
    ha_open: float = 0.0
    ha_close: float = 0.0
    ha_high: float = 0.0
    ha_low: float = 0.0
    ha_initialized: bool = False
    ha_trend_streak: int = 0
    ha_streak_bullish: bool | None = None   # color the current streak is counting
    ha_last_bullish: bool | None = None     # latest HA candle's color
    ha_prev_bullish_for_flip: bool | None = None  # color one bar before latest, for flip detection
    ha_doji: bool = False

    # Bar builders
    bar_1m: OHLCBar = field(default_factory=OHLCBar)
    bar_5m: OHLCBar = field(default_factory=OHLCBar)
    bar_15m: OHLCBar = field(default_factory=OHLCBar)
    recent_1m_bars: deque = field(default_factory=lambda: deque(maxlen=20))
    completed_1m_bars: int = 0
    last_completed_1m_ms: int = 0

    # Market structure — fractal swing pivots + trend state + BOS/CHOCH.
    # Mirrors simple_structure_pivot_ma_plan_v6.pine's persistent `var` swing
    # tracking (see feature_engine/features/structure.py). None until a pivot
    # has been confirmed at least once.
    swing_high_1: float | None = None
    swing_high_2: float | None = None
    swing_low_1: float | None = None
    swing_low_2: float | None = None
    trend_state: int = 0                 # 1 = uptrend, -1 = downtrend, 0 = range
    last_event_label: str = "None"       # "Bullish BOS" / "Bullish CHOCH" / etc.
    last_break_high: float | None = None  # swing_high_1 value that last triggered a break (dedup)
    last_break_low: float | None = None
    structure_event: bool = False        # True only on the bar a break just fired

    # Extended swing-point history for Fibonacci retracement/extension/
    # projection confluence (features/fibonacci.py). BOS/CHOCH above only
    # needs the latest 2 highs/lows; Fibonacci confluence needs several
    # confirmed swings to find where independent levels cluster, so this is
    # tracked separately rather than widening swing_high_1/2. Each entry is
    # (price, "high"|"low", completed_1m_bars index at confirmation).
    swing_points: deque = field(default_factory=lambda: deque(maxlen=10))

    # RSI paired with each confirmed swing point (Phase 13.11 -- regular/
    # hidden RSI divergence, features/divergence.py). Deliberately a
    # SEPARATE deque from swing_points above, not an extra element on its
    # tuples -- fibonacci.py unpacks swing_points as exactly 3 values in
    # several places, so widening that tuple would break it. Populated at
    # the exact same confirmation event in features/structure.py, so this
    # is never out of sync with swing_points -- same real pivots, just
    # also carrying the RSI value structure.py doesn't otherwise see.
    rsi_swing_points: deque = field(default_factory=lambda: deque(maxlen=10))

    # Candlestick pattern sizing baseline (EMA of body size, matches Pine v6's
    # bodyAvgEma — used by Marubozu / Three Soldiers-Crows thresholds).
    body_size_ema: float = 0.0
    body_size_ema_initialized: bool = False

    # Supply / demand zones (imbalance-candle methodology, see features/zones.py).
    # Each zone is (top, bottom, formed_at_ms) or None when no zone is active.
    supply_zone: tuple[float, float, int] | None = None
    demand_zone: tuple[float, float, int] | None = None

    # ICT: Fair Value Gaps (3-candle imbalance, BISI/SIBI) — see features/ict.py.
    # Each is (bottom, top, formed_at_bar) or None when no gap is active.
    fvg_bullish: tuple[float, float, int] | None = None
    fvg_bearish: tuple[float, float, int] | None = None
    fvg_bullish_touches: int = 0
    fvg_bearish_touches: int = 0

    # ICT: most recent liquidity sweep event — set only on the bar it fires,
    # cleared every bar (a point-in-time event, not a standing state).
    last_liquidity_sweep: str | None = None  # "sellside" | "buyside" | None

    # ICT: Order Blocks (liquidity-sweep precondition + close validation,
    # not just a big candle). Each is (low, high, formed_at_bar, validated).
    order_block_bullish: tuple[float, float, int, bool] | None = None
    order_block_bearish: tuple[float, float, int, bool] | None = None

    # Timing
    last_tick_exchange_ms: int = 0
    last_feature_compute_us: int = 0
    # EBIE EB-0: wall-clock receipt time (received_at_us) of the PREVIOUS
    # tick processed for this symbol -- used to compute session_gap_ms
    # (evidence of a feed gap / likely missed bar) at the next tick.
    last_tick_received_at_us: int = 0
