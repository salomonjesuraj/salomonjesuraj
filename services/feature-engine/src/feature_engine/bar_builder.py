"""Bar builder — tick -> 1m/5m/15m OHLC bar aggregation."""

from feature_engine.state import OHLCBar, SymbolState


def update_bars(state: SymbolState, ltp: float, volume_delta: int, exchange_ms: int):
    """
    Update 1m/5m/15m bar builders with new tick.
    Returns list of completed bars (timeframe_minutes, bar) when a bar closes.
    """
    completed = []

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
