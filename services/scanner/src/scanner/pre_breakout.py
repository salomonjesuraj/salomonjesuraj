"""Pre-breakout state machine — core institutional intelligence.

Identifies stocks BEFORE breakout expansion by tracking compression,
accumulation, and coiling patterns in real-time.

States (strictly deterministic transitions):

    IDLE → COMPRESSING → ACCUMULATING → COILED → TRIGGERED
                ↓              ↓           ↓
              EXPIRED        EXPIRED     EXPIRED

State definitions:
  IDLE:          Default. No compression detected.
  COMPRESSING:   Bollinger width declining, price range narrowing.
  ACCUMULATING:  Volume rising while price remains compressed.
  COILED:        Extreme compression + volume + RSI sweet spot.
                 This is the HIGH-PROBABILITY breakout readiness state.
  TRIGGERED:     Breakout fired (vol_vwap_breakout signal emitted).
                 Transitions back to IDLE after cooldown.
  EXPIRED:       Setup degraded or timed out. Aggressive cleanup.

Design principles:
  - Event-driven: transitions occur ONLY on feature updates
  - Deterministic: same feature stream → identical state progression
  - Bounded: per-symbol state, TTL cleanup, no unbounded growth
  - Observable: every transition logged with timestamp, reason, duration
  - Conservative: COILED requires multiple confirming conditions
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import structlog
from infusion_common.timing import now_us
from infusion_streams.constants import KEY_PRE_BREAKOUT_PREFIX
from redis.asyncio import Redis

from scanner.config import ScannerSettings
from scanner.state import ScannerSymbolState

logger = structlog.get_logger()


class PBState(StrEnum):
    """Pre-breakout states."""

    IDLE = "idle"
    COMPRESSING = "compressing"
    ACCUMULATING = "accumulating"
    COILED = "coiled"
    TRIGGERED = "triggered"
    EXPIRED = "expired"


@dataclass
class PreBreakoutSnapshot:
    """Immutable snapshot of a pre-breakout state for observability."""

    symbol: str
    state: str
    prev_state: str
    entered_at_us: int
    duration_sec: float
    readiness_score: float
    transition_reason: str
    bb_width: float = 0.0
    bb_width_declining_count: int = 0
    rel_vol: float = 0.0
    rsi: float = 0.0
    ticks_in_state: int = 0


class PreBreakoutTracker:
    """Tracks pre-breakout state for all symbols.

    Called by the scanner engine on every feature update.
    Produces state transition events for observability and
    enriches signal explanations when state is COILED/TRIGGERED.

    Usage:
        tracker = PreBreakoutTracker(redis, settings)
        snapshot = await tracker.update(symbol, features, scanner_state)
        if snapshot:
            # state transition occurred — log/persist
    """

    def __init__(self, redis: Redis, settings: ScannerSettings):
        self.redis = redis
        self._s = settings
        self._transitions = 0

    async def update(
        self,
        symbol: str,
        features: dict,
        state: ScannerSymbolState,
    ) -> PreBreakoutSnapshot | None:
        """Evaluate pre-breakout state transition for a symbol.

        Args:
            symbol: instrument symbol
            features: current FeatureVectorV1 as dict
            state: per-symbol scanner state (mutated in place)

        Returns:
            PreBreakoutSnapshot if a state transition occurred, None otherwise.
        """
        current = PBState(state.pre_breakout_state or PBState.IDLE)
        bb_width = features.get("bb_width", 0.0)
        rel_vol = features.get("rel_vol_20d", 0.0)
        rsi = features.get("rsi_14", 50.0)
        ts = now_us()

        # Track Bollinger width trend
        if state.prev_bb_width > 0 and bb_width > 0:
            if bb_width < state.prev_bb_width:
                state.bb_width_declining_count += 1
            else:
                # Reset on expansion (allow 1 tick tolerance)
                if bb_width > state.prev_bb_width * 1.02:
                    state.bb_width_declining_count = 0

        state.ticks_in_pre_breakout += 1

        # Check timeout — aggressive expiry
        if current != PBState.IDLE and state.pre_breakout_entered_us > 0:
            elapsed_sec = (ts - state.pre_breakout_entered_us) / 1_000_000
            if elapsed_sec > self._s.pb_max_state_sec:
                return await self._transition(
                    state,
                    PBState.EXPIRED,
                    ts,
                    f"timeout_{elapsed_sec:.0f}s_in_{current.value}",
                    bb_width,
                    rel_vol,
                    rsi,
                )

        # Evaluate transitions based on current state
        new_state = self._evaluate(current, state, bb_width, rel_vol, rsi)

        if new_state != current:
            reason = self._transition_reason(current, new_state, bb_width, rel_vol, rsi, state)
            return await self._transition(state, new_state, ts, reason, bb_width, rel_vol, rsi)

        return None

    def _evaluate(
        self,
        current: PBState,
        state: ScannerSymbolState,
        bb_width: float,
        rel_vol: float,
        rsi: float,
    ) -> PBState:
        """Pure evaluation — no side effects, deterministic."""

        if current == PBState.IDLE:
            # IDLE → COMPRESSING: bb_width declining for N ticks AND width < threshold
            if (
                state.bb_width_declining_count >= self._s.pb_compress_ticks
                and bb_width < self._s.pb_compress_bb_max
                and bb_width > 0
            ):
                return PBState.COMPRESSING
            return PBState.IDLE

        elif current == PBState.COMPRESSING:
            # COMPRESSING → ACCUMULATING: volume rising while compressed
            if rel_vol >= self._s.pb_accumulate_rel_vol:
                return PBState.ACCUMULATING
            # COMPRESSING → IDLE: conditions reversed for N ticks
            if (
                state.bb_width_declining_count == 0
                and state.ticks_in_pre_breakout >= self._s.pb_reversal_ticks
            ):
                return PBState.EXPIRED
            return PBState.COMPRESSING

        elif current == PBState.ACCUMULATING:
            # ACCUMULATING → COILED: extreme compression + volume + RSI sweet spot
            if (
                bb_width < self._s.pb_coiled_bb_max
                and rel_vol >= self._s.pb_coiled_rel_vol
                and self._s.pb_coiled_rsi_min <= rsi <= self._s.pb_coiled_rsi_max
            ):
                return PBState.COILED
            # ACCUMULATING → EXPIRED: volume dropped or bb expanding
            if rel_vol < 1.0 or bb_width > self._s.pb_compress_bb_max:
                return PBState.EXPIRED
            return PBState.ACCUMULATING

        elif current == PBState.COILED:
            # COILED → TRIGGERED: handled externally by scanner engine
            #   when vol_vwap_breakout fires
            # COILED → EXPIRED: conditions degraded
            if bb_width > self._s.pb_compress_bb_max:
                # Bollinger expanded without breakout — false compression
                return PBState.EXPIRED
            if rel_vol < 1.0:
                # Volume dried up
                return PBState.EXPIRED
            if rsi > 80 or rsi < 30:
                # Extreme RSI — setup invalidated
                return PBState.EXPIRED
            return PBState.COILED

        elif current == PBState.TRIGGERED:
            # TRIGGERED → IDLE: immediate reset after trigger
            return PBState.IDLE

        elif current == PBState.EXPIRED:
            # EXPIRED → IDLE: immediate reset
            return PBState.IDLE

        return current

    def _transition_reason(
        self,
        old: PBState,
        new: PBState,
        bb_width: float,
        rel_vol: float,
        rsi: float,
        state: ScannerSymbolState,
    ) -> str:
        """Generate structured transition reason."""
        if new == PBState.COMPRESSING:
            return f"bb_declining_{state.bb_width_declining_count}_ticks_width_{bb_width:.4f}"
        elif new == PBState.ACCUMULATING:
            return f"vol_rising_{rel_vol:.1f}x_while_compressed_{bb_width:.4f}"
        elif new == PBState.COILED:
            return f"extreme_compression_bb_{bb_width:.4f}_vol_{rel_vol:.1f}x_rsi_{rsi:.1f}"
        elif new == PBState.TRIGGERED:
            return "breakout_signal_fired"
        elif new == PBState.EXPIRED:
            if old == PBState.COMPRESSING:
                return f"compression_reversed_bb_{bb_width:.4f}"
            elif old == PBState.ACCUMULATING:
                return f"accumulation_degraded_vol_{rel_vol:.1f}x_bb_{bb_width:.4f}"
            elif old == PBState.COILED:
                return f"coil_invalidated_bb_{bb_width:.4f}_vol_{rel_vol:.1f}x_rsi_{rsi:.1f}"
            return f"expired_from_{old.value}"
        elif new == PBState.IDLE:
            return f"reset_from_{old.value}"
        return f"{old.value}_to_{new.value}"

    async def _transition(
        self,
        state: ScannerSymbolState,
        new_state: PBState,
        ts: int,
        reason: str,
        bb_width: float,
        rel_vol: float,
        rsi: float,
    ) -> PreBreakoutSnapshot:
        """Execute state transition, persist to Redis, return snapshot."""
        old_state = state.pre_breakout_state or PBState.IDLE
        entered_us = state.pre_breakout_entered_us or ts
        duration_sec = (ts - entered_us) / 1_000_000 if entered_us > 0 else 0.0

        # Compute readiness score
        readiness = self._compute_readiness(
            new_state, bb_width, rel_vol, rsi, state.bb_width_declining_count
        )

        snapshot = PreBreakoutSnapshot(
            symbol=state.symbol,
            state=new_state.value,
            prev_state=old_state,
            entered_at_us=ts,
            duration_sec=duration_sec,
            readiness_score=readiness,
            transition_reason=reason,
            bb_width=bb_width,
            bb_width_declining_count=state.bb_width_declining_count,
            rel_vol=rel_vol,
            rsi=rsi,
            ticks_in_state=state.ticks_in_pre_breakout,
        )

        # Update scanner state
        state.pre_breakout_state = new_state.value
        state.pre_breakout_entered_us = ts
        state.ticks_in_pre_breakout = 0

        # Reset declining count on IDLE/EXPIRED
        if new_state in (PBState.IDLE, PBState.EXPIRED):
            state.bb_width_declining_count = 0

        # Persist to Redis
        await self._persist(state.symbol, snapshot, new_state)

        self._transitions += 1
        logger.info(
            "prebreak_transition",
            symbol=state.symbol,
            old=old_state,
            new=new_state.value,
            readiness=readiness,
            reason=reason,
            duration_sec=round(duration_sec, 1),
            bb_width=round(bb_width, 4),
            rel_vol=round(rel_vol, 1),
        )

        return snapshot

    def _compute_readiness(
        self,
        state: PBState,
        bb_width: float,
        rel_vol: float,
        rsi: float,
        bb_declining_count: int,
    ) -> float:
        """Compute deterministic readiness score (0-100).

        Components:
          Compression quality:  0-30  (Bollinger width tightness)
          Volume accumulation:  0-25  (relative volume magnitude)
          RSI positioning:      0-15  (sweet spot proximity)
          Trend consistency:    0-15  (declining tick count)
          State advancement:    0-15  (higher state = more ready)
        """
        score = 0.0

        # ── Compression quality (0-30) ─────────────
        if bb_width > 0:
            if bb_width < 0.008:
                score += 30.0  # extreme compression
            elif bb_width < 0.012:
                score += 25.0
            elif bb_width < 0.015:
                score += 20.0
            elif bb_width < 0.02:
                score += 15.0
            elif bb_width < 0.03:
                score += 8.0

        # ── Volume accumulation (0-25) ─────────────
        if rel_vol >= 3.0:
            score += 25.0
        elif rel_vol >= 2.0:
            score += 20.0
        elif rel_vol >= 1.5:
            score += 15.0
        elif rel_vol >= 1.3:
            score += 10.0
        elif rel_vol >= 1.0:
            score += 5.0

        # ── RSI positioning (0-15) ─────────────────
        if 48 <= rsi <= 58:
            score += 15.0  # perfect coil RSI
        elif 45 <= rsi <= 62:
            score += 12.0
        elif 40 <= rsi <= 65:
            score += 8.0
        elif 35 <= rsi <= 70:
            score += 4.0

        # ── Trend consistency (0-15) ───────────────
        if bb_declining_count >= 15:
            score += 15.0
        elif bb_declining_count >= 10:
            score += 12.0
        elif bb_declining_count >= 7:
            score += 8.0
        elif bb_declining_count >= 5:
            score += 5.0

        # ── State advancement (0-15) ───────────────
        state_scores = {
            PBState.IDLE: 0.0,
            PBState.COMPRESSING: 3.0,
            PBState.ACCUMULATING: 7.0,
            PBState.COILED: 15.0,
            PBState.TRIGGERED: 15.0,
            PBState.EXPIRED: 0.0,
        }
        score += state_scores.get(state, 0.0)

        return min(score, 100.0)

    async def _persist(
        self,
        symbol: str,
        snapshot: PreBreakoutSnapshot,
        new_state: PBState,
    ) -> None:
        """Write pre-breakout state to Redis hot state."""
        key = f"{KEY_PRE_BREAKOUT_PREFIX}{symbol}"

        if new_state in (PBState.IDLE, PBState.EXPIRED):
            # Clean up — remove key entirely
            await self.redis.delete(key)
            return

        await self.redis.hset(
            key,
            mapping={
                "state": snapshot.state,
                "prev_state": snapshot.prev_state,
                "entered_at_us": str(snapshot.entered_at_us),
                "readiness_score": str(snapshot.readiness_score),
                "transition_reason": snapshot.transition_reason,
                "bb_width": str(snapshot.bb_width),
                "bb_declining_count": str(snapshot.bb_width_declining_count),
                "rel_vol": str(snapshot.rel_vol),
                "rsi": str(snapshot.rsi),
                "ticks_in_state": str(snapshot.ticks_in_state),
                "duration_sec": str(round(snapshot.duration_sec, 1)),
            },
        )
        await self.redis.expire(key, self._s.pb_state_ttl_sec)

    def mark_triggered(self, state: ScannerSymbolState) -> None:
        """Called by scanner engine when a breakout signal fires.

        Synchronously transitions to TRIGGERED — the next update()
        call will reset to IDLE.
        """
        state.pre_breakout_state = PBState.TRIGGERED.value
        state.ticks_in_pre_breakout = 0

    @property
    def stats(self) -> dict:
        return {"prebreak_transitions": self._transitions}
