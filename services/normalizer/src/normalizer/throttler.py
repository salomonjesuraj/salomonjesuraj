"""Tier-based tick throttler.

Tier 1: no throttling (forward every tick)
Tier 2: min 500ms between forwards
Tier 3: min 2000ms between forwards
"""

import time


class TierThrottler:
    def __init__(self, tier2_ms: int = 500, tier3_ms: int = 2000):
        self._thresholds = {
            1: 0,                     # no throttling
            2: tier2_ms / 1000.0,
            3: tier3_ms / 1000.0,
        }
        self._last_forwarded: dict[str, float] = {}
        self._dropped = 0

    def should_forward(self, symbol: str, tier: int) -> bool:
        """Returns True if this tick should be forwarded."""
        threshold = self._thresholds.get(tier, 0)
        if threshold == 0:
            return True

        now = time.monotonic()
        last = self._last_forwarded.get(symbol, 0)
        if now - last < threshold:
            self._dropped += 1
            return False

        self._last_forwarded[symbol] = now
        return True

    @property
    def dropped_count(self) -> int:
        return self._dropped
