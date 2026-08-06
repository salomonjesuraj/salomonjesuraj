"""Volume-VWAP Breakout strategy.

Thesis: Detect stocks where volume is expanding significantly AND price
reclaims VWAP from below, suggesting institutional participation in a
developing breakout.

Sub-conditions (ALL must be true for signal):
  1. Volume expansion: rel_vol_20d >= threshold
  2. VWAP reclaim: price crossed above VWAP (crossover)
  3. Price above EMA(9)
  4. RSI in range (momentum, not overbought)
  5. Bollinger not dead (width > threshold)
  6. Order flow positive (imbalance > 0)
  7. Spread filter (liquid, not manipulated)

Design:
  - Pure function: no I/O, no side effects
  - Deterministic: same features + state → same result
  - Conservative: intentionally under-triggers
"""

from __future__ import annotations

from scanner.config import ScannerSettings
from scanner.pine_confidence import practical_option_targets
from scanner.state import ScannerSymbolState
from scanner.strategies.base import BaseStrategy, SignalCandidate


class VolVwapBreakout(BaseStrategy):
    """Volume expansion + VWAP reclaim breakout detection."""

    def __init__(self, settings: ScannerSettings):
        self._s = settings

    @property
    def strategy_id(self) -> str:
        return "vol_vwap_breakout"

    def evaluate(
        self,
        features: dict,
        state: ScannerSymbolState,
    ) -> SignalCandidate | None:
        """Evaluate all 7 sub-conditions. Returns candidate only if ALL pass."""

        ltp = features.get("ltp", 0.0)
        vwap = features.get("vwap", 0.0)
        rel_vol = features.get("rel_vol_20d", 0.0)
        rsi = features.get("rsi_14", 50.0)
        ema_9 = features.get("ema_9", 0.0)
        bb_width = features.get("bb_width", 0.0)
        order_imbalance = features.get("order_imbalance", 0.0)
        spread_bps = features.get("spread_bps", 0.0)

        # Guard: need valid prices, warmed candle state and a historical
        # time-of-day volume profile. Missing history is not neutral volume.
        if (ltp <= 0 or vwap <= 0 or state.prev_ltp <= 0
                or not features.get("indicator_ready", False)
                or not features.get("volume_profile_ready", False)):
            return None

        conditions: dict[str, bool] = {}
        explanation: list[str] = []

        # ── Condition 1: Volume expansion ──────────────────
        vol_ok = rel_vol >= self._s.vvb_min_rel_vol
        conditions["vol_expansion"] = vol_ok
        if vol_ok:
            explanation.append(f"Volume expanded {rel_vol:.1f}x vs 20-day avg")

        # ── Condition 2: VWAP reclaim (crossover) ──────────
        # Current: ltp > vwap  AND  Previous: prev_ltp <= prev_vwap
        vwap_above = ltp > vwap
        prev_vwap = state.prev_vwap if state.prev_vwap > 0 else vwap
        vwap_was_below = state.prev_ltp <= prev_vwap
        vwap_reclaim = vwap_above and vwap_was_below
        conditions["vwap_reclaim"] = vwap_reclaim
        if vwap_reclaim:
            explanation.append(
                f"Price reclaimed VWAP (₹{vwap:,.2f}) from below"
            )

        # ── Condition 3: Price above EMA(9) ────────────────
        ema9_ok = ltp > ema_9 > 0
        conditions["above_ema9"] = ema9_ok
        if ema9_ok:
            explanation.append(f"Trading above EMA(9) at ₹{ema_9:,.2f}")

        # ── Condition 4: RSI in range ──────────────────────
        rsi_ok = self._s.vvb_min_rsi < rsi < self._s.vvb_max_rsi
        conditions["rsi_range"] = rsi_ok
        if rsi_ok:
            if rsi >= 60:
                explanation.append(f"RSI({rsi:.1f}) — strong bullish momentum")
            elif rsi >= 50:
                explanation.append(f"RSI({rsi:.1f}) — bullish momentum, not overbought")
            else:
                explanation.append(f"RSI({rsi:.1f}) — building momentum")

        # ── Condition 5: Bollinger context ─────────────────
        bb_ok = bb_width > self._s.vvb_min_bb_width
        conditions["bb_context"] = bb_ok
        if bb_ok:
            bb_upper = features.get("bb_upper", 0.0)
            if ltp > bb_upper > 0:
                explanation.append(
                    f"Breaking above Bollinger upper (width {bb_width:.3f})"
                )
            else:
                explanation.append(
                    f"Bollinger width {bb_width:.3f} — not in dead squeeze"
                )

        # ── Condition 6: Order flow ────────────────────────
        flow_ok = order_imbalance > self._s.vvb_min_order_imbalance
        conditions["order_flow"] = flow_ok
        if flow_ok:
            explanation.append(
                f"Buy-side order imbalance {order_imbalance:+.2f}"
            )

        # ── Condition 7: Spread filter ─────────────────────
        spread_ok = spread_bps < self._s.vvb_max_spread_bps
        conditions["spread_filter"] = spread_ok
        if spread_ok:
            explanation.append(f"Spread {spread_bps:.1f} bps — liquid")

        # ── All conditions must pass ───────────────────────
        if not all(conditions.values()):
            return None

        # ── Compute price levels ───────────────────────────
        atr = features.get("atr_14", 0.0)
        entry = ltp
        atr_safe = atr if atr > 0 else max(ltp * 0.0045, 0.50)
        invalidation = min(vwap - (atr_safe * 0.35), entry - max(atr_safe * 0.75, entry * 0.0035, 0.50))
        target, target2, effective_risk, target_method = practical_option_targets(
            bullish=True,
            entry=entry,
            invalidation=invalidation,
            atr=atr_safe,
            ltp=ltp,
        )

        return SignalCandidate(
            symbol=state.symbol,
            strategy_id=self.strategy_id,
            signal_type="bullish",
            conditions_met=conditions,
            explanation=explanation,
            features_snapshot={
                "ltp": ltp,
                "vwap": vwap,
                "rel_vol_20d": rel_vol,
                "rsi_14": rsi,
                "ema_9": ema_9,
                "ema_20": features.get("ema_20", 0.0),
                "atr_14": atr,
                "bb_width": bb_width,
                "bb_upper": features.get("bb_upper", 0.0),
                "order_imbalance": order_imbalance,
                "spread_bps": spread_bps,
                "change_pct": features.get("change_pct", 0.0),
                "prev_close": features.get("prev_close", 0.0),
                "t1_price": round(target, 2),
                "t2_price": round(target2, 2),
                "risk_per_share": round(effective_risk, 2),
                "target_method": target_method,
            },
            entry_price=entry,
            invalidation_price=invalidation,
            target_price=target,
        )
