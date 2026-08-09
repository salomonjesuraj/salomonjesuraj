"""Backtest / proof routes.

Phase 5 starts with honest outcome-proof from archived signals. This is not a
full candle replay engine yet; it summarizes actual archived scanner signals
and tracked outcomes so the dashboard can show whether the current logic has
enough evidence to trust, tune, or keep paper-first.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from aiohttp import web

routes = web.RouteTableDef()

# Sub-signal fields added across Phases 1/4/6/7/8/9/10 this session, all
# deliberately kept informational-only (not wired into the live conviction
# score) pending exactly this kind of evidence. Listed here purely for
# discoverability -- /api/backtest/feature-ablation accepts any top-level
# features_snapshot key, this list is not enforced.
KNOWN_ABLATION_FIELDS = [
    "fib_targets", "ma_regime", "ma_regime_cross_recent", "chart_patterns",
    "fvg_bullish_ce", "fvg_bearish_ce", "last_liquidity_sweep",
    "order_block_bullish_validated", "order_block_bearish_validated",
    "donchian_fresh_high_breakout", "donchian_fresh_low_breakout",
    "wyckoff_structural_failure", "wyckoff_sot", "wyckoff_sos_sow",
    "volman_entry_triggered",
]
# cross_confirmation (Phase 9) lives in sub_scores, not features_snapshot --
# every other field above is in features_snapshot. feature-ablation takes an
# explicit ?column= to query either.
KNOWN_ABLATION_FIELDS_SUB_SCORES = ["cross_confirmation"]

_LIVE_GUARD_DEPLOYED_AT_UTC = datetime(2026, 8, 1, 16, 48, tzinfo=timezone.utc)
_LIVE_GUARD_PROFILE = {
    "min_score": 80,
    "min_rr": 1.2,
    "min_grade_rank": 0,
    "sessions": "closing",
}


def _reliability(decided: int, total: int, precision: float | None) -> tuple[str, str]:
    if total <= 0:
        return "NO_DATA", "No archived signals yet. Treat the engine as unproven."
    if decided < 10:
        return "LOW_SAMPLE", "Too few completed outcomes for statistical confidence."
    if decided < 30:
        return "BUILDING", "Evidence is building; keep paper-first or reduced size."
    if precision is None:
        return "UNKNOWN", "Signals exist, but completed outcomes are not enough."
    if precision >= 60:
        return "PROMISING", "Precision is promising; continue controlled forward testing."
    if precision >= 45:
        return "MIXED", "Edge is mixed; review filters by sector/session/setup."
    return "WEAK", "Proof is weak; tighten logic before risking capital."


def _precision(wins: int, losses: int) -> float | None:
    decided = wins + losses
    return round(wins / decided * 100, 1) if decided else None


def _profile_status(precision: float | None, decided: int, days: int, target: float, min_decided: int) -> str:
    trades_per_day = decided / max(days, 1)
    if precision is None or decided <= 0:
        return "NO_TRADES"
    if decided < min_decided:
        return "LOW_SAMPLE"
    if trades_per_day < 0.35:
        return "TOO_RARE"
    if precision >= target:
        return "TARGET_MET"
    return "BELOW_TARGET"


def _grade_rank(grade: str | None) -> int:
    order = {"A+": 4, "A": 3, "B+": 2, "B": 1}
    return order.get((grade or "").upper(), 0)


def _row_matches_profile(row: dict, profile: dict) -> bool:
    if float(row.get("conviction_score") or 0) < profile["min_score"]:
        return False
    rr = row.get("risk_reward_ratio")
    if rr is not None and float(rr or 0) < profile["min_rr"]:
        return False
    if _grade_rank(row.get("conviction_grade")) < profile["min_grade_rank"]:
        return False
    if profile["sessions"] != "regular":
        if (row.get("session_hour") or "unknown") != profile["sessions"]:
            return False
    return True


def _describe_profile(profile: dict) -> str:
    grade_labels = {4: "A+ only", 3: "A and A+", 2: "B+ and above", 1: "B and above", 0: "All grades"}
    session = "regular market only" if profile["sessions"] == "regular" else str(profile["sessions"]).replace("_", " ")
    return (
        f"Score >= {profile['min_score']}, R:R >= {profile['min_rr']}, "
        f"{grade_labels.get(profile['min_grade_rank'], 'All grades')}, {session}"
    )


def _profile_metrics(rows: list[dict], profile: dict, days: int) -> dict:
    matched = [r for r in rows if _row_matches_profile(r, profile)]
    wins = sum(1 for r in matched if r.get("outcome_label") == "TARGET_HIT")
    losses = sum(1 for r in matched if r.get("outcome_label") == "STOP_HIT")
    decided = wins + losses
    precision = _precision(wins, losses)
    return {
        "wins": wins,
        "losses": losses,
        "decided": decided,
        "precision_pct": precision,
        "trades_per_day": round(decided / max(days, 1), 2),
        "avg_score": round(sum(float(r.get("conviction_score") or 0) for r in matched) / decided, 1) if decided else None,
        "avg_rr": round(sum(float(r.get("risk_reward_ratio") or 0) for r in matched) / decided, 2) if decided else None,
    }


def _walkforward_status(test: dict, target: float, min_test: int) -> tuple[str, str]:
    precision = test.get("precision_pct")
    decided = int(test.get("decided") or 0)
    if decided <= 0:
        return "NO_FORWARD_TRADES", "No out-of-sample signals yet. Keep paper-first."
    if decided < min_test:
        return "FORWARD_BUILDING", "Out-of-sample sample is still too small for live confidence."
    if precision is not None and precision >= target:
        return "FORWARD_TARGET_MET", "Out-of-sample proof is meeting target. Continue paper-forward until sample is larger."
    if precision is not None and precision >= max(55.0, target - 15.0):
        return "FORWARD_MIXED", "Out-of-sample proof is below target but not broken. Review filters before tightening."
    return "FORWARD_FAILED", "Out-of-sample proof failed target. Do not promote this profile."


@routes.get("/api/backtest/summary")
async def backtest_summary(request):
    pool = request.app.get("pg_pool")
    if not pool:
        return web.json_response({
            "available": False,
            "reason": "Postgres analytics pool is not available.",
            "phase": "Phase 5",
        })

    days = max(1, min(365, int(request.query.get("days", "60") or 60)))
    strategy = request.query.get("strategy", "")
    where_strategy = "AND strategy = $2" if strategy else ""
    params = [days]
    if strategy:
        params.append(strategy)

    async with pool.acquire() as conn:
        try:
            overview = await conn.fetchrow(f"""
                SELECT
                    COUNT(*)::int AS total,
                    COUNT(*) FILTER (WHERE NOT COALESCE(suppressed, false))::int AS active,
                    COUNT(*) FILTER (WHERE COALESCE(suppressed, false))::int AS suppressed,
                    COUNT(*) FILTER (WHERE outcome_label = 'TARGET_HIT')::int AS target_hits,
                    COUNT(*) FILTER (WHERE outcome_label = 'STOP_HIT')::int AS stop_hits,
                    COUNT(*) FILTER (WHERE outcome_label = 'EXPIRED')::int AS expired,
                    AVG(conviction_score)::float AS avg_score,
                    AVG(risk_reward_ratio)::float AS avg_rr,
                    AVG(max_favorable_pct)::float AS avg_mfe,
                    AVG(max_adverse_pct)::float AS avg_mae
                FROM signals
                WHERE created_at >= now() - ($1::int * interval '1 day')
                {where_strategy}
            """, *params)

            by_grade = await conn.fetch(f"""
                SELECT
                    COALESCE(conviction_grade, '-') AS label,
                    COUNT(*)::int AS total,
                    COUNT(*) FILTER (WHERE outcome_label = 'TARGET_HIT')::int AS wins,
                    COUNT(*) FILTER (WHERE outcome_label = 'STOP_HIT')::int AS losses
                FROM signals
                WHERE created_at >= now() - ($1::int * interval '1 day')
                {where_strategy}
                GROUP BY 1
                ORDER BY total DESC
                LIMIT 8
            """, *params)

            by_session = await conn.fetch(f"""
                SELECT
                    COALESCE(session_hour, 'unknown') AS label,
                    COUNT(*)::int AS total,
                    COUNT(*) FILTER (WHERE outcome_label = 'TARGET_HIT')::int AS wins,
                    COUNT(*) FILTER (WHERE outcome_label = 'STOP_HIT')::int AS losses
                FROM signals
                WHERE created_at >= now() - ($1::int * interval '1 day')
                {where_strategy}
                GROUP BY 1
                ORDER BY total DESC
                LIMIT 8
            """, *params)

            by_sector = await conn.fetch(f"""
                SELECT
                    COALESCE(sector_id, '-') AS label,
                    COUNT(*)::int AS total,
                    COUNT(*) FILTER (WHERE outcome_label = 'TARGET_HIT')::int AS wins,
                    COUNT(*) FILTER (WHERE outcome_label = 'STOP_HIT')::int AS losses
                FROM signals
                WHERE created_at >= now() - ($1::int * interval '1 day')
                {where_strategy}
                GROUP BY 1
                ORDER BY total DESC
                LIMIT 8
            """, *params)

            # Phase N8: how far a TARGET_HIT signal actually ran, not just
            # that it hit *a* target. target_level_hit is NULL on rows
            # archived before migration 003 (pre-existing TARGET_HIT rows
            # with no level recorded) -- counted separately as "unknown"
            # rather than silently folded into T1, so old data doesn't
            # masquerade as "every hit stopped at T1".
            target_levels = await conn.fetchrow(f"""
                SELECT
                    COUNT(*) FILTER (WHERE target_level_hit = 'T1')::int AS t1,
                    COUNT(*) FILTER (WHERE target_level_hit = 'T2')::int AS t2,
                    COUNT(*) FILTER (WHERE target_level_hit = 'T3')::int AS t3,
                    COUNT(*) FILTER (WHERE target_level_hit IS NULL)::int AS unknown
                FROM signals
                WHERE created_at >= now() - ($1::int * interval '1 day')
                  AND outcome_label = 'TARGET_HIT'
                {where_strategy}
            """, *params)
        except Exception as exc:
            return web.json_response({
                "available": False,
                "phase": "Phase 5",
                "reason": f"Backtest summary could not read archived signal outcomes: {exc}",
            }, status=200)

    o = dict(overview or {})
    total = int(o.get("total") or 0)
    hits = int(o.get("target_hits") or 0)
    stops = int(o.get("stop_hits") or 0)
    expired = int(o.get("expired") or 0)
    decided = hits + stops
    precision = round(hits / decided * 100, 1) if decided else None
    reliability, note = _reliability(decided, total, precision)

    def rows(records):
        out = []
        for r in records:
            d = dict(r)
            wins = int(d.get("wins") or 0)
            losses = int(d.get("losses") or 0)
            dec = wins + losses
            out.append({
                "label": d.get("label") or "-",
                "total": int(d.get("total") or 0),
                "wins": wins,
                "losses": losses,
                "precision_pct": round(wins / dec * 100, 1) if dec else None,
            })
        return out

    return web.json_response({
        "available": True,
        "phase": "Phase 5",
        "days": days,
        "strategy": strategy or "all",
        "total": total,
        "active": int(o.get("active") or 0),
        "suppressed": int(o.get("suppressed") or 0),
        "target_hits": hits,
        "stop_hits": stops,
        "expired": expired,
        "decided": decided,
        "precision_pct": precision,
        "avg_score": round(float(o.get("avg_score") or 0), 1) if total else None,
        "avg_rr": round(float(o.get("avg_rr") or 0), 2) if total else None,
        "avg_mfe_pct": round(float(o.get("avg_mfe") or 0), 2) if o.get("avg_mfe") is not None else None,
        "avg_mae_pct": round(float(o.get("avg_mae") or 0), 2) if o.get("avg_mae") is not None else None,
        "reliability": reliability,
        "note": note,
        "by_grade": rows(by_grade),
        "by_session": rows(by_session),
        "by_sector": rows(by_sector),
        # Phase N8. "unknown" = TARGET_HIT rows archived before migration
        # 003 added target_level_hit -- real historical hits, just no
        # level recorded for them, not zero.
        "target_levels": {
            "t1": int(target_levels["t1"] or 0) if target_levels else 0,
            "t2": int(target_levels["t2"] or 0) if target_levels else 0,
            "t3": int(target_levels["t3"] or 0) if target_levels else 0,
            "unknown": int(target_levels["unknown"] or 0) if target_levels else 0,
        },
    })


@routes.get("/api/backtest/optimize")
async def backtest_optimize(request):
    """Sweep archived outcomes for stricter parameter profiles.

    This is an optimizer, not a guarantee. It deliberately reports sample size
    and trade frequency so a high percentage from too few trades is not treated
    as a live-ready edge.
    """
    pool = request.app.get("pg_pool")
    if not pool:
        return web.json_response({
            "available": False,
            "reason": "Postgres analytics pool is not available.",
            "phase": "Phase 5.1",
        })

    days = max(5, min(365, int(request.query.get("days", "60") or 60)))
    target = max(50.0, min(95.0, float(request.query.get("target", "80") or 80)))
    min_decided = max(10, min(2000, int(request.query.get("min_decided", "50") or 50)))
    valid_sessions = {"opening", "mid_morning", "midday", "closing"}

    async with pool.acquire() as conn:
        try:
            records = await conn.fetch("""
                SELECT
                    symbol,
                    COALESCE(sector_id, '-') AS sector_id,
                    COALESCE(session_hour, 'unknown') AS session_hour,
                    COALESCE(conviction_score, 0)::float AS conviction_score,
                    COALESCE(conviction_grade, '-') AS conviction_grade,
                    COALESCE(risk_reward_ratio, 0)::float AS risk_reward_ratio,
                    outcome_label,
                    COALESCE(suppressed, false) AS suppressed,
                    COALESCE(strategy, '-') AS strategy,
                    COALESCE(pre_breakout_state, '-') AS pre_breakout_state,
                    COALESCE(market_regime, '-') AS market_regime
                FROM signals
                WHERE created_at >= now() - ($1::int * interval '1 day')
                  AND outcome_label IN ('TARGET_HIT', 'STOP_HIT')
                  AND NOT COALESCE(suppressed, false)
            """, days)
        except Exception as exc:
            return web.json_response({
                "available": False,
                "phase": "Phase 5.1",
                "reason": f"Precision optimizer could not read archived outcomes: {exc}",
            }, status=200)

    rows = []
    for record in records:
        item = dict(record)
        if (item.get("session_hour") or "unknown") in valid_sessions:
            rows.append(item)
    current_wins = sum(1 for r in rows if r.get("outcome_label") == "TARGET_HIT")
    current_losses = sum(1 for r in rows if r.get("outcome_label") == "STOP_HIT")
    current_precision = _precision(current_wins, current_losses)

    profiles = []
    score_floors = [40, 50, 55, 60, 65, 70, 75, 80, 85, 90]
    rr_floors = [0, 1.2, 1.5, 1.8, 2.0, 2.5, 3.0]
    grade_floors = [0, 1, 2, 3, 4]
    session_filters = ["regular", "opening", "mid_morning", "midday", "closing"]

    for min_score in score_floors:
        for min_rr in rr_floors:
            for min_grade_rank in grade_floors:
                for sessions in session_filters:
                    profile = {
                        "min_score": min_score,
                        "min_rr": min_rr,
                        "min_grade_rank": min_grade_rank,
                        "sessions": sessions,
                    }
                    matched = [r for r in rows if _row_matches_profile(r, profile)]
                    wins = sum(1 for r in matched if r.get("outcome_label") == "TARGET_HIT")
                    losses = sum(1 for r in matched if r.get("outcome_label") == "STOP_HIT")
                    decided = wins + losses
                    precision = _precision(wins, losses)
                    if decided <= 0:
                        continue
                    avg_score = round(sum(float(r.get("conviction_score") or 0) for r in matched) / decided, 1)
                    avg_rr = round(sum(float(r.get("risk_reward_ratio") or 0) for r in matched) / decided, 2)
                    status = _profile_status(precision, decided, days, target, min_decided)
                    utility = (
                        (precision or 0) * 100
                        + min(decided, min_decided * 4) * 2
                        + avg_rr * 20
                        - (0 if status == "TARGET_MET" else 800)
                        - (300 if status == "LOW_SAMPLE" else 0)
                        - (150 if status == "TOO_RARE" else 0)
                    )
                    profiles.append({
                        **profile,
                        "label": _describe_profile(profile),
                        "wins": wins,
                        "losses": losses,
                        "decided": decided,
                        "precision_pct": precision,
                        "trades_per_day": round(decided / days, 2),
                        "avg_score": avg_score,
                        "avg_rr": avg_rr,
                        "status": status,
                        "utility": round(utility, 1),
                    })

    profiles.sort(key=lambda p: (p["status"] == "TARGET_MET", p["utility"], p["precision_pct"] or 0, p["decided"]), reverse=True)
    stable = [p for p in profiles if p["status"] == "TARGET_MET"]
    near = [p for p in profiles if p["status"] in {"BELOW_TARGET", "TOO_RARE"} and p["decided"] >= min_decided]
    low_sample = [p for p in profiles if p["precision_pct"] is not None and p["precision_pct"] >= target and p["status"] == "LOW_SAMPLE"]
    recommended = stable[0] if stable else (near[0] if near else (low_sample[0] if low_sample else (profiles[0] if profiles else None)))
    best_precision = sorted(profiles, key=lambda p: (p["precision_pct"] or 0, p["decided"]), reverse=True)[0] if profiles else None

    note = "Target profile found. Use paper-forward validation before live parameter change."
    if not stable and low_sample:
        note = "80%+ exists only in low-sample filters. Do not use as live proof yet."
    elif not stable:
        note = "No stable 80% profile found in archived active regular-session outcomes. Tighten logic and collect more proof."

    return web.json_response({
        "available": True,
        "phase": "Phase 5.1",
        "days": days,
        "target_precision_pct": target,
        "min_decided": min_decided,
        "universe": "active archived signals, regular sessions only",
        "current": {
            "wins": current_wins,
            "losses": current_losses,
            "decided": current_wins + current_losses,
            "precision_pct": current_precision,
            "trades_per_day": round((current_wins + current_losses) / days, 2),
        },
        "recommended": recommended,
        "best_precision": best_precision,
        "target_met": bool(stable),
        "low_sample_target_hits": len(low_sample),
        "candidates": profiles[:12],
        "note": note,
    })


@routes.get("/api/backtest/forward")
async def backtest_forward(request):
    """Forward proof for the currently deployed precision guard.

    This separates live-after-change evidence from historical optimization so
    the UI can confirm whether the 80% profile survives forward testing.
    """
    pool = request.app.get("pg_pool")
    if not pool:
        return web.json_response({
            "available": False,
            "reason": "Postgres analytics pool is not available.",
            "phase": "Phase 5.2",
        })

    min_decided = max(10, min(1000, int(request.query.get("min_decided", "30") or 30)))
    target = max(50.0, min(95.0, float(request.query.get("target", "80") or 80)))

    async with pool.acquire() as conn:
        try:
            overview = await conn.fetchrow("""
                SELECT
                    COUNT(*)::int AS total,
                    COUNT(*) FILTER (WHERE outcome_label = 'TARGET_HIT')::int AS wins,
                    COUNT(*) FILTER (WHERE outcome_label = 'STOP_HIT')::int AS losses,
                    COUNT(*) FILTER (WHERE outcome_label = 'EXPIRED')::int AS expired,
                    COUNT(*) FILTER (WHERE NOT outcome_tracked)::int AS open,
                    AVG(conviction_score)::float AS avg_score,
                    AVG(risk_reward_ratio)::float AS avg_rr,
                    MIN(created_at) AS first_signal_at,
                    MAX(created_at) AS last_signal_at
                FROM signals
                WHERE created_at >= $1::timestamptz
                  AND NOT COALESCE(suppressed, false)
                  AND COALESCE(conviction_score, 0) >= $2
                  AND COALESCE(risk_reward_ratio, 0) >= $3
                  AND COALESCE(session_hour, 'unknown') = $4
            """,
                _LIVE_GUARD_DEPLOYED_AT_UTC,
                _LIVE_GUARD_PROFILE["min_score"],
                _LIVE_GUARD_PROFILE["min_rr"],
                _LIVE_GUARD_PROFILE["sessions"],
            )

            by_direction = await conn.fetch("""
                SELECT
                    COALESCE(signal_type, '-') AS label,
                    COUNT(*)::int AS total,
                    COUNT(*) FILTER (WHERE outcome_label = 'TARGET_HIT')::int AS wins,
                    COUNT(*) FILTER (WHERE outcome_label = 'STOP_HIT')::int AS losses,
                    COUNT(*) FILTER (WHERE outcome_label = 'EXPIRED')::int AS expired
                FROM signals
                WHERE created_at >= $1::timestamptz
                  AND NOT COALESCE(suppressed, false)
                  AND COALESCE(conviction_score, 0) >= $2
                  AND COALESCE(risk_reward_ratio, 0) >= $3
                  AND COALESCE(session_hour, 'unknown') = $4
                GROUP BY 1
                ORDER BY total DESC
            """,
                _LIVE_GUARD_DEPLOYED_AT_UTC,
                _LIVE_GUARD_PROFILE["min_score"],
                _LIVE_GUARD_PROFILE["min_rr"],
                _LIVE_GUARD_PROFILE["sessions"],
            )
        except Exception as exc:
            return web.json_response({
                "available": False,
                "phase": "Phase 5.2",
                "reason": f"Forward validation could not read corrected guard outcomes: {exc}",
            }, status=200)

    o = dict(overview or {})
    wins = int(o.get("wins") or 0)
    losses = int(o.get("losses") or 0)
    decided = wins + losses
    precision = _precision(wins, losses)
    status = _profile_status(precision, decided, 1, target, min_decided)
    if status == "TOO_RARE":
        status = "BUILDING"
    if status == "TARGET_MET":
        note = "Forward proof is meeting the target so far; keep paper-first until the sample is larger."
    elif decided < min_decided:
        note = "Forward proof is still building. Do not treat historical optimization as live proof yet."
    else:
        note = "Forward proof is below target. Re-optimize only after enough corrected live outcomes."

    def rows(records):
        out = []
        for r in records:
            d = dict(r)
            rw = int(d.get("wins") or 0)
            rl = int(d.get("losses") or 0)
            out.append({
                "label": d.get("label") or "-",
                "total": int(d.get("total") or 0),
                "wins": rw,
                "losses": rl,
                "expired": int(d.get("expired") or 0),
                "precision_pct": _precision(rw, rl),
            })
        return out

    return web.json_response({
        "available": True,
        "phase": "Phase 5.2",
        "target_precision_pct": target,
        "min_decided": min_decided,
        "deployed_at_utc": _LIVE_GUARD_DEPLOYED_AT_UTC.isoformat(),
        "profile": {
            **_LIVE_GUARD_PROFILE,
            "label": _describe_profile(_LIVE_GUARD_PROFILE),
        },
        "total": int(o.get("total") or 0),
        "wins": wins,
        "losses": losses,
        "expired": int(o.get("expired") or 0),
        "open": int(o.get("open") or 0),
        "decided": decided,
        "precision_pct": precision,
        "avg_score": round(float(o.get("avg_score") or 0), 1) if o.get("avg_score") is not None else None,
        "avg_rr": round(float(o.get("avg_rr") or 0), 2) if o.get("avg_rr") is not None else None,
        "first_signal_at": o.get("first_signal_at").isoformat() if o.get("first_signal_at") else None,
        "last_signal_at": o.get("last_signal_at").isoformat() if o.get("last_signal_at") else None,
        "status": status,
        "note": note,
        "by_direction": rows(by_direction),
    })


async def compute_walkforward(
    pool,
    days: int = 120,
    target: float = 80.0,
    min_train: int = 30,
    min_test: int = 15,
    train_pct: float = 0.70,
) -> dict:
    """Out-of-sample rule validation -- the actual computation, extracted
    from the /api/backtest/walkforward route handler so other callers
    (the optimizer-drift proposal check below, the scheduler) can reuse
    the exact same, already-tested logic rather than a second copy of it.
    The route handler is now a thin wrapper: same inputs, same output
    shape, same behavior as before this refactor.

    The optimizer chooses a profile only from the older training window, then
    reports how that same profile performs on the newer forward window. This is
    intentionally conservative so the dashboard does not mistake curve-fitting
    for a proven 80% edge.
    """
    if not pool:
        return {
            "available": False,
            "reason": "Postgres analytics pool is not available.",
            "phase": "Phase 5.3",
        }

    days = max(20, min(365, int(days or 120)))
    target = max(50.0, min(95.0, float(target or 80)))
    min_train = max(10, min(2000, int(min_train or 30)))
    min_test = max(5, min(1000, int(min_test or 15)))
    train_pct = max(0.50, min(0.85, float(train_pct or 0.70)))
    valid_sessions = {"opening", "mid_morning", "midday", "closing"}

    async with pool.acquire() as conn:
        try:
            records = await conn.fetch("""
                SELECT
                    created_at,
                    symbol,
                    COALESCE(sector_id, '-') AS sector_id,
                    COALESCE(session_hour, 'unknown') AS session_hour,
                    COALESCE(conviction_score, 0)::float AS conviction_score,
                    COALESCE(conviction_grade, '-') AS conviction_grade,
                    COALESCE(risk_reward_ratio, 0)::float AS risk_reward_ratio,
                    outcome_label,
                    COALESCE(suppressed, false) AS suppressed,
                    COALESCE(strategy, '-') AS strategy,
                    COALESCE(pre_breakout_state, '-') AS pre_breakout_state,
                    COALESCE(market_regime, '-') AS market_regime
                FROM signals
                WHERE created_at >= now() - ($1::int * interval '1 day')
                  AND outcome_label IN ('TARGET_HIT', 'STOP_HIT')
                  AND NOT COALESCE(suppressed, false)
                ORDER BY created_at ASC
            """, days)
        except Exception as exc:
            return {
                "available": False,
                "phase": "Phase 5.3",
                "reason": f"Walk-forward validation could not read archived outcomes: {exc}",
            }

    rows = [dict(r) for r in records if (dict(r).get("session_hour") or "unknown") in valid_sessions]
    total = len(rows)
    if total < min_train + min_test:
        return {
            "available": True,
            "phase": "Phase 5.3",
            "status": "LOW_SAMPLE",
            "target_precision_pct": target,
            "total_decided": total,
            "train_size": min(total, int(total * train_pct)),
            "test_size": max(0, total - int(total * train_pct)),
            "recommended": None,
            "candidates": [],
            "note": (
                f"Need at least {min_train + min_test} completed outcomes for walk-forward proof. "
                f"Current decided sample is {total}."
            ),
        }

    split = max(min_train, min(total - min_test, int(total * train_pct)))
    train_rows = rows[:split]
    test_rows = rows[split:]
    train_days = max(1, int(days * train_pct))
    test_days = max(1, days - train_days)

    profiles = []
    score_floors = [50, 55, 60, 65, 70, 75, 80, 85, 90]
    rr_floors = [0, 1.2, 1.5, 1.8, 2.0, 2.5, 3.0]
    grade_floors = [0, 1, 2, 3, 4]
    session_filters = ["regular", "opening", "mid_morning", "midday", "closing"]

    for min_score in score_floors:
        for min_rr in rr_floors:
            for min_grade_rank in grade_floors:
                for sessions in session_filters:
                    profile = {
                        "min_score": min_score,
                        "min_rr": min_rr,
                        "min_grade_rank": min_grade_rank,
                        "sessions": sessions,
                    }
                    train = _profile_metrics(train_rows, profile, train_days)
                    if train["decided"] < min_train:
                        continue
                    test = _profile_metrics(test_rows, profile, test_days)
                    status, status_note = _walkforward_status(test, target, min_test)
                    overfit_gap = None
                    if train["precision_pct"] is not None and test["precision_pct"] is not None:
                        overfit_gap = round(train["precision_pct"] - test["precision_pct"], 1)
                    train_precision = train["precision_pct"] or 0
                    test_precision = test["precision_pct"] or 0
                    utility = (
                        test_precision * 120
                        + min(test["decided"], min_test * 4) * 5
                        + (train_precision * 20)
                        + float(test.get("avg_rr") or 0) * 20
                        - max(overfit_gap or 0, 0) * 35
                        - (0 if status == "FORWARD_TARGET_MET" else 1200)
                        - (400 if status == "FORWARD_BUILDING" else 0)
                    )
                    profiles.append({
                        **profile,
                        "label": _describe_profile(profile),
                        "train": train,
                        "test": test,
                        "status": status,
                        "status_note": status_note,
                        "overfit_gap_pct": overfit_gap,
                        "utility": round(utility, 1),
                    })

    profiles.sort(
        key=lambda p: (
            p["status"] == "FORWARD_TARGET_MET",
            p["utility"],
            p["test"].get("precision_pct") or 0,
            p["test"].get("decided") or 0,
        ),
        reverse=True,
    )
    recommended = profiles[0] if profiles else None
    target_met = bool(recommended and recommended["status"] == "FORWARD_TARGET_MET")
    note = (
        recommended["status_note"]
        if recommended
        else "No trainable profile survived the minimum sample rules."
    )

    return {
        "available": True,
        "phase": "Phase 5.3",
        "method": "walk_forward_out_of_sample",
        "target_precision_pct": target,
        "min_train": min_train,
        "min_test": min_test,
        "days": days,
        "train_pct": train_pct,
        "total_decided": total,
        "train_size": len(train_rows),
        "test_size": len(test_rows),
        "target_met": target_met,
        "status": recommended["status"] if recommended else "NO_PROFILE",
        "recommended": recommended,
        "candidates": profiles[:10],
        "note": note,
    }


@routes.get("/api/backtest/walkforward")
async def backtest_walkforward(request):
    """Thin route wrapper around compute_walkforward() -- same behavior as
    before this was extracted into a reusable function."""
    pool = request.app.get("pg_pool")
    days = request.query.get("days", "120")
    target = request.query.get("target", "80")
    min_train = request.query.get("min_train", "30")
    min_test = request.query.get("min_test", "15")
    train_pct = request.query.get("train_pct", "0.70")
    result = await compute_walkforward(
        pool,
        days=int(days) if days else 120,
        target=float(target) if target else 80.0,
        min_train=int(min_train) if min_train else 30,
        min_test=int(min_test) if min_test else 15,
        train_pct=float(train_pct) if train_pct else 0.70,
    )
    return web.json_response(result)


def _ablation_field_present(value) -> bool:
    """Python-truthy presence check for a decoded features_snapshot/sub_scores
    field. Deliberately plain Python truthiness (not "key exists") so that
    e.g. an empty dict/list/0/False/None/"" all count as "absent" -- matches
    how every Phase 1-10 informational field is actually consumed today
    (checked with `if features.get(...)` style guards, never `in`)."""
    if value is None:
        return False
    if isinstance(value, (dict, list, str)):
        return len(value) > 0
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return bool(value)


def _ablation_group_metrics(rows: list[dict], column: str, field: str, want_present: bool) -> dict:
    wins = 0
    losses = 0
    for row in rows:
        raw = row.get(column)
        try:
            decoded = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except (json.JSONDecodeError, TypeError):
            decoded = {}
        if not isinstance(decoded, dict):
            decoded = {}
        present = _ablation_field_present(decoded.get(field))
        if present != want_present:
            continue
        if row.get("outcome_label") == "TARGET_HIT":
            wins += 1
        elif row.get("outcome_label") == "STOP_HIT":
            losses += 1
    decided = wins + losses
    return {
        "wins": wins,
        "losses": losses,
        "decided": decided,
        "precision_pct": _precision(wins, losses),
    }


async def compute_feature_ablation(pool, field: str, column: str = "features_snapshot", days: int = 90) -> dict:
    """Split archived decided (TARGET_HIT/STOP_HIT), non-suppressed signals by
    presence/truthiness of one features_snapshot or sub_scores field, and
    compare precision between the "present" and "absent" groups.

    This is diagnostic evidence only -- it never changes live scoring. Every
    field in KNOWN_ABLATION_FIELDS(_SUB_SCORES) was added across Phases
    1-10 this session as informational-only, specifically pending this kind
    of before-wiring-it-in evidence. A positive lift here is a candidate for
    a *future*, separately reviewed change to scanner/scoring.py -- never
    auto-applied from this endpoint.
    """
    if not pool:
        return {
            "available": False,
            "reason": "Postgres analytics pool is not available.",
            "phase": "Phase 11",
        }
    # "features_snapshot" is the name this field is known by everywhere else
    # (the Redis signal hash, ai.py, KNOWN_ABLATION_FIELDS) -- but the
    # archiver (archiver/writer.py's _INSERT_SQL) writes that same payload
    # key into a Postgres column literally named `features`, not
    # `features_snapshot`. Aliased back in the SELECT below so this function
    # and _ablation_group_metrics() can keep using the familiar name.
    db_column_alias = {"features_snapshot": "features", "sub_scores": "sub_scores"}
    if column not in db_column_alias:
        return {
            "available": False,
            "phase": "Phase 11",
            "reason": f"Unknown column '{column}'. Must be 'features_snapshot' or 'sub_scores'.",
        }
    if not field:
        return {
            "available": False,
            "phase": "Phase 11",
            "reason": "Missing required 'field' query parameter.",
        }

    days = max(1, min(365, int(days or 90)))
    db_column = db_column_alias[column]

    async with pool.acquire() as conn:
        try:
            records = await conn.fetch(f"""
                SELECT
                    outcome_label,
                    {db_column} AS {column}
                FROM signals
                WHERE created_at >= now() - ($1::int * interval '1 day')
                  AND outcome_label IN ('TARGET_HIT', 'STOP_HIT')
                  AND NOT COALESCE(suppressed, false)
            """, days)
        except Exception as exc:
            return {
                "available": False,
                "phase": "Phase 11",
                "reason": f"Feature-ablation query failed: {exc}",
            }

    rows = [dict(r) for r in records]
    total = len(rows)
    present = _ablation_group_metrics(rows, column, field, True)
    absent = _ablation_group_metrics(rows, column, field, False)

    lift = None
    if present["precision_pct"] is not None and absent["precision_pct"] is not None:
        lift = round(present["precision_pct"] - absent["precision_pct"], 1)

    min_group = 20
    note_parts = []
    if present["decided"] < min_group or absent["decided"] < min_group:
        note_parts.append(
            f"At least one group has fewer than {min_group} decided outcomes -- "
            "not enough sample to treat this lift as meaningful evidence yet."
        )
    if lift is not None and abs(lift) >= 10 and present["decided"] >= min_group and absent["decided"] >= min_group:
        direction = "higher" if lift > 0 else "lower"
        note_parts.append(
            f"'{field}' present shows {abs(lift)} pts {direction} precision than absent, "
            f"with adequate sample on both sides -- worth a human-reviewed follow-up to "
            f"consider wiring this into scanner/scoring.py. Not auto-applied."
        )
    if not note_parts:
        note_parts.append("No meaningful lift detected; keep this field informational-only.")

    return {
        "available": True,
        "phase": "Phase 11",
        "method": "feature_ablation_precision_split",
        "field": field,
        "column": column,
        "days": days,
        "total_decided": total,
        "present": present,
        "absent": absent,
        "precision_lift_pct": lift,
        "note": " ".join(note_parts),
    }


@routes.get("/api/backtest/feature-ablation")
async def backtest_feature_ablation(request):
    """GET /api/backtest/feature-ablation?field=X&column=features_snapshot&days=90

    Diagnostic-only precision comparison for one Phase 1-10 informational
    sub-signal field. See KNOWN_ABLATION_FIELDS / KNOWN_ABLATION_FIELDS_SUB_SCORES
    at the top of this file for the known field names and which column each
    lives in; any field name is accepted, not just the known list, so this
    also works for ad-hoc exploration.
    """
    pool = request.app.get("pg_pool")
    field = request.query.get("field", "")
    column = request.query.get("column", "features_snapshot")
    days = request.query.get("days", "90")
    result = await compute_feature_ablation(
        pool,
        field=field,
        column=column,
        days=int(days) if days else 90,
    )
    return web.json_response(result)


# Phase 11: mirrors scanner/main.py's KEY_LIVE_CONFIG -- kept as a plain string
# constant rather than a cross-service import (api and scanner are separate
# deployables with no shared dependency on each other's code).
LIVE_CONFIG_KEY = "infusion:scanner:live_config"
OPTIMIZER_PROPOSAL_KEY = "infusion:optimizer:proposal"

# How far the walk-forward recommendation must diverge from the live
# precision_guard config before this is worth a human's attention. Chosen
# conservatively (bigger than routine profile-to-profile noise from one
# night's sweep) so the proposal chip doesn't cry wolf on every run.
_PROPOSAL_MIN_SCORE_DIVERGENCE = 5.0
_PROPOSAL_MIN_RR_DIVERGENCE = 0.3


async def _read_live_config(redis) -> dict | None:
    if redis is None:
        return None
    raw = await redis.hgetall(LIVE_CONFIG_KEY)
    if not raw:
        return None
    decoded = {}
    for k, v in raw.items():
        kk = k.decode() if isinstance(k, bytes) else k
        vv = v.decode() if isinstance(v, bytes) else v
        decoded[kk] = vv
    try:
        return {
            "precision_guard_enabled": decoded.get("precision_guard_enabled") == "True",
            "precision_guard_min_score": float(decoded.get("precision_guard_min_score", 0) or 0),
            "precision_guard_min_rr": float(decoded.get("precision_guard_min_rr", 0) or 0),
            "precision_guard_sessions": decoded.get("precision_guard_sessions", ""),
            "precision_guard_strategy_ids": decoded.get("precision_guard_strategy_ids", ""),
            "published_at_us": int(decoded.get("published_at_us", 0) or 0),
        }
    except (TypeError, ValueError):
        return None


async def compute_optimizer_proposal(pool, redis, days: int = 120, target: float = 80.0) -> dict:
    """Compare the scanner's live precision_guard config against tonight's
    walk-forward recommendation and, if they diverge beyond a conservative
    threshold, write a PROPOSAL record to Redis for a human to review.

    This NEVER writes to the scanner's actual config -- precision_guard_* are
    env-var settings on the scanner service and are not runtime-mutable from
    here even if this wanted to change them. It only ever writes a diff
    description to OPTIMIZER_PROPOSAL_KEY; a human decides whether to update
    the scanner's env vars and redeploy. Same "propose only, never auto-
    apply" discipline as the CAS session-boundary change earlier this
    session.
    """
    if not pool:
        return {"available": False, "phase": "Phase 11", "reason": "Postgres analytics pool is not available."}

    live = await _read_live_config(redis)
    if live is None:
        return {
            "available": False,
            "phase": "Phase 11",
            "reason": (
                "No live scanner config found at "
                f"{LIVE_CONFIG_KEY} -- scanner service may not have started "
                "since this endpoint was added, or Redis was flushed. "
                "Retries automatically every 5 minutes from the scanner side."
            ),
        }

    walkforward = await compute_walkforward(pool, days=days, target=target)
    recommended = walkforward.get("recommended")

    proposed_at_us = int(time.time() * 1_000_000)

    if not walkforward.get("available") or not recommended or walkforward.get("status") != "FORWARD_TARGET_MET":
        result = {
            "available": True,
            "phase": "Phase 11",
            "status": "NO_PROPOSAL",
            "reason": (
                "Walk-forward has no profile currently meeting its out-of-sample "
                "target -- nothing trustworthy enough to propose against the live config."
            ),
            "live_config": live,
            "walkforward_status": walkforward.get("status"),
            "checked_at_us": proposed_at_us,
        }
        await redis.set(OPTIMIZER_PROPOSAL_KEY, json.dumps(result))
        return result

    score_diff = round(recommended["min_score"] - live["precision_guard_min_score"], 1)
    rr_diff = round(recommended["min_rr"] - live["precision_guard_min_rr"], 2)
    live_sessions = {s.strip() for s in live["precision_guard_sessions"].split(",") if s.strip()}
    recommended_session = recommended["sessions"]
    # "regular" means the recommended profile applies no session filter at
    # all (fires in every session) -- structurally different from live's
    # allow-list, so this is reported as a note, never folded into the
    # numeric divergence check below.
    sessions_note = None
    if recommended_session == "regular":
        sessions_note = (
            "Recommended profile applies no session filter (fires in every "
            f"session) vs live's allow-list of {sorted(live_sessions)}. "
            "Session semantics differ structurally from precision_guard_sessions "
            "(single-filter walk-forward profile vs a live allow-list) -- "
            "reported for awareness, not counted in the divergence check below."
        )
    elif recommended_session not in live_sessions:
        sessions_note = (
            f"Recommended profile's best session ('{recommended_session}') is "
            f"not in live's current allow-list ({sorted(live_sessions)})."
        )

    diverged = abs(score_diff) >= _PROPOSAL_MIN_SCORE_DIVERGENCE or abs(rr_diff) >= _PROPOSAL_MIN_RR_DIVERGENCE

    result = {
        "available": True,
        "phase": "Phase 11",
        "status": "PROPOSED" if diverged else "NO_DRIFT",
        "live_config": live,
        "recommended": recommended,
        "score_diff": score_diff,
        "rr_diff": rr_diff,
        "sessions_note": sessions_note,
        "checked_at_us": proposed_at_us,
        "note": (
            (
                f"Walk-forward recommends min_score={recommended['min_score']} "
                f"(live={live['precision_guard_min_score']}, diff={score_diff}), "
                f"min_rr={recommended['min_rr']} (live={live['precision_guard_min_rr']}, "
                f"diff={rr_diff}) at {recommended.get('test', {}).get('precision_pct')}% "
                "out-of-sample precision. Divergence exceeds the review threshold "
                "-- this is a PROPOSAL only, review before changing scanner env vars "
                "and redeploying. Nothing was changed automatically."
            )
            if diverged
            else (
                "Live config is within the conservative divergence threshold of "
                "tonight's walk-forward recommendation. No proposal needed."
            )
        ),
    }
    await redis.set(OPTIMIZER_PROPOSAL_KEY, json.dumps(result))
    return result


@routes.get("/api/backtest/optimizer-proposal")
async def backtest_optimizer_proposal(request):
    """GET /api/backtest/optimizer-proposal?days=120&target=80

    Reads the scanner's live precision_guard config from Redis, runs
    tonight's walk-forward sweep, and reports whether the recommendation has
    drifted meaningfully from what's actually live. Writes the result to
    Redis (infusion:optimizer:proposal) either way so the scheduler's daily
    trigger and this on-demand route see the same latest record -- never
    writes to the scanner's actual config.
    """
    pool = request.app.get("pg_pool")
    redis = request.app.get("redis")
    days = request.query.get("days", "120")
    target = request.query.get("target", "80")
    result = await compute_optimizer_proposal(
        pool,
        redis,
        days=int(days) if days else 120,
        target=float(target) if target else 80.0,
    )
    return web.json_response(result)


@routes.get("/api/backtest/optimizer-proposal/latest")
async def backtest_optimizer_proposal_latest(request):
    """GET /api/backtest/optimizer-proposal/latest -- reads the last written
    proposal from Redis without re-running the walk-forward sweep (cheap,
    for the dashboard to poll)."""
    redis = request.app.get("redis")
    if redis is None:
        return web.json_response({"available": False, "reason": "Redis not available."})
    raw = await redis.get(OPTIMIZER_PROPOSAL_KEY)
    if not raw:
        return web.json_response({
            "available": False,
            "reason": "No proposal has been computed yet. Call /api/backtest/optimizer-proposal or wait for the scheduler's daily run.",
        })
    text = raw.decode() if isinstance(raw, bytes) else raw
    try:
        return web.json_response(json.loads(text))
    except (json.JSONDecodeError, TypeError):
        return web.json_response({"available": False, "reason": "Stored proposal record is corrupt."})
