"""Scanner signal models.

ScanSignalV2: full-lifecycle signal with scoring, suppression, and explanation.
ScanSignalV1: deprecated — kept for backward compatibility during schema migration.
"""

from typing import Any

from pydantic import BaseModel, Field


class ScanSignalV1(BaseModel, frozen=True):
    """DEPRECATED — use ScanSignalV2. Kept for schema registry v1 compat."""

    symbol: str
    strategy: str
    signal_type: str  # bullish / bearish / neutral
    strength: float = 0.0
    price_at_signal: float = 0.0
    volume_at_signal: int = 0
    features_snapshot: dict[str, Any] = Field(default_factory=dict)
    exchange_timestamp_ms: int = 0


class ScanSignalV2(BaseModel, frozen=True):
    """Signal emitted by the scanner engine.

    Carries full lifecycle state, conviction scoring, suppression context,
    entry/invalidation levels, and human-readable explanation.
    """

    # Identity
    signal_id: str  # UUID4 — unique per signal instance
    symbol: str
    strategy_id: str  # "vol_vwap_breakout"
    signal_type: str  # "bullish" | "bearish"

    # Lifecycle
    lifecycle: str = "candidate"  # SignalLifecycle value
    created_at_us: int = 0  # microsecond epoch
    confirmed_at_us: int = 0  # when all conditions met
    expired_at_us: int = 0
    ttl_sec: int = 300  # signal valid for 5 minutes

    # Scoring
    conviction_score: float = 0.0  # 0-100 composite score
    conviction_grade: str = ""  # A+, A, B, C, D
    # LIVE-CAUGHT REGRESSION (found while checking the dashboard for live
    # errors, 2026-08-25): this field was narrowed from a bare `dict` to
    # `dict[str, float]` by the strict-typing pass (commit 8387190) as a
    # mechanical mypy fix, on the assumption the comment's own stale
    # example ("volume": 25, "vwap": 20) still described real usage. It
    # doesn't -- scanner/engine.py has stored rich nested dicts here under
    # "verdict" (EB-8), "trap_risk" (EB-9), "portfolio_fit" (EB-11),
    # "cross_confirmation", "position_sizing", and "ml_classifier" for
    # most of this project's history. A bare `dict` in Pydantic v2 is
    # dict[Any, Any] and never validated those values; `dict[str, float]`
    # actively DOES validate them, and every one of those keys is a dict,
    # not a float -- so ScanSignalV2(...) construction started raising a
    # real ValidationError for essentially every candidate that reaches
    # full scoring. Confirmed live: 1486 "feature_processing_error" log
    # entries in the ~23 hours between that deploy and this fix (~226/hr,
    # ~19% of all scanner log lines), each one a fully-scored candidate
    # silently dropped before publish/archive. Fixed to dict[str, Any],
    # matching features_snapshot's own correct pattern two fields below --
    # still satisfies strict mypy's type-arg requirement, but no longer
    # rejects the real payload shape.
    sub_scores: dict[str, Any] = Field(default_factory=dict)

    # Price context
    price_at_signal: float = 0.0
    entry_price: float = 0.0  # suggested entry
    invalidation_price: float = 0.0  # stop-loss / signal invalidation
    target_price: float = 0.0  # first target
    risk_reward_ratio: float = 0.0

    # Feature snapshot
    features_snapshot: dict[str, Any] = Field(
        default_factory=dict
    )  # frozen FeatureVector at signal time

    # Context
    sector_id: str = ""
    sector_strength: float = 0.0  # 0-100
    market_regime: str = ""  # MarketRegime value
    pre_breakout_state: str = ""  # PreBreakoutState value
    tier: int = 1

    # Suppression
    suppressed: bool = False
    suppression_reason: str = ""  # "cooldown", "sector_weak", etc.
    # Pipeline audit fix B5: stable RejectionCode value alongside the
    # free-text reason above, when the gate that suppressed this signal
    # has a matching taxonomy member (see infusion_models.rejection) --
    # "" (not every gate maps to one yet), never a fabricated code.
    suppression_code: str = ""

    # Explanation
    explanation: list[str] = Field(default_factory=list)  # human-readable signal reasons
    conditions_met: dict[str, bool] = Field(default_factory=dict)  # { "vol_expansion": true, ... }
