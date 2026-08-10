"""Base strategy ABC.

All strategies must inherit from BaseStrategy and implement evaluate().

Design principles:
  - Pure function: evaluate() depends ONLY on features + prev state
  - Deterministic: same inputs → same output
  - No I/O: no Redis, no network, no disk
  - No side effects: does not mutate state (caller does that)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from scanner.state import ScannerSymbolState


@dataclass
class SignalCandidate:
    """Intermediate signal before scoring and suppression.

    Produced by a strategy when all sub-conditions are met.
    The scanner engine then scores, suppresses, and publishes.
    """

    symbol: str
    strategy_id: str
    signal_type: str                            # "bullish" | "bearish"
    conditions_met: dict[str, bool] = field(default_factory=dict)
    explanation: list[str] = field(default_factory=list)
    features_snapshot: dict = field(default_factory=dict)

    # Price levels (strategy-computed)
    entry_price: float = 0.0
    invalidation_price: float = 0.0
    target_price: float = 0.0

    # Watch-episode write-back (Phase W, optional -- only strategies that
    # opt into episode-freezing set these). A strategy may only READ
    # state.watch_episodes inside evaluate() (see the class docstring's
    # "must not mutate state" contract below); if it decides a watch
    # episode's ladder should be created/refreshed, it says so here and
    # the ENGINE (the caller) is the one that actually writes
    # state.watch_episodes[episode_key] = episode_snapshot, immediately
    # after evaluate() returns -- same pattern as state.update_from_features()
    # already being a caller-side, post-evaluate mutation.
    episode_key: str | None = None
    episode_snapshot: dict | None = None


class BaseStrategy(ABC):
    """Abstract base class for scanner strategies.

    Subclasses implement evaluate() which returns a SignalCandidate
    if all conditions are met, or None if not.
    """

    @property
    @abstractmethod
    def strategy_id(self) -> str:
        """Unique identifier for this strategy (e.g., 'vol_vwap_breakout')."""
        ...

    @abstractmethod
    def evaluate(
        self,
        features: dict,
        state: ScannerSymbolState,
    ) -> SignalCandidate | None:
        """Evaluate strategy conditions against current features.

        Args:
            features: current FeatureVectorV1 as dict
            state: per-symbol scanner state (contains prev_* values)

        Returns:
            SignalCandidate if all conditions met, None otherwise.

        Contract:
            - Must be pure: no I/O, no side effects
            - Must be deterministic: same inputs → same output
            - Must NOT mutate state (caller handles state updates)
        """
        ...
