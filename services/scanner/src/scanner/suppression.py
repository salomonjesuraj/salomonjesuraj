"""Suppression gate — first-class signal quality filter.

Evaluation order is strict and deterministic:
  1. F&O BAN CHECK    — symbol under NSE trading ban (MWPL>=95%)?
  2. DUPLICATE CHECK  — active signal for same symbol+strategy?
  3. COOLDOWN CHECK   — cooldown key exists?
  4. SECTOR FILTER    — sector strength above threshold?
  5. REGIME FILTER    — market regime compatible with strategy?
  6. CONVICTION FLOOR — score above minimum?

  (a 7th gate, PRECISION GUARD, runs after the above when enabled for a
  given strategy — see evaluate() below)

The F&O ban gate (Phase 13.13) is the one gate here that isn't a signal-
quality judgment — it's a hard, NSE-published constraint (no NEW F&O
position may legally be opened in a banned symbol), so unlike every other
Phase 1-13.x field it is deliberately wired as a real suppression rather
than left informational. See nse_scraper/fo_ban.py for the capture side.

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


def _current_session(now: dt_time | None = None) -> str:
    """IST session boundaries.

    "closing" ends at 15:15, not 15:30 -- SEBI's Closing Auction Session
    (CAS), effective 3 Aug 2026, stops continuous trading of F&O-eligible
    stocks' cash equity at 15:15 (halt 15:15-15:20, auction order entry
    15:20-15:30, single-price match 15:30-15:35; F&O contracts themselves
    still trade normally to 15:40, but every feature this system computes
    -- RSI/VWAP/EMA/ATR/structure/pivots -- is derived from the underlying
    stock's own tick feed, which stops reflecting genuine continuous
    trading at 15:15). 15:15-15:30 is its own "cas_auction" session,
    deliberately NOT folded into "closing" and NOT in
    precision_guard_sessions' default allow-list -- the 81% closing-session
    precision backtest predates CAS entirely, so that number doesn't (yet)
    say anything about signal quality during the new auction window.

    `now` is injectable (IST wall-clock time) for testing; omitted in
    production, where it defaults to the real current time.
    """
    if now is None:
        now = datetime.now(tz=_IST).time()
    if dt_time(9, 15) <= now < dt_time(10, 0):
        return "opening"
    if dt_time(10, 0) <= now < dt_time(12, 0):
        return "mid_morning"
    if dt_time(12, 0) <= now < dt_time(14, 0):
        return "midday"
    if dt_time(14, 0) <= now < dt_time(15, 15):
        return "closing"
    if dt_time(15, 15) <= now < dt_time(15, 30):
        return "cas_auction"
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

        # ── Gate 1: F&O ban (Phase 13.13) ──────────────
        # Cheapest possible check (single SISMEMBER), and a hard legal/
        # exchange constraint rather than a quality judgment, so it goes
        # first -- no reason to compute anything else for a symbol you
        # cannot open a new F&O position in today regardless of how good
        # the setup looks.
        is_banned = await self._check_fo_ban(symbol)
        if is_banned:
            return SuppressionResult(
                passed=False,
                reason="fo_trading_ban",
                gate="fo_ban",
            )

        # ── Gate 2: Duplicate active signal ────────────
        is_dup = await self._check_duplicate(symbol, strategy_id)
        if is_dup:
            return SuppressionResult(
                passed=False,
                reason="duplicate_active",
                gate="duplicate",
            )

        # ── Gate 3: Cooldown ───────────────────────────
        in_cooldown = await self._check_cooldown(symbol, strategy_id)
        if in_cooldown:
            return SuppressionResult(
                passed=False,
                reason="cooldown_active",
                gate="cooldown",
            )

        # ── Gate 4: Sector strength ────────────────────
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

        # ── Gate 5: Market regime ──────────────────────
        if market_regime == "volatile":
            return SuppressionResult(
                passed=False,
                reason="regime_unfavorable",
                gate="regime",
            )

        # ── Gate 6: Conviction floor ───────────────────
        if conviction_score < self._min_conviction:
            return SuppressionResult(
                passed=False,
                reason="low_conviction",
                gate="conviction",
            )

        # ── Gate 7: Precision guard from optimizer ─────────────────
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

    async def _check_fo_ban(self, symbol: str) -> bool:
        """Phase 13.13. Set is captured/refreshed by nse-scraper
        (nse_scraper/fo_ban.py) every 30 min from NSE's real daily ban
        list; absence of the key (fetch never ran / failed) means "don't
        suppress" -- same fail-open behavior as _sector_strength below,
        since a missing key is a data-availability gap, not evidence the
        symbol is actually banned."""
        exists = await self.redis.exists("infusion:nse:fo_ban:symbols")
        if not exists:
            return False
        return bool(await self.redis.sismember("infusion:nse:fo_ban:symbols", symbol))

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
