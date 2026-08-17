"""Scanner engine orchestrator.

Receives feature vectors from the stream, runs registered strategies,
scores candidates, applies suppression gates, and publishes signals.

Pipeline per feature tick:
  1. Deserialize feature vector
  2. Get/create per-symbol state
  3. Skip if warmup not complete
  4. Run all registered strategies
  5. For each SignalCandidate:
     a. Compute conviction score + grade
     b. Evaluate suppression gates
     c. If passed: publish to signals stream, set cooldown, update active set
     d. If suppressed: publish to suppressed stream (audit)
  6. Update symbol state (prev_* values)

Design principles:
  - Event-driven: one feature tick → one evaluation pass
  - Deterministic: same feature stream → same signals
  - Bounded: state per symbol, no growing collections
  - Auditable: suppressed signals logged with reason
"""

from __future__ import annotations

import time
import uuid
import json
from datetime import datetime, timedelta, timezone

import structlog
from redis.asyncio import Redis

from scanner.config import ScannerSettings
from scanner.state import StateManager
from scanner.scoring import compute_conviction, grade_conviction, compute_risk_reward
from scanner.suppression import SuppressionGate
from scanner.pre_breakout import PreBreakoutTracker
from scanner.sector import SectorEngine
from scanner.strategies import get_strategies
from scanner.strategies.base import SignalCandidate
from scanner.ml_score import score_signal as ml_score_signal, classify_session_ist

from infusion_models.events import EventType
from infusion_models.signal import ScanSignalV2
from infusion_streams.codec import encode_event
from infusion_streams.constants import (
    STREAM_SCAN_SIGNALS,
    STREAM_SCAN_SUPPRESSED,
    MAXLEN_SIGNALS,
    MAXLEN_SCAN_SUPPRESSED,
    KEY_SIGNAL_PREFIX,
    KEY_SIGNAL_ACTIVE,
)
from infusion_common.timing import now_us
from infusion_common.sizing import compute_position_size

logger = structlog.get_logger()

JOURNAL_KEY = "infusion:journal:paper_trades"
JOURNAL_SIGNAL_IDS_KEY = "infusion:journal:signal_ids"
MAX_JOURNAL_ROWS = 300
IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")


