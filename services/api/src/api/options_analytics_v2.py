"""Dynamic option-chain analytics — EBIE EB-5.

Per docs/EBIE-IMPLEMENTATION-ANSWERS.md Q3.4: "Upgrade the existing
options analytics boundary, shadow the dynamic version, then remove
static reads from verdict voting... New verdict inputs: ΔOI, OI
velocity, wall strength, wall weakening, wall migration, near-ATM
weighted PCR, PCR velocity."

This is explicitly a SHADOW addition alongside api/options_analytics.py
(the static PCR/Max Pain/OI-S-R module), not a replacement — per the
authorization's own migration plan: "Keep for display/research: raw
PCR, Max Pain, absolute OI, largest OI strikes. Remove from primary
verdict logic [only after] verified cutover." Nothing here removes or
overrides the existing static module; this only adds the genuinely new,
time-aware reads that module structurally cannot provide (it has no
history — every call is a fresh, stateless computation over one chain
snapshot).

Everything below operates on the SAME full-chain row shape
options_analytics.py already documents (one row per strike, `strike_
price`, `call_options.market_data.oi`, `put_options.market_data.oi`).
Wall dynamics need a previous-sweep snapshot to compare against —
that's the one new stateful piece, owned by the sweep loop (options_
dynamics_queue.py), not this module (kept pure/stateless, same
discipline as options_analytics.py itself).
"""

from __future__ import annotations

from typing import Any

WALL_CHANGE_THRESHOLD = 0.05  # +/-5% OI change to call a strike strengthening/weakening
TOP_N_STRIKES = 3  # track this many strikes per side for wall dynamics


def _leg_oi(row: dict[str, Any], leg_name: str) -> float:
    leg = row.get(leg_name) or {}
    market = leg.get("market_data") or {}
    try:
        return float(market.get("oi") or 0)
    except (TypeError, ValueError):
        return 0.0


def compute_weighted_pcr(rows: list[dict[str, Any]], spot: float) -> dict[str, Any] | None:
    """PCR weighted toward near-ATM strikes, per docs/EBIE-BLUEPRINT.md
    Section 4.8.5: "weighted_PCR... the direction and rate of change
    matter more than a fixed threshold." Weight = 1 / (1 + distance_pct)
    -- a smooth decay with strike distance from spot rather than a hard
    ATM-window cutoff, so a strike just outside an arbitrary window
    isn't discarded entirely.
    """
    if spot <= 0:
        return None
    weighted_call = 0.0
    weighted_put = 0.0
    for row in rows:
        strike = float(row.get("strike_price") or 0)
        if strike <= 0:
            continue
        distance_pct = abs(strike - spot) / spot
        weight = 1.0 / (
            1.0 + distance_pct * 20.0
        )  # ~50% weight at 5% OTM, tuned for typical NSE strike spacing
        weighted_call += _leg_oi(row, "call_options") * weight
        weighted_put += _leg_oi(row, "put_options") * weight

    if weighted_call <= 0:
        return None
    weighted_pcr = weighted_put / weighted_call
    return {
        "weighted_pcr": round(weighted_pcr, 3),
        "weighted_call_oi": round(weighted_call, 1),
        "weighted_put_oi": round(weighted_put, 1),
    }


def compute_pcr_velocity(current_pcr: float | None, prev_pcr: float | None) -> float | None:
    """Simple delta -- direction and rate of change, not a snapshot."""
    if current_pcr is None or prev_pcr is None:
        return None
    return round(current_pcr - prev_pcr, 3)


def compute_pcr_acceleration(
    current_velocity: float | None, prev_velocity: float | None
) -> float | None:
    """EBIE EB-15 Phase 5 item 8's own "OI velocity, OI ACCELERATION"
    requirement -- the second derivative, same shape as
    compute_pcr_velocity() one level up (a delta-of-the-delta, not a
    fresh recomputation). None (not 0.0) whenever either input is
    genuinely unavailable -- needs at least 3 real sweeps of history
    (2 velocities) before this can mean anything, so it stays honestly
    absent for the first sweep after a symbol enters coverage and the
    one right after that, not a fabricated flat 0.
    """
    if current_velocity is None or prev_velocity is None:
        return None
    return round(current_velocity - prev_velocity, 3)


