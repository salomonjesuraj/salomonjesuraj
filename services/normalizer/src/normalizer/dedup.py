"""Duplicate tick detection using per-symbol ring buffer of exchange timestamps."""

from collections import deque


class TickDedup:
    """
    Detects duplicate ticks using per-symbol ring buffer of exchange timestamps.
    Key: (symbol, exchange_timestamp_ms)
    """

    def __init__(self, ring_size: int = 20):
        self._ring_size = ring_size
        self._rings: dict[str, deque[int]] = {}
        self._dupes = 0

    def is_duplicate(self, symbol: str, exchange_timestamp_ms: int) -> bool:
        """Returns True if this tick was already seen."""
        ring = self._rings.get(symbol)
        if ring is None:
            ring = deque(maxlen=self._ring_size)
            self._rings[symbol] = ring

        if exchange_timestamp_ms in ring:
            self._dupes += 1
            return True

        ring.append(exchange_timestamp_ms)
        return False

    @property
    def duplicate_count(self) -> int:
        return self._dupes
