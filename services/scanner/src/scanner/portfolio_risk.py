"""EBIE EB-11 (increment 1) -- portfolio-level guardrails, informational
only (shadow/advisory), per docs/EBIE-BLUEPRINT.md Section 26 ("Track:
total open risk, total directional delta, sector concentration,
correlated positions... Create portfolio_fit_score... GOOD SETUP --
REJECTED DUE TO PORTFOLIO CORRELATION").

Per docs/EBIE-IMPLEMENTATION-ANSWERS.md Q2.4's authorized governance,
explicit and load-bearing: "While Infusion remains paper-only:
portfolio risk is informational/advisory, it does NOT suppress the
underlying setup from research, it records both raw setup quality and
portfolio-adjusted actionability." This module NEVER blocks a signal --
portfolio_fit_score/label are pure evidence, exactly like every other
EBIE sub_score this session has shipped. "Before any future live-
capital mode, portfolio hard blocking becomes mandatory" -- not yet.

The "current open portfolio" for a paper-trading system with no
executed positions is, honestly, the set of currently ACTIVE signals
(services/scanner/src/scanner/engine.py's own KEY_SIGNAL_ACTIVE zset) --
signals a user could reasonably act on right now. This is a real,
defensible proxy, not a fabrication: every metric below is computed
from data already genuinely tracked (sector_id, direction, strategy_id,
and each signal's own already-computed position_sizing.risk_amount),
no new position-tracking infrastructure invented.

Disclosed scope boundaries, not built here (need data this system does
not have at signal time): index beta (no per-stock beta coefficients
computed anywhere), expiry concentration (every current signal trades
the same near-month contract -- no multi-expiry selection exists yet,
so this metric would be a trivial always-100% and isn't meaningfully
informative), option gamma exposure (needs a live per-position Greeks
fetch scanner has no access to, the same recurring architectural
constraint already disclosed for EB-8's option-liquidity gates and
EB-9's IV-spike indicator). Daily loss budget and consecutive-losses
tracking need real archived Postgres history scanner doesn't have
direct access to -- deferred to EB-11 increment 2 (an api-side sweep
cached to Redis, same pattern as VIX multiplier/Kelly sizing).
"""

from __future__ import annotations

from typing import Any

# Thresholds are v1 judgment calls, not yet calibrated against real
# outcome data (same disclosed-heuristic status as every EBIE
# threshold this session has shipped without a validated backtest --
# EB-9's WEAK_VOLUME_THRESHOLD/WIDE_SPREAD_BPS, EB-8's SECTOR_SUPPORTIVE/
# CONTRARY bands, etc.).
SECTOR_CONCENTRATION_WARN_PCT = 40.0
SECTOR_CONCENTRATION_HIGH_PCT = 60.0
STRATEGY_CONCENTRATION_WARN_PCT = 70.0
CORRELATED_COUNT_WARN = (
    2  # this many already-active same-sector, same-direction signals is worth flagging
)


