"""Scanner signal models.

ScanSignalV2: full-lifecycle signal with scoring, suppression, and explanation.
ScanSignalV1: deprecated — kept for backward compatibility during schema migration.
"""

from pydantic import BaseModel


class ScanSignalV1(BaseModel, frozen=True):
    """DEPRECATED — use ScanSignalV2. Kept for schema registry v1 compat."""

    symbol: str
    strategy: str
    signal_type: str  # bullish / bearish / neutral
    strength: float = 0.0
    price_at_signal: float = 0.0
    volume_at_signal: int = 0
    features_snapshot: dict = {}
    exchange_timestamp_ms: int = 0


class ScanSignalV2(BaseModel, frozen=True):
    """Signal emitted by the scanner engine.

    Carries full lifecycle state, conviction scoring, suppression context,
    entry/invalidation levels, and human-readable explanation.
    """

    # Identity
    signal_id: str                          # UUID4 — unique per signal instance
    symbol: str
    strategy_id: str                        # "vol_vwap_breakout"
    signal_type: str                        # "bullish" | "bearish"

    # Lifecycle
    lifecycle: str = "candidate"            # SignalLifecycle value
    created_at_us: int = 0                  # microsecond epoch
    confirmed_at_us: int = 0                # when all conditions met
    expired_at_us: int = 0
    ttl_sec: int = 300                      # signal valid for 5 minutes

    # Scoring
    conviction_score: float = 0.0           # 0-100 composite score
    conviction_grade: str = ""              # A+, A, B, C, D
    sub_scores: dict = {}                   # { "volume": 25, "vwap": 20, ... }

    # Price context
    price_at_signal: float = 0.0
    entry_price: float = 0.0                # suggested entry
    invalidation_price: float = 0.0         # stop-loss / signal invalidation
    target_price: float = 0.0               # first target
    risk_reward_ratio: float = 0.0

    # Feature snapshot
    features_snapshot: dict = {}            # frozen FeatureVector at signal time

    # Context
    sector_id: str = ""
    sector_strength: float = 0.0            # 0-100
    market_regime: str = ""                 # MarketRegime value
    pre_breakout_state: str = ""            # PreBreakoutState value
    tier: int = 1

    # Suppression
    suppressed: bool = False
    suppression_reason: str = ""            # "cooldown", "sector_weak", etc.

    # Explanation
    explanation: list[str] = []             # human-readable signal reasons
    conditions_met: dict = {}               # { "vol_expansion": true, ... }