class ScannerEngine:
    """Orchestrates strategy evaluation, scoring, and signal publishing."""

    def __init__(
        self,
        redis: Redis,
        settings: ScannerSettings,
        symbol_sectors: dict[str, str],
        symbol_lot_sizes: dict[str, int] | None = None,
    ):
        self.redis = redis
        self.settings = settings
        self.state_mgr = StateManager()
        self.suppression = SuppressionGate(redis, settings)
        self.pre_breakout = PreBreakoutTracker(redis, settings)
        self.sector = SectorEngine(redis, settings, symbol_sectors)
        self._symbol_sectors = symbol_sectors
        self._symbol_lot_sizes = symbol_lot_sizes or {}
        self._signals_emitted = 0
        self._signals_suppressed = 0
        self._evaluations = 0

    async def process_feature(self, payload: dict) -> None:
        """Process a single feature vector from the stream.

        This is the core hot path — called for every feature tick.
        """
        symbol = payload.get("symbol", "")
        if not symbol:
            return

        self._evaluations += 1
        state = self.state_mgr.get_or_create(symbol)

        # Populate sector_id from symbol map (once)
        if not state.sector_id:
            state.sector_id = self._symbol_sectors.get(symbol, "UNCATEGORIZED")

        # Update sector engine (incremental, every tick)
        await self.sector.update_symbol(symbol, payload)

        # Signal and pre-breakout logic is candle based. Live feature vectors
        # still update LTP/VWAP between closes, but must not advance indicators
        # or state-machine counters.
        if not payload.get("bar_closed_1m", False):
            return

        # Do not evaluate partially warmed indicators.
        if not payload.get("indicator_ready", False):
            state.update_from_features(payload)
            return

        # Update pre-breakout state machine (event-driven, every tick)
        await self.pre_breakout.update(symbol, payload, state)

        # Attach the real historical-MTF cache (api/routes/mtf.py's
        # compute_mtf(), kept warm by mtf_queue.py) so pine_confidence.py can
        # prefer it over its synthetic live-feature proxy. Strategies stay
        # pure functions — this is the one place the engine does I/O before
        # invoking them, same pattern as the cooldown/sector lookups below.
        mtf_cache = await self._fetch_mtf_cache(symbol)
        if mtf_cache is not None:
            payload = {**payload, "mtf_cache": mtf_cache}

        # Run all registered strategies
        strategies = get_strategies()
        for strategy in strategies:
            candidate = strategy.evaluate(payload, state)
            await self._persist_diagnostics(symbol, strategy.strategy_id, payload, state, candidate is not None)
            if candidate is not None:
                # Phase W: caller-side write-back of the watch-episode
                # ladder a strategy decided on -- evaluate() only reads
                # state.watch_episodes (strategies/base.py's contract
                # forbids mutating state from inside evaluate()), so this
                # is the one place that actually persists it, same as
                # state.update_from_features() below being a caller-side,
                # post-evaluate mutation rather than something the
                # strategy does itself.
                if candidate.episode_key and candidate.episode_snapshot is not None:
                    state.watch_episodes[candidate.episode_key] = candidate.episode_snapshot
                await self._process_candidate(candidate, payload, state)

        # Update state AFTER evaluation (so prev_* reflects pre-evaluation)
        state.update_from_features(payload)

    async def _recommended_lots(
        self, symbol: str, entry_price: float, invalidation_price: float,
        atr: float | None = None, strategy_id: str = "",
    ) -> dict:
        """Signal-time position-size estimate using underlying entry/stop —
        matches simple_structure_pivot_ma_plan_v6.pine's Position Sizing
        (1% Rule). This is an early estimate for the dashboard/alert to show
        immediately; api/routes/execution.py recomputes the authoritative
        number once real option premium/delta are available at staging.

        `atr` (underlying ATR, price terms) is optional and, when supplied,
        activates the Turtle-style volatility cap in compute_position_size —
        see libs/infusion_common/sizing.py for why this exists (a tight
        stop alone can imply a larger size than the instrument's actual
        volatility supports). Not threaded into api/routes/execution.py's
        call: that one sizes off option PREMIUM risk, a different unit from
        the underlying's ATR, and would need a delta-scaled ATR proxy to be
        correct there rather than the raw underlying value — left as a
        separate, not-yet-done enhancement rather than wiring in a
        unit-mismatched number.
        """
        per_unit_risk = abs(entry_price - invalidation_price)
        lot_size = self._symbol_lot_sizes.get(symbol, 1)
        try:
            raw = await self.redis.get("infusion:risk:settings")
            settings = json.loads(raw.decode() if isinstance(raw, bytes) else raw) if raw else {}
        except Exception:
            settings = {}
        risk_amount = float(settings.get("risk_per_trade_amount") or 0.0)
        if risk_amount <= 0:
            capital = float(settings.get("capital") or 50000.0)
            risk_pct = float(settings.get("risk_per_trade_pct") or 1.0)
            risk_amount = capital * risk_pct / 100.0
        max_lots = int(settings.get("max_lots") or 5)
        sizing = compute_position_size(risk_amount, per_unit_risk, lot_size, max_lots=max_lots, atr=atr)
        kelly = await self._read_kelly_sizing(strategy_id) if strategy_id else {}
        vix = await self._read_vix_multiplier()
        return {**sizing, "lot_size": lot_size, "risk_amount": round(risk_amount, 2), **kelly, **vix}

    async def _read_vix_multiplier(self) -> dict:
        """Best-effort read of the India VIX-tiered size multiplier the
        scheduler's ~5-min sweep writes (see compute_vix_multiplier() in
        api/routes/market.py, api/vix_sizing.py). Informational only,
        added alongside (never replacing) the ATR-scaled sizing and
        half-Kelly read above. Never raises into the hot path: a missing/
        stale cache (e.g. Upstox auth expired, or the sweep hasn't run
        yet) just means these fields come back empty.
        """
        try:
            raw = await self.redis.get("infusion:vix:multiplier")
            if not raw:
                return {}
            text = raw.decode() if isinstance(raw, bytes) else raw
            decoded = json.loads(text)
            if not decoded.get("available"):
                return {}
            return {
                "vix_level": decoded.get("vix_level"),
                "vix_tier": decoded.get("vix_tier"),
                "vix_size_multiplier_pct": decoded.get("vix_size_multiplier_pct"),
            }
        except Exception:
            return {}

    async def _read_kelly_sizing(self, strategy_id: str) -> dict:
        """Phase 13.10: best-effort read of the half-Kelly sizing stat the
        scheduler's daily sweep writes (see compute_kelly_sizing() in
        api/routes/backtest.py) -- informational only, added alongside
        (never replacing) the ATR-scaled sizing above. Never raises into
        the hot path: a missing/stale cache just means these fields come
        back empty, same as any other best-effort Redis read in this file.
        """
        try:
            raw = await self.redis.hgetall(f"infusion:kelly:{strategy_id}")
            if not raw:
                return {}
            decoded = {}
            for k, v in raw.items():
                key = k.decode() if isinstance(k, bytes) else k
                val = v.decode() if isinstance(v, bytes) else v
                decoded[key] = val
            half_kelly = decoded.get("half_kelly_pct")
            return {
                "kelly_reliable": decoded.get("reliable") == "True",
                "kelly_half_pct": float(half_kelly) if half_kelly not in (None, "None") else None,
                "kelly_win_rate_pct": float(decoded["win_rate_pct"]) if decoded.get("win_rate_pct") not in (None, "None") else None,
                "kelly_sample_size": int(float(decoded["decided"])) if decoded.get("decided") else 0,
            }
        except Exception:
            return {}

    async def _fetch_mtf_cache(self, symbol: str) -> dict | None:
        """Best-effort read of the cached historical-MTF payload.

        Never raises into the hot path: any Redis/decode failure just means
        pine_confidence.py falls back to its synthetic proxy, same as if the
        cache entry didn't exist yet.
        """
        try:
            raw = await self.redis.get(f"infusion:mtf:{symbol.upper()}")
            if not raw:
                return None
            text = raw.decode() if isinstance(raw, bytes) else raw
            return json.loads(text)
        except Exception:
            return None

    async def _persist_diagnostics(self, symbol, strategy_id, features, state, candidate):
        """Persist explainable gates for zero-signal investigation."""
        if strategy_id != "vol_vwap_breakout":
            return
        rel_vol = float(features.get("rel_vol_20d", 0.0))
        ltp = float(features.get("ltp", 0.0))
        vwap = float(features.get("vwap", 0.0))
        rsi = float(features.get("rsi_14", 0.0))
        ema9 = float(features.get("ema_9", 0.0))
        bb = float(features.get("bb_width", 0.0))
        flow = float(features.get("order_imbalance", 0.0))
        spread = float(features.get("spread_bps", 0.0))
        gates = {
            "history_ready": {"value": bool(features.get("volume_profile_ready")), "pass": bool(features.get("volume_profile_ready"))},
            "indicators_ready": {"value": bool(features.get("indicator_ready")), "pass": bool(features.get("indicator_ready"))},
            "volume_expansion": {"value": rel_vol, "required": self.settings.vvb_min_rel_vol, "pass": rel_vol >= self.settings.vvb_min_rel_vol},
            "vwap_reclaim": {"value": ltp - vwap, "pass": ltp > vwap and state.prev_ltp <= (state.prev_vwap or vwap)},
            "above_ema9": {"value": ltp - ema9, "pass": ltp > ema9 > 0},
            "rsi_range": {"value": rsi, "required": [self.settings.vvb_min_rsi, self.settings.vvb_max_rsi], "pass": self.settings.vvb_min_rsi < rsi < self.settings.vvb_max_rsi},
            "bollinger_context": {"value": bb, "required": self.settings.vvb_min_bb_width, "pass": bb > self.settings.vvb_min_bb_width},
            "order_flow": {"value": flow, "required": self.settings.vvb_min_order_imbalance, "pass": flow > self.settings.vvb_min_order_imbalance},
            "spread": {"value": spread, "required": self.settings.vvb_max_spread_bps, "pass": spread < self.settings.vvb_max_spread_bps},
        }
        await self.redis.hset(f"infusion:signal-diagnostics:{symbol}", mapping={
            "symbol": symbol, "strategy_id": strategy_id,
            "candidate": "1" if candidate else "0",
            "evaluated_at_us": str(now_us()), "gates": json.dumps(gates),
        })
        await self.redis.expire(f"infusion:signal-diagnostics:{symbol}", 86400)

    async def _process_candidate(
        self,
        candidate: SignalCandidate,
        features: dict,
        state,
    ) -> None:
        """Score, suppress, and publish a signal candidate."""

        # ── Score ──────────────────────────────────────
        score, sub_scores = compute_conviction(candidate.features_snapshot)

        # ── Sector conviction adjustment ───────────────
        sector_adj, sector_explanations = self.sector.compute_sector_adjustment(
            state.sector_id, candidate.symbol, candidate.signal_type
        )
        score = min(max(score + sector_adj, 0), 100)
        if sector_adj != 0:
            sub_scores["sector_adjustment"] = sector_adj

        # ── Cross-index confirmation (Phase 9, informational) ──
        # Dow-Theory-style 2-of-3 multi-measure check + NSE concentration-cap
        # awareness. Does NOT adjust `score` -- see compute_cross_confirmation's
        # docstring for why not wiring this into the already-tuned score yet.
        sub_scores["cross_confirmation"] = self.sector.compute_cross_confirmation(
            state.sector_id, candidate.symbol, candidate.signal_type
        )

        grade = grade_conviction(score)
        rr = compute_risk_reward(
            candidate.entry_price,
            candidate.invalidation_price,
            candidate.target_price,
        )

        # ── Position sizing (early estimate) ───────────
        atr_for_sizing = candidate.features_snapshot.get("atr_14")
        sub_scores["position_sizing"] = await self._recommended_lots(
            candidate.symbol, candidate.entry_price, candidate.invalidation_price,
            atr=atr_for_sizing, strategy_id=candidate.strategy_id,
        )

        created_us = now_us()
        signal_id = str(uuid.uuid4())

        # ── Sector context ─────────────────────────────
        sector_strength = self.sector.get_sector_strength(state.sector_id)
        market_regime = self.sector.regime.value

        # ── ML classifier (informational, Phase: trained ML classifier) ──
        # Applies the cached, api-trained logistic regression model (see
        # scanner/ml_score.py) to this exact candidate's own core metadata
        # + features_snapshot/sub_scores -- best-effort, {} on any failure
        # or missing model, same convention as _read_kelly_sizing above.
        # session_hour derived here matches EXACTLY what archiver/writer.py
        # will independently derive for this same signal's own row (same
        # created_us moment, same bucketing rule), so the live score is
        # always evaluated on the identical encoding its own archived
        # outcome will later be judged against.
        sub_scores["ml_classifier"] = await ml_score_signal(
            self.redis,
            core={
                "conviction_score": score,
                "risk_reward_ratio": rr,
                "conviction_grade": grade,
                "session_hour": classify_session_ist(created_us),
                "strategy": candidate.strategy_id,
                "sector_id": state.sector_id,
                "market_regime": market_regime,
                "pre_breakout_state": state.pre_breakout_state,
            },
            features=candidate.features_snapshot,
            sub_scores=sub_scores,
        )

        # ── Suppression gate ───────────────────────────
        result = await self.suppression.evaluate(
            symbol=candidate.symbol,
            strategy_id=candidate.strategy_id,
            conviction_score=score,
            sector_id=state.sector_id,
            market_regime=market_regime,
            signal_type=candidate.signal_type,
            risk_reward_ratio=rr,
        )

        # ── Build signal ──────────────────────────────
        signal = ScanSignalV2(
            signal_id=signal_id,
            symbol=candidate.symbol,
            strategy_id=candidate.strategy_id,
            signal_type=candidate.signal_type,
            lifecycle="active" if result.passed else "suppressed",
            created_at_us=created_us,
            confirmed_at_us=created_us,
            ttl_sec=self.settings.signal_ttl_sec,
            conviction_score=score,
            conviction_grade=grade,
            sub_scores=sub_scores,
            price_at_signal=candidate.features_snapshot.get("ltp", 0.0),
            entry_price=candidate.entry_price,
            invalidation_price=candidate.invalidation_price,
            target_price=candidate.target_price,
            risk_reward_ratio=rr,
            features_snapshot=candidate.features_snapshot,
            sector_id=state.sector_id,
            sector_strength=sector_strength,
            market_regime=market_regime,
            pre_breakout_state=state.pre_breakout_state,
            tier=state.tier,
            suppressed=not result.passed,
            suppression_reason=result.reason if not result.passed else "",
            explanation=self._enrich_explanation(
                candidate.explanation, state, sector_explanations
            ),
            conditions_met=candidate.conditions_met,
        )

        if result.passed:
            await self._publish_active(signal, state)
        else:
            await self._publish_suppressed(signal)

    def _enrich_explanation(
        self, explanation: list[str], state, sector_explanations: list[str] | None = None
    ) -> list[str]:
        """Enrich signal explanation with pre-breakout and sector context."""
        enriched = list(explanation)  # copy to preserve determinism
        pb = state.pre_breakout_state
        if pb and pb not in ("idle", "expired"):
            entered_us = state.pre_breakout_entered_us
            if entered_us > 0:
                duration_sec = (now_us() - entered_us) / 1_000_000
                duration_min = duration_sec / 60
                enriched.append(
                    f"Pre-breakout: was {pb.upper()} for {duration_min:.1f} min"
                )
            else:
                enriched.append(f"Pre-breakout state: {pb.upper()}")
        if sector_explanations:
            enriched.extend(sector_explanations)
        return enriched

    async def _publish_active(self, signal: ScanSignalV2, state=None) -> None:
        """Publish confirmed signal and update active state."""
        payload = signal.model_dump()
        encoded = encode_event(
            EventType.SCAN_SIGNAL, payload, signal.created_at_us
        )

        # Publish to signals stream
        await self.redis.xadd(
            STREAM_SCAN_SIGNALS,
            {"data": encoded},
            maxlen=MAXLEN_SIGNALS,
            approximate=True,
        )

        # Update hot state: full intelligence payload per symbol
        # All fields from ScanSignalV2 persisted for dashboard consumption
        import json as _json
        conditions_str = _json.dumps(signal.conditions_met) if signal.conditions_met else "{}"
        sub_scores_str = _json.dumps(signal.sub_scores) if signal.sub_scores else "{}"
        snapshot_str = _json.dumps(signal.features_snapshot) if signal.features_snapshot else "{}"
        explanation_str = " | ".join(signal.explanation) if signal.explanation else ""
        fs = signal.features_snapshot or {}

        signal_mapping = {
                # Identity
                "signal_id": signal.signal_id,
                "symbol": signal.symbol,
                "strategy_id": signal.strategy_id,
                "signal_type": signal.signal_type,       # "bullish" | "bearish"
                "option_bias": str(signal.features_snapshot.get("option_bias", "")),
                "lifecycle": signal.lifecycle,

                # Scoring
                "conviction_score": str(round(signal.conviction_score, 2)),
                "conviction_grade": signal.conviction_grade,
                "sub_scores": sub_scores_str,
                "features_snapshot": snapshot_str,
                "pine_confidence": str(round(float(fs.get("pine_confidence") or 0), 2)),
                "bull_confidence": str(round(float(fs.get("bull_confidence") or 0), 2)),
                "bear_confidence": str(round(float(fs.get("bear_confidence") or 0), 2)),
                "mtf_text": str(fs.get("mtf_text") or ""),
                "mtf": _json.dumps(fs.get("mtf") or {}),
                "mtf_dots": _json.dumps(fs.get("mtf_dots") or {}),
                "anti_chase_ok": "1" if fs.get("anti_chase_ok") else "0",
                "anti_chase_reasons": _json.dumps(fs.get("anti_chase_reasons") or []),
                "rejection_reasons": _json.dumps(fs.get("rejection_reasons") or []),
                "primary_trade_map": _json.dumps(fs.get("primary_trade_map") or {}),
                "mtf_alternate_trade_map": _json.dumps(fs.get("mtf_alternate_trade_map") or {}),
                "mtf_conflict": "1" if fs.get("mtf_conflict") else "0",
                "mtf_conflict_note": str(fs.get("mtf_conflict_note") or ""),
                "breakout_explanation": str(fs.get("breakout_explanation") or ""),
                "trade_horizon": str(fs.get("trade_horizon") or ""),
                "chase_quality": str(fs.get("chase_quality") or ""),
                "sustain_rule": str(fs.get("sustain_rule") or ""),

                # Price levels
                "price_at_signal": str(round(signal.price_at_signal, 2)),
                "entry_price": str(round(signal.entry_price, 2)),
                "invalidation_price": str(round(signal.invalidation_price, 2)),
                "target_price": str(round(signal.target_price, 2)),
                "t1_price": str(round(float(fs.get("t1_price") or signal.target_price or 0), 2)),
                "t2_price": str(round(float(fs.get("t2_price") or signal.target_price or 0), 2)),
                "risk_per_share": str(round(float(fs.get("risk_per_share") or 0), 2)),
                "target_method": str(fs.get("target_method") or ""),
                "vwap_distance_atr": str(round(float(fs.get("vwap_distance_atr") or 0), 2)),
                "signal_candle_atr": str(round(float(fs.get("signal_candle_atr") or 0), 2)),
                "stop_distance_atr": str(round(float(fs.get("stop_distance_atr") or 0), 2)),
                "risk_reward_ratio": str(round(signal.risk_reward_ratio, 2)),

                # Context
                "sector_id": signal.sector_id,
                "sector_strength": str(round(signal.sector_strength, 2)),
                "market_regime": signal.market_regime,
                "pre_breakout_state": signal.pre_breakout_state,
                "tier": str(signal.tier),

                # Explanation & conditions
                "explanation": explanation_str,
                "conditions_met": conditions_str,

                # Timestamps
                "created_at_us": str(signal.created_at_us),
                "ttl_sec": str(signal.ttl_sec),
        }

        signal_key = f"{KEY_SIGNAL_PREFIX}{signal.symbol}:{signal.strategy_id}"
        legacy_signal_key = f"{KEY_SIGNAL_PREFIX}{signal.symbol}"
        await self.redis.hset(
            signal_key,
            mapping=signal_mapping,
        )
        await self.redis.hset(legacy_signal_key, mapping=signal_mapping)
        await self.redis.expire(
            signal_key,
            signal.ttl_sec,
        )
        await self.redis.expire(legacy_signal_key, signal.ttl_sec)

        # Add to active signals ZSET (scored by conviction)
        member = f"{signal.symbol}:{signal.strategy_id}"
        await self.redis.zadd(KEY_SIGNAL_ACTIVE, {member: signal.conviction_score})

        # P1 data integrity: auto-log every confirmed signal once. The trader's
        # later choice becomes discretionary_action, not the existence of data.
        await self._auto_log_signal(signal, fs)

        # Schedule auto-removal from active set via TTL
        # (handled by expiry check in cleanup, or overwrite on next signal)

        # Set cooldown
        await self.suppression.set_cooldown(
            signal.symbol,
            signal.strategy_id,
            signal.signal_id,
            self.settings.cooldown_sec,
        )

        # Mark pre-breakout as TRIGGERED -- only for a genuine, chaseable
        # breakout confirmation, not every watch-tier ("Wait for trigger")
        # publish. Previously unconditional here (Phase W finding): a
        # non-chaseable candidate would flip the symbol to TRIGGERED and
        # reset it to IDLE on the very next tick, silently discarding any
        # COMPRESSING/ACCUMULATING/COILED progress PreBreakoutTracker had
        # already built up for a setup that hadn't actually broken out yet.
        if state is not None and bool(signal.features_snapshot.get("chaseable")):
            self.pre_breakout.mark_triggered(state)

        self._signals_emitted += 1
        logger.info(
            "signal_active",
            symbol=signal.symbol,
            strategy=signal.strategy_id,
            grade=signal.conviction_grade,
            score=signal.conviction_score,
            entry=signal.entry_price,
            stop=signal.invalidation_price,
            target=signal.target_price,
            rr=signal.risk_reward_ratio,
        )

    async def _auto_log_signal(self, signal: ScanSignalV2, fs: dict) -> None:
        """Write an honest journal row for every emitted signal, idempotently."""
        dedupe_id = signal.signal_id or f"{signal.symbol}:{signal.strategy_id}:{signal.created_at_us}"
        added = await self.redis.sadd(JOURNAL_SIGNAL_IDS_KEY, dedupe_id)
        await self.redis.expire(JOURNAL_SIGNAL_IDS_KEY, 86400 * 120)
        if not added:
            return

        decision = str(fs.get("option_bias") or ("BUY CE" if signal.signal_type == "bullish" else "BUY PE")).upper()
        option_bias = "CE" if "CE" in decision or signal.signal_type == "bullish" else "PE"
        risk_points = abs(float(signal.entry_price or 0) - float(signal.invalidation_price or 0))
        rr1 = round(abs(float(signal.target_price or 0) - float(signal.entry_price or 0)) / risk_points, 2) if risk_points else 0.0
        row = {
            "id": f"auto_{signal.created_at_us}_{signal.symbol}",
            "created_at_ist": _now_ist(),
            "source": "scanner_auto_signal",
            "signal_id": dedupe_id,
            "strategy_id": signal.strategy_id,
            "status": "WATCH",
            "discretionary_action": "NOT_REVIEWED",
            "skip_reason": "",
            "symbol": signal.symbol,
            "sector": signal.sector_id,
            "decision": decision,
            "ltp": round(float(signal.price_at_signal or 0), 4),
            "entry": round(float(signal.entry_price or 0), 4),
            "stop": round(float(signal.invalidation_price or 0), 4),
            "target1": round(float(fs.get("t1_price") or signal.target_price or 0), 4),
            "target2": round(float(fs.get("t2_price") or signal.target_price or 0), 4),
            "rr1": rr1,
            "setup_strength": round(float(fs.get("setup_strength") or signal.conviction_score or 0), 4),
            "option_readiness": round(float(fs.get("option_readiness") or signal.conviction_score or 0), 4),
            "mtf_score": round(float(fs.get("mtf_score") or fs.get("pine_confidence") or 0), 4),
            "mtf_text": str(fs.get("mtf_text") or ""),
            "positive_above": round(float(fs.get("positive_above") or signal.entry_price or 0), 4),
            "negative_below": round(float(fs.get("negative_below") or signal.invalidation_price or 0), 4),
            "risk_amount": 0.0,
            "risk_pct": 0.0,
            "option": {
                "execution_status": "CHAIN_PENDING",
                "quality_grade": "-",
                "trade_ready": False,
                "suggested_contract": f"{signal.symbol} ATM {option_bias} - waiting for Upstox chain",
                "instrument_key": "",
                "premium": 0.0,
                "spread_pct": 0.0,
                "oi": 0.0,
                "iv": 0.0,
                "iv_rank": None,
                "delta": 0.0,
                "premium_risk_pct": 0.0,
                "underlying_risk": risk_points,
                "required_move_pct": 0.0,
                "expected_move_pct": 0.0,
                "breakeven_underlying": 0.0,
                "gross_pnl": 0.0,
                "total_costs": 0.0,
                "net_pnl": 0.0,
                "cost_as_pct_of_premium": 0.0,
                "expiry_days": 0.0,
                "blockers": ["Option chain snapshot not attached at signal time"],
                "hard_blockers": [],
            },
            "news_edge": {
                "stance": "NO_NEWS",
                "score": 0.0,
                "confidence": "LOW",
                "action": "Use price confirmation first",
                "risks": [],
                "boosters": [],
                "blockers": [],
            },
            "strength_reasons": list(signal.explanation or [])[:8],
            "rejection_reasons": [],
            "market_regime": signal.market_regime,
            "regime_at_decision": signal.market_regime,
            "outcome": None,
        }
        await self.redis.lpush(JOURNAL_KEY, json.dumps(row, separators=(",", ":")))
        await self.redis.ltrim(JOURNAL_KEY, 0, MAX_JOURNAL_ROWS - 1)

    async def _publish_suppressed(self, signal: ScanSignalV2) -> None:
        """Publish suppressed signal to audit stream."""
        payload = signal.model_dump()
        encoded = encode_event(
            EventType.SCAN_SUPPRESSED, payload, signal.created_at_us
        )

        await self.redis.xadd(
            STREAM_SCAN_SUPPRESSED,
            {"data": encoded},
            maxlen=MAXLEN_SCAN_SUPPRESSED,
            approximate=True,
        )

        self._signals_suppressed += 1
        logger.debug(
            "signal_suppressed",
            symbol=signal.symbol,
            strategy=signal.strategy_id,
            reason=signal.suppression_reason,
            score=signal.conviction_score,
        )

    async def cleanup_expired(self) -> None:
        """Remove expired entries from active signal ZSET.

        Called periodically (e.g., every 60 seconds).
        """
        members = await self.redis.zrange(KEY_SIGNAL_ACTIVE, 0, -1)
        for member in members:
            m = member.decode() if isinstance(member, bytes) else member
            symbol = m.split(":")[0] if ":" in m else m
            key = f"{KEY_SIGNAL_PREFIX}{symbol}"
            exists = await self.redis.exists(key)
            if not exists:
                await self.redis.zrem(KEY_SIGNAL_ACTIVE, m)

    @property
    def stats(self) -> dict:
        return {
            "evaluations": self._evaluations,
            "signals_emitted": self._signals_emitted,
            "signals_suppressed": self._signals_suppressed,
            **self.state_mgr.stats,
            **self.pre_breakout.stats,
            **self.sector.stats,
        }
