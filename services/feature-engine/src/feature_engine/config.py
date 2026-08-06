"""Feature engine service configuration."""

from infusion_common.config import InfusionSettings


class FeatureEngineSettings(InfusionSettings):
    service_name: str = "feature-engine"

    # Micro-batch
    batch_timer_ms: int = 5              # flush every 5ms
    batch_max_ticks: int = 200           # flush if buffer reaches 200

    # Consumer
    consumer_batch_size: int = 200
    consumer_block_ms: int = 2

    # Feature computation
    ema_periods: list[int] = [5, 9, 20, 50]
    rsi_period: int = 14
    atr_period: int = 14
    bb_period: int = 20
    bb_std: float = 2.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    cci_period: int = 20
    volume_sma_period: int = 20

    # OHLC bar retention in Redis sorted sets
    ohlc_1m_max: int = 390               # 1 day of 1-min bars
    ohlc_5m_max: int = 78                # 1 day of 5-min bars
    ohlc_15m_max: int = 26               # 1 day of 15-min bars