def compute_portfolio_fit(
    *,
    candidate_symbol: str,
    candidate_sector: str,
    candidate_direction: str,  # "bullish" | "bearish"
    candidate_strategy_id: str,
    candidate_risk_amount: float,
    active_portfolio: list[dict[str, Any]],
) -> dict[str, Any]:
    """active_portfolio: one dict per currently-active signal, each
    {symbol, strategy_id, sector_id, direction, risk_amount}. Computes
    what the portfolio would look like WITH this candidate added,
    entirely as evidence -- never a suppression signal.
    """
    # Exclude the candidate's own symbol+strategy from the "before" set
    # if it's somehow already present (shouldn't happen -- the
    # duplicate gate blocks this -- but defensive, not assumed).
    others = [
        p
        for p in active_portfolio
        if not (
            p.get("symbol") == candidate_symbol and p.get("strategy_id") == candidate_strategy_id
        )
    ]

    total_open_risk_before = sum(float(p.get("risk_amount") or 0.0) for p in others)
    total_open_risk_after = total_open_risk_before + candidate_risk_amount

    directional_delta_before = sum(
        (
            float(p.get("risk_amount") or 0.0)
            if p.get("direction") == "bullish"
            else -float(p.get("risk_amount") or 0.0)
        )
        for p in others
    )
    candidate_signed_risk = (
        candidate_risk_amount if candidate_direction == "bullish" else -candidate_risk_amount
    )
    directional_delta_after = directional_delta_before + candidate_signed_risk

    sector_risk_before = sum(
        float(p.get("risk_amount") or 0.0) for p in others if p.get("sector_id") == candidate_sector
    )
    sector_risk_after = sector_risk_before + candidate_risk_amount
    sector_concentration_pct = (
        round(100 * sector_risk_after / total_open_risk_after, 1)
        if total_open_risk_after > 0
        else None
    )

    strategy_risk_after = (
        sum(
            float(p.get("risk_amount") or 0.0)
            for p in others
            if p.get("strategy_id") == candidate_strategy_id
        )
        + candidate_risk_amount
    )
    strategy_concentration_pct = (
        round(100 * strategy_risk_after / total_open_risk_after, 1)
        if total_open_risk_after > 0
        else None
    )

    correlated = [
        p["symbol"]
        for p in others
        if p.get("sector_id") == candidate_sector and p.get("direction") == candidate_direction
    ]
    same_symbol_other_strategy = [
        p["strategy_id"] for p in others if p.get("symbol") == candidate_symbol
    ]

    score = 100.0
    reasons: list[str] = []

    # Concentration is only a meaningful concept relative to OTHER
    # already-active capital -- a lone candidate with nothing else
    # active is trivially "100% of open risk" by definition (there's
    # nothing else for it to be concentrated against), which is a real
    # bug this exact case caught in this module's own unit tests before
    # any live deploy: penalizing it would flag every single first-of-
    # the-day signal as "highly concentrated," which is false. Gate all
    # concentration/correlation penalties on len(others) > 0.
    has_other_active = len(others) > 0

    if (
        has_other_active
        and sector_concentration_pct is not None
        and sector_concentration_pct >= SECTOR_CONCENTRATION_HIGH_PCT
    ):
        score -= 35
        reasons.append(f"sector concentration would reach {sector_concentration_pct}% of open risk")
    elif (
        has_other_active
        and sector_concentration_pct is not None
        and sector_concentration_pct >= SECTOR_CONCENTRATION_WARN_PCT
    ):
        score -= 15
        reasons.append(f"sector concentration would reach {sector_concentration_pct}% of open risk")

    if len(correlated) >= CORRELATED_COUNT_WARN:
        score -= 25
        reasons.append(
            f"{len(correlated)} already-active {candidate_direction} signal(s) in the same sector "
            f"({', '.join(correlated)}) -- correlated, not independent evidence"
        )
    elif len(correlated) == 1:
        score -= 10
        reasons.append(
            f"1 already-active {candidate_direction} signal in the same sector ({correlated[0]})"
        )

    if (
        has_other_active
        and strategy_concentration_pct is not None
        and strategy_concentration_pct >= STRATEGY_CONCENTRATION_WARN_PCT
    ):
        score -= 10
        reasons.append(
            f"strategy concentration would reach {strategy_concentration_pct}% of open risk"
        )

    if same_symbol_other_strategy:
        score -= 15
        reasons.append(
            f"{candidate_symbol} already has an active signal via {same_symbol_other_strategy[0]}"
        )

    score = max(0.0, min(100.0, score))
    if score >= 85:
        label = "GOOD FIT"
    elif score >= 60:
        label = "PORTFOLIO CONCENTRATION FLAGGED"
    else:
        label = "HIGHLY CONCENTRATED -- PORTFOLIO ADVISORY"

    return {
        "portfolio_fit_score": round(score, 1),
        "portfolio_fit_label": label,
        "portfolio_fit_reasons": reasons,
        "total_open_risk_before": round(total_open_risk_before, 2),
        "total_open_risk_after": round(total_open_risk_after, 2),
        "directional_delta_before": round(directional_delta_before, 2),
        "directional_delta_after": round(directional_delta_after, 2),
        "sector_concentration_pct": sector_concentration_pct,
        "strategy_concentration_pct": strategy_concentration_pct,
        "correlated_symbols": correlated,
        "same_symbol_active_via": same_symbol_other_strategy,
        "active_signal_count": len(others),
        # Per Q2.4: never a hard block -- advisory only, the raw setup
        # (verdict/score/grade computed elsewhere) is completely
        # unaffected by this field.
        "advisory_only": True,
    }
