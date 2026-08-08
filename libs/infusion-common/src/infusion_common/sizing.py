"""Risk-based position sizing — shared between the api service (execution
staging, with real option premium/delta) and the scanner service (a lighter
underlying-risk estimate attached to the signal itself at fire time).

Matches simple_structure_pivot_ma_plan_v6.pine's Position Sizing (1% Rule):
`lots = floor(risk_amount / (per_unit_risk * lot_size))`, capped at
`max_lots`.

Optionally also applies the Turtle-style ATR volatility cap (Covel, Trend
Following, Appendix F): the production Turtle sizing formula takes the
SMALLER of two independent size estimates —
`min(2%*Equity / |entry-stop|, 2%*Equity / (2 * 15-day-ATR))` — rather than
sizing off stop distance alone. The ATR arm exists specifically to catch
the case a stop-distance-only formula can't: an unusually TIGHT stop
implies a large position by the risk-distance math alone, even when the
instrument's actual volatility (ATR) doesn't support trading that large.
Passing `atr` turns this on; omitting it preserves the exact original
behavior for existing callers (see the `atr is not None` branches below —
the returned dict only gains the extra `sizing_method` key when ATR sizing
was actually considered, so this is additive, not a signature break).
"""

from __future__ import annotations

import math

ATR_SIZING_MULTIPLIER = 2.0  # approximates Turtle's "2 x ATR" convention, expressed in price terms


def compute_position_size(
    risk_amount: float,
    per_unit_risk: float,
    lot_size: int,
    max_lots: int | None = None,
    atr: float | None = None,
    atr_multiplier: float = ATR_SIZING_MULTIPLIER,
) -> dict:
    """Returns {"quantity": int, "lot_count": int}, plus "sizing_method"
    ("risk_distance" | "atr_capped") when `atr` is supplied.

    Computed via qty-then-lots (double floor) rather than a single division —
    never overestimates size, matching the conservative bias the rest of the
    risk system uses elsewhere (see api/routes/risk.py, api/routes/safety.py).
    """
    if risk_amount <= 0 or per_unit_risk <= 0 or lot_size <= 0:
        return {"quantity": 0, "lot_count": 0}

    qty_by_risk = math.floor(risk_amount / per_unit_risk)
    method = "risk_distance"

    atr_active = atr is not None and atr > 0
    if atr_active:
        qty_by_atr = math.floor(risk_amount / (atr_multiplier * atr))
        if qty_by_atr < qty_by_risk:
            qty_by_risk = qty_by_atr
            method = "atr_capped"

    lot_count = math.floor(qty_by_risk / lot_size)
    if max_lots is not None:
        lot_count = min(lot_count, max_lots)
    lot_count = max(0, lot_count)

    result = {"quantity": lot_count * lot_size, "lot_count": lot_count}
    if atr_active:
        result["sizing_method"] = method
    return result
