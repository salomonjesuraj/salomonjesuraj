"""Supply / demand zones — imbalance-candle methodology, matching
`simple_structure_pivot_ma_plan_v6.pine` (credited there to
razorbladekisses/Tradingview-Indicators):

A candle whose body is >= `multiplier` x the PRIOR opposite-colour candle's
body carves a zone from that prior candle's open to its extreme (high for a
bearish impulse -> supply/resistance, low for a bullish impulse ->
demand/support). The zone is self-cleaning: it disappears once price closes
back through it.
"""

from __future__ import annotations

ZONE_SIZE_MULTIPLIER = 1.8   # Pine's zoneSizeMultiplier default


def update_zones(state, bar_start_ms: int, multiplier: float = ZONE_SIZE_MULTIPLIER) -> None:
    """Advance supply/demand zone state by one completed bar. Reads the last
    two bars from `state.recent_1m_bars`; no-ops until at least 2 exist."""
    items = list(state.recent_1m_bars)
    if len(items) < 2:
        return

    cur, prev = items[-1], items[-2]
    o, c = float(cur["o"]), float(cur["c"])
    po, ph, pl, pc = float(prev["o"]), float(prev["h"]), float(prev["l"]), float(prev["c"])

    body = abs(c - o)
    prev_body = abs(pc - po)
    prior_bull_base = pc > po
    prior_bear_base = pc < po
    impulse_is_bear = c < o and body >= prev_body * multiplier
    impulse_is_bull = c > o and body >= prev_body * multiplier

    if prior_bull_base and impulse_is_bear:
        state.supply_zone = (ph, po, bar_start_ms)  # (top=prior high, bottom=prior open)
    if state.supply_zone is not None and c > state.supply_zone[0]:
        state.supply_zone = None

    if prior_bear_base and impulse_is_bull:
        state.demand_zone = (po, pl, bar_start_ms)  # (top=prior open, bottom=prior low)
    if state.demand_zone is not None and c < state.demand_zone[1]:
        state.demand_zone = None


def zone_snapshot(state) -> dict:
    """Compact dict for FeatureVectorV1.ml_features."""
    supply = state.supply_zone
    demand = state.demand_zone
    return {
        "supply_zone_top": supply[0] if supply else None,
        "supply_zone_bottom": supply[1] if supply else None,
        "demand_zone_top": demand[0] if demand else None,
        "demand_zone_bottom": demand[1] if demand else None,
    }
