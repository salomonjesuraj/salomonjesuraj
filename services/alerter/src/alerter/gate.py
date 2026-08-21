"""Delivery gate — delivery-layer filtering for the alerter.

This is SEPARATE from the scanner suppression gate. The scanner gate
filters signal quality (conviction, sector, regime). This gate filters
delivery concerns: priority tiers, mute controls, delivery cooldowns,
rate limits, and burst protection.

Evaluation order (deterministic — first failure wins):
  1. PRIORITY CHECK   — grade meets minimum tier?
  2. MUTE CHECK       — symbol or strategy muted?
  3. DUPLICATE CHECK  — signal_id already delivered?
  4. DELIVERY COOLDOWN — per-symbol cooldown active?
  5. RATE LIMIT       — hourly counter exceeds global limit?
  6. BURST LIMIT      — 5-min counter exceeds burst limit? (skip for CRITICAL)

Priority tiers:
  A+  → CRITICAL  (bypasses burst limit)
  A   → HIGH      (subject to burst limit)
  B+  → NORMAL    (subject to all limits)
  B, C, D → PASSIVE (not delivered, logged only)
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from infusion_streams.constants import (
    KEY_ALERT_BURST,
    KEY_ALERT_COOLDOWN_PREFIX,
    KEY_ALERT_DELIVERED,
    KEY_ALERT_MUTE_STRATEGIES,
    KEY_ALERT_MUTE_SYMBOLS,
    KEY_ALERT_RATE,
)
from redis.asyncio import Redis

from alerter.config import AlerterSettings

logger = structlog.get_logger()

# ═══════════════════════════════════════════════════
# Priority tier mapping
# ═══════════════════════════════════════════════════
PRIORITY_TIERS: dict[str, str] = {
    "A+": "CRITICAL",
    "A": "HIGH",
    "B+": "NORMAL",
    "B": "PASSIVE",
    "C": "PASSIVE",
    "D": "PASSIVE",
}

# Grades that meet or exceed each minimum grade threshold
_GRADE_RANK: dict[str, int] = {
    "A+": 5,
    "A": 4,
    "B+": 3,
    "B": 2,
    "C": 1,
    "D": 0,
}


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    """Result of delivery gate evaluation."""

    passed: bool
    reason: str = ""
    priority_tier: str = "PASSIVE"


class DeliveryGate:
    """Evaluates delivery gates in strict deterministic order.

    Usage:
        gate = DeliveryGate(redis, settings)
        result = await gate.evaluate(signal_id, symbol, grade, strategy_id)
        if result.passed:
            # proceed to format and deliver
    """

    def __init__(self, redis: Redis, settings: AlerterSettings):
        self.redis = redis
        self._min_grade_rank = _GRADE_RANK.get(settings.alert_min_grade, 3)
        self._global_rate_limit = settings.global_rate_limit
        self._burst_limit = settings.burst_limit

    async def evaluate(
        self,
        signal_id: str,
        symbol: str,
        grade: str,
        strategy_id: str = "",
    ) -> DeliveryResult:
        """Evaluate all delivery gates in strict order.

        Returns DeliveryResult — first failing gate wins.
        """
        tier = PRIORITY_TIERS.get(grade, "PASSIVE")

        # ── Gate 1: Priority check ─────────────────────
        grade_rank = _GRADE_RANK.get(grade, 0)
        if grade_rank < self._min_grade_rank:
            return DeliveryResult(
                passed=False,
                reason="below_min_grade",
                priority_tier=tier,
            )

        # ── Gate 2: Mute check ─────────────────────────
        if await self.is_muted(symbol):
            return DeliveryResult(
                passed=False,
                reason="symbol_muted",
                priority_tier=tier,
            )
        if strategy_id and await self.is_strategy_muted(strategy_id):
            return DeliveryResult(
                passed=False,
                reason="strategy_muted",
                priority_tier=tier,
            )

        # ── Gate 3: Duplicate check ────────────────────
        already = await self.redis.sismember(KEY_ALERT_DELIVERED, signal_id)
        if already:
            return DeliveryResult(
                passed=False,
                reason="already_delivered",
                priority_tier=tier,
            )

        # ── Gate 4: Delivery cooldown ──────────────────
        cooldown_key = f"{KEY_ALERT_COOLDOWN_PREFIX}{symbol}"
        exists = await self.redis.exists(cooldown_key)
        if exists:
            return DeliveryResult(
                passed=False,
                reason="delivery_cooldown",
                priority_tier=tier,
            )

        # ── Gate 5: Rate limit (hourly) ────────────────
        rate_raw = await self.redis.get(KEY_ALERT_RATE)
        if rate_raw is not None:
            rate_count = int(rate_raw)
            if rate_count >= self._global_rate_limit:
                return DeliveryResult(
                    passed=False,
                    reason="rate_limit_hourly",
                    priority_tier=tier,
                )

        # ── Gate 6: Burst limit (5-min) ────────────────
        # CRITICAL tier bypasses burst limit
        if tier != "CRITICAL":
            burst_raw = await self.redis.get(KEY_ALERT_BURST)
            if burst_raw is not None:
                burst_count = int(burst_raw)
                if burst_count >= self._burst_limit:
                    return DeliveryResult(
                        passed=False,
                        reason="burst_limit",
                        priority_tier=tier,
                    )

        return DeliveryResult(passed=True, priority_tier=tier)

    async def record_delivery(self, signal_id: str, symbol: str, cooldown_sec: int) -> None:
        """Record a successful delivery: set cooldown, increment counters.

        Called after successful Telegram send.
        """
        pipe = self.redis.pipeline(transaction=False)

        # Delivery cooldown per symbol
        cooldown_key = f"{KEY_ALERT_COOLDOWN_PREFIX}{symbol}"
        pipe.set(cooldown_key, "1", ex=cooldown_sec)

        # Hourly rate counter (initialize with TTL if missing)
        pipe.incr(KEY_ALERT_RATE)

        # 5-min burst counter
        pipe.incr(KEY_ALERT_BURST)

        # Add signal_id to delivered set
        pipe.sadd(KEY_ALERT_DELIVERED, signal_id)

        await pipe.execute()

        # Ensure TTLs are set (only set if key has no expiry yet)
        # Rate counter: 3600s (1 hour)
        ttl_rate = await self.redis.ttl(KEY_ALERT_RATE)
        if ttl_rate == -1:  # no expiry set
            await self.redis.expire(KEY_ALERT_RATE, 3600)

        # Burst counter: 300s (5 min)
        ttl_burst = await self.redis.ttl(KEY_ALERT_BURST)
        if ttl_burst == -1:
            await self.redis.expire(KEY_ALERT_BURST, 300)

        logger.debug(
            "delivery_recorded",
            signal_id=signal_id,
            symbol=symbol,
            cooldown_sec=cooldown_sec,
        )

    async def is_muted(self, symbol: str) -> bool:
        """Check if a symbol is in the mute set."""
        return bool(await self.redis.sismember(KEY_ALERT_MUTE_SYMBOLS, symbol))

    async def is_strategy_muted(self, strategy_id: str) -> bool:
        """Check if a strategy is in the mute set."""
        return bool(await self.redis.sismember(KEY_ALERT_MUTE_STRATEGIES, strategy_id))
