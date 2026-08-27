"""Suppression gate — first-class signal quality filter.

Evaluation order is strict and deterministic:
  1. F&O BAN CHECK    — symbol under NSE trading ban (MWPL>=95%)?
  2. DUPLICATE CHECK  — active signal for same symbol+strategy?
  3. COOLDOWN CHECK   — cooldown key exists?
  4. SECTOR FILTER    — sector strength above threshold?
  5. REGIME FILTER    — market regime compatible with strategy?
  6. CONVICTION FLOOR — score above minimum?
  7. THETA CUTOFF     — new option-buying entries after 14:30 IST?
     (pipeline audit fix B3 — see ScannerSettings.theta_cutoff_* )

  (an 8th gate, PRECISION GUARD, runs after the above when enabled for a
  given strategy — see evaluate() below)

  A gate that used to sit here, REJECTED_CHASING_OB (hard-rejecting a
  setup extended past its Order Block/FVG), was removed 2026-08-27 --
  see evaluate()'s own comment at that point in the sequence for the
  full context. That gap in the numbering is deliberate, not an error.

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

from datetime import datetime
from datetime import time as dt_time
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from infusion_models.rejection import RejectionCode
from infusion_streams.constants import (
    KEY_COOLDOWN_PREFIX,
    KEY_SECTOR_PREFIX,
    KEY_SIGNAL_ACTIVE,
)
from redis.asyncio import Redis

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


def _parse_hhmm(value: str, default: dt_time = dt_time(14, 30)) -> dt_time:
    """Parse a "HH:MM" config string into a dt_time, IST. Falls back to
    the theta-cutoff default rather than raising on a malformed env var
    -- a typo in config must degrade to the safe default, not crash the
    scanner."""
    try:
        hour_str, minute_str = str(value).strip().split(":", 1)
        return dt_time(int(hour_str), int(minute_str))
    except (ValueError, AttributeError):
        return default


class SuppressionResult:
    """Result of suppression gate evaluation.

    `code` (pipeline audit fix B5) is a stable RejectionCode alongside
    the free-text `reason`, for dashboard slicing that doesn't depend on
    exact wording. Not every gate maps to one of the taxonomy's members
    yet (fo_ban/duplicate/cooldown/regime don't) -- `code` is None there,
    a disclosed gap rather than a forced, ill-fitting mapping.
    """

    __slots__ = ("code", "gate", "passed", "reason")

    def __init__(
        self,
        passed: bool,
        reason: str = "",
        gate: str = "",
        code: RejectionCode | None = None,
    ) -> None:
        self.passed = passed
        self.reason = reason
        self.gate = gate
        self.code = code

    def __repr__(self) -> str:
        if self.passed:
            return "SuppressionResult(PASS)"
        return f"SuppressionResult(SUPPRESSED: {self.gate}={self.reason}, code={self.code})"


class SuppressionGate:
    """Evaluates suppression gates in strict order.

    Usage:
        gate = SuppressionGate(redis, settings)
        result = await gate.evaluate(symbol, strategy_id, conviction_score)
        if not result.passed:
            # signal suppressed — result.reason explains why
    """

    def __init__(self, redis: Redis, settings: Any) -> None:
        self.redis = redis
        self._min_conviction = settings.min_conviction_score
        self._min_sector_strength = settings.min_sector_strength
        self._theta_cutoff_enabled = settings.theta_cutoff_enabled
        self._theta_cutoff_time = _parse_hhmm(settings.theta_cutoff_time)
        self._theta_cutoff_strategy_ids = {
            s.strip() for s in str(settings.theta_cutoff_strategy_ids).split(",") if s.strip()
        }
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
        now: dt_time | None = None,
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
                    code=RejectionCode.REJECTED_SECTOR_WEAK,
                )
            if strength is not None and bearish and strength > 75:
                return SuppressionResult(
                    passed=False,
                    reason="sector_too_strong_for_pe",
                    gate="sector",
                    # Same underlying family (sector strength working
                    # against this candidate's direction) as sector_weak
                    # above, just the mirror-image bearish case -- the
                    # taxonomy has one sector code, not two.
                    code=RejectionCode.REJECTED_SECTOR_WEAK,
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
                code=RejectionCode.REJECTED_LOW_CONVICTION,
            )

        # ── Gate 7: Late-session theta cutoff (pipeline audit fix B3) ──
        # Deliberately after the conviction floor (a weak setup should
        # still be rejected for being weak, not relabeled as a theta-
        # cutoff rejection just because it's also late in the day) and
        # before precision_guard (an optimizer-tunable mechanism this
        # hard safety rail should not depend on being enabled/disabled).
        if (
            self._theta_cutoff_enabled
            and strategy_id in self._theta_cutoff_strategy_ids
            and (now if now is not None else datetime.now(tz=_IST).time())
            >= self._theta_cutoff_time
        ):
            return SuppressionResult(
                passed=False,
                reason=f"late_session_theta_decay_after_{self._theta_cutoff_time.strftime('%H:%M')}",
                gate="theta_cutoff",
                code=RejectionCode.REJECTED_THETA_CUTOFF,
            )

        # ── Gate 8 (REMOVED, 2026-08-27): institutional anti-chase ──────
        # The hard REJECTED_CHASING_OB rejection this gate used to apply
        # (LTP more than CHASING_OB_MAX_DISTANCE_PCT from the nearest OB/
        # FVG) is gone -- explicit philosophy change from "hard
        # suppression" to "probabilistic grading + warning tags": a
        # setup extended past its own base no longer gets hidden
        # outright, it gets a lower win-probability score (scanner/
        # scoring.py's own soft OB/FVG decay) and a LATE_ENTRY warning
        # tag on the TradeBlueprint (api/trade_blueprint.py) instead,
        # surfaced to the trader rather than withheld from them. This
        # was a disclosed, deliberate architecture pivot, not a data-
        # driven calibration -- no backtest/paper-validation window
        # informed it. RejectionCode.REJECTED_CHASING_OB itself stays
        # in the taxonomy (historical rows already carry it; StrEnum
        # members aren't removed just because a gate stops assigning
        # them -- same "uncoded rejection is a disclosed gap, not a
        # bug" posture the taxonomy's own docstring already states, in
        # reverse: a coded-but-now-unassigned value is fine to leave).

        # ── Gate 8: Precision guard from optimizer ─────────────────
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
