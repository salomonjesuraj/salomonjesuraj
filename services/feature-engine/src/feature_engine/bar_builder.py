"""Bar builder — tick -> 1m/5m/15m OHLC bar aggregation."""

from feature_engine.state import OHLCBar, SymbolState

# Grace period after a bar's window has fully elapsed before the
# wall-clock timer (force_close_stale_bars) force-closes it -- gives a
# real, in-flight tick a fair chance to close it the normal way first,
# so the timer only ever acts on genuine quiet/illiquid gaps, never
# races the tick-driven path in update_bars() below.
FORCE_CLOSE_GRACE_MS = 3_000


def update_bars(
    state: SymbolState, ltp: float, volume_delta: int, exchange_ms: int
) -> list[tuple[int, OHLCBar]]:
    """
    Update 1m/5m/15m bar builders with new tick.
    Returns list of completed bars (timeframe_minutes, bar) when a bar closes.
    """
    completed: list[tuple[int, OHLCBar]] = []

    for tf_minutes, bar_attr in [(1, "bar_1m"), (5, "bar_5m"), (15, "bar_15m")]:
        bar: OHLCBar = getattr(state, bar_attr)
        bar_duration_ms = tf_minutes * 60 * 1000

        # Determine bar boundary
        bar_start = (exchange_ms // bar_duration_ms) * bar_duration_ms

        if bar.bar_start_ms == 0:
            # First tick -- initialize bar
            bar.bar_start_ms = bar_start

        if bar_start != bar.bar_start_ms:
            # New bar period -- close previous bar and start new one
            if bar.tick_count > 0:
                completed.append((tf_minutes, bar))

            # Reset for new bar
            new_bar = OHLCBar(
                open=ltp,
                high=ltp,
                low=ltp,
                close=ltp,
                volume=max(volume_delta, 0),
                tick_count=1,
                bar_start_ms=bar_start,
            )
            setattr(state, bar_attr, new_bar)
        else:
            # Same bar -- update
            if bar.tick_count == 0:
                bar.open = ltp
            bar.high = max(bar.high, ltp)
            bar.low = min(bar.low, ltp)
            bar.close = ltp
            bar.volume += max(volume_delta, 0)
            bar.tick_count += 1

    return completed


def force_close_stale_bars(state: SymbolState, now_ms: int) -> list[tuple[int, OHLCBar]]:
    """Pipeline audit fix C3: update_bars() above only closes a bar when
    a tick from the *next* period arrives -- for a symbol that goes
    quiet across a bar boundary (illiquid names, or any ingestion
    micro-gap), the bar-close event, and every indicator gated on it
    (RSI/MACD/ATR/structure/zones/ICT via bar_closed_1m), was previously
    delayed by however long the next tick took to arrive, with no
    periodic flush to force it. This is the wall-clock-driven
    complement: called from a periodic timer (see engine.py's
    bar_flush_timer), not from a tick, so it is the one place in this
    module that measures "has this bar's window elapsed" against real
    time instead of the next tick's own timestamp.

    A force-closed bar is marked spent by zeroing tick_count (NOT by
    resetting bar_start_ms) -- so when a real tick eventually does
    arrive for a later period, update_bars()'s own
    `if bar.tick_count > 0: completed.append(...)` guard correctly
    skips re-emitting the same bar a second time; only a genuinely new
    bar gets created from that tick, exactly the normal path.
    """
    completed: list[tuple[int, OHLCBar]] = []
    for tf_minutes, bar_attr in [(1, "bar_1m"), (5, "bar_5m"), (15, "bar_15m")]:
        bar: OHLCBar = getattr(state, bar_attr)
        if bar.tick_count <= 0 or bar.bar_start_ms <= 0:
            continue
        bar_duration_ms = tf_minutes * 60 * 1000
        bar_end_ms = bar.bar_start_ms + bar_duration_ms
        if now_ms >= bar_end_ms + FORCE_CLOSE_GRACE_MS:
            completed.append((tf_minutes, bar))
            bar.tick_count = 0
    return completed
