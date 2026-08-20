"""Microstructure features — spread, order imbalance, and (EBIE EB-6)
real multi-level book depth.

Real naming/wiring quirk found while verifying this (pre-existing, not
introduced here -- flagged separately, not fixed inline): despite its
name, get_order_imbalance() below does NOT read Upstox's exchange-wide
tbq/tsq aggregate fields. RawTickV1 computes those correctly from the
codec, but normalizer/transformer.py's NormalizedTickV1 never carries
them through -- engine.py instead feeds state.total_buy_qty/
total_sell_qty from best_bid_qty/best_ask_qty (level-1 quantity only),
so get_order_imbalance() is actually already a level-1-only imbalance
read today, not the exchange-wide one its field names imply.

compute_book_imbalance below is still a genuine upgrade over it: a
WEIGHTED imbalance across all 5 real depth levels (see upstox_codec.py's
EB-6 fix -- this data was previously discarded after level 1), not just
level 1 alone.
"""

from feature_engine.state import SymbolState

BOOK_IMBALANCE_EMA_PERIOD = 5   # short -- this is meant to track FAST book pressure shifts
DEPTH_LEVEL_WEIGHTS = [1.0, 0.5, 0.333, 0.25, 0.2]   # harmonic decay, level 1 counts most


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


def compute_book_imbalance(depth_levels: list[dict]) -> float | None:
    """Weighted bid/ask quantity imbalance across up to 5 real depth
    levels, per docs/EBIE-BLUEPRINT.md Section 4.6.1 -- weight decays
    with level depth (level 1 counts most, level 5 least) rather than
    treating every level as equally informative. Range -1 (pure ask-side
    pressure) to +1 (pure bid-side pressure). None (never a fabricated
    0.0) when there's no real depth to compute from.
    """
    if not depth_levels:
        return None
    weighted_bid = 0.0
    weighted_ask = 0.0
    for i, level in enumerate(depth_levels[:5]):
        weight = DEPTH_LEVEL_WEIGHTS[i]
        weighted_bid += float(level.get("bidQ") or 0) * weight
        weighted_ask += float(level.get("askQ") or 0) * weight
    total = weighted_bid + weighted_ask
    if total <= 0:
        return None
    return (weighted_bid - weighted_ask) / total


def compute_depth_concentration(depth_levels: list[dict]) -> float | None:
    """Fraction of total visible bid+ask quantity sitting at level 1 --
    high concentration = thin/fragile book (a modest trade could move
    price meaningfully past the best level); low concentration = a real
    depth cushion behind the touch. None when there's no depth.
    """
    if not depth_levels:
        return None
    total_qty = sum(
        float(lv.get("bidQ") or 0) + float(lv.get("askQ") or 0) for lv in depth_levels[:5]
    )
    if total_qty <= 0:
        return None
    l1 = depth_levels[0]
    l1_qty = float(l1.get("bidQ") or 0) + float(l1.get("askQ") or 0)
    return l1_qty / total_qty


def update_book_imbalance_ema(state: SymbolState, depth_levels: list[dict]) -> None:
    """Advance the book-imbalance EMA -- "imbalance persistence" per the
    blueprint's own Section 4.6.1 wording. Call once per tick (unlike
    the completed-1m-bar-only indicators elsewhere in this file, book
    pressure is meaningful tick-by-tick, not just on bar close).
    """
    imbalance = compute_book_imbalance(depth_levels)
    if imbalance is None:
        return
    k = 2.0 / (BOOK_IMBALANCE_EMA_PERIOD + 1)
    if not state.book_imbalance_ema_initialized:
        state.book_imbalance_ema = imbalance
        state.book_imbalance_ema_initialized = True
    else:
        state.book_imbalance_ema = imbalance * k + state.book_imbalance_ema * (1 - k)


def microstructure_depth_snapshot(state: SymbolState) -> dict:
    """Informational snapshot -- not wired into scoring, matches this
    codebase's "compute it, let feature-ablation earn its way in"
    governance used for every prior EBIE/Phase field."""
    depth_levels = state.latest_depth_levels
    return {
        "book_imbalance": (
            round(compute_book_imbalance(depth_levels), 4) if depth_levels else None
        ),
        "book_imbalance_ema": (
            round(state.book_imbalance_ema, 4) if state.book_imbalance_ema_initialized else None
        ),
        "depth_concentration": (
            round(compute_depth_concentration(depth_levels), 4) if depth_levels else None
        ),
        "depth_levels_count": len(depth_levels),
    }
