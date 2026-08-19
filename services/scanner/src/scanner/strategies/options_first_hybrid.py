"""Options-first hybrid intraday strategy.

Thesis:
  For an options trader, the scanner should first decide whether the
  underlying move is good enough for CE/PE consideration, then let the
  option-chain layer confirm execution quality.

This strategy is intentionally broader than vol_vwap_breakout:
  - It can produce bullish BUY CE candidates.
  - It can produce bearish BUY PE candidates.
  - It does not require a fresh VWAP crossover; sustained trend alignment
    can qualify as a watch/trade candidate.

The alerter still controls Telegram delivery by grade/rate limits, so lower
quality candidates can exist in the system without becoming noisy alerts.
"""

from __future__ import annotations

from scanner.alignment import compute_signal_alignment
from scanner.config import ScannerSettings
from scanner.episode_manager import finalize_episode, resolve_ladder_basis
from scanner.pine_confidence import compute_pine_decision
from scanner.state import ScannerSymbolState
from scanner.strategies.base import BaseStrategy, SignalCandidate


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


class OptionsFirstHybrid(BaseStrategy):
    """Hybrid CE/PE candidate detector for intraday options trading."""

    def __init__(self, settings: ScannerSettings):
        self._s = settings

    @property
    def strategy_id(self) -> str:
        return "options_first_hybrid"

    def evaluate(
        self,
        features: dict,
        state: ScannerSymbolState,
    ) -> SignalCandidate | None:
        ltp = float(features.get("ltp") or 0.0)
        vwap = float(features.get("vwap") or 0.0)
        ema5 = float(features.get("ema_5") or 0.0)
        ema9 = float(features.get("ema_9") or 0.0)
        ema20 = float(features.get("ema_20") or 0.0)
        ema50 = float(features.get("ema_50") or 0.0)
        rsi = float(features.get("rsi_14") or 50.0)
        macd = float(features.get("macd") or 0.0)
        macd_signal = float(features.get("macd_signal") or 0.0)
        macd_hist = float(features.get("macd_hist") or 0.0)
        rel_vol = float(features.get("rel_vol_20d") or 0.0)
        bb_width = float(features.get("bb_width") or 0.0)
        atr = float(features.get("atr_14") or 0.0)
        atr_trail_stop = float(features.get("atr_trail_stop") or 0.0)
        atr_trend = str(features.get("atr_trend") or "NEUTRAL").upper()
        squeeze_state = str(features.get("squeeze_state") or "").upper()
        nr_pattern = str(features.get("nr_pattern") or "")
        candle_pattern = str(features.get("candle_pattern") or "")
        spread_bps = float(features.get("spread_bps") or 999.0)
        change_pct = float(features.get("change_pct") or 0.0)
        flow = float(features.get("order_imbalance") or 0.0)

        if ltp <= 0 or vwap <= 0 or not features.get("indicator_ready", False):
            return None

        above_vwap = ltp > vwap
        below_vwap = ltp < vwap
        ema_bull = ltp > ema5 > ema9 > ema20 > 0 or ltp > ema9 > ema20 > 0
        ema_bear = ltp < ema5 < ema9 < ema20 and ema20 > 0 or ltp < ema9 < ema20 and ema20 > 0
        macd_bull = macd > macd_signal and macd_hist > 0
        macd_bear = macd < macd_signal and macd_hist < 0
        rsi_bull = 50 <= rsi <= 72
        rsi_bear = 28 <= rsi <= 50
        volume_ok = rel_vol >= self._s.options_hybrid_min_rel_vol
        compression_ok = bb_width > 0 and bb_width <= 0.018
        pk_squeeze_ok = squeeze_state in {"EXTREME", "COILED", "BUILDING"} or nr_pattern in {"NR4", "NR7"}
        liquid_ok = spread_bps < self._s.options_hybrid_max_spread_bps
        atr_bull = atr_trend == "BULL" and (atr_trail_stop <= 0 or ltp > atr_trail_stop)
        atr_bear = atr_trend == "BEAR" and (atr_trail_stop <= 0 or ltp < atr_trail_stop)
        bull_candle = candle_pattern in {"Bullish Engulfing", "Hammer"}
        bear_candle = candle_pattern in {"Bearish Engulfing", "Shooting Star"}

        bull_gates = {
            "above_vwap": above_vwap,
            "ema_bull": ema_bull,
            "macd_bull": macd_bull,
            "rsi_bull_zone": rsi_bull,
            "atr_trail_bull": atr_bull,
            "positive_change": change_pct >= 0,
            "volume_or_squeeze": volume_ok or compression_ok or pk_squeeze_ok,
            "liquid_underlying": liquid_ok,
        }
        bear_gates = {
            "below_vwap": below_vwap,
            "ema_bear": ema_bear,
            "macd_bear": macd_bear,
            "rsi_bear_zone": rsi_bear,
            "atr_trail_bear": atr_bear,
            "negative_change": change_pct <= 0,
            "volume_or_squeeze": volume_ok or compression_ok or pk_squeeze_ok,
            "liquid_underlying": liquid_ok,
        }

        bull_count = sum(1 for ok in bull_gates.values() if ok)
        bear_count = sum(1 for ok in bear_gates.values() if ok)
        if bull_count == bear_count:
            return None

        bullish = bull_count > bear_count
        gates = bull_gates if bullish else bear_gates
        core_count = bull_count if bullish else bear_count
        if core_count < self._s.options_hybrid_min_core_gates:
            return None

        score = self._score(
            bullish=bullish,
            ltp=ltp,
            vwap=vwap,
            ema_ok=ema_bull if bullish else ema_bear,
            macd_ok=macd_bull if bullish else macd_bear,
            rsi=rsi,
            rel_vol=rel_vol,
            bb_width=bb_width,
            atr_ok=atr_bull if bullish else atr_bear,
            pk_squeeze_ok=pk_squeeze_ok,
            candle_ok=bull_candle if bullish else bear_candle,
            spread_bps=spread_bps,
            flow=flow,
            change_pct=change_pct,
        )
        if score < self._s.options_hybrid_min_score:
            return None

        signal_type = "bullish" if bullish else "bearish"
        option_bias = "BUY CE" if bullish else "BUY PE"

        # Phase W (EBIE EB-1: now via the shared episode_manager rather
        # than inline logic -- see that module's docstring): reuse a
        # still-open watch episode's frozen ladder instead of recomputing
        # entry/SL/T1-T3 off today's live `ltp` every cycle -- the
        # confirmed root cause of the same setup re-alerting with a
        # different, drifted price ladder each time (GRASIM: 3x today, 3
        # different ladders, all "Wait for trigger"). Read-only on `state`
        # here (strategies/base.py's contract forbids mutating it) --
        # engine.py writes the episode back via candidate.episode_key/
        # episode_snapshot after evaluate() returns.
        now_us = int(features.get("timestamp_us") or 0)
        episode_key = f"{self.strategy_id}:{signal_type}"
        episode = state.watch_episodes.get(episode_key)
        ttl_us = self._s.options_hybrid_watch_ttl_min * 60 * 1_000_000

        def _invalidated(frozen_stop: float) -> bool:
            return (bullish and ltp < frozen_stop) or (not bullish and ltp > frozen_stop)

        def _fresh_entry_invalidation() -> tuple[float, float]:
            entry = ltp
            atr_safe = atr if atr > 0 else max(ltp * 0.0045, 0.50)
            stop_buffer = max(atr_safe * 0.85, ltp * 0.0035, 0.50)
            return entry, (entry - stop_buffer if bullish else entry + stop_buffer)

        basis = resolve_ladder_basis(
            episode=episode, now_us=now_us, ttl_us=ttl_us,
            invalidated=_invalidated, compute_fresh_entry_invalidation=_fresh_entry_invalidation,
        )
        entry = basis.entry_price
        invalidation = basis.invalidation_price
        pine = compute_pine_decision(features, bullish=bullish, entry=entry, invalidation=invalidation)
        suppress, episode_snapshot, target, target2, target3, effective_risk, target_method = finalize_episode(
            basis, pine, bool(pine.chaseable)
        )
        if suppress:
            # Same still-open episode, nothing changed since the last
            # alert (or it was already chaseable last cycle too) -- the
            # actual fix: stay silent instead of re-publishing/re-
            # alerting/re-archiving a structurally identical candidate.
            # `_persist_diagnostics` in engine.py only instruments
            # vol_vwap_breakout, so returning None here has no other
            # observability cost.
            return None

        explanation = [
            f"{option_bias} candidate from options-first hybrid model",
            f"Trend gates passed {core_count}/{len(gates)}",
            f"Pine confidence CE {pine.bull_confidence:.0f} / PE {pine.bear_confidence:.0f}",
            f"MTF: {pine.mtf_text}",
            f"VWAP {'above' if above_vwap else 'below'} price context",
            f"EMA {'bullish' if ema_bull else 'bearish' if ema_bear else 'mixed'}",
            f"MACD {'bullish' if macd_bull else 'bearish' if macd_bear else 'flat'}",
            f"ATR trail {atr_trend.lower()}",
            f"RSI {rsi:.1f}",
        ]
        if volume_ok:
            explanation.append(f"Relative volume {rel_vol:.1f}x")
        if compression_ok:
            explanation.append(f"Bollinger squeeze width {bb_width:.4f}")
        if nr_pattern:
            explanation.append(f"{nr_pattern} narrow-range setup")
        if squeeze_state and squeeze_state != "NA":
            explanation.append(f"Squeeze state {squeeze_state}")
        if candle_pattern:
            explanation.append(f"Candle pattern: {candle_pattern}")
        if liquid_ok:
            explanation.append(f"Spread {spread_bps:.1f} bps acceptable")
        if pine.anti_chase_ok:
            explanation.append("Anti-chase: clean location")
        else:
            explanation.extend([f"Anti-chase: {reason}" for reason in pine.anti_chase_reasons[:2]])

        ml = features.get("ml_features") or {}
        mtf_cache = features.get("mtf_cache") or {}
        ma_regime = mtf_cache.get("ma_regime") or {}
        chart_patterns = mtf_cache.get("chart_patterns") or []
        donchian = mtf_cache.get("donchian") or {}
        wyckoff_failure = mtf_cache.get("wyckoff_structural_failure")
        wyckoff_sot = mtf_cache.get("wyckoff_sot")
        wyckoff_sos_sow = mtf_cache.get("wyckoff_sos_sow")
        vcp = mtf_cache.get("vcp") or {}
        alignment = compute_signal_alignment(
            bullish=bullish, ml=ml, ma_regime=ma_regime, donchian=donchian,
            wyckoff_sos_sow=wyckoff_sos_sow, atr_trend=atr_trend, candle_pattern=candle_pattern,
        )
        snapshot = {
            "ltp": ltp,
            "vwap": vwap,
            "ema_5": ema5,
            "ema_9": ema9,
            "ema_20": ema20,
            "ema_50": ema50,
            "rsi_14": rsi,
            "macd": macd,
            "macd_signal": macd_signal,
            "macd_hist": macd_hist,
            "rel_vol_20d": rel_vol,
            "bb_width": bb_width,
            "atr_14": atr,
            "atr_trail_stop": atr_trail_stop,
            "atr_trend": atr_trend,
            "squeeze_state": squeeze_state,
            "nr_pattern": nr_pattern,
            "candle_pattern": candle_pattern,
            "spread_bps": spread_bps,
            "change_pct": change_pct,
            "order_imbalance": flow,
            "direction": signal_type,
            "option_bias": option_bias,
            "hybrid_raw_score": round(score, 1),
            "pine_confidence": round(pine.dominant_confidence, 1),
            "bull_confidence": round(pine.bull_confidence, 1),
            "bear_confidence": round(pine.bear_confidence, 1),
            "mtf_text": pine.mtf_text,
            "mtf_source": pine.mtf_source,
            "strength_score": pine.strength_score,
            "chaseable": pine.chaseable,
            "anti_chase_ok": pine.anti_chase_ok,
            "anti_chase_reasons": pine.anti_chase_reasons,
            "rejection_reasons": pine.rejection_reasons,
            "t1_price": round(pine.t1_price, 2),
            "t2_price": round(target2, 2),
            "t3_price": round(target3, 2),
            "risk_per_share": round(effective_risk, 2),
            "target_method": target_method,
            "vwap_distance_atr": round(pine.vwap_distance_atr, 2),
            "signal_candle_atr": round(pine.signal_candle_atr, 2),
            "stop_distance_atr": round(pine.stop_distance_atr, 2),
            "mtf": pine.mtf,
            "mtf_dots": pine.mtf_dots,
            "core_gates_passed": core_count,
            "strategy_family": "options_first",
            # Structure/zone story from feature_engine (Pine v6 alignment) —
            # same vocabulary the TradingView chart shows, so alerter and AI
            # advisor can say the same thing the chart says.
            "trend_state": ml.get("trend_state"),
            "trend_text": ml.get("trend_text"),
            "last_event_label": ml.get("last_event_label"),
            "supply_zone_top": ml.get("supply_zone_top"),
            "supply_zone_bottom": ml.get("supply_zone_bottom"),
            "demand_zone_top": ml.get("demand_zone_top"),
            "demand_zone_bottom": ml.get("demand_zone_bottom"),
            # Fibonacci confluence — alternate T1/T2/T3 mode alongside the
            # ATR-based one above; None until a >=3-level cluster forms near
            # price (see pine_confidence.compute_fib_targets).
            "fib_targets": pine.fib_targets,
            "fib_cluster_center": ml.get("fib_cluster_center"),
            "fib_cluster_hits": ml.get("fib_cluster_hits"),
            # Daily golden/death-cross regime (Phase 4) -- informational only,
            # not wired into conviction score/suppression. See _ma_regime()
            # in api/routes/mtf.py for why.
            "ma_regime": ma_regime.get("regime"),
            "ma_regime_stack": ma_regime.get("stack"),
            "ma_regime_cross_recent": ma_regime.get("cross_recent"),
            # Chart-pattern geometry (Phase 5) -- daily-timeframe only, see
            # api/chart_patterns.py. Informational, not wired into score.
            "chart_patterns": chart_patterns,
            # ICT: FVG + proper Order Block + liquidity sweep (Phase 6), see
            # feature_engine/features/ict.py. Also informational only.
            "fvg_bullish_ce": ml.get("fvg_bullish_ce"),
            "fvg_bearish_ce": ml.get("fvg_bearish_ce"),
            "last_liquidity_sweep": ml.get("last_liquidity_sweep"),
            "order_block_bullish_low": ml.get("order_block_bullish_low"),
            "order_block_bullish_high": ml.get("order_block_bullish_high"),
            "order_block_bullish_validated": ml.get("order_block_bullish_validated"),
            "order_block_bearish_low": ml.get("order_block_bearish_low"),
            "order_block_bearish_high": ml.get("order_block_bearish_high"),
            "order_block_bearish_validated": ml.get("order_block_bearish_validated"),
            # Volman entry-timing layer (Phase 10) -- see
            # feature_engine/features/volman.py. Informational only.
            "volman_source": ml.get("volman_source"),
            "volman_quality": ml.get("volman_quality"),
            "volman_entry_triggered": ml.get("volman_entry_triggered"),
            # Donchian N-day channel (Phase 7), informational -- see
            # api/routes/mtf.py's _donchian_channel().
            "donchian_high": donchian.get("high"),
            "donchian_low": donchian.get("low"),
            "donchian_fresh_high_breakout": donchian.get("fresh_high_breakout"),
            "donchian_fresh_low_breakout": donchian.get("fresh_low_breakout"),
            # Wyckoff (partial, Phase 8) -- daily timeframe, see api/wyckoff.py.
            "wyckoff_structural_failure": wyckoff_failure,
            "wyckoff_sot": wyckoff_sot,
            "wyckoff_sos_sow": wyckoff_sos_sow,
            # Session VWAP standard-deviation bands (Phase 13.5) -- see
            # feature_engine/features/price.py's get_vwap_sd_bands().
            # Informational only, not wired into score.
            "vwap_stdev": ml.get("vwap_stdev"),
            "vwap_sd1_upper": ml.get("vwap_sd1_upper"),
            "vwap_sd1_lower": ml.get("vwap_sd1_lower"),
            "vwap_sd2_upper": ml.get("vwap_sd2_upper"),
            "vwap_sd2_lower": ml.get("vwap_sd2_lower"),
            "vwap_sd_ready": ml.get("vwap_sd_ready"),
            # 52-week high/low distance (Phase 13.5) -- see
            # api/routes/mtf.py's _week52_stats(). Informational only.
            "week52_high": (mtf_cache.get("week52") or {}).get("week52_high"),
            "week52_low": (mtf_cache.get("week52") or {}).get("week52_low"),
            "week52_high_distance_pct": (mtf_cache.get("week52") or {}).get("week52_high_distance_pct"),
            "week52_near_high": (mtf_cache.get("week52") or {}).get("week52_near_high"),
            # NSE delivery % (Phase 13.5) -- T-1 session's confirmed delivery,
            # captured post-market by nse-scraper (see nse_scraper/delivery.py).
            # delivery_pct itself is a first-class FeatureVectorV1 field, not
            # ml_features -- read from `features` directly, not `ml`.
            # Informational only, same "not wired into score until
            # feature-ablation earns it" governance as every field above.
            "delivery_pct": features.get("delivery_pct"),
            "delivery_pct_avg_20d": ml.get("delivery_pct_avg_20d"),
            "delivery_avg_days": ml.get("delivery_avg_days"),
            # Heiken-Ashi (Phase 13.7) -- see feature_engine/features/heiken_ashi.py.
            # Informational only, a correlated-not-new confirmation of the
            # existing Supertrend/MA-stack/ADX trend read.
            "ha_trend": ml.get("ha_trend"),
            "ha_trend_streak": ml.get("ha_trend_streak"),
            "ha_doji": ml.get("ha_doji"),
            "ha_color_flip": ml.get("ha_color_flip"),
            # Signal alignment (Phase 13.9) -- breadth-of-evidence across 8
            # independent signal families, informational only. See
            # scanner/alignment.py.
            **alignment,
            # RSI divergence (Phase 13.11) -- informational only. See
            # feature_engine/features/divergence.py.
            "rsi_divergence_bullish_regular": ml.get("rsi_divergence_bullish_regular"),
            "rsi_divergence_bullish_hidden": ml.get("rsi_divergence_bullish_hidden"),
            "rsi_divergence_bearish_regular": ml.get("rsi_divergence_bearish_regular"),
            "rsi_divergence_bearish_hidden": ml.get("rsi_divergence_bearish_hidden"),
            # VCP / Minervini Stage-2 composite (Phase 13.12) -- see
            # api/vcp.py. Daily-timeframe, informational only.
            "vcp_score": vcp.get("score"),
            "vcp_grade": vcp.get("grade"),
            "vcp_reliable": vcp.get("reliable"),
        }

        return SignalCandidate(
            symbol=state.symbol,
            strategy_id=self.strategy_id,
            signal_type=signal_type,
            conditions_met=gates,
            explanation=explanation,
            features_snapshot=snapshot,
            entry_price=entry,
            invalidation_price=invalidation,
            target_price=target,
            episode_key=episode_key,
            episode_snapshot=episode_snapshot,
        )

    def _score(
        self,
        *,
        bullish: bool,
        ltp: float,
        vwap: float,
        ema_ok: bool,
        macd_ok: bool,
        rsi: float,
        rel_vol: float,
        bb_width: float,
        atr_ok: bool,
        pk_squeeze_ok: bool,
        candle_ok: bool,
        spread_bps: float,
        flow: float,
        change_pct: float,
    ) -> float:
        directional_vwap = (ltp - vwap) / vwap * 100 if bullish else (vwap - ltp) / vwap * 100
        vwap_score = 18 if 0 < directional_vwap <= 0.8 else 14 if directional_vwap <= 1.6 else 8
        ema_score = 18 if ema_ok else 6
        macd_score = 14 if macd_ok else 4
        if bullish:
            rsi_score = 14 if 52 <= rsi <= 66 else 10 if 45 <= rsi <= 72 else 4
            flow_score = 5 if flow > 0 else 2
            change_score = 5 if change_pct >= 0 else 0
        else:
            rsi_score = 14 if 34 <= rsi <= 48 else 10 if 28 <= rsi <= 55 else 4
            flow_score = 5 if flow < 0 else 2
            change_score = 5 if change_pct <= 0 else 0
        volume_score = 14 if rel_vol >= 2.5 else 10 if rel_vol >= 1.5 else 6 if rel_vol >= 1.0 else 0
        squeeze_score = 10 if pk_squeeze_ok else 8 if 0 < bb_width <= 0.012 else 5 if bb_width <= 0.02 else 2
        atr_score = 8 if atr_ok else 2
        candle_score = 4 if candle_ok else 0
        spread_score = 4 if spread_bps < 35 else 2 if spread_bps < self._s.options_hybrid_max_spread_bps else 0
        return _clamp(
            vwap_score + ema_score + macd_score + rsi_score
            + volume_score + squeeze_score + atr_score + candle_score
            + spread_score + flow_score + change_score
        )
