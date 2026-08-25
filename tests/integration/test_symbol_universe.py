"""Real checks against `infusion:symbols`, the live F&O universe hash
every scanner/api/dashboard component this session touched ultimately
reads from (option_chain_queue.py's `_symbol_universe()`,
ebie_state_queue.py's sweep, ai_query.py's `load_known_symbols()`, the
dashboard's /api/symbols).
"""

from __future__ import annotations

import msgpack

SYMBOLS_KEY = "infusion:symbols"


async def test_symbol_universe_is_populated(redis_client) -> None:
    count = await redis_client.hlen(SYMBOLS_KEY)
    assert count > 0, (
        f"{SYMBOLS_KEY} is empty -- scheduler's bootstrap hasn't run, or "
        "the stack was just started and hasn't finished its first sweep yet."
    )


async def test_symbol_universe_entries_are_real_decodable_metadata(redis_client) -> None:
    """Every entry must be a real msgpack dict carrying a `symbol` field --
    the exact shape option_chain_queue.py's `_symbol_universe()` and
    ai_query.py's `load_known_symbols()` both depend on to build their own
    symbol lists (a raw decode failure there is silently swallowed as
    "skip this entry", so a corrupt universe wouldn't otherwise be
    noticed until real coverage quietly dropped)."""
    raw = await redis_client.hgetall(SYMBOLS_KEY)
    assert raw, "no entries to check -- see test_symbol_universe_is_populated"

    decoded = 0
    for meta_raw in raw.values():
        meta = msgpack.unpackb(meta_raw, raw=False)
        assert isinstance(meta, dict), f"non-dict symbol metadata: {meta!r}"
        assert meta.get("symbol"), f"symbol metadata missing its own 'symbol' field: {meta!r}"
        decoded += 1

    assert decoded == len(raw), "some symbol entries failed to decode as real msgpack dicts"


async def test_symbol_universe_is_a_realistic_nse_fo_size(redis_client) -> None:
    """Not a strict count (the real F&O universe changes over time), just
    a sanity band -- catches "someone flushed Redis and only one test
    symbol got re-seeded" without hard-coding today's exact 208."""
    count = await redis_client.hlen(SYMBOLS_KEY)
    assert 50 <= count <= 500, f"symbol universe size {count} is outside a plausible NSE F&O range"
