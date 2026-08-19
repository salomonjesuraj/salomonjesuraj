"""Accumulation/distribution evidence — EBIE EB-2.

Close-Location Value (CLV): where within a bar's range did it close?
+1 = closed at the high (buying pressure absorbed the bar), -1 = closed
at the low (selling pressure absorbed it). Per docs/EBIE-BLUEPRINT.md
Section 4.3.2, this is a persistent-pressure read a simple volume spike
misses — a symbol can show unremarkable single-bar volume while quietly
closing near its highs bar after bar, which raw volume/RVOL never
surfaces on its own.

RVOL_TOD (blueprint Section 4.3.3's time-of-day-normalized relative
volume) is deliberately NOT rebuilt here — confirmed before writing any
of this module that feature_engine/features/volume.py's existing
get_relative_volume() already IS this exact formula (session cumulative
volume divided by a 20-session average at the SAME minute-of-day, not a
naive full-day average). Genuine reuse, not a duplicate build.
"""

from feature_engine.state import SymbolState

CLV_EMA_PERIOD = 14
# |clv| >= this counts as an upper/lower-quartile close. 0.5 means the
# close sat in the outer half of the bar's range on the buy or sell side
# -- named "quartile" to match the blueprint's own Section 4.3.2 wording
# ("closing-in-upper-quartile frequency"), not a literal 0.25 cutoff.
CLV_QUARTILE_THRESHOLD = 0.5
CLV_READY_MIN_BARS = 3


def compute_clv(high: float, low: float, close: float) -> float:
    """((close-low) - (high-close)) / max(high-low, epsilon). Range -1..+1."""
    rng = max(high - low, 1e-6)
    return ((close - low) - (high - close)) / rng


def update_clv(state: SymbolState, high: float, low: float, close: float, volume: int) -> None:
    """Advance CLV state. Call once per completed 1m bar, same contract as
    the other update_* functions (update_body_ema, update_heiken_ashi, etc).
    """
    clv = compute_clv(high, low, close)

    k = 2.0 / (CLV_EMA_PERIOD + 1)
    if not state.clv_ema_initialized:
        state.clv_ema = clv
        state.clv_ema_initialized = True
    else:
        state.clv_ema = clv * k + state.clv_ema * (1 - k)

    if volume > 0:
        state.clv_vwap_numerator += clv * volume
        state.clv_vwap_denominator += volume

    state.clv_bar_count += 1
    if clv >= CLV_QUARTILE_THRESHOLD:
        state.clv_upper_quartile_count += 1
    elif clv <= -CLV_QUARTILE_THRESHOLD:
        state.clv_lower_quartile_count += 1


def clv_snapshot(state: SymbolState) -> dict:
    """Informational snapshot -- not wired into scoring, matches this
    codebase's "compute it, let feature-ablation earn its way in"
    governance already applied to every Phase 1-13.x field. `_ready`
    stays False (values None, not a misleading 0.0) until enough bars
    have accumulated this session for the quartile rates to mean
    anything -- same "refuses to guess until enough data" discipline as
    the session VWAP SD bands' own VWAP_SD_MIN_BARS floor.
    """
    volume_weighted = (
        state.clv_vwap_numerator / state.clv_vwap_denominator
        if state.clv_vwap_denominator > 0 else None
    )
    ready = state.clv_bar_count >= CLV_READY_MIN_BARS
    return {
        "clv_ema": round(state.clv_ema, 3) if state.clv_ema_initialized else None,
        "clv_volume_weighted": round(volume_weighted, 3) if volume_weighted is not None else None,
        "clv_upper_quartile_rate": (
            round(state.clv_upper_quartile_count / state.clv_bar_count, 3) if ready else None
        ),
        "clv_lower_quartile_rate": (
            round(state.clv_lower_quartile_count / state.clv_bar_count, 3) if ready else None
        ),
        "clv_bar_count": state.clv_bar_count,
        "clv_ready": ready,
    }
