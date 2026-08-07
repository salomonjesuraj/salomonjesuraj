"""Suppression gate — first-class signal quality filter.

Evaluation order is strict and deterministic:
  1. DUPLICATE CHECK  — active signal for same symbol+strategy?
  2. COOLDOWN CHECK   — cooldown key exists?
  3. SECTOR FILTER    — sector strength above threshold?
  4. REGIME FILTER    — market regime compatible with strategy?
  5. CONVICTION FLOOR — score above minimum?

If ANY gate fails, the signal is SUPPRESSED with a structured reason.
Suppressed signals are published to the audit stream for observability.

Design principles:
  - Deterministic: same state → same suppression decision
  - Auditable: every suppression has a reason
  - Ordered: gates evaluated in strict sequence (first failure wins)
  - Async: Redis lookups for cooldown and active signals
"""

from __future__ import annotations

from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

import structlog
from redis.asyncio import Redis

from infusion_streams.constants import (
    KEY_SIGNAL_ACTIVE,
    KEY_COOLDOWN_PREFIX,
    KEY_SECTOR_PREFIX,
)

logger = structlog.get_logger()
_IST = ZoneInfo("Asia/Kolkata")


def _current_session() -> str:
    now = datetime.now(tz=_IST).time()
    if dt_time(9, 15) <= now < dt_time(10, 0):
        return "opening"
    if dt_time(10, 0) <= now < dt_time(12, 0):
        return "mid_morning"
    if dt_time(12, 0) <= now < dt_time(14, 0):
        return "midday"
    if dt_time(14, 0) <= now < dt_time(15, 30):
        return "closing"
    if now < dt_time(9, 15):
        return "pre_market"
    return "post_market"


class SuppressionResult:
    """Result of suppression gate evaluation."""

    __slots__ = ("passed", "reason", "gate")

    def __init__(self, passed: bool, reason: str = "", gate: str = ""):
        self.passed = passed
        self.reason = reason
        self.gate = gate

    def __repr__(self) -> str:
        if self.passed:
            return "SuppressionResult(PASS)"
        return f"SuppressionResult(SUPPRESSED: {self.gate}={self.reason})"


class SuppressionGate:
    """Evaluates suppression gates in strict order.

    Usage:
        gate = SuppressionGate(redis, settings)
        result = await gate.evaluate(symbol, strategy_id, conviction_score)
        if not result.passed:
            # signal suppressed — result.reason explains why
    """

    def __init__(self, redis: Redis, settings):
        self.redis = redis
        self._min_conviction = settings.min_conviction_score
        self._min_sector_strength = settings.min_sector_strength
        self._precision_guard_enabled = settings.precision_guard_enabled
        self._precision_guard_min_score = settings.precision_guard_min_score
        self._precision_guard_min_rr = settings.precision_guard_min_rr
        self._precision_guard_sessions = {
            s.strip() for s in str(settings.precision_guard_sessions).split(",") if s.strip()
        }
        self._precision_guard_strategy_ids = {
            s.strip() for s in str(settings.precision_guard_strategy_ids).split(",") if s.strip()
        }

    async def evaluate(
        self,
        symbol: str,
        strategy_id: str,
        conviction_score: float,
        sector_id: str = "",
        market_regime: str = "",
        signal_type: str = "bullish",
        risk_reward_ratio: float = 0.0,
    ) -> SuppressionResult:
        """Evaluate all suppression gates in strict order.

        Returns SuppressionResult — first failing gate wins.
        """

        # ── Gate 1: Duplicate active signal ────────────
        is_dup = await self._check_duplicate(symbol, strategy_id)
        if is_dup:
            return SuppressionResult(
                passed=False,
                reason="duplicate_active",
                gate="duplicate",
            )

        # ── Gate 2: Cooldown ───────────────────────────
        in_cooldown = await self._check_cooldown(symbol, strategy_id)
        if in_cooldown:
            return SuppressionResult(
                passed=False,
                reason="cooldown_active",
                gate="cooldown",
            )

        # ── Gate 3: Sector strength ────────────────────
        if sector_id:
            strength = await self._sector_strength(sector_id)
            bearish = str(signal_type).lower() == "bearish"
            if strength is not None and not bearish and strength < self._min_sector_strength:
                return SuppressionResult(
                    passed=False,
                    reason="sector_weak",
                    gate="sector",
                )
            if strength is not None and bearish and strength > 75:
                return SuppressionResult(
                    passed=False,
                    reason="sector_too_strong_for_pe",
                    gate="sector",
                )

        # ── Gate 4: Market regime ──────────────────────
        if market_regime == "volatile":
            return SuppressionResult(
                passed=False,
                reason="regime_unfavorable",
                gate="regime",
            )

        # ── Gate 5: Conviction floor ───────────────────
        if conviction_score < self._min_conviction:
            return SuppressionResult(
                passed=False,
                reason="low_conviction",
                gate="conviction",
            )

        # ── Gate 6: Precision guard from optimizer ─────────────────
        if self._precision_guard_enabled and strategy_id in self._precision_guard_strategy_ids:
            if conviction_score < self._precision_guard_min_score:
                return SuppressionResult(
                    passed=False,
                    reason="precision_guard_score",
                    gate="precision_guard",
                )
            if risk_reward_ratio < self._precision_guard_min_rr:
                return SuppressionResult(
                    passed=False,
                    reason="precision_guard_rr",
                    gate="precision_guard",
                )
            if self._precision_guard_sessions:
                current_session = _current_session()
                if current_session not in self._precision_guard_sessions:
                    return SuppressionResult(
                        passed=False,
                        reason=f"precision_guard_session_{current_session}",
                        gate="precision_guard",
                    )

        return SuppressionResult(passed=True)

    async def set_cooldown(
        self, symbol: str, strategy_id: str, signal_id: str, ttl_sec: int
    ) -> None:
        """Set cooldown after a signal is confirmed."""
        key = f"{KEY_COOLDOWN_PREFIX}{symbol}:{strategy_id}"
        await self.redis.set(key, signal_id, ex=ttl_sec)
        logger.debug(
            "cooldown_set",
            symbol=symbol,
            strategy=strategy_id,
            ttl_sec=ttl_sec,
        )

    async def _check_duplicate(self, symbol: str, strategy_id: str) -> bool:
        """Check if there's an active signal for the same symbol+strategy."""
        # Active signals are stored in ZSET with member = "symbol:strategy_id"
        member = f"{symbol}:{strategy_id}"
        score = await self.redis.zscore(KEY_SIGNAL_ACTIVE, member)
        return score is not None

    async def _check_cooldown(self, symbol: str, strategy_id: str) -> bool:
        """Check if cooldown is active for symbol+strategy."""
        key = f"{KEY_COOLDOWN_PREFIX}{symbol}:{strategy_id}"
        exists = await self.redis.exists(key)
        return bool(exists)

    async def _sector_strength(self, sector_id: str) -> float | None:
        """Return sector strength if known."""
        key = f"{KEY_SECTOR_PREFIX}{sector_id}"
        raw = await self.redis.hget(key, "strength_score")
        if raw is None:
            # No sector data — don't suppress (allow signal through)
            return None
        try:
            return float(raw)
        except (ValueError, TypeError):
            return None
