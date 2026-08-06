"""Microstructure features — spread, order imbalance."""

from feature_engine.state import SymbolState


def get_spread_bps(state: SymbolState) -> float:
    """Bid-ask spread in basis points."""
    if state.best_bid <= 0 or state.best_ask <= 0:
        return 0.0
    mid = (state.best_bid + state.best_ask) / 2
    if mid == 0:
        return 0.0
    return (state.best_ask - state.best_bid) / mid * 10_000


def get_order_imbalance(state: SymbolState) -> float:
    """(buy_qty - sell_qty) / (buy_qty + sell_qty). Range: -1 to +1."""
    total = state.total_buy_qty + state.total_sell_qty
    if total == 0:
        return 0.0
    return (state.total_buy_qty - state.total_sell_qty) / total
