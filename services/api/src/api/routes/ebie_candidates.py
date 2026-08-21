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
import time
from datetime import datetime

import msgpack
from aiohttp import web

from infusion_streams.constants import KEY_EBIE_VERDICT_LITE_PREFIX

routes = web.RouteTableDef()

# EBIE-KNOWN-GAPS.md §1.7 -- the three rolling-subset caches this route
# already reads from (mtf/options-dynamics/option-chain) only cover a
# 7-24% slice of the universe at any moment. A candidate's
# `unavailable_evidence_families` list can't currently tell a reader
# "this evidence has never been computed for this symbol" apart from
# "it was computed recently but the short-TTL cache already expired" --
# those look identical today. This maps each rolling family to its live
# short-TTL cache prefix and its companion long-lived last-seen marker
# (see mtf.py's MTF_LAST_SEEN_PREFIX / options_dynamics_queue.py's
# LAST_SEEN_PREFIX / option_chain_queue.py's now-widened LAST_REFRESH_PREFIX
# for why each marker exists).
_FRESHNESS_SOURCES = {
    "relative_strength": ("infusion:mtf:", "infusion:mtf-last-seen:", "epoch"),
    "options_positioning": ("infusion:options-dynamics:", "infusion:options-dynamics-last-seen:", "epoch"),
    "option_tradeability": ("infusion:option-chain:", "infusion:option-chain-last-refresh:", "refreshed_at_iso"),
}


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


def _parse_last_seen_age_sec(raw, kind: str, now: float) -> float | None:
    """Best-effort age (seconds) from a last-seen marker's raw redis value.
    `raw` is bytes or None. Returns None (never a guessed 0) on absence or
    any decode failure -- a garbled marker is treated the same as no
    marker, not as "just seen"."""
    if not raw:
        return None
    try:
        if kind == "epoch":
            ts = float(raw.decode() if isinstance(raw, bytes) else raw)
            return max(0.0, now - ts)
        if kind == "refreshed_at_iso":
            payload = json.loads(raw)
            iso = payload.get("refreshed_at")
            if not iso:
                return None
            ts = datetime.fromisoformat(iso).timestamp()
            return max(0.0, now - ts)
    except (ValueError, TypeError, json.JSONDecodeError, AttributeError):
        return None
    return None


def _freshness_status(live_present: bool, last_seen_age_sec: float | None) -> dict:
    """EBIE-KNOWN-GAPS.md §1.7's own suggested fix: distinguish "never
    cached" from "cached but stale" for the three rolling-subset caches,
    rather than leaving both look identical as a bare absence.
    - `fresh`: the live short-TTL cache has this symbol right now.
    - `stale`: no live cache, but a real last-seen marker exists -- this
      symbol WAS computed before, just not recently.
    - `never_cached`: no live cache and no last-seen marker at all --
      this symbol has never had this evidence computed, as far as this
      long-lived marker's own TTL window can tell.
    """
    if live_present:
        return {"status": "fresh", "age_sec": 0}
    if last_seen_age_sec is not None:
        return {"status": "stale", "age_sec": round(last_seen_age_sec)}
    return {"status": "never_cached", "age_sec": None}


# EBIE-KNOWN-GAPS.md §7.1 -- two independent, disagreeing verdict
# computations exist for the same symbol: this full weighted verdict
# (EB-8 + EB-15 Phase 4) only runs for symbols with a real
# SignalCandidate, while EB-15 Phase 3's lightweight verdict
# (compute_lightweight_verdict()) runs for every symbol every 60s sweep
# off a smaller, unweighted input set. They can genuinely disagree.
# Reconciling here means making the disagreement VISIBLE where both
# exist, not merging the two computations into one (the lightweight
# verdict is deliberately cheap -- reading it against the full weighted
# family engine's inputs for every symbol every sweep would defeat its
# whole purpose) and not inventing a fabricated "agreement score" (same
# discipline as §7.2's drift-monitor: a real state<->state comparison,
# nothing dressed up as a number that doesn't exist yet).
def _direction_agreement(full_direction, lite_direction) -> str:
    full = str(full_direction or "").strip().lower()
    lite = str(lite_direction or "").strip().lower()
    if full not in ("bullish", "bearish") or lite not in ("bullish", "bearish"):
        return "unknown"
    return "agree" if full == lite else "disagree"


