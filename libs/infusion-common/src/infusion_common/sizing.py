"""Risk-based position sizing — shared between the api service (execution
staging, with real option premium/delta) and the scanner service (a lighter
underlying-risk estimate attached to the signal itself at fire time).

Matches simple_structure_pivot_ma_plan_v6.pine's Position Sizing (1% Rule):
`lots = floor(risk_amount / (per_unit_risk * lot_size))`, capped at
`max_lots`.
"""

from __future__ import annotations

import math


def compute_position_size(
    risk_amount: float, per_unit_risk: float, lot_size: int, max_lots: int | None = None
) -> dict:
    """Returns {"quantity": int, "lot_count": int}.

    Computed via qty-then-lots (double floor) rather than a single division —
    never overestimates size, matching the conservative bias the rest of the
    risk system uses elsewhere (see api/routes/risk.py, api/routes/safety.py).
    """
    if risk_amount <= 0 or per_unit_risk <= 0 or lot_size <= 0:
        return {"quantity": 0, "lot_count": 0}
    qty_by_risk = math.floor(risk_amount / per_unit_risk)
    lot_count = math.floor(qty_by_risk / lot_size)
    if max_lots is not None:
        lot_count = min(lot_count, max_lots)
    lot_count = max(0, lot_count)
    return {"quantity": lot_count * lot_size, "lot_count": lot_count}
