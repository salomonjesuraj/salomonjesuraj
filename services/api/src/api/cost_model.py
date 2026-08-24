"""Central option cost and slippage model for Infusion.

All paper/execution analytics must use this module instead of independently
estimating option P&L.  The model intentionally assumes realistic directional
option buying fills:

- entry at ask
- exit at bid
- no LTP/mid/last-price fills

The default rates are configurable enough for a local paper engine.  They
should be reviewed against the broker's latest charge sheet before live order
automation is enabled.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OptionCostAssumptions:
    brokerage_per_order: float = 20.0
    stt_sell_rate: float = 0.000625
    exchange_txn_rate: float = 0.0003503
    sebi_rate: float = 0.000001
    gst_rate: float = 0.18
    stamp_buy_rate: float = 0.00003


@dataclass(frozen=True)
class OptionTradeCostInput:
    entry_ask: float
    exit_bid: float
    bid_at_entry: float
    quantity: int
    assumptions: OptionCostAssumptions = OptionCostAssumptions()


def compute(trade: OptionTradeCostInput) -> dict[str, Any]:
    """Compute gross/net option P&L with charges and spread crossing.

    A flat premium move, where exit_bid equals entry_ask, must return a
    negative net_pnl because costs are real even when price does not move.
    """
    entry_ask = max(float(trade.entry_ask or 0.0), 0.0)
    exit_bid = max(float(trade.exit_bid or 0.0), 0.0)
    bid_at_entry = max(float(trade.bid_at_entry or 0.0), 0.0)
    quantity = max(int(trade.quantity or 0), 0)

    buy_turnover = entry_ask * quantity
    sell_turnover = exit_bid * quantity
    gross_pnl = (exit_bid - entry_ask) * quantity
    brokerage = trade.assumptions.brokerage_per_order * 2 if quantity > 0 else 0.0
    stt = sell_turnover * trade.assumptions.stt_sell_rate
    exchange = (buy_turnover + sell_turnover) * trade.assumptions.exchange_txn_rate
    sebi = (buy_turnover + sell_turnover) * trade.assumptions.sebi_rate
    gst = (brokerage + exchange) * trade.assumptions.gst_rate
    stamp = buy_turnover * trade.assumptions.stamp_buy_rate
    slippage_cost = max(0.0, entry_ask - bid_at_entry) * quantity
    total_costs = brokerage + stt + exchange + sebi + gst + stamp + slippage_cost
    premium_paid = max(buy_turnover, 1.0)

    return {
        "gross_pnl": round(gross_pnl, 2),
        "brokerage": round(brokerage, 2),
        "stt": round(stt, 2),
        "exchange_charges": round(exchange, 2),
        "sebi_fees": round(sebi, 2),
        "gst": round(gst, 2),
        "stamp_duty": round(stamp, 2),
        "slippage_cost": round(slippage_cost, 2),
        "total_costs": round(total_costs, 2),
        "net_pnl": round(gross_pnl - total_costs, 2),
        "cost_as_pct_of_premium": round(total_costs / premium_paid * 100, 3),
        "fill_policy": "ENTRY_ASK_EXIT_BID",
    }


def estimate_entry_costs_per_unit(
    entry_ask: float, bid_at_entry: float, quantity: int = 1
) -> float:
    """Return per-unit estimated cost for breakeven checks at entry time."""
    q = max(int(quantity or 1), 1)
    flat = compute(
        OptionTradeCostInput(
            entry_ask=float(entry_ask or 0.0),
            exit_bid=float(entry_ask or 0.0),
            bid_at_entry=float(bid_at_entry or 0.0),
            quantity=q,
        )
    )
    return round(float(flat.get("total_costs") or 0.0) / q, 4)
