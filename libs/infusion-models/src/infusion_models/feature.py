"""Feature vector models.

FeatureVectorV1: computed features for a single symbol at a point in time.
All features are incrementally updated on each tick — no batch recomputation.
"""

from pydantic import BaseModel


class FeatureVectorV1(BaseModel, frozen=True):
    """Computed features for a single symbol at a point in time."""

    symbol: str
    timestamp_us: int  # when features were computed

    # EBIE EB-0 event-time lineage. Prior to this, the tick's own
    # exchange_timestamp_ms/received_at_us were dropped by the time a
    # feature vector was emitted -- nothing downstream of feature-engine
    # could trace a feature back to the tick that produced it. These carry
    # that lineage forward; tick_lag_ms/session_gap_ms are derived from it
    # and feed data_quality_score below.
    source_exchange_timestamp_ms: int = 0  # the underlying tick's exchange time
    source_received_at_us: int = 0  # when ingestion received that tick
    tick_lag_ms: int = 0  # now - received_at_us, at feature-compute time
    session_gap_ms: int = 0  # gap since the PREVIOUS tick for this symbol
    is_out_of_order: bool = False  # carried from normalizer (see NormalizedTickV1)

    # EBIE EB-0 Data Quality Score v1 (0-100). Computed per docs/EBIE-
    # IMPLEMENTATION-ANSWERS.md Q6.2's authorized policy from tick_lag_ms/
    # session_gap_ms/is_out_of_order above. reasons is always explicit --
    # never a silent number (matches this file's own volume_profile_ready/
    # indicator_ready convention). NOT yet wired into any hard gate --
    # this increment computes and exposes the score; gating live signal
    # eligibility on it is deferred until real DQ distributions have been
    # observed, per the authorization's own explicit sequencing.
    data_quality_score: int = 100
    data_quality_reasons: list[str] = []

    # Price
    ltp: float
    vwap: float = 0.0
    gap_pct: float = 0.0  # (open - prev_close) / prev_close * 100
    day_high: float = 0.0
    day_low: float = 0.0
    prev_close: float = 0.0
    change_pct: float = 0.0  # (ltp - prev_close) / prev_close * 100

    # Moving averages
    ema_5: float = 0.0
    ema_9: float = 0.0
    ema_20: float = 0.0
    ema_50: float = 0.0

    # Volatility
    atr_14: float = 0.0
    atr_trail_stop: float = 0.0
    atr_trend: str = "NEUTRAL"  # BULL / BEAR / NEUTRAL
    bb_upper: float = 0.0
    bb_lower: float = 0.0
    bb_width: float = 0.0
    squeeze_state: str = "NA"  # EXTREME / COILED / BUILDING / EXPANDING
    nr_pattern: str = ""  # NR4 / NR7 when latest range is narrow
    candle_pattern: str = ""  # High-value recent candle label

    # Momentum
    rsi_14: float = 50.0
    macd: float = 0.0
    macd_signal: float = 0.0
    macd_hist: float = 0.0
    stochastic_k: float = 50.0
    stochastic_d: float = 50.0
    cci_20: float = 0.0

    # Volume
    rel_vol_20d: float = 0.0  # cumulative volume / 20-session same-time avg
    obv: float = 0.0
    volume_sma_20: float = 0.0
    volume_profile_ready: bool = False

    # Scanner timing contract. Strategies evaluate only on completed 1m bars.
    bar_closed_1m: bool = False
    indicator_ready: bool = False
    completed_1m_bars: int = 0

    # Microstructure
    spread_bps: float = 0.0  # (ask - bid) / mid * 10000
    order_imbalance: float = 0.0  # (buy_qty - sell_qty) / (buy_qty + sell_qty)
    delivery_pct: float = 0.0  # from NSE data (when available)

    # ML features (free-form, for future model iteration)
    ml_features: dict = {}
