"""Scanner service configuration.

Extends InfusionSettings with scanner-specific parameters.
All strategy thresholds are centralized here — no magic numbers in strategy code.
"""

from infusion_common.config import InfusionSettings


class ScannerSettings(InfusionSettings):
    """Scanner-specific settings. All overridable via environment variables."""

    # ─── Consumer ───────────────────────────────────
    scanner_consumer_name: str = "scanner-0"
    scanner_batch_size: int = 50
    scanner_block_ms: int = 100

    # ─── Signal lifecycle ───────────────────────────
    signal_ttl_sec: int = 300               # 5 minutes
    max_active_signals: int = 50            # bounded active signal ZSET

    # ─── Cooldown ───────────────────────────────────
    cooldown_sec: int = 900                 # 15 minutes per symbol+strategy

    # ─── Suppression thresholds ─────────────────────
    min_conviction_score: float = 80.0      # optimizer v4.4.1: below this → suppressed
    min_sector_strength: float = 30.0       # below this → sector_weak

    # ─── Phase 5.1 precision guard ───────────────────
    # Backtest winner on archived active regular-session signals:
    # score >= 80, R:R >= 1.2, closing session only → ~85.9% precision on 333 decided trades.
    precision_guard_enabled: bool = True
    precision_guard_min_score: float = 80.0
    precision_guard_min_rr: float = 1.2
    precision_guard_session: str = "closing"
    precision_guard_strategy_ids: str = "options_first_hybrid,vol_vwap_breakout"

    # ─── Vol-VWAP Breakout strategy ─────────────────
    vvb_min_rel_vol: float = 2.0            # minimum relative volume
    vvb_min_rsi: float = 40.0               # RSI lower bound
    vvb_max_rsi: float = 75.0               # RSI upper bound
    vvb_min_bb_width: float = 0.01          # minimum Bollinger width
    vvb_max_spread_bps: float = 50.0        # maximum spread in bps
    vvb_min_order_imbalance: float = 0.0    # minimum order imbalance (> 0)

    # ─── Options-first hybrid strategy ───────────────
    options_hybrid_min_score: float = 62.0  # candidate floor before scanner scoring
    options_hybrid_min_rel_vol: float = 1.1 # softer than strict breakout
    options_hybrid_max_spread_bps: float = 70.0
    options_hybrid_min_core_gates: int = 4  # trend gates required for CE/PE candidate

    # ─── Pre-breakout state machine ─────────────────
    pb_compress_ticks: int = 5              # ticks of declining bb_width
    pb_compress_bb_max: float = 0.03        # max bb_width to enter COMPRESSING
    pb_accumulate_rel_vol: float = 1.3      # min rel_vol for ACCUMULATING
    pb_coiled_bb_max: float = 0.015         # max bb_width for COILED
    pb_coiled_rel_vol: float = 1.5          # min rel_vol for COILED
    pb_coiled_rsi_min: float = 45.0         # RSI lower for COILED
    pb_coiled_rsi_max: float = 60.0         # RSI upper for COILED
    pb_reversal_ticks: int = 10             # ticks to reverse back to IDLE
    pb_max_state_sec: int = 1800            # 30 min max in any pre-breakout state
    pb_state_ttl_sec: int = 3600            # 1 hour Redis TTL for prebreak keys

    # ─── Warmup ─────────────────────────────────────
    warmup_ticks: int = 5                   # min ticks before evaluating a symbol
