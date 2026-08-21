"""Sector context layer — core ranking and filtering intelligence.

Philosophy: Strong stocks inside strong sectors during supportive market regimes.

Components:
  1. SectorState: per-sector aggregate metrics computed incrementally
  2. SectorEngine: computes breadth, strength, ranking, regime
  3. MarketRegimeEngine: classifies RISK_ON / NEUTRAL / RISK_OFF from index

Formulas (all deterministic, no fuzzy logic):

  Breadth = (above_vwap% * 0.4) + (above_ema20% * 0.3) + (positive_change% * 0.3)
  Strength = (breadth * 0.35) + (avg_rel_vol_norm * 0.20) + (avg_rsi_norm * 0.20)
             + (avg_change_norm * 0.15) + (momentum_bonus * 0.10)
  Momentum = change in strength over recent window (improving/deteriorating)

Design:
  - Incremental: each feature update → update ONE symbol in ONE sector
  - Bounded: fixed per-sector state, no growing collections
  - Deterministic: same feature stream → same rankings
  - Event-driven: no polling, no rescans
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import structlog
from infusion_common.timing import now_us
from infusion_streams.constants import KEY_SECTOR_PREFIX
from redis.asyncio import Redis

from scanner.config import ScannerSettings

logger = structlog.get_logger()


class MarketRegime(StrEnum):
    RISK_ON = "risk_on"
    NEUTRAL = "neutral"
    RISK_OFF = "risk_off"


class SectorTrend(StrEnum):
    IMPROVING = "improving"
    STABLE = "stable"
    DETERIORATING = "deteriorating"


@dataclass
class SymbolSnapshot:
    """Latest feature snapshot for a sector constituent."""

    symbol: str
    ltp: float = 0.0
    vwap: float = 0.0
    ema_20: float = 0.0
    rsi_14: float = 50.0
    rel_vol_20d: float = 1.0
    change_pct: float = 0.0
    bb_width: float = 0.0
    updated_us: int = 0


@dataclass
class SectorState:
    """Per-sector aggregate state — incrementally maintained."""

    sector_id: str
    constituents: dict[str, SymbolSnapshot] = field(default_factory=dict)

    # Computed metrics (updated on every constituent change)
    breadth_score: float = 0.0  # 0-100
    strength_score: float = 0.0  # 0-100
    avg_rsi: float = 50.0
    avg_rel_vol: float = 1.0
    avg_change_pct: float = 0.0
    above_vwap_pct: float = 0.0  # % of constituents above VWAP
    above_ema20_pct: float = 0.0  # % above EMA20
    positive_change_pct: float = 0.0  # % with positive change
    rank: int = 0  # 1 = strongest sector
    trend: str = SectorTrend.STABLE

    # Momentum tracking (strength history for rotation detection)
    prev_strength: float = 0.0
    strength_delta: float = 0.0  # current - previous
    updated_us: int = 0


class SectorEngine:
    """Computes sector breadth, strength, and rankings incrementally.

    Called by the scanner engine on every feature update.

    Usage:
        engine = SectorEngine(redis, settings, symbol_sectors)
        await engine.update_symbol(symbol, features)
        sector_state = engine.get_sector(sector_id)
        regime = engine.regime
    """

    def __init__(
        self,
        redis: Redis,
        settings: ScannerSettings,
        symbol_sectors: dict[str, str],
    ):
        self.redis = redis
        self._s = settings
        self._symbol_sectors = symbol_sectors

        # Per-sector state
        self._sectors: dict[str, SectorState] = {}

        # Reverse: sector_id → set of symbols
        self._sector_symbols: dict[str, set[str]] = {}
        for sym, sec in symbol_sectors.items():
            if sec == "INDEX":
                continue  # INDEX is not a tradeable sector
            self._sector_symbols.setdefault(sec, set()).add(sym)

        # Initialize sector states
        for sec_id in self._sector_symbols:
            self._sectors[sec_id] = SectorState(sector_id=sec_id)

        # Market regime
        self._regime = MarketRegime.NEUTRAL
        self._regime_reason = "initial"
        self._index_snapshot = SymbolSnapshot(symbol="INDEX")

        # Identify index symbols for regime classification
        self._index_symbols: set[str] = {
            sym for sym, sec in symbol_sectors.items() if sec == "INDEX"
        }

        # Stats
        self._updates = 0
        self._rank_recalcs = 0

    async def startup(self) -> None:
        """Seed sectors from existing Redis feature data, then persist.

        Reads infusion:feature:{symbol} hashes for all known symbols,
        populates constituent snapshots, computes metrics, and persists.
        This ensures sectors have real data even after restart or post-market.
        """
        seeded = 0
        for symbol, sector_id in self._symbol_sectors.items():
            if sector_id == "INDEX" or sector_id not in self._sectors:
                continue
            # Read existing feature data from Redis
            raw = await self.redis.hgetall(f"infusion:feature:{symbol}")
            if not raw:
                # Try tick data as fallback
                raw = await self.redis.hgetall(f"infusion:tick:{symbol}")
            if not raw:
                continue

            features = {}
            for k, v in raw.items():
                kk = k.decode() if isinstance(k, bytes) else k
                vv = v.decode() if isinstance(v, bytes) else v
                try:
                    features[kk] = float(vv)
                except (ValueError, TypeError):
                    features[kk] = vv

            # Populate constituent snapshot
            sector = self._sectors[sector_id]
            snap = sector.constituents.get(symbol)
            if snap is None:
                snap = SymbolSnapshot(symbol=symbol)
                sector.constituents[symbol] = snap

            snap.ltp = features.get("ltp", snap.ltp)
            snap.vwap = features.get("vwap", snap.vwap)
            snap.ema_20 = features.get("ema_20", snap.ema_20)
            snap.rsi_14 = features.get("rsi_14", snap.rsi_14)
            snap.rel_vol_20d = features.get("rel_vol_20d", snap.rel_vol_20d)
            snap.change_pct = features.get("change_pct", snap.change_pct)
            snap.bb_width = features.get("bb_width", snap.bb_width)
            snap.updated_us = int(features.get("timestamp_us", 0)) or int(
                features.get("updated_at", 0)
            )
            seeded += 1

        # Recompute all sector metrics
        for sector in self._sectors.values():
            self._recompute_sector(sector)

        self._recalculate_rankings()
        await self._persist_all()
        logger.info(
            "sector_engine_startup",
            sectors=len(self._sectors),
            seeded_symbols=seeded,
            sector_ids=sorted(self._sectors.keys()),
        )

    async def update_symbol(self, symbol: str, features: dict) -> None:
        """Update sector metrics when a symbol's features change.

        This is the hot path — called on every feature tick.
        """
        sector_id = self._symbol_sectors.get(symbol, "")

        # Update index for regime classification
        if symbol in self._index_symbols or sector_id == "INDEX":
            self._update_index(features)
            return

        if not sector_id or sector_id == "INDEX":
            return

        sector = self._sectors.get(sector_id)
        if sector is None:
            return

        # Update constituent snapshot
        snap = sector.constituents.get(symbol)
        if snap is None:
            snap = SymbolSnapshot(symbol=symbol)
            sector.constituents[symbol] = snap

        snap.ltp = features.get("ltp", snap.ltp)
        snap.vwap = features.get("vwap", snap.vwap)
        snap.ema_20 = features.get("ema_20", snap.ema_20)
        snap.rsi_14 = features.get("rsi_14", snap.rsi_14)
        snap.rel_vol_20d = features.get("rel_vol_20d", snap.rel_vol_20d)
        snap.change_pct = features.get("change_pct", snap.change_pct)
        snap.bb_width = features.get("bb_width", snap.bb_width)
        snap.updated_us = now_us()

        # Recompute sector metrics
        self._recompute_sector(sector)
        self._updates += 1

        # Recalculate rankings every 5 updates
        if self._updates % 5 == 0:
            self._recalculate_rankings()
            await self._persist_all()

    def _update_index(self, features: dict) -> None:
        """Update market regime from NIFTY50 index features."""
        self._index_snapshot.ltp = features.get("ltp", self._index_snapshot.ltp)
        self._index_snapshot.vwap = features.get("vwap", self._index_snapshot.vwap)
        self._index_snapshot.rsi_14 = features.get("rsi_14", self._index_snapshot.rsi_14)
        self._index_snapshot.rel_vol_20d = features.get(
            "rel_vol_20d", self._index_snapshot.rel_vol_20d
        )
        self._index_snapshot.change_pct = features.get(
            "change_pct", self._index_snapshot.change_pct
        )
        self._index_snapshot.bb_width = features.get("bb_width", self._index_snapshot.bb_width)
        self._index_snapshot.updated_us = now_us()

        self._classify_regime()

    def _recompute_sector(self, sector: SectorState) -> None:
        """Recompute sector metrics from constituent snapshots.

        All formulas are deterministic — same constituents → same metrics.
        """
        constituents = list(sector.constituents.values())
        if not constituents:
            return

        n = len(constituents)

        # ── Breadth components ────────────────────────
        above_vwap = sum(1 for s in constituents if s.ltp > s.vwap > 0)
        above_ema20 = sum(1 for s in constituents if s.ltp > s.ema_20 > 0)
        positive_change = sum(1 for s in constituents if s.change_pct > 0)

        sector.above_vwap_pct = (above_vwap / n) * 100
        sector.above_ema20_pct = (above_ema20 / n) * 100
        sector.positive_change_pct = (positive_change / n) * 100

        # ── Breadth score (0-100) ─────────────────────
        # Weighted: VWAP participation 40%, EMA20 30%, positive change 30%
        sector.breadth_score = (
            sector.above_vwap_pct * 0.40
            + sector.above_ema20_pct * 0.30
            + sector.positive_change_pct * 0.30
        )

        # ── Aggregates ────────────────────────────────
        sector.avg_rsi = sum(s.rsi_14 for s in constituents) / n
        sector.avg_rel_vol = sum(s.rel_vol_20d for s in constituents) / n
        sector.avg_change_pct = sum(s.change_pct for s in constituents) / n

        # ── Strength score (0-100) ────────────────────
        # Components: breadth 35%, volume 20%, RSI 20%, change 15%, momentum 10%

        # Normalize avg_rel_vol: 1.0 = 50, capped at 100
        vol_norm = min((sector.avg_rel_vol / 2.0) * 100, 100)

        # Normalize RSI: 50 center, scale to 0-100
        # RSI 30 → 0, RSI 50 → 50, RSI 70 → 100
        rsi_norm = min(max((sector.avg_rsi - 30) / 40 * 100, 0), 100)

        # Normalize change_pct: -2% → 0, 0% → 50, +2% → 100
        change_norm = min(max((sector.avg_change_pct + 2) / 4 * 100, 0), 100)

        # Momentum bonus: positive delta → up to 100
        momentum_bonus = 0.0
        if sector.strength_delta > 0:
            momentum_bonus = min(sector.strength_delta * 10, 100)

        prev_strength = sector.strength_score
        sector.strength_score = (
            sector.breadth_score * 0.35
            + vol_norm * 0.20
            + rsi_norm * 0.20
            + change_norm * 0.15
            + momentum_bonus * 0.10
        )
        sector.strength_score = min(sector.strength_score, 100.0)

        # ── Momentum delta ────────────────────────────
        sector.prev_strength = prev_strength
        sector.strength_delta = sector.strength_score - prev_strength

        # ── Trend classification ──────────────────────
        if sector.strength_delta > 2.0:
            sector.trend = SectorTrend.IMPROVING
        elif sector.strength_delta < -2.0:
            sector.trend = SectorTrend.DETERIORATING
        else:
            sector.trend = SectorTrend.STABLE

        sector.updated_us = now_us()

    def _classify_regime(self) -> None:
        """Classify market regime from NIFTY50 index.

        Deterministic rules:
          RISK_ON:  RSI > 55 AND change > 0 AND above VWAP
          RISK_OFF: RSI < 40 OR change < -1.5%
          NEUTRAL:  everything else
        """
        idx = self._index_snapshot
        old_regime = self._regime

        if idx.rsi_14 < 40 or idx.change_pct < -1.5:
            self._regime = MarketRegime.RISK_OFF
            self._regime_reason = f"index_rsi={idx.rsi_14:.1f}_change={idx.change_pct:+.2f}%"
        elif idx.rsi_14 > 55 and idx.change_pct > 0 and idx.ltp > idx.vwap > 0:
            self._regime = MarketRegime.RISK_ON
            self._regime_reason = (
                f"index_rsi={idx.rsi_14:.1f}_change={idx.change_pct:+.2f}%_above_vwap"
            )
        else:
            self._regime = MarketRegime.NEUTRAL
            self._regime_reason = f"index_rsi={idx.rsi_14:.1f}_change={idx.change_pct:+.2f}%"

        if self._regime != old_regime:
            logger.info(
                "regime_change",
                old=old_regime,
                new=self._regime,
                reason=self._regime_reason,
            )

    def _recalculate_rankings(self) -> None:
        """Rank sectors by strength_score descending. Deterministic."""
        sectors = sorted(
            self._sectors.values(),
            key=lambda s: s.strength_score,
            reverse=True,
        )
        for i, sector in enumerate(sectors, start=1):
            sector.rank = i
        self._rank_recalcs += 1

    async def _persist_all(self) -> None:
        """Persist all sector states to Redis hot state."""
        pipe = self.redis.pipeline()
        for sector in self._sectors.values():
            key = f"{KEY_SECTOR_PREFIX}{sector.sector_id}"
            # Count advancing/declining constituents
            advancing = sum(1 for s in sector.constituents.values() if s.change_pct > 0)
            declining = sum(1 for s in sector.constituents.values() if s.change_pct < 0)
            pipe.hset(
                key,
                mapping={
                    "sector_id": sector.sector_id,
                    "strength_score": str(round(sector.strength_score, 2)),
                    "breadth_score": str(round(sector.breadth_score, 2)),
                    "avg_rsi": str(round(sector.avg_rsi, 2)),
                    "avg_rel_vol": str(round(sector.avg_rel_vol, 2)),
                    "avg_change_pct": str(round(sector.avg_change_pct, 4)),
                    "above_vwap_pct": str(round(sector.above_vwap_pct, 1)),
                    "above_ema20_pct": str(round(sector.above_ema20_pct, 1)),
                    "positive_change_pct": str(round(sector.positive_change_pct, 1)),
                    "rank": str(sector.rank),
                    "trend": sector.trend,
                    "strength_delta": str(round(sector.strength_delta, 2)),
                    "constituents": str(len(sector.constituents)),
                    "advancing": str(advancing),
                    "declining": str(declining),
                    "updated_us": str(sector.updated_us),
                },
            )
            pipe.expire(key, 86400)  # 24 hour TTL

        # Persist regime
        pipe.hset(
            "infusion:regime",
            mapping={
                "regime": self._regime,
                "reason": self._regime_reason,
                "index_rsi": str(round(self._index_snapshot.rsi_14, 2)),
                "index_change_pct": str(round(self._index_snapshot.change_pct, 4)),
                "index_ltp": str(round(self._index_snapshot.ltp, 2)),
                "index_vwap": str(round(self._index_snapshot.vwap, 2)),
                "updated_us": str(self._index_snapshot.updated_us),
            },
        )
        pipe.expire("infusion:regime", 86400)

        await pipe.execute()

    # ─── Public API ──────────────────────────────────

    @property
    def regime(self) -> MarketRegime:
        return self._regime

    @property
    def regime_reason(self) -> str:
        return self._regime_reason

    def get_sector(self, sector_id: str) -> SectorState | None:
        return self._sectors.get(sector_id)

    def get_rankings(self) -> list[SectorState]:
        """Return sectors ordered by rank (1 = strongest)."""
        return sorted(self._sectors.values(), key=lambda s: s.rank or 999)

    def get_sector_strength(self, sector_id: str) -> float:
        """Return sector strength score (0-100), or 50.0 if unknown."""
        sector = self._sectors.get(sector_id)
        if sector is None:
            return 50.0
        return sector.strength_score

    def get_relative_strength(self, symbol: str, sector_id: str) -> float:
        """Compute stock-vs-sector relative strength.

        Returns delta: positive = stock outperforming sector.
        """
        sector = self._sectors.get(sector_id)
        if sector is None:
            return 0.0
        snap = sector.constituents.get(symbol)
        if snap is None:
            return 0.0
        return snap.change_pct - sector.avg_change_pct

    def compute_sector_adjustment(
        self, sector_id: str, symbol: str, signal_type: str = "bullish"
    ) -> tuple[float, list[str]]:
        """Compute conviction adjustment based on sector context.

        Returns:
            (adjustment, explanation_lines)
            adjustment: -20 to +15 points
        """
        sector = self._sectors.get(sector_id)
        if sector is None:
            return 0.0, []

        adjustment = 0.0
        explanations = []

        # ── Sector strength ────────────────────────
        if sector.strength_score >= 70:
            adjustment += 8.0
            explanations.append(
                f"Strong sector {sector_id} (strength={sector.strength_score:.0f}, rank={sector.rank})"
            )
        elif sector.strength_score >= 50:
            adjustment += 3.0
            explanations.append(
                f"Moderate sector {sector_id} (strength={sector.strength_score:.0f})"
            )
        elif sector.strength_score < 30:
            adjustment -= 10.0
            explanations.append(f"Weak sector {sector_id} (strength={sector.strength_score:.0f})")

        # ── Breadth confirmation ───────────────────
        if sector.breadth_score >= 70:
            adjustment += 5.0
            explanations.append(f"Strong breadth ({sector.above_vwap_pct:.0f}% above VWAP)")
        elif sector.breadth_score < 30:
            adjustment -= 5.0
            explanations.append(f"Weak breadth ({sector.above_vwap_pct:.0f}% above VWAP)")

        # ── Relative strength bonus ────────────────
        rel_strength = self.get_relative_strength(symbol, sector_id)
        if rel_strength > 1.0:
            adjustment += 5.0
            explanations.append(f"Outperforming sector by {rel_strength:+.2f}%")
        elif rel_strength < -1.0:
            adjustment -= 5.0
            explanations.append(f"Underperforming sector by {rel_strength:+.2f}%")

        # ── Sector trend ───────────────────────────
        if sector.trend == SectorTrend.IMPROVING:
            adjustment += 2.0
            explanations.append("Sector trend improving")
        elif sector.trend == SectorTrend.DETERIORATING:
            adjustment -= 3.0
            explanations.append("Sector trend deteriorating")

        # ── Market regime ──────────────────────────
        if self._regime == MarketRegime.RISK_ON:
            adjustment += 3.0
            explanations.append("Market regime: RISK_ON")
        elif self._regime == MarketRegime.RISK_OFF:
            adjustment -= 10.0
            explanations.append("Market regime: RISK_OFF")

        if str(signal_type).lower() == "bearish":
            adjustment = -adjustment * 0.75
            explanations.append("Bearish/PE setup: sector context inverted")

        # Clamp adjustment
        adjustment = max(min(adjustment, 15.0), -20.0)

        return adjustment, explanations

    def compute_cross_confirmation(
        self, sector_id: str, symbol: str, signal_type: str = "bullish"
    ) -> dict:
        """Dow-Theory-style multi-measure confirmation (Schannep's 2-of-3
        index rule, translated to NSE) — is this signal happening in an
        environment where multiple INDEPENDENT measures agree, or is it an
        isolated single-stock move? Also operationalizes the NSE index
        concentration-cap finding from the corpus report (SEBI allows a
        single stock up to 33% of a sector index's weight): a
        concentration_flag fires when the sector's apparent average move
        reverses sign once the single largest mover is excluded — i.e.,
        "sector strength" that's really one stock in disguise.

        Three measures, independent of each other and of the signal itself:
          1. sector_breadth: direction implied by the MAJORITY of
             constituent names (>50% positive/negative) — already
             count-based, not cap-weighted, so less concentration-prone
             than a real NSE sector index to start with.
          2. concentration_adjusted: sector_breadth's average with the
             single largest mover excluded — a second, independent read
             specifically checking whether breadth survives without that
             one name.
          3. broad_market: NIFTY50 index direction (self._index_snapshot).

        Informational only — this does NOT modify compute_sector_adjustment
        or the live conviction score. Wiring a new confirmation signal into
        an already-tuned, already-backtested score without separate
        walk-forward validation first is exactly the mistake this session
        has deliberately avoided in every phase since Phase 4.
        """
        sector = self._sectors.get(sector_id)
        empty = {
            "measures": {},
            "confirmations": [],
            "confirmation_count": 0,
            "required": 2,
            "confirmed": False,
            "concentration_flag": False,
            "top_mover_share_pct": 0.0,
        }
        if sector is None or not sector.constituents:
            return empty

        breadth_dir = (
            "bullish"
            if sector.positive_change_pct > 50
            else "bearish"
            if sector.positive_change_pct < 50
            else "neutral"
        )

        constituents = list(sector.constituents.values())
        total_abs_move = sum(abs(c.change_pct) for c in constituents)
        if total_abs_move > 0:
            top_mover = max(constituents, key=lambda c: abs(c.change_pct))
            top_mover_share_pct = abs(top_mover.change_pct) / total_abs_move * 100
            excl = [c for c in constituents if c is not top_mover]
            excl_avg = sum(c.change_pct for c in excl) / len(excl) if excl else 0.0
            concentration_dir = (
                "bullish" if excl_avg > 0.1 else "bearish" if excl_avg < -0.1 else "neutral"
            )
            concentration_flag = (sector.avg_change_pct > 0) != (
                excl_avg > 0
            ) and top_mover_share_pct > 40.0
        else:
            top_mover_share_pct = 0.0
            concentration_dir = "neutral"
            concentration_flag = False

        idx = self._index_snapshot
        market_dir = (
            "bullish" if idx.change_pct > 0.1 else "bearish" if idx.change_pct < -0.1 else "neutral"
        )

        measures = {
            "sector_breadth": breadth_dir,
            "concentration_adjusted": concentration_dir,
            "broad_market": market_dir,
        }
        signal_dir = "bearish" if str(signal_type).lower() == "bearish" else "bullish"
        confirmations = [name for name, direction in measures.items() if direction == signal_dir]

        return {
            "measures": measures,
            "confirmations": confirmations,
            "confirmation_count": len(confirmations),
            "required": 2,
            "confirmed": len(confirmations) >= 2,
            "concentration_flag": concentration_flag,
            "top_mover_share_pct": round(top_mover_share_pct, 1),
        }

    @property
    def stats(self) -> dict:
        return {
            "sector_updates": self._updates,
            "sector_rank_recalcs": self._rank_recalcs,
            "sector_count": len(self._sectors),
            "regime": self._regime,
        }
