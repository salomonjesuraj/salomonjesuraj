"""Backtest / proof routes.

Phase 5 starts with honest outcome-proof from archived signals. This is not a
full candle replay engine yet; it summarizes actual archived scanner signals
and tracked outcomes so the dashboard can show whether the current logic has
enough evidence to trust, tune, or keep paper-first.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

from aiohttp import web

from api.statistics_utils import (
    r_multiple, sharpe_stats, expected_max_sharpe, probabilistic_sharpe_ratio, pearson_r,
)
from api.cost_model import compute as cost_model_compute, OptionTradeCostInput
from api.ml_classifier import train_classifier, read_cached_model
from api.trap_labels import compute_false_break_stats

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
    "volman_entry_triggered", "vcp_grade",
]
# cross_confirmation (Phase 9) lives in sub_scores, not features_snapshot --
# every other field above is in features_snapshot. feature-ablation takes an
# explicit ?column= to query either.
KNOWN_ABLATION_FIELDS_SUB_SCORES = ["cross_confirmation"]
# Phase 13.12 -- vcp_score is a continuous 0-100 composite (api/vcp.py), not
# a presence/absence field like everything above. compute_feature_ic
# correlates it directly against R-multiple (see _ic_encode) rather than
# 0/1-encoding it -- a real IC on the raw factor score is more informative
# here than a truthy/falsy split would be.
CONTINUOUS_IC_FIELDS = ["vcp_score"]

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


def _profile_sharpe(rows: list[dict], profile: dict) -> dict:
    """Phase 13.8: R-multiple-based Sharpe stats for one profile's matched
    rows -- the "trial" Sharpe that feeds the Deflated Sharpe Ratio
    correction in compute_walkforward(). Deliberately NOT annualized (a
    per-trade Sharpe): DSR only needs trial Sharpes to be computed
    consistently with each other for the multiple-testing correction to
    hold, and annualizing would need a trades-per-year assumption this
    codebase doesn't otherwise model. See statistics_utils.py for the
    R-multiple convention and formulas.
    """
    matched = [r for r in rows if _row_matches_profile(r, profile)]
    r_multiples = [
        rm for r in matched
        if (rm := r_multiple(r.get("outcome_label"), r.get("risk_reward_ratio"))) is not None
    ]
    return sharpe_stats(r_multiples)


MIN_NET_OF_COST_SAMPLE = 10  # below this, a net-precision % from real premium data is noise, not evidence


def _net_of_cost_metrics(rows: list[dict], profile: dict | None = None) -> dict:
    """Phase 13.4b: net-of-cost precision for whichever decided rows
    actually have real captured option premium data (entry_premium_ask/
    entry_premium_bid/exit_premium_bid -- Phase 13.4's capture
    infrastructure, and sub_scores.position_sizing.quantity, already
    archived for every signal). Distinct from every other precision_pct
    in this file, which only ever knows binary TARGET_HIT/STOP_HIT and is
    blind to whether a cheap-premium "win" was actually cost-negative
    after brokerage/STT/GST/stamp duty -- see cost_model.py.

    gross_precision_pct_same_subset is recomputed independently from each
    row's own gross_pnl sign (not trusted from outcome_label) -- the whole
    point of this function is checking whether a technical TARGET_HIT
    actually meant the option premium moved favorably too, so silently
    assuming that agreement would defeat the purpose.

    Gated on MIN_NET_OF_COST_SAMPLE: real capture only started with Phase
    13.4 and (confirmed live, this session) is producing roughly 3-4
    decided trades with full premium data per trading day so far --
    reporting a "net precision" from a handful of trades would be
    statistical noise dressed up as evidence, so this returns an honest
    "not enough sample yet" below the floor instead of a number.

    `profile`, when given, filters to that profile's matched rows first
    (same _row_matches_profile() convention _profile_metrics()/
    _profile_sharpe() already use) -- omitted for an unfiltered, whole-
    window aggregate read.
    """
    candidates = [r for r in rows if _row_matches_profile(r, profile)] if profile else rows
    usable = []
    n_with_premium = 0
    n_zero_qty = 0
    for r in candidates:
        if r.get("outcome_label") not in ("TARGET_HIT", "STOP_HIT"):
            continue
        entry_ask = r.get("entry_premium_ask")
        entry_bid = r.get("entry_premium_bid")
        exit_bid = r.get("exit_premium_bid")
        if entry_ask is None or entry_bid is None or exit_bid is None:
            continue
        n_with_premium += 1
        sub_scores = r.get("sub_scores")
        if isinstance(sub_scores, str):
            try:
                sub_scores = json.loads(sub_scores)
            except (json.JSONDecodeError, TypeError):
                sub_scores = {}
        sub_scores = sub_scores if isinstance(sub_scores, dict) else {}
        quantity = int((sub_scores.get("position_sizing") or {}).get("quantity") or 0)
        if quantity <= 0:
            # A real, separate reason from "no premium data" -- the
            # recommended lot count rounded to zero (large F&O lot size
            # vs. a small risk_amount), so there is no trade to compute a
            # P&L on. Confirmed live (this session): every one of the
            # real premium-captured signals so far hit exactly this,
            # since Infusion's default risk_amount is small relative to
            # several of these symbols' real lot sizes -- worth flagging
            # distinctly from "capture hasn't produced enough data yet"
            # rather than folding both into one undifferentiated count.
            n_zero_qty += 1
            continue
        usable.append(cost_model_compute(OptionTradeCostInput(
            entry_ask=float(entry_ask), exit_bid=float(exit_bid),
            bid_at_entry=float(entry_bid), quantity=quantity,
        )))

    n = len(usable)
    if n < MIN_NET_OF_COST_SAMPLE:
        reason = (
            f"Only {n} decided trade(s) in this window have both real captured premium data "
            f"AND a non-zero recommended position size (need >= {MIN_NET_OF_COST_SAMPLE})."
        )
        if n_zero_qty:
            reason += (
                f" {n_zero_qty} of {n_with_premium} premium-captured trade(s) had a recommended "
                "size of 0 lots (F&O lot size larger than the configured risk budget supports) "
                "and can't contribute a P&L either way."
            )
        reason += " Net-of-cost precision needs real option premium history to accumulate -- see Phase 13.4's capture infrastructure."
        return {
            "available": False,
            "n_with_premium_data": n_with_premium,
            "n_zero_recommended_qty": n_zero_qty,
            "n_usable": n,
            "min_sample": MIN_NET_OF_COST_SAMPLE,
            "reason": reason,
        }

    net_wins = sum(1 for u in usable if u["net_pnl"] > 0)
    gross_wins = sum(1 for u in usable if u["gross_pnl"] > 0)
    return {
        "available": True,
        "n_with_premium_data": n_with_premium,
        "n_zero_recommended_qty": n_zero_qty,
        "n_usable": n,
        "net_precision_pct": round(net_wins / n * 100, 1),
        "gross_precision_pct_same_subset": round(gross_wins / n * 100, 1),
        "avg_net_pnl": round(sum(u["net_pnl"] for u in usable) / n, 2),
        "avg_cost_pct_of_premium": round(sum(u["cost_as_pct_of_premium"] for u in usable) / n, 3),
    }


def _purge_and_embargo(
    rows: list[dict], split: int, embargo_min: float
) -> tuple[list[dict], list[dict], int, int]:
    """Phase 13.3: harden the chronological train/test cut against a
    specific leakage mode a plain index split ignores.

    Two related but distinct problems, both fixed here:

    1. PURGE -- a training row's outcome can resolve AFTER the split
       boundary even though the row itself was created before it. The
       archiver's outcome tracker (archiver/tracker.py) polls every 30s
       and marks a signal TARGET_HIT/STOP_HIT/EXPIRED within
       signal_ttl_min minutes of creation (archiver/config.py, default
       5) -- so a signal created a couple of minutes before the split can
       still resolve a couple of minutes into what should be the
       untouched test window. Scoring that row as pure "training
       evidence" lets information from inside the test period leak into
       the profile that gets chosen using train_rows. Purged here by
       dropping any train row whose target_hit_at/stop_hit_at (whichever
       matches its own outcome_label) falls at or after the split
       boundary's timestamp.

    2. EMBARGO -- even a train row that resolves cleanly before the
       boundary can sit on conditions (a live intraday move, a sector
       rotation) that are still unfolding when the test window opens.
       A short buffer of test rows immediately after the boundary is
       excluded entirely (neither trained on nor tested against) so nothing
       in the "test" set was created while that carry-over was still live.

    embargo_min defaults to signal_ttl_min (5) in compute_walkforward()
    below -- the exact maximum time any TARGET_HIT/STOP_HIT outcome can
    take to resolve, so the window is grounded in the tracker's own
    real behavior rather than picked arbitrarily.
    """
    if split <= 0 or split >= len(rows):
        return rows[:split], rows[split:], 0, 0

    split_ts = rows[split]["created_at"]
    train_candidates = rows[:split]

    purged_train = []
    purged_count = 0
    for r in train_candidates:
        resolved_at = r.get("target_hit_at") if r.get("outcome_label") == "TARGET_HIT" else r.get("stop_hit_at")
        if resolved_at is not None and resolved_at >= split_ts:
            purged_count += 1
            continue
        purged_train.append(r)

    embargo_end = split_ts + timedelta(minutes=embargo_min)
    embargoed_test = []
    embargoed_count = 0
    for r in rows[split:]:
        if r["created_at"] < embargo_end:
            embargoed_count += 1
            continue
        embargoed_test.append(r)

    return purged_train, embargoed_test, purged_count, embargoed_count


def _compute_dsr(profiles: list[dict], recommended: dict | None) -> dict:
    """Phase 13.8: Deflated Sharpe Ratio for the recommended (best-utility)
    profile, benchmarked against the expected-max-Sharpe-by-chance across
    every profile the grid search actually evaluated -- i.e. corrects for
    exactly the selection bias compute_walkforward()'s own 1,575-profile
    grid search creates by trying that many variants and picking a winner.
    See statistics_utils.py for the underlying formulas (Bailey & Lopez de
    Prado). Advisory number only -- never gates target_met/status above.
    """
    trial_sharpes = [
        p["test_sharpe"]["sharpe"] for p in profiles
        if p.get("test_sharpe", {}).get("sharpe") is not None
    ]
    n_trials = len(trial_sharpes)
    benchmark = expected_max_sharpe(trial_sharpes)

    if not recommended or benchmark is None:
        return {
            "available": False,
            "n_trials": n_trials,
            "reason": "Not enough profiles with a computable Sharpe (need std(R-multiples) > 0 across >=2 profiles).",
        }

    rec_sharpe = recommended.get("test_sharpe") or {}
    sr_hat = rec_sharpe.get("sharpe")
    n = rec_sharpe.get("n")
    skew = rec_sharpe.get("skew")
    kurtosis = rec_sharpe.get("kurtosis")
    if sr_hat is None or not n or skew is None or kurtosis is None:
        return {
            "available": False,
            "n_trials": n_trials,
            "benchmark_sharpe": round(benchmark, 4),
            "reason": "Recommended profile's test set has no computable Sharpe (all wins, all losses, or too few decided trades).",
        }

    dsr = probabilistic_sharpe_ratio(sr_hat, benchmark, n, skew, kurtosis)
    return {
        "available": True,
        "n_trials": n_trials,
        "recommended_sharpe": round(sr_hat, 4),
        "benchmark_sharpe": round(benchmark, 4),
        "recommended_n_trades": n,
        "deflated_sharpe_ratio": round(dsr, 4) if dsr is not None else None,
        "note": (
            "Probability the recommended profile's real edge exceeds what pure chance "
            f"would produce as the best of {n_trials} tried variants -- not a precision "
            "number, a confidence-in-the-selection number. Per-trade Sharpe (R-multiple "
            "based), not annualized."
        ),
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


_SUMMARY_CACHE_PREFIX = "infusion:backtest:summary:cache:"
_SUMMARY_CACHE_TTL_SEC = 90

# Enhanced follow-up to the version that shipped with Phase N8: that one
# ran 5 independent queries (overview, by_grade, by_session, by_sector,
# target_levels), each doing its own WHERE created_at >= ... full scan --
# live-measured at 23s for days=7 against this archive's 269,560 rows in
# that window (269,479 of them suppressed), and a separate check hit
# nginx's 30s gateway timeout outright. GROUPING SETS computes all four
# breakdowns (plus the single overview row) from ONE scan of the filtered
# row set instead of five, using GROUPING(col) to tell which aggregation
# level each result row belongs to -- the standard, correct way to do
# "give me the overall total AND broken out by X AND by Y AND by Z" in
# one query rather than a UNION of separately-filtered queries.
_SUMMARY_SQL = """
SELECT
    GROUPING(conviction_grade) AS g_grade,
    GROUPING(session_hour) AS g_session,
    GROUPING(sector_id) AS g_sector,
    COALESCE(conviction_grade, '-') AS grade_label,
    COALESCE(session_hour, 'unknown') AS session_label,
    COALESCE(sector_id, '-') AS sector_label,
    COUNT(*)::int AS total,
    COUNT(*) FILTER (WHERE NOT COALESCE(suppressed, false))::int AS active,
    COUNT(*) FILTER (WHERE COALESCE(suppressed, false))::int AS suppressed,
    COUNT(*) FILTER (WHERE outcome_label = 'TARGET_HIT')::int AS target_hits,
    COUNT(*) FILTER (WHERE outcome_label = 'STOP_HIT')::int AS stop_hits,
    COUNT(*) FILTER (WHERE outcome_label = 'EXPIRED')::int AS expired,
    AVG(conviction_score)::float AS avg_score,
    AVG(risk_reward_ratio)::float AS avg_rr,
    AVG(max_favorable_pct)::float AS avg_mfe,
    AVG(max_adverse_pct)::float AS avg_mae,
    COUNT(*) FILTER (WHERE target_level_hit = 'T1')::int AS t1,
    COUNT(*) FILTER (WHERE target_level_hit = 'T2')::int AS t2,
    COUNT(*) FILTER (WHERE target_level_hit = 'T3')::int AS t3,
    COUNT(*) FILTER (WHERE outcome_label = 'TARGET_HIT' AND target_level_hit IS NULL)::int AS target_unknown