def _row_to_candidate(r, market_ctx: dict | None = None, opt_ctx: dict | None = None,
                       cache_freshness: dict | None = None, lite_verdict: dict | None = None) -> dict:
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
            # EBIE-KNOWN-GAPS.md §1.7 -- per-family cache freshness for the
            # three families backed by a rolling-subset queue (relative_
            # strength/mtf, options_positioning/options-dynamics), plus
            # option_tradeability (the Phase 5 hard-gate's own cache, not
            # itself a scored "family"). A LIVE, right-now read -- may
            # differ from what was true when this candidate actually
            # fired, same disclosed caveat as market_context/option_chain
            # below. None when the freshness enrichment wasn't computed
            # (e.g. Redis unavailable) -- never a guessed status.
            "cache_freshness": cache_freshness,
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

        # EBIE EB-15 Phase 7 -- Phase 4's market-context family and Phase
        # 5's option-tradeability gate already SCORE against this data
        # (verdict.bullish_families/risks/hard_gates carry it as generic
        # text), but the directive's own P7 checklist asks for "market and
        # sector context" and "option tradeability status" as their own
        # visible sections, not buried in a reasons list. Both are LIVE
        # reads of the same Redis caches Phase 4/5 already populate (a
        # snapshot as of right now, not the value in effect when this
        # candidate fired minutes/hours ago) -- correctly None when the
        # sweep hasn't cached anything for this symbol yet, never guessed.
        "market_context": market_ctx,
        "option_chain": (
            {
                "execution_status": opt_ctx.get("execution_status"),
                "quality_grade": opt_ctx.get("quality_grade"),
                "option_score": opt_ctx.get("option_score"),
                "expiry": opt_ctx.get("expiry"),
                "strike": opt_ctx.get("strike"),
                "blockers": opt_ctx.get("blockers") or [],
                "hard_blockers": opt_ctx.get("hard_blockers") or [],
            }
            if opt_ctx else None
        ),

        # EBIE-KNOWN-GAPS.md §7.1 -- the SAME symbol's current lightweight
        # verdict (EB-15 Phase 3), shown alongside this full verdict so a
        # reader can see when the two disagree rather than trusting
        # whichever one they happened to open. A LIVE, right-now read of
        # a genuinely different, independently-computed system -- may not
        # match what the lightweight verdict said at the moment THIS
        # candidate actually fired. None when nothing is cached for this
        # symbol right now (rare -- Phase 3's sweep covers the full
        # universe every 60s, unlike the rolling-subset caches above).
        "lightweight_verdict": (
            {
                "verdict": lite_verdict.get("verdict"),
                "direction": lite_verdict.get("direction"),
                "ebie_state": lite_verdict.get("ebie_state"),
                "confidence_band": lite_verdict.get("confidence_band"),
                "checked_at": lite_verdict.get("checked_at"),
            }
            if lite_verdict else None
        ),
        "direction_agreement": _direction_agreement(d.get("signal_type"), (lite_verdict or {}).get("direction")),
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

    # EBIE EB-15 Phase 7 -- live market-context (Phase 4) and option-chain
    # (Phase 5) reads, batched with one mget per cache rather than N round
    # trips. Best-effort: a Redis error here must never fail the whole
    # candidate list, so a symbol with a missing/expired cache entry (or
    # any read failure) just gets None -- the existing "never a silent
    # number" convention, not a hidden zero.
    redis = request.app.get("redis")
    market_ctx_map: dict[str, dict] = {}
    opt_ctx_map: dict[str, dict] = {}
    symbols = sorted({r["symbol"] for r in rows if r.get("symbol")})
    def _decode_cache_json(raw) -> dict:
        # Redis client here is decode_responses=False (main.py), so `raw`
        # is bytes, not str -- _decode_json() above only special-cases
        # str (it's built for asyncpg's JSONB-as-str columns) and would
        # silently return {} for every bytes value. json.loads() accepts
        # bytes/bytearray directly, so this is a separate small helper
        # rather than reusing _decode_json() and getting that wrong.
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    if redis and symbols:
        try:
            market_raw = await redis.mget([f"infusion:market-context:{s}" for s in symbols])
            for s, raw in zip(symbols, market_raw):
                parsed = _decode_cache_json(raw)
                if parsed:
                    market_ctx_map[s] = parsed
        except Exception:
            pass
        try:
            opt_raw = await redis.mget([f"infusion:option-chain:{s}" for s in symbols])
            for s, raw in zip(symbols, opt_raw):
                parsed = _decode_cache_json(raw)
                if parsed:
                    opt_ctx_map[s] = parsed
        except Exception:
            pass

    # EBIE-KNOWN-GAPS.md §7.1 -- the lightweight verdict cache is
    # msgpack (KEY_EBIE_VERDICT_LITE_PREFIX's own comment: consumed by
    # api's own routes, unlike the JSON caches above which scanner also
    # reads), same encoding ebie_state.py's own lightweight-verdicts
    # route already unpacks.
    lite_verdict_map: dict[str, dict] = {}
    if redis and symbols:
        try:
            lite_raw = await redis.mget([f"{KEY_EBIE_VERDICT_LITE_PREFIX}{s}" for s in symbols])
            for s, raw in zip(symbols, lite_raw):
                if not raw:
                    continue
                try:
                    parsed = msgpack.unpackb(raw, raw=False)
                except Exception:
                    continue
                if isinstance(parsed, dict):
                    lite_verdict_map[s] = parsed
        except Exception:
            pass

    # EBIE-KNOWN-GAPS.md §1.7 -- per-symbol freshness for the three
    # rolling-subset caches (see _FRESHNESS_SOURCES). Reuses the
    # option-chain live-cache read above (opt_ctx_map) rather than a
    # redundant second mget of the same key; only the other two live
    # caches (mtf/options-dynamics) plus all three last-seen markers need
    # fresh mgets. Six mgets total (2 already done above + 4 here) for up
    # to 100 symbols -- six round trips, not up to 600.
    freshness_map: dict[str, dict] = {s: {} for s in symbols}
    now = time.time()
    if redis and symbols:
        for family, (live_prefix, last_seen_prefix, kind) in _FRESHNESS_SOURCES.items():
            live_present: dict[str, bool] = {}
            last_seen_age: dict[str, float | None] = {}
            if family == "option_tradeability":
                # Live presence already fetched into opt_ctx_map above.
                for s in symbols:
                    live_present[s] = s in opt_ctx_map
            else:
                try:
                    live_raw = await redis.mget([f"{live_prefix}{s}" for s in symbols])
                    for s, raw in zip(symbols, live_raw):
                        live_present[s] = bool(raw)
                except Exception:
                    for s in symbols:
                        live_present[s] = False
            try:
                seen_raw = await redis.mget([f"{last_seen_prefix}{s}" for s in symbols])
                for s, raw in zip(symbols, seen_raw):
                    last_seen_age[s] = _parse_last_seen_age_sec(raw, kind, now)
            except Exception:
                for s in symbols:
                    last_seen_age[s] = None
            for s in symbols:
                freshness_map[s][family] = _freshness_status(live_present.get(s, False), last_seen_age.get(s))

    return web.json_response({
        "available": True,
        "count": len(rows),
        "candidates": [
            _row_to_candidate(
                r,
                market_ctx_map.get(r.get("symbol")),
                opt_ctx_map.get(r.get("symbol")),
                freshness_map.get(r.get("symbol")) if redis else None,
                lite_verdict_map.get(r.get("symbol")),
            )
            for r in rows
        ],
    })
