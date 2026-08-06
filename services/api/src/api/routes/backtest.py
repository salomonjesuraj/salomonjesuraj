"""Backtest / proof routes.

Phase 5 starts with honest outcome-proof from archived signals. This is not a
full candle replay engine yet; it summarizes actual archived scanner signals
and tracked outcomes so the dashboard can show whether the current logic has
enough evidence to trust, tune, or keep paper-first.
"""

from __future__ import annotations

from datetime import datetime, timezone

from aiohttp import web

routes = web.RouteTableDef()

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


@routes.get("/api/backtest/walkforward")
async def backtest_walkforward(request):
    """Out-of-sample rule validation.

    The optimizer chooses a profile only from the older training window, then
    reports how that same profile performs on the newer forward window. This is
    intentionally conservative so the dashboard does not mistake curve-fitting
    for a proven 80% edge.
    """
    pool = request.app.get("pg_pool")
    if not pool:
        return web.json_response({
            "available": False,
            "reason": "Postgres analytics pool is not available.",
            "phase": "Phase 5.3",
        })

    days = max(20, min(365, int(request.query.get("days", "120") or 120)))
    target = max(50.0, min(95.0, float(request.query.get("target", "80") or 80)))
    min_train = max(10, min(2000, int(request.query.get("min_train", "30") or 30)))
    min_test = max(5, min(1000, int(request.query.get("min_test", "15") or 15)))
    train_pct = max(0.50, min(0.85, float(request.query.get("train_pct", "0.70") or 0.70)))
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
            return web.json_response({
                "available": False,
                "phase": "Phase 5.3",
                "reason": f"Walk-forward validation could not read archived outcomes: {exc}",
            }, status=200)

    rows = [dict(r) for r in records if (dict(r).get("session_hour") or "unknown") in valid_sessions]
    total = len(rows)
    if total < min_train + min_test:
        return web.json_response({
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
        })

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

    return web.json_response({
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
    })
