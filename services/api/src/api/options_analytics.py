"""Options-chain analytics — Put-Call Ratio, OI-based support/resistance,
and Max Pain. All three sourced directly from Upstox's own learning-center
content (the actual broker Infusion integrates with) rather than generic
textbook convention:

  - PCR: upstox.com/learning-center/futures-and-options/
    what-is-the-put-call-ratio-pcr-in-the-share-market/article-1590/
  - OI-based support/resistance: upstox.com/learning-center/futures-and-
    options/a-beginner-s-guide-to-option-chain-analysis-in-the-stock-
    market/article-1543/ and .../best-ways-to-perform-option-chain-
    analysis-.../article-1018/
  - Max Pain: upstox.com/market-talk/all-about-max-pain/ and
    upstox.com/uplearn/structure-courses/basics-of-options/max-pain/
    (same publisher as the learning center; methodology confirmed
    consistent across both).

These operate on the FULL option chain (every strike, both call and put
OI), not the near-ATM-only subset api/routes/market.py's
_upstox_option_context() filters down to for its own per-signal scoring —
that function is untouched by this module; see _fetch_full_option_chain()
in market.py for the shared (new) full-chain fetch path.

Expected row shape (Upstox option-chain API, one row per strike):
    {"strike_price": float, "call_options": {"market_data": {"oi": float, ...}},
     "put_options": {"market_data": {"oi": float, ...}}}
"""

from __future__ import annotations

from typing import Any

PCR_BALANCED_BAND = 0.05  # +/- around 1.0 treated as "balanced" -- see classify_pcr()


def _leg_oi(row: dict[str, Any], leg_name: str) -> float:
    leg = row.get(leg_name) or {}
    market = leg.get("market_data") or {}
    try:
        return float(market.get("oi") or 0)
    except (TypeError, ValueError):
        return 0.0


def classify_pcr(pcr: float) -> str:
    """5-tier PCR sentiment classification, per Upstox's own stated
    thresholds (article-1590): <0.7 strong bullish, 0.7-1.0 neutral-to-
    mildly-bullish, ~1.0 balanced, 1.0-1.3 mild bearish, >1.3 strong
    bearish. Upstox's own two articles give slightly overlapping phrasing
    right around 1.0 (one says "0.7-1.0 = neutral-to-bullish", another
    separately calls "~1.0" balanced) -- resolved here with an explicit
    +/-0.05 "balanced" band centered on 1.0 so the table is monotonic;
    this band width is Infusion's own calibration, not Upstox-stated.
    """
    if pcr < 0.7:
        return "strong_bullish"
    if pcr < 1.0 - PCR_BALANCED_BAND:
        return "neutral_bullish"
    if pcr <= 1.0 + PCR_BALANCED_BAND:
        return "balanced"
    if pcr <= 1.3:
        return "mild_bearish"
    return "strong_bearish"


def compute_pcr(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Put-Call Ratio = Total Put OI / Total Call OI, summed across the
    full chain. Returns None when there's no call OI to divide by (thin/
    pre-market chain) rather than raising or returning a misleading 0."""
    total_call_oi = 0.0
    total_put_oi = 0.0
    for row in rows:
        total_call_oi += _leg_oi(row, "call_options")
        total_put_oi += _leg_oi(row, "put_options")

    if total_call_oi <= 0:
        return None

    pcr = total_put_oi / total_call_oi
    return {
        "pcr": round(pcr, 3),
        "total_call_oi": int(total_call_oi),
        "total_put_oi": int(total_put_oi),
        "sentiment": classify_pcr(pcr),
    }


def compute_oi_support_resistance(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Strike with the highest Call OI = resistance; strike with the
    highest Put OI = support (Upstox's own stated rule, article-1543/1018).
    """
    best_call: tuple[float, float] | None = None  # (oi, strike)
    best_put: tuple[float, float] | None = None

    for row in rows:
        strike = float(row.get("strike_price") or 0)
        if strike <= 0:
            continue
        call_oi = _leg_oi(row, "call_options")
        put_oi = _leg_oi(row, "put_options")
        if call_oi > 0 and (best_call is None or call_oi > best_call[0]):
            best_call = (call_oi, strike)
        if put_oi > 0 and (best_put is None or put_oi > best_put[0]):
            best_put = (put_oi, strike)

    if best_call is None and best_put is None:
        return None

    return {
        "resistance": best_call[1] if best_call else None,
        "resistance_oi": int(best_call[0]) if best_call else None,
        "support": best_put[1] if best_put else None,
        "support_oi": int(best_put[0]) if best_put else None,
    }


def compute_max_pain(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Max Pain: the strike at which total payout to option holders (loss
    to option writers) is minimized, evaluated across every strike in the
    chain as a candidate expiry-close level:

        for each candidate strike C:
            pain(C) = sum(call_oi[K] * max(0, C - K) for every strike K)
                    + sum(put_oi[K]  * max(0, K - C) for every strike K)
        max_pain_strike = argmin(pain(C) over all candidates C)

    O(n^2) over the strike count, which is fine for a single expiry's
    chain (tens of strikes, not thousands).
    """
    chain: list[tuple[float, float, float]] = []  # (strike, call_oi, put_oi)
    for row in rows:
        strike = float(row.get("strike_price") or 0)
        if strike <= 0:
            continue
        chain.append((strike, _leg_oi(row, "call_options"), _leg_oi(row, "put_options")))

    if not chain:
        return None

    candidates = sorted({strike for strike, _, _ in chain})
    best_strike: float | None = None
    best_pain: float | None = None

    for candidate in candidates:
        pain = 0.0
        for strike, call_oi, put_oi in chain:
            if candidate > strike:
                pain += call_oi * (candidate - strike)
            elif candidate < strike:
                pain += put_oi * (strike - candidate)
        if best_pain is None or pain < best_pain:
            best_pain = pain
            best_strike = candidate

    return {
        "max_pain_strike": best_strike,
        "min_total_payout": round(best_pain, 2) if best_pain is not None else None,
        "candidates_evaluated": len(candidates),
    }
