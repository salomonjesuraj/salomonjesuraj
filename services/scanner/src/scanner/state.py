"""Per-symbol mutable state for the scanner engine.

Tracks prior feature values for crossover detection (e.g., VWAP reclaim).
This is scanner-domain state — NOT feature engine state.

Design principles:
  - Bounded: fixed fields, no growing collections
  - Deterministic: same input sequence → same state
  - Lightweight: dataclass, no heavy allocations
"""

from dataclasses import dataclass, field


@dataclass
class ScannerSymbolState:
    """Mutable per-symbol state for crossover and temporal detection."""

    symbol: str

    # Watch-episode ladders (Phase W) -- freezes a strategy's entry/SL/
    # T1-T3 the first time it flags a non-chaseable "watch this" candidate,
    # so re-evaluating the same still-open setup on a later, moved LTP
    # doesn't produce a new, different-looking ladder every cycle (the
    # exact bug reported live: the same GRASIM "Wait for trigger" setup
    # re-alerting 3x today with 3 different price ladders as price
    # drifted). Keyed "{strategy_id}:{signal_type}", e.g.
    # "options_first_hybrid:bullish" -- a small, fixed key space bounded
    # by (strategies) x (bullish/bearish), not unbounded growth, so this
    # stays consistent with this file's "bounded, fixed fields" principle
    # in spirit even though the container itself is a dict.
    #
    # Strategies only ever READ this (strategies/base.py's evaluate()
    # contract explicitly forbids mutating state) -- engine.py writes it
    # back after evaluate() returns, the same caller-writes-after pattern
    # already used for update_from_features() below.
    watch_episodes: dict[str, dict] = field(default_factory=dict)

    # Previous feature values (for crossover detection)
    prev_ltp: float = 0.0
    prev_vwap: float = 0.0
    prev_rsi: float = 50.0
    prev_ema_9: float = 0.0
    prev_bb_width: float = 0.0
    prev_rel_vol: float = 1.0

    # Metadata
    sector_id: str = ""
    tier: int = 1
    tick_count: int = 0  # completed 1m feature observations
    last_feature_us: int = 0

    # Pre-breakout state (Phase 3C)
    pre_breakout_state: str = "idle"
    pre_breakout_entered_us: int = 0
    bb_width_declining_count: int = 0
    ticks_in_pre_breakout: int = 0

    def update_from_features(self, features: dict) -> None:
        """Snapshot current values as 'previous' and update from new features.

        Called AFTER strategy evaluation so that prev_* values reflect
        the state at evaluation time.
        """
        self.prev_ltp = features.get("ltp", self.prev_ltp)
        self.prev_vwap = features.get("vwap", self.prev_vwap)
        self.prev_rsi = features.get("rsi_14", self.prev_rsi)
        self.prev_ema_9 = features.get("ema_9", self.prev_ema_9)
        self.prev_bb_width = features.get("bb_width", self.prev_bb_width)
        self.prev_rel_vol = features.get("rel_vol_20d", self.prev_rel_vol)
        self.last_feature_us = features.get("timestamp_us", self.last_feature_us)
        self.tick_count += 1


class StateManager:
    """Manages per-symbol scanner state with bounded lifecycle."""

    def __init__(self):
        self._states: dict[str, ScannerSymbolState] = {}

    def get_or_create(self, symbol: str) -> ScannerSymbolState:
        if symbol not in self._states:
            self._states[symbol] = ScannerSymbolState(symbol=symbol)
        return self._states[symbol]

    def get(self, symbol: str) -> ScannerSymbolState | None:
        return self._states.get(symbol)

    @property
    def symbol_count(self) -> int:
        return len(self._states)

    @property
    def stats(self) -> dict:
        return {
            "symbols_tracked": len(self._states),
            "total_ticks": sum(s.tick_count for s in self._states.values()),
        }
