"""EBIE EB-12 (increment 1) -- ranked candidate list backend, the first
EBIE-phase dashboard data source. Per docs/EBIE-BLUEPRINT.md Section 32
("Main table... Do not show 30 indicator columns by default", "Expand
row: Why Now / Price Structure / Accumulation / Derivatives / Sentiment
/ Risk / Option") and Section 34 ("Why Not? Rejection UI... A mature
scanner should explain why it refused a trade, not only why it likes
one").

Read-only view over already-archived signals (services/archiver writes
every candidate -- fired AND suppressed -- with its full sub_scores/
features_snapshot JSONB, per this codebase's existing persistence).
Nothing here computes anything new: verdict (EB-8), trap risk (EB-9),
and portfolio fit (EB-11) already exist in sub_scores; accumulation/
derivatives/sentiment/data-quality fields already exist in
features_snapshot (CLV, VCP, futures basis, option-chain wall dynamics,
news sentiment -- every evidence family wired into features_snapshot
across EB-2 through EB-11). This route is pure formatting/aggregation
over data that's already there.

"Why Not" for a rejected setup combines THREE real, distinct sources,
not fabricated: sub_scores.verdict.risks (evidence contradicting the
candidate's own direction), sub_scores.verdict.hard_gates (a genuine
EB-8 hard block, e.g. F&O ban/DQ failure), and suppression_reason (the
existing, separate scanner suppression gate's own real reason, e.g.
cooldown/duplicate/portfolio-heat). These are three independent systems
-- shown together, not merged into one undifferentiated list, so a
reader can tell which gate actually fired.
"""

from __future__ import annotations

import json

from aiohttp import web

routes = web.RouteTableDef()


def _decode_json(raw) -> dict:
    try:
        decoded = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (json.JSONDecodeError, TypeError):
        decoded = {}
    return decoded if isinstance(decoded, dict) else {}


def _num(v) -> float | None:
    """asyncpg returns NUMERIC columns as Decimal, which the stdlib json
    module can't serialize -- convert to float (JSON has no decimal
    type anyway, same precision loss every other route in this codebase
    already accepts for money/score fields)."""
    return float(v) if v is not None else None


# EBIE EB-15 Phase 1 item 3 -- literal P6 data-quality policy (the exact
# thresholds already enforced inside scanner/verdict_engine.py's
# DQ_HARD_FAIL/DQ_DEGRADED gates), surfaced here as the plain-language
# status label the directive requires the dashboard to show. This route
# computes no new number -- data_quality_score already exists in
# features_snapshot (EB-0); this just labels it consistently with the
# gate that already acts on it.
def _dq_status(score) -> str:
    if score is None:
        return "UNKNOWN"
    if score < 80:
        return "DATA_UNRELIABLE"
    if score < 90:
        return "DEGRADED"
    return "READY"


