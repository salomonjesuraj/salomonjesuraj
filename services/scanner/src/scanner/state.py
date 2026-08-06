"""Per-symbol mutable state for the scanner engine.

Tracks prior feature values for crossover detection (e.g., VWAP reclaim).
This is scanner-domain state — NOT feature engine state.

Design principles:
  - Bounded: fixed fields, no growing collections
  - Deterministic: same input sequence → same state
  - Lightweight: dataclass, no heavy allocations
"""

from dataclasses import dataclass


@dataclass
class ScannerSymbolState:
    """Mutable per-symbol state for crossover and temporal detection."""

    symbol: str

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