FROM signals
WHERE created_at >= now() - ($1::int * interval '1 day')
{where_strategy}
GROUP BY GROUPING SETS ((), (conviction_grade), (session_hour), (sector_id))
"""


async def _compute_backtest_summary(pool, days: int, strategy: str) -> dict:
    """The actual query + shaping, extracted so the route handler can
    cache around it. Same output shape as before this follow-up -- only
    how it's computed changed, not what callers get back."""
    where_strategy = "AND strategy = $2" if strategy else ""
    params = [days]
    if strategy:
        params.append(strategy)

    async with pool.acquire() as conn:
        try:
            grouped = await conn.fetch(_SUMMARY_SQL.format(where_strategy=where_strategy), *params)
        except Exception as exc:
            return {
                "available": False,
                "phase": "Phase 5",
                "reason": f"Backtest summary could not read archived signal outcomes: {exc}",
            }

    overview_row = None
    grade_rows, session_rows, sector_rows = [], [], []
    for r in grouped:
        d = dict(r)
        # GROUPING(col) is 1 when col is NOT a grouping column for this
        # row (i.e. it was aggregated over), 0 when it IS -- confirmed
        # directly against Postgres before trusting it, not assumed from
        # memory (SELECT GROUPING(x) ... GROUP BY GROUPING SETS ((),(x))
        # returns g=1 for the ()-row, g=0 for the (x)-rows). So the
        # single overall-totals row -- grouped by NONE of the three
        # columns -- is (1,1,1), not (0,0,0) as an earlier version of
        # this function had it (a real bug: it left `overview_row` stuck
        # at None, silently zeroing out total/active/target_hits/etc.
        # while by_grade/by_session/by_sector kept working -- caught by
        # comparing this endpoint's own overview numbers against its own
        # by_grade total, which didn't add up).
        if d["g_grade"] == 1 and d["g_session"] == 1 and d["g_sector"] == 1:
            overview_row = d
        elif d["g_grade"] == 0 and d["g_session"] == 1 and d["g_sector"] == 1:
            grade_rows.append(d)
        elif d["g_session"] == 0 and d["g_grade"] == 1 and d["g_sector"] == 1:
            session_rows.append(d)
        elif d["g_sector"] == 0 and d["g_grade"] == 1 and d["g_session"] == 1:
            sector_rows.append(d)

    o = overview_row or {}
    total = int(o.get("total") or 0)
    hits = int(o.get("target_hits") or 0)
    stops = int(o.get("stop_hits") or 0)
    expired = int(o.get("expired") or 0)
    decided = hits + stops
    precision = round(hits / decided * 100, 1) if decided else None
    reliability, note = _reliability(decided, total, precision)

    def rows(records, label_key):
        out = []
        for d in records:
            wins = int(d.get("target_hits") or 0)
            losses = int(d.get("stop_hits") or 0)
            dec = wins + losses
            out.append({
                "label": d.get(label_key) or "-",
                "total": int(d.get("total") or 0),
                "wins": wins,
                "losses": losses,
                "precision_pct": round(wins / dec * 100, 1) if dec else None,
            })
        out.sort(key=lambda x: x["total"], reverse=True)
        return out[:8]

    return {
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
        "by_grade": rows(grade_rows, "grade_label"),
        "by_session": rows(session_rows, "session_label"),
        "by_sector": rows(sector_rows, "sector_label"),
        # Phase N8. "unknown" = TARGET_HIT rows archived before migration
        # 003 added target_level_hit -- real historical hits, just no
        # level recorded for them, not zero.
        "target_levels": {
            "t1": int(o.get("t1") or 0),
            "t2": int(o.get("t2") or 0),
            "t3": int(o.get("t3") or 0),
            "unknown": int(o.get("target_unknown") or 0),
        },
    }


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

    # Short-TTL cache: the query is real work even after the GROUPING SETS
    # consolidation (still one full scan of the date-range row set), and
    # this endpoint gets hit repeatedly with the same days/strategy within
    # short windows (Signal Integrity's window pills, the Optimizer panel,
    # dashboard polling) with no need for sub-90s freshness on a rolling
    # N-day aggregate. Same request.app["redis"] + json.dumps/loads pattern
    # this file's optimizer-proposal cache already uses below.
    redis = request.app.get("redis")
    cache_key = f"{_SUMMARY_CACHE_PREFIX}{days}:{strategy or 'all'}"
    if redis is not None:
        cached_raw = await redis.get(cache_key)
        if cached_raw:
            result = json.loads(cached_raw.decode() if isinstance(cached_raw, bytes) else cached_raw)
            result["cached"] = True
            return web.json_response(result)

    result = await _compute_backtest_summary(pool, days, strategy)
    result["cached"] = False
    result["computed_at_us"] = int(time.time() * 1_000_000)
    if redis is not None and result.get("available"):
        await redis.set(cache_key, json.dumps(result), ex=_SUMMARY_CACHE_TTL_SEC)
    return web.json_response(result)


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
    embargo_min: float = 5.0,
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

    Phase 13.3: the chronological cut is additionally purged and embargoed
    around the split boundary -- see _purge_and_embargo()'s docstring for
    why a plain index split isn't enough on its own. embargo_min defaults
    to 5, matching archiver/config.py's signal_ttl_min -- the exact
    maximum time any TARGET_HIT/STOP_HIT outcome can take to resolve.
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
    embargo_min = max(0.0, min(120.0, float(embargo_min if embargo_min is not None else 5.0)))
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
                    target_hit_at,
                    stop_hit_at,
                    COALESCE(suppressed, false) AS suppressed,
                    COALESCE(strategy, '-') AS strategy,
                    COALESCE(pre_breakout_state, '-') AS pre_breakout_state,
                    COALESCE(market_regime, '-') AS market_regime,
                    entry_premium_ask, entry_premium_bid, exit_premium_bid, sub_scores
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
    train_rows, test_rows, purged_count, embargoed_count = _purge_and_embargo(rows, split, embargo_min)
    train_days = max(1, int(days * train_pct))
    test_days = max(1, days - train_days)

    # Purging/embargoing can drop either side below the minimum sample
    # size that already passed the pre-split total-rows check above --
    # re-validate post-split rather than silently running a grid search
    # against a test set embargo left too thin to mean anything.
    if len(train_rows) < min_train or len(test_rows) < min_test:
        return {
            "available": True,
            "phase": "Phase 5.3",
            "status": "LOW_SAMPLE",
            "target_precision_pct": target,
            "total_decided": total,
            "train_size": len(train_rows),
            "test_size": len(test_rows),
            "purged_train_count": purged_count,
            "embargoed_test_count": embargoed_count,
            "embargo_min": embargo_min,
            "recommended": None,
            "candidates": [],
            "note": (
                f"Purging ({purged_count} train row(s) whose outcome resolved after the split) "
                f"and a {embargo_min:.0f}-min embargo ({embargoed_count} test row(s) dropped) "
                f"left too few decided outcomes on one side of the split "
                f"(train={len(train_rows)}, need {min_train}; test={len(test_rows)}, need {min_test}). "
                f"Wait for more archived signals or widen the days window."
            ),
        }

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
                    test_sharpe = _profile_sharpe(test_rows, profile)
                    test_net_of_cost = _net_of_cost_metrics(test_rows, profile)
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
                        "test_sharpe": test_sharpe,
                        "test_net_of_cost": test_net_of_cost,
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
    dsr = _compute_dsr(profiles, recommended)
    # Phase 13.4b: whole-window (no profile filter) net-of-cost read --
    # visible even while every individual profile above is still below
    # MIN_NET_OF_COST_SAMPLE, since real premium capture (Phase 13.4) is
    # young and thinly spread across 1,575 profiles. This aggregate is the
    # first place a real net-vs-gross gap will become visible as capture
    # accumulates, before any single profile has enough of its own.
    net_of_cost = _net_of_cost_metrics(rows)

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
        # Phase 13.3: purge/embargo counts, always reported (even 0) so
        # this is verifiable rather than a claimed-but-invisible method --
        # matches this session's standard of never asserting a technique
        # is in effect without a number a human can check.
        "purged_train_count": purged_count,
        "embargoed_test_count": embargoed_count,
        "embargo_min": embargo_min,
        "target_met": target_met,
        "status": recommended["status"] if recommended else "NO_PROFILE",
        "recommended": recommended,
        "candidates": profiles[:10],
        "note": note,
        "dsr": dsr,
        "net_of_cost": net_of_cost,
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
    embargo_min = request.query.get("embargo_min", "5")
    result = await compute_walkforward(
        pool,
        days=int(days) if days else 120,
        target=float(target) if target else 80.0,
        min_train=int(min_train) if min_train else 30,
        min_test=int(min_test) if min_test else 15,
        train_pct=float(train_pct) if train_pct else 0.70,
        embargo_min=float(embargo_min) if embargo_min else 5.0,
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


def _decode_json_column(raw) -> dict:
    try:
        decoded = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (json.JSONDecodeError, TypeError):
        decoded = {}
    return decoded if isinstance(decoded, dict) else {}


def _ic_encode(field: str, value) -> float | None:
    """0/1 presence encoding for one field's raw value, feeding
    statistics_utils.pearson_r as the point-biserial x-variable. Plain
    truthiness (_ablation_field_present, already used by feature-ablation
    above) covers every KNOWN_ABLATION_FIELDS/_SUB_SCORES entry except
    ma_regime, which is a 3-way string enum ("golden_cross"/"death_cross"/
    "unknown") where truthiness alone can't tell a confirmed bearish
    regime apart from "we don't know yet" -- both are non-empty strings.
    ma_regime is special-cased to test specifically "is this a confirmed
    golden cross", excluding "unknown" rows from the sample entirely
    rather than folding them into either side.

    vcp_score (Phase 13.12, CONTINUOUS_IC_FIELDS) is special-cased the
    other direction: it's already a real 0-100 composite, so the raw value
    is passed straight through rather than collapsed to 0/1 -- correlating
    the actual factor score against R-multiple is the more informative
    read, and is literally what "information coefficient" means for a
    continuous factor in quant finance.
    """
    if field == "ma_regime":
        if value == "golden_cross":
            return 1
        if value == "death_cross":
            return 0
        return None
    if field in CONTINUOUS_IC_FIELDS:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return 1 if _ablation_field_present(value) else 0


async def compute_feature_ic(pool, days: int = 90) -> dict:
    """Phase 13.8: per-feature Information Coefficient -- Pearson (here,
    point-biserial since every field is 0/1-encoded) correlation between
    each KNOWN_ABLATION_FIELDS(_SUB_SCORES) field's presence and the
    R-multiple outcome of the signal it was attached to. Complements
    feature-ablation's precision-split view with a single, comparable-
    across-fields number, computed from the same archived outcomes.

    Same governance as everything else this session: diagnostic evidence
    only, ranks fields for a human to review, never auto-wires anything
    into scanner/scoring.py.
    """
    if not pool:
        return {"available": False, "reason": "Postgres analytics pool is not available.", "phase": "Phase 13.8"}

    days = max(1, min(365, int(days or 90)))
    async with pool.acquire() as conn:
        try:
            records = await conn.fetch("""
                SELECT outcome_label, risk_reward_ratio, features, sub_scores
                FROM signals
                WHERE created_at >= now() - ($1::int * interval '1 day')
                  AND outcome_label IN ('TARGET_HIT', 'STOP_HIT')
                  AND NOT COALESCE(suppressed, false)
            """, days)
        except Exception as exc:
            return {"available": False, "phase": "Phase 13.8", "reason": f"Feature-IC query failed: {exc}"}

    rows = []
    for r in records:
        d = dict(r)
        rm = r_multiple(d.get("outcome_label"), d.get("risk_reward_ratio"))
        if rm is None:
            continue
        rows.append({
            "r_multiple": rm,
            "features": _decode_json_column(d.get("features")),
            "sub_scores": _decode_json_column(d.get("sub_scores")),
        })

    total = len(rows)
    field_specs = (
        [(f, "features") for f in KNOWN_ABLATION_FIELDS]
        + [(f, "sub_scores") for f in KNOWN_ABLATION_FIELDS_SUB_SCORES]
        + [(f, "features") for f in CONTINUOUS_IC_FIELDS]
    )
    results = []
    for field, column in field_specs:
        xs, ys = [], []
        for row in rows:
            encoded = _ic_encode(field, row[column].get(field))
            if encoded is None:
                continue
            xs.append(float(encoded))
            ys.append(row["r_multiple"])
        ic = pearson_r(xs, ys)
        continuous = field in CONTINUOUS_IC_FIELDS
        if continuous:
            # No presence/absence axis for a continuous score -- every row
            # in xs already has a real computed value (encoded is None was
            # skipped above), so "present" just means "used".
            results.append({
                "field": field,
                "column": "features_snapshot" if column == "features" else column,
                "ic": round(ic, 4) if ic is not None else None,
                "n_used": len(xs),
                "n_present": len(xs),
                "n_absent": None,
                "continuous": True,
            })
            continue
        n_present = sum(1 for x in xs if x == 1.0)
        results.append({
            "field": field,
            "column": "features_snapshot" if column == "features" else column,
            "ic": round(ic, 4) if ic is not None else None,
            "n_used": len(xs),
            "n_present": n_present,
            "n_absent": len(xs) - n_present,
            "continuous": False,
        })

    # A field's total n_used is nearly always huge (most archived signals
    # simply predate the field existing at all, and absence-because-never-
    # computed is indistinguishable from absence-because-not-triggered --
    # both correctly encode to 0). That makes total-N alone a misleading
    # reliability gate: a correlation computed from 3 "present" rows
    # against 12,000+ "absent" ones is statistical noise no matter how
    # large n_used looks. Reliability requires enough rows on BOTH sides,
    # not just a big denominator (caught live: every Phase 1-10 field
    # currently has single-digit real presence counts in the archive,
    # since most rows predate these fields being added to
    # features_snapshot at all).
    min_side = 15
    # Continuous fields (vcp_score) have no absent side to gate on -- reliability
    # is just "enough rows used", same threshold shape as Kelly's `decided >= 30`.
    min_continuous_n = 30
    for r in results:
        if r.get("continuous"):
            r["reliable"] = r["ic"] is not None and r["n_used"] >= min_continuous_n
        else:
            r["reliable"] = r["ic"] is not None and r["n_present"] >= min_side and r["n_absent"] >= min_side
    reliable = [r for r in results if r["reliable"]]
    return {
        "available": True,
        "phase": "Phase 13.8",
        "method": "point_biserial_ic_vs_r_multiple",
        "days": days,
        "total_decided": total,
        "min_side_for_reliable": min_side,
        "min_n_for_reliable_continuous": min_continuous_n,
        "fields": results,
        "note": (
            f"{len(reliable)}/{len(results)} fields have >= {min_side} decided outcomes on BOTH "
            "the present and absent side (not just a large total N -- most fields here are new "
            "enough that the vast majority of archived history predates them, so a big n_used "
            "with a tiny n_present is not a reliable correlation regardless of its IC value). "
            f"vcp_score (Phase 13.12) is continuous, not presence/absence -- it's reliable at "
            f">= {min_continuous_n} rows used, correlated on its raw 0-100 value, not a 0/1 split. "
            "|IC| above ~0.1 is a real, if modest, signal in a noisy per-trade series among "
            "reliable fields; treat anything below that, or any unreliable field, as noise. "
            "Diagnostic only -- never auto-wired into scanner/scoring.py."
        ),
    }


@routes.get("/api/backtest/feature-ic")
async def backtest_feature_ic(request):
    """GET /api/backtest/feature-ic?days=90 -- ranked Information
    Coefficient for every KNOWN_ABLATION_FIELDS(_SUB_SCORES) field against
    real R-multiple outcomes. See compute_feature_ic()."""
    pool = request.app.get("pg_pool")
    days = request.query.get("days", "90")
    result = await compute_feature_ic(pool, days=int(days) if days else 90)
    return web.json_response(result)


@routes.get("/api/backtest/false-break-rate")
async def backtest_false_break_rate(request):
    """GET /api/backtest/false-break-rate?days=90 -- EBIE EB-9 increment
    2. Real false-break rate from archived outcomes, by strategy/grade,
    plus a live check of whether EB-9 increment 1's trap_risk heuristic
    actually correlates with real trap outcomes. See
    api/trap_labels.py's compute_false_break_stats()."""
    pool = request.app.get("pg_pool")
    days = request.query.get("days", "90")
    result = await compute_false_break_stats(pool, days=int(days) if days else 90)
    return web.json_response(result)


KELLY_KEY_PREFIX = "infusion:kelly:"


async def compute_kelly_sizing(pool, redis, days: int = 180) -> dict:
    """Phase 13.10: half-Kelly position-sizing stat per strategy, computed
    from Infusion's own archived TARGET_HIT/STOP_HIT outcomes, and written
    to Redis (KELLY_KEY_PREFIX + strategy_id) for scanner/engine.py's
    _recommended_lots() to read cheaply -- scanner has no Postgres access,
    matching the "propose only, scanner reads a Redis cache another
    service wrote" pattern optimizer-proposal/F&O-ban already use here.

    Kelly% = W - (1-W)/R (the standard Kelly criterion for a binary win/
    loss bet), where W = win rate and R = average win size in R-multiples
    (average loss is always exactly 1R by this codebase's own
    risk_reward_ratio convention -- see statistics_utils.r_multiple).
    Half-Kelly (Kelly%/2) is what's actually surfaced -- full Kelly's
    optimal-growth guarantee assumes W/R are known exactly, which they
    never are from a finite sample; half-Kelly is the standard
    practitioner discount against that estimation error.

    A NEGATIVE Kelly% is reported as-is, not clipped to zero -- it means
    the strategy's own historical numbers argue against sizing up at all,
    a real and important reading, not an edge case to hide.

    Gated on >= 30 decided outcomes per strategy (the same minimum this
    session's earlier nse-trading-skills GitHub research cited) -- below
    that, reports "not enough sample" rather than a number nobody should
    trust. Informational only, surfaced alongside (never replacing) the
    existing ATR-scaled Turtle sizing in engine.py's _recommended_lots().
    """
    if not pool:
        return {"available": False, "reason": "Postgres analytics pool is not available.", "phase": "Phase 13.10"}

    days = max(1, min(365, int(days or 180)))
    min_sample = 30

    async with pool.acquire() as conn:
        try:
            records = await conn.fetch("""
                SELECT COALESCE(strategy, '-') AS strategy, outcome_label, risk_reward_ratio
                FROM signals
                WHERE created_at >= now() - ($1::int * interval '1 day')
                  AND outcome_label IN ('TARGET_HIT', 'STOP_HIT')
                  AND NOT COALESCE(suppressed, false)
            """, days)
        except Exception as exc:
            return {"available": False, "phase": "Phase 13.10", "reason": f"Kelly-sizing query failed: {exc}"}

    by_strategy: dict[str, list[dict]] = {}
    for r in records:
        d = dict(r)
        by_strategy.setdefault(d["strategy"], []).append(d)

    strategies: dict[str, dict] = {}
    for strategy_id, rows in by_strategy.items():
        wins = [r for r in rows if r["outcome_label"] == "TARGET_HIT"]
        losses = [r for r in rows if r["outcome_label"] == "STOP_HIT"]
        decided = len(wins) + len(losses)
        win_rate = (len(wins) / decided) if decided else None
        avg_win_r = (
            sum((float(r.get("risk_reward_ratio") or 0) or 1.0) for r in wins) / len(wins)
            if wins else None
        )
        reliable = decided >= min_sample and win_rate is not None and bool(avg_win_r) and avg_win_r > 0
        kelly_pct = half_kelly_pct = None
        if reliable:
            kelly_pct = win_rate - (1 - win_rate) / avg_win_r
            half_kelly_pct = kelly_pct / 2
        strategies[strategy_id] = {
            "decided": decided,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": round(win_rate * 100, 1) if win_rate is not None else None,
            "avg_win_r": round(avg_win_r, 3) if avg_win_r is not None else None,
            "kelly_pct": round(kelly_pct * 100, 2) if kelly_pct is not None else None,
            "half_kelly_pct": round(half_kelly_pct * 100, 2) if half_kelly_pct is not None else None,
            "reliable": bool(reliable),
        }

    if redis is not None:
        for strategy_id, stat in strategies.items():
            key = f"{KELLY_KEY_PREFIX}{strategy_id}"
            mapping = {k: str(v) for k, v in stat.items()}
            pipe = redis.pipeline(transaction=False)
            pipe.hset(key, mapping=mapping)
            pipe.expire(key, 3 * 86400)  # a stale cache is worse than a missing one past a few days
            await pipe.execute()

    return {
        "available": True,
        "phase": "Phase 13.10",
        "days": days,
        "min_sample": min_sample,
        "strategies": strategies,
        "note": (
            "Half-Kelly position-size multiplier per strategy, from real archived outcomes. "
            "A negative Kelly% means the strategy's own historical numbers argue against "
            "sizing up at all, not just for smaller size. Informational only -- surfaced "
            "alongside, never replacing, the existing ATR-scaled position sizing."
        ),
    }


@routes.get("/api/backtest/kelly-sizing")
async def backtest_kelly_sizing(request):
    """GET /api/backtest/kelly-sizing?days=180 -- half-Kelly sizing stat
    per strategy, also written to Redis for scanner to read. See
    compute_kelly_sizing()."""
    pool = request.app.get("pg_pool")
    redis = request.app.get("redis")
    days = request.query.get("days", "180")
    result = await compute_kelly_sizing(pool, redis, days=int(days) if days else 180)
    return web.json_response(result)


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


# Phase 13.4 (capture infra only -- see the plan's "Net-of-Cost Walk-Forward
# Validation" section for why net-precision reporting itself is a separate,
# later follow-up rather than part of this same change).
_PREMIUM_LOOKBACK_MIN = 10
_PREMIUM_BATCH_LIMIT = 20


async def capture_missing_premiums(pool, redis) -> dict:
    """Fill in entry/exit option premium for recently-fired/-resolved
    signals that don't have it yet. Called on a short interval by
    services/scheduler/src/scheduler/main.py's premium_capture_loop --
    never by the dashboard. Two independent, bounded (LIMIT 20 each)
    passes so one slow Upstox call can't block the other:

    1. Recently-published active signals missing entry_premium_ask --
       fetch the near-ATM contract's current ask/bid for that signal's
       side (CE for a bullish signal_type, PE for bearish -- the
       signals table has no separate option_bias column, but signal_type
       already encodes this 1:1, see options_first_hybrid.py's
       `option_bias = "BUY CE" if bullish else "BUY PE"`) and write it.
    2. Recently-decided (TARGET_HIT/STOP_HIT) signals missing
       exit_premium_bid -- same fetch, write just the bid (the exit
       fill).

    Deliberately scoped to the last _PREMIUM_LOOKBACK_MIN minutes only:
    an option contract fetched for a signal from hours ago may have
    already rolled or thinned out enough that "current chain" no longer
    means what it did at signal time, and there's no point endlessly
    retrying a contract lookup for a signal that's aged out of being
    capturable with any real accuracy.
    """
    if not pool or not redis:
        return {"available": False, "reason": "Postgres pool or Redis not available."}

    # Local import: routes/market.py pulls in the heavier Upstox-auth
    # import chain (aiohttp session helpers, instrument-key resolution)
    # that only matters when this capture actually runs -- same pattern
    # ai.py already uses for the identical reason.
    from api.routes.market import _capture_option_premium

    entry_captured = 0
    entry_attempted = 0
    exit_captured = 0
    exit_attempted = 0

    async with pool.acquire() as conn:
        entry_rows = await conn.fetch(
            """
            SELECT signal_id, symbol, signal_type
            FROM signals
            WHERE created_at >= now() - ($1::int * interval '1 minute')
              AND NOT COALESCE(suppressed, false)
              AND entry_premium_ask IS NULL
            ORDER BY created_at DESC
            LIMIT $2
            """,
            _PREMIUM_LOOKBACK_MIN, _PREMIUM_BATCH_LIMIT,
        )
        for row in entry_rows:
            entry_attempted += 1
            bias = "CE" if row["signal_type"] == "bullish" else "PE"
            premium = await _capture_option_premium(redis, row["symbol"], bias)
            if premium is None:
                continue
            await conn.execute(
                """
                UPDATE signals
                SET entry_premium_ask = $1, entry_premium_bid = $2, option_instrument_key = $3
                WHERE signal_id = $4
                """,
                premium["ask"], premium["bid"], premium["instrument_key"], row["signal_id"],
            )
            entry_captured += 1

        exit_rows = await conn.fetch(
            """
            SELECT signal_id, symbol, signal_type
            FROM signals
            WHERE outcome_label IN ('TARGET_HIT', 'STOP_HIT')
              AND COALESCE(target_hit_at, stop_hit_at) >= now() - ($1::int * interval '1 minute')
              AND exit_premium_bid IS NULL
            ORDER BY COALESCE(target_hit_at, stop_hit_at) DESC
            LIMIT $2
            """,
            _PREMIUM_LOOKBACK_MIN, _PREMIUM_BATCH_LIMIT,
        )
        for row in exit_rows:
            exit_attempted += 1
            bias = "CE" if row["signal_type"] == "bullish" else "PE"
            premium = await _capture_option_premium(redis, row["symbol"], bias)
            if premium is None:
                continue
            await conn.execute(
                "UPDATE signals SET exit_premium_bid = $1 WHERE signal_id = $2",
                premium["bid"], row["signal_id"],
            )
            exit_captured += 1

    return {
        "available": True,
        "entry_attempted": entry_attempted,
        "entry_captured": entry_captured,
        "exit_attempted": exit_attempted,
        "exit_captured": exit_captured,
    }


@routes.post("/api/backtest/capture-premiums")
async def backtest_capture_premiums(request):
    """POST /api/backtest/capture-premiums -- called by scheduler's
    premium_capture_loop on a short interval, not by the dashboard.
    See capture_missing_premiums()'s docstring for what it does."""
    pool = request.app.get("pg_pool")
    redis = request.app.get("redis")
    result = await capture_missing_premiums(pool, redis)
    return web.json_response(result)


@routes.get("/api/backtest/ml-classifier")
async def backtest_ml_classifier_train(request):
    """GET /api/backtest/ml-classifier?days=400 -- (re)trains the
    classifier against real archived outcomes and caches the result.
    Real cost: ~15-20s CPU-bound (run off the event loop via
    asyncio.to_thread inside train_classifier -- other requests keep
    being served while this runs). Called by the scheduler's daily
    ml_classifier_loop, not meant for the dashboard to poll directly --
    see /api/backtest/ml-classifier/latest for that. See
    api/ml_classifier.py's module docstring for the real first-run
    finding (near-zero lift over the existing conviction_score with
    today's coverage-gated feature set) and why that's honest, not a bug.
    """
    pool = request.app.get("pg_pool")
    redis = request.app.get("redis")
    days = request.query.get("days", "400")
    result = await train_classifier(pool, redis, days=int(days) if days else 400)
    return web.json_response(result)


@routes.get("/api/backtest/ml-classifier/latest")
async def backtest_ml_classifier_latest(request):
    """GET /api/backtest/ml-classifier/latest -- reads the last-trained
    model + its held-out test metrics from Redis without retraining
    (cheap, for the dashboard to poll)."""
    redis = request.app.get("redis")
    result = await read_cached_model(redis)
    return web.json_response(result)