def _row_to_candidate(r) -> dict:
    d = dict(r)
    sub_scores = _decode_json(d.get("sub_scores"))
    features = _decode_json(d.get("features"))
    verdict = sub_scores.get("verdict") or {}
    trap = sub_scores.get("trap_risk") or {}
    portfolio = sub_scores.get("portfolio_fit") or {}

    why_not: list[str] = []
    why_not.extend(verdict.get("hard_gates") or [])
    if d.get("suppressed") and d.get("suppression_reason"):
        why_not.append(d["suppression_reason"])
    why_not.extend(verdict.get("risks") or [])

    return {
        "signal_id": str(d.get("signal_id")) if d.get("signal_id") else None,
        "symbol": d.get("symbol"),
        "strategy_id": d.get("strategy"),
        "direction": d.get("signal_type"),
        "created_at": d["created_at"].isoformat() if d.get("created_at") else None,
        "suppressed": bool(d.get("suppressed")),
        "outcome_label": d.get("outcome_label"),

        # Main-table columns (Section 32) -- everything already computed,
        # this route only selects/renames, never invents a new number.
        "verdict": verdict.get("verdict"),
        "score": _num(d.get("conviction_score")),
        "grade": d.get("conviction_grade"),
        "bull_score": verdict.get("bull_score"),
        "bear_score": verdict.get("bear_score"),
        "directional_score": verdict.get("directional_score"),
        "trap_risk_score": trap.get("trap_risk_score"),
        "portfolio_fit_score": portfolio.get("portfolio_fit_score"),
        "portfolio_fit_label": portfolio.get("portfolio_fit_label"),
        "data_quality_score": features.get("data_quality_score"),
        "sector_id": d.get("sector_id"),
        "risk_reward_ratio": _num(d.get("risk_reward_ratio")),

        # EBIE EB-15 Phase 1 item 3: the dashboard must show data-quality
        # status "without hiding the setup" (the directive's own wording)
        # -- so this is additive detail on the existing row/expand-row,
        # never a filter that removes a candidate from the list.
        "data_quality": {
            "score": features.get("data_quality_score"),
            "status": _dq_status(features.get("data_quality_score")),
            "reasons": features.get("data_quality_reasons") or [],
            "unavailable_evidence_families": verdict.get("unavailable_families") or [],
        },

        # Expand-row sections
        "why_now": verdict.get("top_reasons") or [],
        "why_not": why_not,
        "price_structure": {
            "entry_price": _num(d.get("entry_price")),
            "invalidation_price": _num(d.get("invalidation_price")),
            "target_price": _num(d.get("target_price")),
        },
        "accumulation": {
            "clv_ema": features.get("clv_ema"),
            "vcp_score": features.get("vcp_score"),
            "vcp_grade": features.get("vcp_grade"),
            "rel_vol_20d": features.get("rel_vol_20d"),
            "microstructure_book_imbalance": (features.get("microstructure_depth") or {}).get("book_imbalance_ema"),
        },
        "derivatives": {
            "futures_basis_pct": features.get("futures_basis_pct"),
            "futures_oi_change_pct": features.get("futures_oi_change_pct"),
            "weighted_pcr": features.get("weighted_pcr"),
            "pcr_velocity": features.get("pcr_velocity"),
            "call_wall_state": features.get("call_wall_state"),
            "put_wall_state": features.get("put_wall_state"),
        },
        "sentiment": {
            "news_sentiment": features.get("news_sentiment"),
            "news_sentiment_impact": features.get("news_sentiment_impact"),
            "news_article_count": features.get("news_article_count"),
        },
        "risk": {
            "trap_risk_score": trap.get("trap_risk_score"),
            "trap_reasons": trap.get("trap_reasons") or [],
            "portfolio_fit_reasons": portfolio.get("portfolio_fit_reasons") or [],
            "correlated_symbols": portfolio.get("correlated_symbols") or [],
        },
    }


@routes.get("/api/ebie/candidates")
async def ebie_candidates(request):
    """GET /api/ebie/candidates?limit=30&suppressed=true|false|all --
    ranked-by-recency candidate list with full Why-Now/Why-Not evidence.
    `suppressed` defaults to 'all' (both fired and rejected candidates,
    matching Section 34's own "explain why it refused a trade" ask)."""
    pool = request.app.get("pg_pool")
    if not pool:
        return web.json_response({"available": False, "reason": "Postgres analytics pool is not available.", "candidates": []})

    limit = min(max(int(request.query.get("limit", "30") or 30), 1), 100)
    suppressed_filter = str(request.query.get("suppressed", "all")).lower()

    where = ""
    params: list = [limit]
    if suppressed_filter == "true":
        where = "WHERE suppressed"
    elif suppressed_filter == "false":
        where = "WHERE NOT suppressed"

    async with pool.acquire() as conn:
        try:
            rows = await conn.fetch(
                f"""
                SELECT signal_id, symbol, strategy, signal_type, conviction_score,
                       conviction_grade, entry_price, invalidation_price, target_price,
                       risk_reward_ratio, sector_id, market_regime, suppressed,
                       suppression_reason, outcome_label, sub_scores, features, created_at
                FROM signals
                {where}
                ORDER BY created_at DESC
                LIMIT $1
                """,
                *params,
            )
        except Exception as exc:
            return web.json_response({"available": False, "reason": f"candidate query failed: {exc}", "candidates": []})

    return web.json_response({
        "available": True,
        "count": len(rows),
        "candidates": [_row_to_candidate(r) for r in rows],
    })
