"""Out-of-order tick detection — EBIE EB-0.

Companion to TickDedup (dedup.py): dedup catches EXACT duplicate exchange
timestamps; this catches a tick whose exchange_timestamp_ms is OLDER than
the newest one already seen for that symbol -- a real ordering violation,
not just a repeat. Nothing in this codebase checked for this before (see
docs/EBIE-IMPLEMENTATION-QUESTIONS.md's codebase survey).

Out-of-order ticks are never dropped -- that would silently lose real
data, which the EBIE authorization's non-negotiable rules explicitly
forbid ("no repainting", "no schema/provider parsing failure allowed to
poison historical research data"). This only flags them so downstream
Data Quality scoring (feature-engine) can account for a symbol whose feed
has been misbehaving.
"""

from __future__ import annotations


class OutOfOrderDetector:
    """Tracks the newest exchange_timestamp_ms seen per symbol and flags
    any tick that arrives with an older one."""

    def __init__(self):
        self._last_ts: dict[str, int] = {}
        self._out_of_order_count = 0

    def check(self, symbol: str, exchange_timestamp_ms: int) -> bool:
        """Returns True if this tick is out-of-order (older than the
        newest one already seen for this symbol).

        Deliberately does NOT advance the "last seen" timestamp for an
        out-of-order tick -- it should stay the newest timestamp actually
        observed, not regress to a late-arriving old one.
        """
        last = self._last_ts.get(symbol)
        if last is not None and exchange_timestamp_ms < last:
            self._out_of_order_count += 1
            return True
        self._last_ts[symbol] = exchange_timestamp_ms
        return False

    @property
    def out_of_order_count(self) -> int:
        return self._out_of_order_count
