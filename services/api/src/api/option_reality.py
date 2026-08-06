"""Reality gates for directional stock-option buying."""

from __future__ import annotations


def derive_option_sl(
    entry_underlying: float,
    sl_underlying: float,
    entry_premium_ask: float,
    delta: float,
) -> dict:
    """Derive option stop from underlying risk and option delta.

    This replaces the old fixed-percentage premium stop.  For stock options,
    a premium stop that ignores the underlying SL distance is not reliable.
    """
    underlying_risk = abs(float(entry_underlying or 0.0) - float(sl_underlying or 0.0))
    entry_premium_ask = max(float(entry_premium_ask or 0.0), 0.0)
    delta_used = float(delta or 0.0)
    premium_risk = underlying_risk * abs(delta_used)
    premium_risk_pct = premium_risk / max(entry_premium_ask, 0.01)
    option_sl_price = max(0.05, entry_premium_ask - premium_risk) if entry_premium_ask else 0.0

    blockers: list[str] = []
    hard_blockers: list[str] = []
    if entry_premium_ask <= 0:
        hard_blockers.append("missing option ask for delta SL")
    if abs(delta_used) <= 0:
        hard_blockers.append("missing option delta for premium SL")
    if premium_risk_pct < 0.12 and entry_premium_ask > 0 and abs(delta_used) > 0:
        hard_blockers.append("SL inside spread - noise stop")
    if premium_risk_pct > 0.45:
        hard_blockers.append("strike too far OTM for this SL distance")

    return {
        "underlying_risk": round(underlying_risk, 2),
        "delta_used": round(delta_used, 4),
        "premium_risk": round(premium_risk, 2),
        "premium_risk_pct": round(premium_risk_pct * 100, 2),
        "option_sl_price": round(option_sl_price, 2),
        "blockers": blockers,
        "hard_blockers": hard_blockers,
    }


def breakeven_gate(
    bias: str,
    spot: float,
    strike: float,
    entry_ask: float,
    spread_per_unit: float,
    est_costs_per_unit: float,
    daily_atr_pct: float,
    expected_holding_days: float,
    underlying_t1: float,
) -> dict:
    """Check whether expected underlying move can pay for the option."""
    bias = str(bias or "").upper()
    spot = max(float(spot or 0.0), 0.0)
    strike = float(strike or 0.0)
    entry_ask = max(float(entry_ask or 0.0), 0.0)
    spread_per_unit = max(float(spread_per_unit or 0.0), 0.0)
    est_costs_per_unit = max(float(est_costs_per_unit or 0.0), 0.0)
    daily_atr_pct = max(float(daily_atr_pct or 0.0), 0.0)
    expected_holding_days = max(float(expected_holding_days or 0.0), 0.0)
    underlying_t1 = float(underlying_t1 or 0.0)

    if bias == "CE":
        breakeven = strike + entry_ask + spread_per_unit + est_costs_per_unit
        target_clears = underlying_t1 > breakeven
    elif bias == "PE":
        breakeven = strike - entry_ask - spread_per_unit - est_costs_per_unit
        target_clears = underlying_t1 < breakeven
    else:
        breakeven = 0.0
        target_clears = False

    required_move_pct = abs(breakeven - spot) / max(spot, 0.01) * 100 if spot and breakeven else 999.0
    expected_move_pct = daily_atr_pct * max(expected_holding_days, 1.0)
    blockers: list[str] = []
    hard_blockers: list[str] = []
    status = "PASS"

    if not target_clears:
        hard_blockers.append("target does not pay for premium")
    if expected_move_pct <= 0:
        hard_blockers.append("ATR expected move unavailable")
    elif required_move_pct > 1.0 * expected_move_pct:
        hard_blockers.append("breakeven too far vs ATR")
    elif required_move_pct > 0.6 * expected_move_pct:
        blockers.append("breakeven elevated vs ATR")

    if hard_blockers:
        status = "AVOID_CONTRACT"
    elif blockers:
        status = "WAIT_CONTRACT"

    return {
        "breakeven_underlying": round(breakeven, 2),
        "required_move_pct": round(required_move_pct, 3),
        "expected_move_pct": round(expected_move_pct, 3),
        "target_clears_breakeven": target_clears,
        "blockers": blockers,
        "hard_blockers": hard_blockers,
        "status": status,
    }


def delta_band_gate(delta: float) -> dict:
    delta = float(delta or 0.0)
    ok = 0.35 <= abs(delta) <= 0.60
    return {
        "delta_band_pass": ok,
        "reason": "" if ok else "delta outside directional band",
    }


def iv_rank_gate(iv_rank: float | None) -> dict:
    if iv_rank is None:
        return {
            "iv_rank_pass": None,
            "status": "CHAIN_PENDING",
            "reason": "need 60-session IV history",
        }
    iv_rank = float(iv_rank)
    if iv_rank > 80:
        return {
            "iv_rank_pass": False,
            "status": "AVOID_CONTRACT",
            "reason": "IV rank extreme - buying into crush",
        }
    if iv_rank >= 60:
        return {
            "iv_rank_pass": False,
            "status": "WAIT_CONTRACT",
            "reason": "elevated IV",
        }
    return {"iv_rank_pass": True, "status": "PASS", "reason": ""}
