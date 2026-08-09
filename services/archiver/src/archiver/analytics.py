"""Signal analytics engine — deterministic statistical computations.

Computes precision, grade breakdown, sector/session/regime analytics,
suppression effectiveness, and watchlist conversion metrics from
persisted signal data in Postgres.

Design:
  - All computations are SQL-based (no in-memory aggregation)
  - Deterministic: same data → same results
  - No AI interpretation, no probabilistic language
  - Pure statistics: counts, percentages, averages
"""

from __future__ import annotations

from datetime import date, datetime, timezone, timedelta

import asyncpg
import structlog

logger = structlog.get_logger()

_IST = timezone(timedelta(hours=5, minutes=30))


class SignalAnalytics:
    """Deterministic analytics computed from Postgres signal data."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    # ═══════════════════════════════════════════════
    # PRECISION METRICS
    # ═══════════════════════════════════════════════

    async def precision(self, trade_date: date | None = None) -> dict:
        """Overall signal precision stats.

        Returns: total, target_hits, stop_hits, expired, precision_pct, avg_score, avg_rr
        """
        where = "WHERE NOT suppressed AND outcome_tracked"
        params = []
        if trade_date:
            where += " AND created_at::date = $1"
            params.append(trade_date)

        sql = f"""
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE outcome_label = 'TARGET_HIT') as target_hits,
            COUNT(*) FILTER (WHERE outcome_label = 'STOP_HIT') as stop_hits,
            COUNT(*) FILTER (WHERE outcome_label = 'EXPIRED') as expired,
            ROUND(AVG(conviction_score)::numeric, 1) as avg_score,
            ROUND(AVG(risk_reward_ratio)::numeric, 2) as avg_rr,
            ROUND(AVG(max_favorable_pct)::numeric, 2) as avg_mfe,
            ROUND(AVG(max_adverse_pct)::numeric, 2) as avg_mae,
            ROUND(AVG(time_to_target_min) FILTER (WHERE outcome_label = 'TARGET_HIT')::numeric, 1) as avg_time_to_target,
            ROUND(AVG(time_to_stop_min) FILTER (WHERE outcome_label = 'STOP_HIT')::numeric, 1) as avg_time_to_stop,
            -- Phase N8: same "unknown = pre-migration TARGET_HIT row, not
            -- zero" convention as backtest.py's target_levels -- added as
            -- extra FILTER clauses on this existing single-pass query
            -- rather than a second query, since this function (unlike
            -- backtest.py's summary) already fetches everything in one
            -- round trip.
            COUNT(*) FILTER (WHERE target_level_hit = 'T1') as t1_hits,
            COUNT(*) FILTER (WHERE target_level_hit = 'T2') as t2_hits,
            COUNT(*) FILTER (WHERE target_level_hit = 'T3') as t3_hits,
            COUNT(*) FILTER (WHERE outcome_label = 'TARGET_HIT' AND target_level_hit IS NULL) as target_level_unknown
        FROM signals {where}
        """

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, *params)

        total = row["total"] or 0
        hits = row["target_hits"] or 0
        stops = row["stop_hits"] or 0
        decided = hits + stops
        precision_pct = round((hits / decided) * 100, 1) if decided > 0 else None

        return {
            "total": total,
            "target_hits": hits,
            "stop_hits": stops,
            "expired": row["expired"] or 0,
            "precision_pct": precision_pct,
            "avg_score": float(row["avg_score"]) if row["avg_score"] else None,
            "avg_rr": float(row["avg_rr"]) if row["avg_rr"] else None,
            "avg_mfe_pct": float(row["avg_mfe"]) if row["avg_mfe"] else None,
            "avg_mae_pct": float(row["avg_mae"]) if row["avg_mae"] else None,
            "avg_time_to_target_min": float(row["avg_time_to_target"]) if row["avg_time_to_target"] else None,
            "avg_time_to_stop_min": float(row["avg_time_to_stop"]) if row["avg_time_to_stop"] else None,
            "target_levels": {
                "t1": row["t1_hits"] or 0,
                "t2": row["t2_hits"] or 0,
                "t3": row["t3_hits"] or 0,
                "unknown": row["target_level_unknown"] or 0,
            },
        }

    async def precision_by_grade(self, trade_date: date | None = None) -> list[dict]:
        """Precision breakdown by conviction grade."""
        where = "WHERE NOT suppressed AND outcome_tracked"
        params = []
        if trade_date:
            where += " AND created_at::date = $1"
            params.append(trade_date)

        sql = f"""
        SELECT
            conviction_grade as grade,
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE outcome_label = 'TARGET_HIT') as target_hits,
            COUNT(*) FILTER (WHERE outcome_label = 'STOP_HIT') as stop_hits,
            COUNT(*) FILTER (WHERE outcome_label = 'EXPIRED') as expired,
            ROUND(AVG(conviction_score)::numeric, 1) as avg_score
        FROM signals {where}
        GROUP BY conviction_grade
        ORDER BY avg_score DESC NULLS LAST
        """

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)

        results = []
        for r in rows:
            hits = r["target_hits"] or 0
            stops = r["stop_hits"] or 0
            decided = hits + stops
            results.append({
                "grade": r["grade"],
                "total": r["total"],
                "target_hits": hits,
                "stop_hits": stops,
                "expired": r["expired"] or 0,
                "precision_pct": round((hits / decided) * 100, 1) if decided > 0 else None,
                "avg_score": float(r["avg_score"]) if r["avg_score"] else None,
            })
        return results

    # ═══════════════════════════════════════════════
    # SECTOR ANALYTICS
    # ═══════════════════════════════════════════════

    async def precision_by_sector(self, trade_date: date | None = None) -> list[dict]:
        """Precision breakdown by sector."""
        where = "WHERE NOT suppressed AND outcome_tracked AND sector_id != ''"
        params = []
        if trade_date:
            where += " AND created_at::date = $1"
            params.append(trade_date)

        sql = f"""
        SELECT
            sector_id,
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE outcome_label = 'TARGET_HIT') as target_hits,
            COUNT(*) FILTER (WHERE outcome_label = 'STOP_HIT') as stop_hits,
            ROUND(AVG(sector_strength)::numeric, 1) as avg_sector_strength,
            ROUND(AVG(conviction_score)::numeric, 1) as avg_score
        FROM signals {where}
        GROUP BY sector_id
        ORDER BY total DESC
        """

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)

        results = []
        for r in rows:
            hits = r["target_hits"] or 0
            stops = r["stop_hits"] or 0
            decided = hits + stops
            results.append({
                "sector_id": r["sector_id"],
                "total": r["total"],
                "target_hits": hits,
                "stop_hits": stops,
                "precision_pct": round((hits / decided) * 100, 1) if decided > 0 else None,
                "avg_sector_strength": float(r["avg_sector_strength"]) if r["avg_sector_strength"] else None,
                "avg_score": float(r["avg_score"]) if r["avg_score"] else None,
            })
        return results

    # ═══════════════════════════════════════════════
    # SESSION ANALYTICS
    # ═══════════════════════════════════════════════

    async def precision_by_session(self, trade_date: date | None = None) -> list[dict]:
        """Precision breakdown by market session hour."""
        where = "WHERE NOT suppressed AND outcome_tracked AND session_hour IS NOT NULL"
        params = []
        if trade_date:
            where += " AND created_at::date = $1"
            params.append(trade_date)

        sql = f"""
        SELECT
            session_hour,
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE outcome_label = 'TARGET_HIT') as target_hits,
            COUNT(*) FILTER (WHERE outcome_label = 'STOP_HIT') as stop_hits,
            ROUND(AVG(conviction_score)::numeric, 1) as avg_score
        FROM signals {where}
        GROUP BY session_hour
        ORDER BY
            CASE session_hour
                WHEN 'opening' THEN 1
                WHEN 'mid_morning' THEN 2
                WHEN 'midday' THEN 3
                WHEN 'closing' THEN 4
                ELSE 5
            END
        """

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)

        results = []
        for r in rows:
            hits = r["target_hits"] or 0
            stops = r["stop_hits"] or 0
            decided = hits + stops
            results.append({
                "session": r["session_hour"],
                "total": r["total"],
                "target_hits": hits,
                "stop_hits": stops,
                "precision_pct": round((hits / decided) * 100, 1) if decided > 0 else None,
                "avg_score": float(r["avg_score"]) if r["avg_score"] else None,
            })
        return results

    # ═══════════════════════════════════════════════
    # SUPPRESSION EFFECTIVENESS
    # ═══════════════════════════════════════════════

    async def suppression_stats(self, trade_date: date | None = None) -> dict:
        """Suppression counts and breakdown by reason."""
        where = "WHERE suppressed"
        params = []
        if trade_date:
            where += " AND created_at::date = $1"
            params.append(trade_date)

        sql = f"""
        SELECT
            COUNT(*) as total_suppressed,
            suppression_reason as reason,
            ROUND(AVG(conviction_score)::numeric, 1) as avg_score
        FROM signals {where}
        GROUP BY suppression_reason
        ORDER BY COUNT(*) DESC
        """

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)

        total = sum(r["total_suppressed"] for r in rows)
        by_reason = []
        for r in rows:
            by_reason.append({
                "reason": r["reason"] or "unknown",
                "count": r["total_suppressed"],
                "avg_score": float(r["avg_score"]) if r["avg_score"] else None,
            })

        return {
            "total_suppressed": total,
            "by_reason": by_reason,
        }

    # ═══════════════════════════════════════════════
    # REGIME ANALYTICS
    # ═══════════════════════════════════════════════

    async def precision_by_regime(self, trade_date: date | None = None) -> list[dict]:
        """Precision breakdown by market regime."""
        where = "WHERE NOT suppressed AND outcome_tracked AND market_regime != ''"
        params = []
        if trade_date:
            where += " AND created_at::date = $1"
            params.append(trade_date)

        sql = f"""
        SELECT
            market_regime as regime,
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE outcome_label = 'TARGET_HIT') as target_hits,
            COUNT(*) FILTER (WHERE outcome_label = 'STOP_HIT') as stop_hits,
            ROUND(AVG(conviction_score)::numeric, 1) as avg_score
        FROM signals {where}
        GROUP BY market_regime
        ORDER BY total DESC
        """

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)

        results = []
        for r in rows:
            hits = r["target_hits"] or 0
            stops = r["stop_hits"] or 0
            decided = hits + stops
            results.append({
                "regime": r["regime"],
                "total": r["total"],
                "target_hits": hits,
                "stop_hits": stops,
                "precision_pct": round((hits / decided) * 100, 1) if decided > 0 else None,
                "avg_score": float(r["avg_score"]) if r["avg_score"] else None,
            })
        return results

    # ═══════════════════════════════════════════════
    # RECENT OUTCOMES
    # ═══════════════════════════════════════════════

    async def recent_outcomes(self, limit: int = 20, trade_date: date | None = None) -> list[dict]:
        """Most recent tracked signal outcomes.

        trade_date (Phase N7, dashboard Signal Integrity tab): optional
        exact-calendar-day filter, same `created_at::date = $N` pattern as
        precision()/precision_by_grade() etc. above -- None (the default)
        keeps the original "most recent N overall" behavior unchanged for
        every existing caller.
        """
        where = "WHERE NOT suppressed AND outcome_tracked"
        params: list = [limit]
        if trade_date:
            where += " AND created_at::date = $2"
            params.append(trade_date)

        sql = f"""
        SELECT
            signal_id, symbol, strategy, conviction_grade, conviction_score,
            entry_price, invalidation_price, target_price,
            outcome_label, session_hour, sector_id, market_regime,
            max_favorable_pct, max_adverse_pct,
            time_to_target_min, time_to_stop_min,
            created_at, target_level_hit
        FROM signals
        {where}
        ORDER BY created_at DESC
        LIMIT $1
        """

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)

        return [
            {
                "signal_id": str(r["signal_id"]),
                "symbol": r["symbol"],
                "strategy": r["strategy"],
                "grade": r["conviction_grade"],
                "score": float(r["conviction_score"]) if r["conviction_score"] else 0,
                "entry_price": float(r["entry_price"]) if r["entry_price"] else 0,
                "stop": float(r["invalidation_price"]) if r["invalidation_price"] else 0,
                "target": float(r["target_price"]) if r["target_price"] else 0,
                "outcome": r["outcome_label"],
                "session": r["session_hour"],
                "sector": r["sector_id"],
                "regime": r["market_regime"],
                "mfe_pct": float(r["max_favorable_pct"]) if r["max_favorable_pct"] else 0,
                "mae_pct": float(r["max_adverse_pct"]) if r["max_adverse_pct"] else 0,
                "time_to_target_min": float(r["time_to_target_min"]) if r["time_to_target_min"] else None,
                "time_to_stop_min": float(r["time_to_stop_min"]) if r["time_to_stop_min"] else None,
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                # Phase N8: 'T1'/'T2'/'T3' on a TARGET_HIT row, null on
                # STOP_HIT/EXPIRED or on signals archived before this
                # column existed.
                "target_level_hit": r["target_level_hit"],
            }
            for r in rows
        ]

    # ═══════════════════════════════════════════════
    # DAILY RECAP DATA
    # ═══════════════════════════════════════════════

    async def daily_recap_data(self, trade_date: date | None = None) -> dict:
        """All data needed for the daily recap summary."""
        td = trade_date or date.today()

        precision_data = await self.precision(td)
        grade_data = await self.precision_by_grade(td)
        sector_data = await self.precision_by_sector(td)
        session_data = await self.precision_by_session(td)
        suppression_data = await self.suppression_stats(td)
        regime_data = await self.precision_by_regime(td)

        # Total signals (active + suppressed)
        async with self._pool.acquire() as conn:
            total_row = await conn.fetchrow(
                "SELECT COUNT(*) as total, "
                "COUNT(*) FILTER (WHERE NOT suppressed) as active, "
                "COUNT(*) FILTER (WHERE suppressed) as suppressed "
                "FROM signals WHERE created_at::date = $1", td
            )

        return {
            "trade_date": td.isoformat(),
            "total_signals": total_row["total"] if total_row else 0,
            "active_signals": total_row["active"] if total_row else 0,
            "suppressed_signals": total_row["suppressed"] if total_row else 0,
            "precision": precision_data,
            "by_grade": grade_data,
            "by_sector": sector_data,
            "by_session": session_data,
            "by_regime": regime_data,
            "suppression": suppression_data,
        }
