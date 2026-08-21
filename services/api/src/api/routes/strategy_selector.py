"""Multi-leg options strategy selector -- Phase 13.6b.

Ranks options_strategies.py's catalog for one symbol using inputs Infusion
already computes (IV Rank, PCR sentiment, Max Pain, directional bias from
mtf.py's compute_mtf) -- no new data source. Advisory only: returns a
ranked shortlist with per-component reasoning for a human to review, never
auto-selects or auto-executes, matching every other "propose only" pattern
in Infusion (kill-switch, optimizer-proposal, feature-ablation).

Local imports from market.py mirror the established pattern already used
by backtest.py's premium-capture code ("avoid loading the heavy Upstox-
auth chain at module import time") -- not a new convention.
"""

from __future__ import annotations

from aiohttp import web

from api.options_strategies import build_all_strategies, rank_strategies

routes = web.RouteTableDef()


async def compute_strategy_selection(redis, symbol: str) -> dict:
    from api.options_analytics import compute_max_pain, compute_pcr
    from api.options_strategies import _atm_index, _row_at_strike, _sorted_strikes
    from api.routes.market import (
        _fetch_full_option_chain,
        _iv_rank,
    )
    from api.routes.mtf import compute_mtf

    chain = await _fetch_full_option_chain(redis, symbol)
    if not chain.get("ready"):
        return {"ready": False, "reason": chain.get("reason", "Option chain unavailable.")}

    rows = chain["rows"]
    spot = float(chain.get("spot") or 0)
    if spot <= 0:
        return {"ready": False, "reason": "No live spot price in the option chain."}

    built = build_all_strategies(rows, spot)
    if not any(r.get("ready") for r in built.values()):
        return {
            "ready": False,
            "reason": "Chain too thin for every catalog strategy (need strikes 4 steps either side of ATM).",
            "strategies_attempted": {k: v.get("reason") for k, v in built.items()},
        }

    pcr_result = compute_pcr(rows)
    pcr_sentiment = pcr_result.get("sentiment") if pcr_result else None
    max_pain_result = compute_max_pain(rows)
    max_pain_strike = max_pain_result.get("max_pain_strike") if max_pain_result else None

    # IV Rank against the ATM call -- one representative contract, same
    # "refuses to guess until 60+ observations exist" gate _iv_rank already
    # enforces for every other caller of it in this codebase.
    strikes = _sorted_strikes(rows)
    atm_idx = _atm_index(strikes, spot)
    iv_rank = None
    if atm_idx is not None:
        atm_row = _row_at_strike(rows, strikes[atm_idx])
        atm_call = (atm_row or {}).get("call_options") or {}
        contract_key = atm_call.get("instrument_key", "")
        current_iv = float((atm_call.get("option_greeks") or {}).get("iv") or 0)
        iv_rank, _ = await _iv_rank(redis, contract_key, current_iv)

    mtf_result = await compute_mtf(redis, symbol, store=False)
    trade_bias = mtf_result.get("trade_bias", "HOLD")

    ranked = rank_strategies(built, trade_bias, iv_rank, pcr_sentiment, spot, max_pain_strike)

    return {
        "ready": True,
        "symbol": symbol,
        "spot": spot,
        "expiry": chain.get("expiry"),
        "trade_bias": trade_bias,
        "mtf_alignment": mtf_result.get("alignment"),
        "iv_rank": iv_rank,
        "pcr_sentiment": pcr_sentiment,
        "max_pain_strike": max_pain_strike,
        "ranked_strategies": ranked,
    }


@routes.get("/api/options/strategy-selector")
async def strategy_selector(request):
    """Ranked multi-leg strategy shortlist for one symbol. Advisory
    evidence only -- see module docstring. Query param: symbol (defaults
    to the same _default_symbol() fallback every other options endpoint
    in this codebase uses)."""
    from api.routes.market import _default_symbol

    redis = request.app["redis"]
    symbol = request.query.get("symbol", "").upper().strip()
    if not symbol:
        symbol = await _default_symbol(redis)
    if not symbol:
        return web.json_response(
            {"ready": False, "reason": "No symbol provided and no default symbol available."}
        )

    result = await compute_strategy_selection(redis, symbol)
    return web.json_response(result)