def _classify_strike_state(current_oi: float, prev_oi: float | None) -> str:
    """strengthening / weakening / stable / new / abandoned -- per
    docs/EBIE-BLUEPRINT.md Section 4.8.3's wall-state vocabulary
    ("strengthening / weakening / migrating / being consumed /
    abandoned"). Migration itself is a chain-level read (does the TOP
    strike change), handled in compute_wall_dynamics below, not here.
    """
    if prev_oi is None:
        return "new" if current_oi > 0 else "none"
    if current_oi <= 0:
        return "abandoned" if prev_oi > 0 else "none"
    if prev_oi <= 0:
        return "new"
    change_pct = (current_oi - prev_oi) / prev_oi
    if change_pct >= WALL_CHANGE_THRESHOLD:
        return "strengthening"
    if change_pct <= -WALL_CHANGE_THRESHOLD:
        return "weakening"
    return "stable"


def compute_wall_dynamics(
    rows: list[dict[str, Any]], prev_snapshot: dict[str, Any] | None
) -> dict[str, Any]:
    """Top call-OI and put-OI strikes this sweep, each classified against
    the previous sweep's OI at that SAME strike, plus a migration flag
    if the #1 strike itself changed since last sweep -- the real upgrade
    over the static module's single-snapshot "highest OI = wall" read.

    prev_snapshot: {"top_call_strike": float, "top_call_oi": float,
    "strikes": {strike: {"call_oi": ..., "put_oi": ...}}} -- the exact
    shape this function itself returns, so the sweep loop can pass one
    call's output straight into the next call's prev_snapshot with no
    reshaping.
    """
    by_strike: dict[float, dict[str, float]] = {}
    for row in rows:
        strike = float(row.get("strike_price") or 0)
        if strike <= 0:
            continue
        by_strike[strike] = {
            "call_oi": _leg_oi(row, "call_options"),
            "put_oi": _leg_oi(row, "put_options"),
        }
    if not by_strike:
        return {"available": False, "reason": "No strikes in chain."}

    prev_strikes = (prev_snapshot or {}).get("strikes") or {}

    def _top_n(leg: str) -> list[dict[str, Any]]:
        ranked = sorted(by_strike.items(), key=lambda kv: kv[1][leg], reverse=True)[:TOP_N_STRIKES]
        out: list[dict[str, Any]] = []
        for strike, legs in ranked:
            if legs[leg] <= 0:
                continue
            prev_oi = (prev_strikes.get(str(strike)) or prev_strikes.get(strike) or {}).get(leg)
            out.append(
                {
                    "strike": strike,
                    "oi": round(legs[leg], 1),
                    "state": _classify_strike_state(legs[leg], prev_oi),
                }
            )
        return out

    call_wall = _top_n("call_oi")
    put_wall = _top_n("put_oi")

    prev_top_call = (prev_snapshot or {}).get("top_call_strike")
    prev_top_put = (prev_snapshot or {}).get("top_put_strike")
    call_migrated = bool(
        call_wall and prev_top_call is not None and float(prev_top_call) != call_wall[0]["strike"]
    )
    put_migrated = bool(
        put_wall and prev_top_put is not None and float(prev_top_put) != put_wall[0]["strike"]
    )

    return {
        "available": True,
        "call_wall": call_wall,
        "put_wall": put_wall,
        "call_wall_migrated": call_migrated,
        "put_wall_migrated": put_migrated,
        # Snapshot shape for the NEXT sweep's prev_snapshot -- string keys
        # since this round-trips through JSON/Redis.
        "top_call_strike": call_wall[0]["strike"] if call_wall else None,
        "top_put_strike": put_wall[0]["strike"] if put_wall else None,
        "strikes": {str(k): v for k, v in by_strike.items()},
    }
